import hashlib
import json
import re
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.catalog_classification import RULES, category_key, infer_sale_unit, resolve_category_ids
from test_catalog_imports import package, snapshot


SPARK_PLUG = 'Свеча зажигания Audi 80, VW Golf/LT/Passat/Polo/T4 1.3-2.5 85-00 (Z92)'


@pytest.fixture
def author(db):
    from app.auth import User
    db.add(User(id='admin-test', role='admin', is_active=True, password_hash='unused-test-password'))
    db.commit()


def test_approved_name_rules_cover_the_entire_reviewed_pdf_without_conflicting_names():
    assert RULES['source']['rowCount'] == 24689
    assert len(RULES['exact']) == 24632
    # Digest of every normalized name/category pair in the approved 24,689-row mapping.
    payload = json.dumps(RULES['exact'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    assert hashlib.sha256(payload).hexdigest() == '4dd88811fc9a0364b2b234c84d64e0f3d5ed56c53ec9f1a081c89b26ee2eadf0'
    assert len(RULES['categories']) == 23
    assert set(RULES['exact'].values()) == set(RULES['categories'])
    assert sum(infer_sale_unit(name) is not None for name in RULES['exact']) == 7
    assert category_key('  СВЕЧА зажигания Audi 80, VW Golf/LT/Passat/Polo/T4 1.3-2.5 85-00 (Z92) ') == 'existing:ignition'
    assert category_key('Воздухоочиститель в сб. МТЗ 82 (Альтернатива)') == 'existing:filters'
    assert category_key('Антифриз HEPU -40 1.5л зеленый концентрат (P999GRN)') == 'existing:oils'


def test_new_names_require_a_unanimous_supported_first_word():
    assert category_key('Фильтр совершенно новый XYZ') == 'existing:filters'
    assert category_key('Масло совершенно новое XYZ 5л') == 'existing:oils'
    assert category_key('Свеча совершенно новая XYZ') is None  # Ignition and glow plugs differ.
    assert category_key('Неведомая деталь XYZ') is None
    assert category_key('Фильтр2 неизвестной модели') is None
    assert category_key('М10 неизвестная деталь') is None


@pytest.mark.parametrize('name, expected', [
    ('Компрессор JAC N75, N80, N120 КАМАЗ Компас-9/12 (ISF 3.8)', 'new:hydraulics-pneumatics'),
    ('Компрессор эл. 2-х цил. Autovirazh 75л/мин (AV-010750)', 'new:tools-workshop'),
    ('Компрессор на пневматический сигнал 12в ST-1509 12V', 'new:electrics-lighting'),
    ('Валик декомпрессора Т-16/25/30/40 Д37М-1019060А-02', 'new:engine-fuel-exhaust'),
    ('Амортизатор подвески глушителя УРАЛ 375Б-1203090-01', 'new:engine-fuel-exhaust'),
    ('Рычаг поворотный МТЗ-80 50-3001040-А1', 'new:steering'),
    ('Шарнир карданный 1/2" 39711', 'new:tools-workshop'),
    ('Компрессор неизвестной модели XYZ', None),
    ('Рессора совершенно новая XYZ', 'existing:suspension'),
])
def test_reviewed_purpose_corrections_do_not_confuse_word_fragments(name, expected):
    assert category_key(name) == expected


def test_every_first_word_rule_has_at_least_three_unanimous_distinct_names():
    examples = defaultdict(list)
    for name, key in RULES['exact'].items():
        first = re.match(r'[^\W\d_]+(?:-[^\W\d_]+)*(?![\w-])', name)
        if first:
            examples[first[0]].append(key)
    expected = {word: keys[0] for word, keys in examples.items() if len(keys) >= 3 and len(set(keys)) == 1}
    assert RULES['firstWord'] == expected


@pytest.mark.parametrize('name, expected', [
    ('Болт М10 (за 1кг)', 'кг'), ('Болт (ЗА 1 КГ)', 'кг'),
    ('Трубка за 1м inf101411', 'м'), ('Трубка за1м', 'м'),
    ('Смазка 1кг', None), ('Масло 5л', None), ('Шланг 1м в упаковке', None),
    ('Лист за 1м2', None), ('Болт за 10кг', None), ('За 1кг или за 1м', None),
])
def test_only_explicit_sale_units_are_inferred(name, expected):
    assert infer_sale_unit(name) == expected


def test_category_resolution_uses_existing_names_and_explicit_preferred_ids_only():
    cat = lambda id, name: SimpleNamespace(id=id, name=name)
    resolved = resolve_category_ids([cat('replacement-oil', '  Масла и жидкости '), cat('filters', 'Фильтры'),
                                     cat('filter-copy', 'фильтры'), cat('ignition', 'Переименованная категория'),
                                     cat('fasteners-id', 'Крепёж')])
    assert resolved['existing:oils'] == 'replacement-oil'
    assert resolved['existing:filters'] == 'filters'
    assert resolved['new:fasteners'] == 'fasteners-id'
    assert 'existing:ignition' not in resolved
    assert 'new:other' not in resolved
    assert 'existing:filters' not in resolve_category_ids([cat('first', 'Фильтры'), cat('second', 'ФИЛЬТРЫ')])


def add_category(db, id, name):
    from app.catalog import Category
    db.add(Category(id=id, name=name, normalized_name=name.casefold()))
    db.flush()


def test_file_prepare_selects_existing_categories_once_and_never_creates_them(db, author):
    from app.catalog import Category
    from app.catalog_imports import package_dto
    from app.catalog_sources import CatalogSourceRow
    for id, name in [('filters', 'Фильтры'), ('oils', 'Масла и жидкости'), ('ignition', 'Свечи зажигания'), ('fasteners-id', 'Крепёж')]:
        add_category(db, id, name)
    names = ['Фильтр новый XYZ', 'Масло новое XYZ 5л', SPARK_PLUG, 'Болт М 10х 40 (за 1кг)', 'Неведомая деталь XYZ']
    p = package(db, snapshot([CatalogSourceRow(position=i, name=name, stock=Decimal('2.125'), price_kopecks=101) for i, name in enumerate(names, 1)]))
    rows = package_dto(db, p)['rows']['items']
    assert [r['categoryId'] for r in rows] == ['filters', 'oils', 'ignition', 'fasteners-id', None]
    assert [r['unit'] for r in rows] == ['шт', 'шт', 'шт', 'кг', 'шт']
    assert db.scalar(select(func.count()).select_from(Category)) == 4


def test_missing_categories_stay_manual_and_adapter_names_are_not_classified(db, author):
    from app.catalog import Category
    from app.catalog_imports import package_dto
    from app.catalog_sources import CatalogSourceRow
    source = snapshot([CatalogSourceRow(position=1, name='Фильтр новый XYZ', stock=Decimal('1'), price_kopecks=101)])
    assert package_dto(db, package(db, source))['rows']['items'][0]['categoryId'] is None
    assert db.scalar(select(func.count()).select_from(Category)) == 0
    add_category(db, 'filters', 'Фильтры')
    source.mode, source.namespace = 'adapter', 'adapter:test'
    source.rows[0].source_product_id = 'stable-id'
    source.rows[0].name = 'Фильтр за 1м'
    row = package_dto(db, package(db, source))['rows']['items'][0]
    assert row['categoryId'] is None and row['unit'] == 'шт'


def test_manual_units_categories_and_mapped_presentation_survive_refresh_and_repeat(db, author):
    from app.catalog import Product, ProductPresentation
    from app.catalog_imports import DecisionPatch, apply_import, package_dto, refresh_import, save_decisions
    from app.catalog_sources import CatalogSourceRow
    add_category(db, 'fasteners-id', 'Крепёж')
    add_category(db, 'manual-id', 'Моя категория')
    source = snapshot([CatalogSourceRow(position=1, name='Болт М 10х 40 (за 1кг)', stock=Decimal('9.785'), price_kopecks=101, unit='л')])
    p = package(db, source)
    assert package_dto(db, p)['rows']['items'][0]['unit'] == 'л'  # Source beats name inference.
    save_decisions(db, p.id, DecisionPatch(filter='all', unit='м', categoryId='manual-id'), '"1"')
    refresh_import(db, p.id, '"2"')
    row = package_dto(db, p)['rows']['items'][0]
    assert row['unit'] == 'м' and row['categoryId'] == 'manual-id'
    apply_import(db, p.id, '"3"', str(uuid4()), True, 'admin-test')
    product = db.scalar(select(Product))
    presentation = db.get(ProductPresentation, product.id)
    presentation.website_title = 'Мой заголовок'
    db.commit()
    source.observed_at += timedelta(seconds=1)
    source.rows[0].unit = None
    repeated = package(db, source)
    row = package_dto(db, repeated)['rows']['items'][0]
    assert row['unit'] == 'м' and row['targetProductId'] == product.id and row['categoryId'] is None
    refresh_import(db, repeated.id, '"1"')
    apply_import(db, repeated.id, '"2"', str(uuid4()), True, 'admin-test')
    assert presentation.category_id == 'manual-id' and presentation.website_title == 'Мой заголовок'

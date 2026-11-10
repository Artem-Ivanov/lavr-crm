import copy
import hashlib
from io import BytesIO
import json
from pathlib import Path
import stat

from PIL import Image
import pytest
from sqlalchemy import delete, event, func, select, text

from app.catalog import Category, Product, ProductPresentation
from app.catalog_imports import CatalogImport, CatalogImportRow
from app.catalog_release import LOCK_KEY, ReleaseError, conditions, execute_release
from app.catalog_sources import CatalogSourceRow, row_signature
from app.common import utcnow
from app.content import Media


@pytest.fixture
def release(db, tmp_path, monkeypatch):
    from app import catalog_release
    from app.auth import User
    from app.config import settings
    from decimal import Decimal
    db.add(User(id='release-admin', role='admin', is_active=True, password_hash='unused-test-password'))
    db.add(Category(id='filters', name='Фильтры', normalized_name='фильтры', position=7, is_visible=True, show_in_menu=True))
    db.commit()
    monkeypatch.setattr(settings, 'media_dir', tmp_path / 'media')
    rows = [CatalogSourceRow(position=1, name='Фильтр', stock=Decimal('9.785'), price_kopecks=101),
            CatalogSourceRow(position=2, name='Патрубок', sku='same', stock=Decimal('3'), price_kopecks=66000),
            CatalogSourceRow(position=3, name='Патрубок', sku='same', stock=Decimal('2'), price_kopecks=56500),
            CatalogSourceRow(position=4, name='Болт набор', kind='bundle', stock=None, price_kopecks=100, raw_cells=['', '', 'Болт набор', '1', '']),
            CatalogSourceRow(position=5, name='Масло просроченное', stock=Decimal('1.125'), price_kopecks=500, unit='л')]
    monkeypatch.setattr(catalog_release, 'parse_pdf', lambda path: (copy.deepcopy(rows), 1))
    pdf = tmp_path / 'source.pdf'; pdf.write_bytes(b'%PDF-1.7 test source only')
    assets = tmp_path / 'assets'; assets.mkdir()
    images = []
    for key, color in [('filter-type', 'red'), ('other-type', 'blue')]:
        out = BytesIO()
        with Image.new('RGB', (8, 6), color) as image:
            image.save(out, format='WEBP')
        data = out.getvalue(); (assets / (key + '.webp')).write_bytes(data)
        images.append({'key': key, 'file': key + '.webp', 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'width': 8, 'height': 6, 'alt': 'Иллюстрация типа товара'})
    manifest = {'schemaVersion': 1, 'releaseId': 'pdf-' + hashlib.sha256(pdf.read_bytes()).hexdigest()[:8],
                'source': {'sha256': hashlib.sha256(pdf.read_bytes()).hexdigest(), 'pageCount': 1, 'rowCount': 5, 'fileName': 'source.pdf'},
                'policy': {'observationTime': 'upload', 'missingStock': 'zero', 'duplicateRows': 'distinct', 'defaultUnit': 'шт', 'fractionalStock': 'preserve', 'specialRows': 'standalone'},
                'categories': [{'key': 'existing:filters', 'id': 'filters', 'name': 'Фильтры', 'action': 'reuse', 'position': 7, 'isVisible': True, 'showInMenu': True},
                               {'key': 'new:other', 'id': None, 'name': 'Прочее', 'action': 'create', 'position': 8, 'isVisible': True, 'showInMenu': False}],
                'images': images,
                'rows': [{'position': row.position, 'signature': row_signature(row), 'categoryKey': 'existing:filters' if row.position == 1 else 'new:other',
                          'imageKey': 'filter-type' if row.position == 1 else 'other-type', 'unit': row.unit or 'шт', 'conditionFlags': ['expired'] if row.position == 5 else []} for row in rows]}
    manifest_path = tmp_path / 'manifest.json'
    receipt = tmp_path / 'journal' / 'receipt.json'
    def run():
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False))
        return execute_release(db, manifest_path, pdf, assets, receipt, 'release-admin')
    return dict(run=run, manifest=manifest, rows=rows, pdf=pdf, assets=assets, receipt=receipt)


@pytest.mark.parametrize('damage', ['source-hash', 'image-hash', 'image-bytes', 'missing-row', 'signature', 'symlink', 'traversal', 'condition'])
def test_invalid_release_fails_before_database_mutations(db, release, damage):
    m = release['manifest']
    if damage == 'source-hash': m['source']['sha256'] = '0' * 64
    if damage == 'image-hash': m['images'][0]['sha256'] = '0' * 64
    if damage == 'image-bytes':
        image = m['images'][0]; data = b'not a WebP'
        (release['assets'] / image['file']).write_bytes(data)
        image['sha256'], image['bytes'] = hashlib.sha256(data).hexdigest(), len(data)
    if damage == 'missing-row': m['rows'].pop()
    if damage == 'signature': m['rows'][0]['signature'] = '0' * 64
    if damage == 'traversal': m['images'][0]['file'] = '../escape.webp'
    if damage == 'condition': m['rows'][0]['conditionFlags'] = ['used']
    if damage == 'symlink':
        image = release['assets'] / m['images'][0]['file']
        other = release['assets'].parent / 'outside.webp'; image.rename(other); image.symlink_to(other)
    with pytest.raises(ReleaseError): release['run']()
    for model in (CatalogImport, Product, Media):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert db.scalar(select(func.count()).select_from(Category)) == 1


def test_exact_stock_distinct_rows_media_category_and_private_repeat_recovery(db, release):
    before = utcnow()
    journal = release['run']()
    assert journal['phase'] == 'complete' and journal['counts']['rows'] == 5
    assert len(set(journal['rowProductIds'].values())) == 5
    products = {position: db.get(Product, product_id) for position, product_id in journal['rowProductIds'].items()}
    assert str(products['1'].stock) == '9.785' and products['1'].unit == 'шт'
    assert products['2'].price_kopecks == 66000 and products['3'].price_kopecks == 56500
    assert products['4'].stock == 0 and products['5'].unit == 'л'
    assert products['5'].name == 'Масло просроченное'
    presentation = db.get(ProductPresentation, products['5'].id)
    assert presentation.website_title.startswith('Просрочено: ')
    assert 'иллюстрация типа товара' in presentation.website_description
    assert len(presentation.image_ids) == 1
    package = db.get(CatalogImport, journal['importId'])
    observed = package.observed_at
    assert before <= observed <= utcnow() and package.raw_pdf == release['pdf'].read_bytes()
    missing = db.scalar(select(CatalogImportRow).where(CatalogImportRow.import_id == package.id, CatalogImportRow.position == 4))
    assert missing.source_data['releaseOriginalStock'] is None and missing.source_data['raw_cells'][-1] == ''
    assert stat.S_IMODE(release['receipt'].stat().st_mode) == 0o600
    assert stat.S_IMODE(release['receipt'].parent.stat().st_mode) == 0o700
    old = db.get(Category, 'filters')
    assert (old.position, old.is_visible, old.show_in_menu, old.version) == (7, True, True, 1)
    assert db.get(Category, 'catalog:other').is_visible
    saved_categories = dict(db.execute(select(CatalogImportRow.position, CatalogImportRow.category_id).where(CatalogImportRow.import_id == package.id)).all())
    assert saved_categories == {1: 'filters', 2: 'catalog:other', 3: 'catalog:other', 4: 'catalog:other', 5: 'catalog:other'}
    counts = tuple(db.scalar(select(func.count()).select_from(model)) for model in (Product, Media, Category))
    assert counts == (5, 2, 3)  # Includes the existing import hidden fallback.
    repeated = release['run']()
    assert repeated['rowProductIds'] == journal['rowProductIds']
    release['receipt'].unlink()  # Recover identity and bindings from the DB, not a second upload.
    recovered = release['run']()
    assert recovered['rowProductIds'] == journal['rowProductIds']
    assert db.get(CatalogImport, journal['importId']).observed_at == observed
    assert counts == tuple(db.scalar(select(func.count()).select_from(model)) for model in (Product, Media, Category))
    db.execute(delete(CatalogImportRow).where(CatalogImportRow.import_id == package.id))
    package.rows_expired = True; db.commit()
    assert release['run']()['rowProductIds'] == journal['rowProductIds']
    release['receipt'].unlink()
    with pytest.raises(ReleaseError, match='expired_rows_without_receipt_map'): release['run']()


def test_failure_after_atomic_apply_keeps_hidden_cards_and_resumes_without_duplicates(db, release):
    schema = db.get_bind().get_execution_options()['schema_translate_map'][None]
    db.execute(text(f"CREATE FUNCTION {schema}.block_publication() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.website_description IS NOT NULL THEN RAISE EXCEPTION 'simulated publication failure'; END IF; RETURN NEW; END $$"))
    db.execute(text(f'CREATE TRIGGER block_publication BEFORE UPDATE ON {schema}.product_presentations FOR EACH ROW EXECUTE FUNCTION {schema}.block_publication()'))
    db.commit()
    with pytest.raises(ReleaseError): release['run']()
    assert db.scalar(select(CatalogImport.status)) == 'applied'
    assert db.scalar(select(func.count()).select_from(Product)) == 5
    assert all(not visible for visible in db.scalars(select(Category.is_visible).join(ProductPresentation, ProductPresentation.category_id == Category.id)))
    assert set(db.scalars(select(CatalogImportRow.category_id))) == {None}
    saved = json.loads(release['receipt'].read_text())
    db.execute(text(f'DROP TRIGGER block_publication ON {schema}.product_presentations'))
    db.execute(text(f'DROP FUNCTION {schema}.block_publication()'))
    db.commit()
    final = release['run']()
    assert final['phase'] == 'complete' and final['rowProductIds'] == saved['rowProductIds']
    assert db.scalar(select(func.count()).select_from(Product)) == 5
    assert db.scalar(select(func.count()).select_from(Media)) == 2


def test_foreign_inventory_is_never_overwritten_or_deleted(db, release):
    db.add(Product(id='foreign', external_id='legacy', sku='', brand='', name='Чужой товар', price_kopecks=123, stock=1, unit='шт'))
    db.commit()
    with pytest.raises(ReleaseError, match='inventory'): release['run']()
    assert db.get(Product, 'foreign').name == 'Чужой товар'
    assert db.scalar(select(func.count()).select_from(CatalogImport)) == 0


def test_existing_same_name_category_and_ready_content_are_reused_without_changes(db, release):
    from app.auth import User
    from app.content import persist_media
    category = Category(id='user-other', name='Прочее', normalized_name='прочее', position=99, is_visible=True, show_in_menu=True, version=5)
    db.add(category); db.commit()
    image = release['manifest']['images'][0]
    media = persist_media(db, db.get(User, 'release-admin'), (release['assets'] / image['file']).read_bytes(), 'image/webp', image['alt'])
    journal = release['run']()
    assert journal['categoryBindings']['new:other'] == 'user-other'
    assert (category.position, category.is_visible, category.show_in_menu, category.version) == (99, True, True, 5)
    assert journal['mediaBindings']['filter-type'] == media['id']
    assert db.scalar(select(func.count()).select_from(Media)) == 2
    assert db.get(Category, 'catalog:other') is None


def test_session_lock_survives_existing_helper_commits(db, release):
    observed = []
    def after_commit(session):
        with session.get_bind().connect() as conn:
            acquired = conn.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': LOCK_KEY})
            observed.append(acquired)
            if acquired: conn.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': LOCK_KEY})
    event.listen(db, 'after_commit', after_commit)
    try: release['run']()
    finally: event.remove(db, 'after_commit', after_commit)
    assert len(observed) >= 5 and not any(observed)
    with db.get_bind().connect() as conn:
        assert conn.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': LOCK_KEY})
        conn.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': LOCK_KEY})


@pytest.mark.parametrize('modification', ['unit', 'version'])
def test_manual_change_to_ready_package_fails_closed(db, release, modification):
    schema = db.get_bind().get_execution_options()['schema_translate_map'][None]
    db.execute(text(f"CREATE FUNCTION {schema}.block_import() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'simulated apply failure'; END $$"))
    db.execute(text(f'CREATE TRIGGER block_import BEFORE INSERT ON {schema}.products FOR EACH ROW EXECUTE FUNCTION {schema}.block_import()'))
    db.commit()
    with pytest.raises(ReleaseError): release['run']()
    package = db.scalar(select(CatalogImport))
    assert package.status == 'ready' and package.version == 2
    if modification == 'unit': db.scalar(select(CatalogImportRow).order_by(CatalogImportRow.position)).unit = 'кг'
    else: package.version = 3
    db.execute(text(f'DROP TRIGGER block_import ON {schema}.products'))
    db.execute(text(f'DROP FUNCTION {schema}.block_import()')); db.commit()
    with pytest.raises(ReleaseError, match='planned_decisions_changed|invalid_package_state'): release['run']()
    assert db.scalar(select(func.count()).select_from(Product)) == 0


def test_condition_flags_do_not_confuse_unpacked_with_used():
    assert conditions('Подшипник б/уп.') == []
    assert conditions('Суппорт б/у') == ['used']
    assert conditions('Масло просроченное') == ['expired']
    assert conditions('Накладки некомплект') == ['incomplete']


def test_media_removed_after_apply_cannot_publish_and_can_resume_after_correction(db, release):
    removed = []
    def after_commit(session):
        if removed: return
        with session.get_bind().begin() as conn:
            if conn.scalar(select(CatalogImport.status)) != 'applied': return
            media = dict(conn.execute(select(Media.__table__).order_by(Media.id)).mappings().first())
            removed.append(media)
            conn.execute(delete(Media).where(Media.id == media['id']))
    event.listen(db, 'after_commit', after_commit)
    try:
        with pytest.raises(ReleaseError, match='publication_media_changed'): release['run']()
    finally:
        event.remove(db, 'after_commit', after_commit)
    assert len(removed) == 1 and db.scalar(select(CatalogImport.status)) == 'applied'
    assert all(not visible for visible in db.scalars(select(Category.is_visible).join(ProductPresentation, ProductPresentation.category_id == Category.id)))
    db.execute(Media.__table__.insert(), removed); db.commit()
    assert release['run']()['phase'] == 'complete'
    assert db.scalar(select(func.count()).select_from(Media)) == 2
    assert db.scalar(select(func.count()).select_from(Product)) == 5

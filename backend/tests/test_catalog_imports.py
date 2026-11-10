from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.common import ApiError, utcnow



@pytest.fixture(autouse=True)
def import_author(db):
    from app.auth import User
    db.add(User(id='admin-test', role='admin', is_active=True, password_hash='unused-test-password'))
    db.commit()


def snapshot(rows=None, **kwargs):
    from app.catalog_sources import CatalogSnapshot, CatalogSourceRow
    return CatalogSnapshot(namespace='file:main-stock:retail', mode='file', observed_at=utcnow() - timedelta(minutes=1), rows=rows or [CatalogSourceRow(position=1, name='Новая деталь', sku='001', stock=Decimal('9.785'), price_kopecks=39500)], **kwargs)


def package(db, source=None):
    from app.catalog_imports import create_snapshot_import
    result = create_snapshot_import(db, source or snapshot(), author_id='admin-test')
    db.commit()
    return result


@pytest.fixture
def legacy_unknown_unit_product(db):
    from app.catalog import Category, Product, ProductPresentation
    from app.catalog_imports import CatalogSourceMapping, identity_for

    source = snapshot()
    category = Category(id='legacy-hidden', name='Неразобранное', normalized_name='неразобранное', position=1000000, is_visible=False, show_in_menu=False)
    product = Product(id='legacy-unknown', external_id='pdf:legacy-unknown', sku='001', brand='', name='Новая деталь', price_kopecks=39500,
                      stock=Decimal('9.785'), reserved=0, unit=None, accounting_source=source.namespace,
                      stock_observed_at=utcnow() - timedelta(minutes=2), accounting_version=1)
    db.add_all([category, product])
    db.flush()
    db.add_all([ProductPresentation(product_id=product.id, category_id=category.id),
                CatalogSourceMapping(namespace=source.namespace, identity=identity_for(source, source.rows[0]), product_id=product.id)])
    db.commit()
    return product


def test_missing_unit_defaults_to_pieces_in_preview_and_applied_stock(db):
    from app.catalog import Product
    from app.catalog_sources import CatalogSourceRow
    from app.catalog_imports import package_dto, apply_import

    p = package(db, snapshot([CatalogSourceRow(position=1, name='Болт', stock=Decimal('14'), price_kopecks=82000)]))
    dto = package_dto(db, p)
    row = dto['rows']['items'][0]
    assert row['unit'] == 'шт'
    assert row['action'] == 'new' and row['issues'] == []
    assert dto['summary']['missingUnit'] == 0
    assert apply_import(db, p.id, '"1"', str(uuid4()), True, 'admin-test')['createdCount'] == 1
    product = db.scalar(select(Product))
    assert product.unit == 'шт' and product.stock == Decimal('14')


@pytest.mark.parametrize('fallback_id', ['existing-import-category', 'catalog-import-unclassified'])
def test_new_imports_stay_private_when_unclassified_category_names_are_visible(client, db, admin_headers, fallback_id):
    from app.catalog import Category, Product, ProductPresentation
    from app.catalog_sources import CatalogSourceRow
    from app.catalog_imports import apply_import

    db.add_all([
        Category(id='visible-unclassified', name='Неразобранное', normalized_name='неразобранное', position=1, is_visible=True, show_in_menu=True),
        Category(id=fallback_id, name='Неразобранное (импорт)', normalized_name='неразобранное (импорт)', position=2, is_visible=True, show_in_menu=True),
    ])
    db.commit()
    hidden_id = None
    for index in range(2):
        p = package(db, snapshot([CatalogSourceRow(position=1, name=f'Новая деталь {index}', stock=Decimal('1'), price_kopecks=100)]))
        assert apply_import(db, p.id, '"1"', str(uuid4()), True, 'admin-test')['createdCount'] == 1
        product = db.scalar(select(Product).where(Product.name == f'Новая деталь {index}'))
        presentation = db.get(ProductPresentation, product.id)
        category = db.get(Category, presentation.category_id)
        assert not category.is_visible and not category.show_in_menu
        assert client.get('/api/catalog/' + product.id).status_code == 404
        if hidden_id is None:
            hidden_id = category.id
        assert category.id == hidden_id
    assert db.get(Category, 'visible-unclassified').is_visible
    assert db.get(Category, fallback_id).is_visible
    assert db.scalar(select(func.count()).select_from(Category)) == 3


@pytest.mark.parametrize('unit', ['кг', 'л'])
def test_explicit_source_unit_preserves_fractional_stock(db, unit):
    from app.catalog import Product
    from app.catalog_sources import CatalogSourceRow
    from app.catalog_imports import package_dto, apply_import

    p = package(db, snapshot([CatalogSourceRow(position=1, name='Масло', stock=Decimal('2.125'), price_kopecks=101, unit=unit)]))
    row = package_dto(db, p)['rows']['items'][0]
    assert row['unit'] == unit and row['issues'] == []
    assert apply_import(db, p.id, '"1"', str(uuid4()), True, 'admin-test')['createdCount'] == 1
    product = db.scalar(select(Product))
    assert product.unit == unit and product.stock == Decimal('2.125')


@pytest.mark.parametrize('clear_unit', [False, True])
def test_missing_or_cleared_unit_preserves_confirmed_unit_and_reserved_stock(db, clear_unit):
    from app.catalog import Product
    from app.catalog_imports import package_dto, save_decisions, apply_import, DecisionPatch

    source = snapshot()
    source.rows[0].unit = 'кг'
    first = package(db, source)
    apply_import(db, first.id, '"1"', str(uuid4()), True, 'admin-test')
    product = db.scalar(select(Product))
    product.reserved = Decimal('1.125')
    db.commit()
    repeated = package(db)
    version = '"1"'
    if clear_unit:
        save_decisions(db, repeated.id, DecisionPatch(filter='all', unit=None), version)
        version = '"2"'
    row = package_dto(db, repeated)['rows']['items'][0]
    assert row['unit'] == 'кг' and row['issues'] == []
    assert row['before']['reservedStock'] == '1.125'
    assert apply_import(db, repeated.id, version, str(uuid4()), True, 'admin-test')['unchangedCount'] == 1
    db.refresh(product)
    assert product.unit == 'кг' and product.reserved == Decimal('1.125') and product.stock == Decimal('9.785')


@pytest.mark.parametrize('refresh_first', [False, True])
@pytest.mark.parametrize('linked_unit', [None, 'кг'])
def test_ready_package_without_unit_defaults_when_refreshed_or_applied(db, refresh_first, linked_unit):
    from app.catalog import Product
    from app.catalog_sources import CatalogSourceRow
    from app.catalog_imports import CatalogImportRow, package_dto, refresh_import, apply_import

    if linked_unit:
        first = package(db, snapshot([CatalogSourceRow(position=1, name='Болт', stock=Decimal('14'), price_kopecks=82000, unit=linked_unit)]))
        apply_import(db, first.id, '"1"', str(uuid4()), True, 'admin-test')
    p = package(db, snapshot([CatalogSourceRow(position=1, name='Болт', stock=Decimal('14'), price_kopecks=82000)]))
    row = db.scalar(select(CatalogImportRow).where(CatalogImportRow.import_id == p.id))
    row.unit = None
    row.issues = [{'code': 'MISSING_UNIT', 'message': 'Единица не задана.', 'blocking': linked_unit is not None}]
    row.blocking = linked_unit is not None
    row.action = 'review' if linked_unit else 'new'
    p.summary = {**p.summary, 'missingUnit': 1, 'conflict': int(row.blocking)}
    db.commit()
    assert package_dto(db, p)['rows']['items'][0]['unit'] is None
    version = '"1"'
    if refresh_first:
        refresh_import(db, p.id, version)
        dto = package_dto(db, p)
        assert dto['rows']['items'][0]['unit'] == (linked_unit or 'шт')
        assert dto['summary']['missingUnit'] == 0 and dto['summary']['conflict'] == 0
        version = '"2"'
    report = apply_import(db, p.id, version, str(uuid4()), True, 'admin-test')
    assert report['unchangedCount' if linked_unit else 'createdCount'] == 1
    product = db.scalar(select(Product))
    assert product.unit == (linked_unit or 'шт') and product.stock == Decimal('14')
    db.refresh(row)
    assert row.unit == (linked_unit or 'шт') and row.issues == []
    final = package_dto(db, p)
    assert final['summary']['missingUnit'] == 0
    assert final['summary']['conflict'] == 0


def test_fractional_missing_unit_requires_manual_unit_before_apply(db):
    from app.catalog import Product, ProductPresentation, Category
    from app.catalog_imports import package_dto, save_decisions, apply_import, DecisionPatch
    p = package(db)
    dto = package_dto(db, p)
    assert dto['summary']['missingUnit'] == 0
    assert db.scalar(select(func.count()).select_from(Product)) == 0
    row = dto['rows']['items'][0]
    assert row['proposedStock'] == '9.785'
    assert row['unit'] == 'шт' and row['action'] == 'review'
    assert any(issue['code'] == 'FRACTIONAL_PIECES' for issue in row['issues'])
    with pytest.raises(ApiError) as failure:
        apply_import(db, p.id, '"1"', str(uuid4()), True, 'admin-test')
    assert failure.value.code == 'UNRESOLVED_ROWS'
    assert any(issue['code'] == 'FRACTIONAL_PIECES' for issue in failure.value.details['issues'])
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Product)) == 0
    save_decisions(db, p.id, DecisionPatch(rowIds=[row['id']], unit='кг'), '"1"')
    report = apply_import(db, p.id, '"2"', str(uuid4()), True, 'admin-test')
    assert report['createdCount'] == 1
    product = db.scalar(select(Product))
    assert product.stock == Decimal('9.785') and product.unit == 'кг'
    assert product.reserved == 0 and product.accounting_source == 'file:main-stock:retail'
    assert not db.get(Category, db.get(ProductPresentation, product.id).category_id).is_visible


def test_duplicate_and_bundle_rows_require_explicit_resolution_and_never_merge(db):
    from app.catalog_sources import CatalogSourceRow
    from app.catalog_imports import package_dto, save_decisions, apply_import, DecisionPatch
    from app.catalog import Product
    rows = [CatalogSourceRow(position=1, name='Патрубок', sku='АГ1547,ПХ355,ВТМ5207791', stock=Decimal('3'), price_kopecks=66000),
            CatalogSourceRow(position=2, name='Патрубок', sku='АГ1547,ПХ355,ВТМ5207791', stock=Decimal('2'), price_kopecks=56500),
            CatalogSourceRow(position=3, name='Компонент', stock=Decimal('1'), price_kopecks=850, kind='component', context='Сборка')]
    p = package(db, snapshot(rows))
    dto = package_dto(db, p)
    assert all(not r['selected'] for r in dto['rows']['items'])
    first = dto['rows']['items'][0]
    save_decisions(db, p.id, DecisionPatch(rowIds=[first['id']], resolution='new', selected=True, unit='шт'), '"1"')
    report = apply_import(db, p.id, '"2"', str(uuid4()), True, 'admin-test')
    assert report['createdCount'] == 1 and report['skippedCount'] == 2
    assert db.scalar(select(Product)).price_kopecks == 66000
    repeated = package(db, snapshot(rows))
    assert all(r['targetProductId'] is None for r in package_dto(db, repeated)['rows']['items'])


def test_repeat_source_mapping_preserves_presentation_and_apply_replays_saved_report(db):
    from app.catalog import Product, ProductPresentation
    from app.catalog_imports import package_dto, save_decisions, apply_import, DecisionPatch
    p = package(db)
    save_decisions(db, p.id, DecisionPatch(filter='all', unit='кг'), '"1"')
    key = str(uuid4())
    report = apply_import(db, p.id, '"2"', key, True, 'admin-test')
    assert apply_import(db, p.id, '"2"', key, True, 'admin-test') == report
    product = db.scalar(select(Product))
    presentation = db.get(ProductPresentation, product.id)
    presentation.website_title = 'Мой заголовок'
    db.commit()
    p2 = package(db)
    assert package_dto(db, p2)['rows']['items'][0]['targetProductId'] == product.id
    apply_import(db, p2.id, '"1"', str(uuid4()), True, 'admin-test')
    assert db.scalar(select(func.count()).select_from(Product)) == 1
    assert db.get(ProductPresentation, product.id).website_title == 'Мой заголовок'


def test_changed_reserve_requires_refresh_and_no_rows_apply(db):
    from app.catalog import Product
    from app.catalog_imports import package_dto, save_decisions, apply_import, refresh_import, DecisionPatch
    p = package(db)
    save_decisions(db, p.id, DecisionPatch(filter='all', unit='кг'), '"1"')
    apply_import(db, p.id, '"2"', str(uuid4()), True, 'admin-test')
    p2 = package(db)
    product = db.scalar(select(Product))
    product.reserved = Decimal('1')
    db.commit()
    with pytest.raises(ApiError) as failure:
        apply_import(db, p2.id, '"1"', str(uuid4()), True, 'admin-test')
    assert failure.value.code == 'BASELINE_CONFLICT'
    db.rollback()
    refresh_import(db, p2.id, '"1"')
    assert package_dto(db, db.get(type(p2), p2.id))['rows']['items'][0]['before']['reservedStock'] == '1'
    assert apply_import(db, p2.id, '"2"', str(uuid4()), True, 'admin-test')['unchangedCount'] == 1


def test_adapter_uses_same_pipeline_and_owner_collision_blocks_atomic_write(db):
    from app.catalog_sources import CatalogSourceRow, CatalogSnapshot
    from app.catalog_imports import create_adapter_import, save_decisions, apply_import, DecisionPatch
    from app.catalog import Product
    class Adapter:
        def fetch_snapshot(self):
            return CatalogSnapshot(namespace='adapter:test', mode='adapter', observed_at=utcnow()-timedelta(minutes=1), rows=[CatalogSourceRow(position=1, name='От адаптера', stock=Decimal('2'), price_kopecks=100, source_product_id='stable-id', unit='шт')])
    p = create_adapter_import(db, Adapter(), author_id='admin-test')
    db.commit()
    assert apply_import(db, p.id, '"1"', str(uuid4()), True, 'admin-test')['createdCount'] == 1
    file_package = package(db)
    save_decisions(db, file_package.id, DecisionPatch(filter='all', unit='кг'), '"1"')
    with pytest.raises(ApiError) as failure:
        apply_import(db, file_package.id, '"2"', str(uuid4()), True, 'admin-test')
    assert failure.value.code == 'ACCOUNTING_OWNER_CONFLICT'
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Product)) == 1


def test_import_api_admin_csrf_bounded_upload_and_versions(client, admin_headers, manager_headers):
    base = '/api/admin/catalog/imports'
    assert client.get(base, headers=manager_headers).status_code == 403
    caps = client.get(base+'/capabilities', headers=admin_headers)
    assert caps.status_code == 200 and not caps.json()['adapter']['enabled']
    args = dict(files={'file':('report.pdf', b'%PDF-1.4\n', 'application/pdf')}, data={'observedAt':(utcnow()-timedelta(minutes=1)).isoformat(), 'priceConfirmed':'true'})
    assert client.post(base, **args, headers=admin_headers).status_code == 400
    headers = {**admin_headers,'Idempotency-Key':str(uuid4())}
    first = client.post(base, **args, headers=headers)
    assert first.status_code == 202, first.text
    assert first.json()['status'] == 'queued'
    replay = client.post(base, **args, headers=headers)
    assert replay.status_code == 202 and replay.json()['id'] == first.json()['id']
    assert client.get(base+'/'+first.json()['id'],params={'limit':101},headers=admin_headers).status_code == 422
    assert client.post(base, **args,headers={k:v for k,v in headers.items() if k != 'X-CSRF-Token'}).status_code == 403


def test_existing_similar_card_requires_explicit_link_or_new_without_auto_merge(db):
    from app.catalog import Product
    from app.catalog_imports import package_dto
    db.add(Product(id='legacy', external_id='erp-code', sku='001', brand='', name='Новая деталь', price_kopecks=100, stock=5, unit='шт'))
    db.commit()
    p = package(db)
    row = package_dto(db,p)['rows']['items'][0]
    assert row['targetProductId'] is None and row['selected'] is False
    assert row['candidateProductIds'] == ['legacy']
    assert any(issue['code']=='POSSIBLE_MATCH' for issue in row['issues'])


def test_cleared_unit_defaults_to_pieces_in_preview_and_apply(db):
    from app.catalog import Product
    from app.catalog_sources import CatalogSourceRow
    from app.catalog_imports import package_dto, save_decisions, apply_import, DecisionPatch
    p = package(db, snapshot([CatalogSourceRow(position=1, name='Болт', stock=Decimal('14'), price_kopecks=82000)]))
    save_decisions(db,p.id,DecisionPatch(filter='all',unit='кг'),'"1"')
    save_decisions(db,p.id,DecisionPatch(filter='all',unit=None),'"2"')
    dto = package_dto(db, p)
    assert dto['summary']['missingUnit'] == 0
    assert dto['rows']['items'][0]['unit'] == 'шт' and dto['rows']['items'][0]['issues'] == []
    assert apply_import(db,p.id,'"3"',str(uuid4()),True,'admin-test')['createdCount'] == 1
    product=db.scalar(select(Product))
    assert product.unit == 'шт' and product.stock == Decimal('14') and product.is_active


def test_stale_observation_and_duplicate_targets_prevent_all_writes(db):
    from app.catalog import Product
    from app.catalog_sources import CatalogSourceRow
    from app.catalog_imports import package_dto,save_decisions,apply_import,DecisionPatch
    p=package(db)
    save_decisions(db,p.id,DecisionPatch(filter='all',unit='кг'),'"1"')
    apply_import(db,p.id,'"2"',str(uuid4()),True,'admin-test')
    product=db.scalar(select(Product))
    previous=product.stock_observed_at
    source=snapshot([CatalogSourceRow(position=1,name='Другой код',sku='B',stock=Decimal('1'),price_kopecks=100),CatalogSourceRow(position=2,name='Ещё код',sku='C',stock=Decimal('2'),price_kopecks=200)])
    source.observed_at=previous-timedelta(hours=1)
    p2=package(db,source)
    rows=package_dto(db,p2)['rows']['items']
    for version,row in enumerate(rows,1):
        save_decisions(db,p2.id,DecisionPatch(rowIds=[row['id']],resolution='link',productId=product.id),'"'+str(version)+'"')
    with pytest.raises(ApiError) as failure:
        apply_import(db,p2.id,'"3"',str(uuid4()),True,'admin-test')
    assert failure.value.code == 'DUPLICATE_TARGET'
    db.rollback()
    save_decisions(db,p2.id,DecisionPatch(rowIds=[rows[1]['id']],selected=False),'"3"')
    with pytest.raises(ApiError) as failure:
        apply_import(db,p2.id,'"4"',str(uuid4()),True,'admin-test')
    assert failure.value.code == 'UNRESOLVED_ROWS'
    assert any(issue['code']=='STALE_OBSERVATION' for issue in failure.value.details['issues'])
    db.rollback()
    db.refresh(product)
    assert product.stock == Decimal('9.785') and product.stock_observed_at==previous


def test_apply_reports_package_and_etag_and_rejects_stale_decisions(client,db,admin_headers):
    from app.catalog_imports import create_snapshot_import
    p=create_snapshot_import(db,snapshot(),author_id='admin')
    db.commit()
    base='/api/admin/catalog/imports/'+p.id
    headers={**admin_headers,'If-Match':'"1"'}
    result=client.patch(base+'/decisions',json={'filter':'all','unit':'кг'},headers=headers)
    assert result.status_code==200 and result.json()['version']==2
    assert client.patch(base+'/decisions',json={'filter':'all','unit':'кг'},headers=headers).status_code==412
    result=client.post(base+'/apply',json={'acknowledgeSkipped':True},headers={**admin_headers,'If-Match':'"2"','Idempotency-Key':str(uuid4())})
    assert result.status_code==200 and result.json()['report']['createdCount']==1 and result.headers['etag']=='"3"'


def test_resolved_bundle_missing_stock_cannot_be_applied_as_zero(db):
    from app.catalog_sources import CatalogSourceRow
    from app.catalog_imports import package_dto,save_decisions,apply_import,DecisionPatch
    p=package(db,snapshot([CatalogSourceRow(position=1,name='Сборка',stock=None,price_kopecks=48167,kind='bundle')]))
    row=package_dto(db,p)['rows']['items'][0]
    save_decisions(db,p.id,DecisionPatch(rowIds=[row['id']],resolution='new',selected=True,unit='шт',acknowledgeStandalone=True),'"1"')
    with pytest.raises(ApiError) as failure:
        apply_import(db,p.id,'"2"',str(uuid4()),True,'admin-test')
    assert failure.value.code=='UNRESOLVED_ROWS'
    assert any(issue['code']=='MISSING_STOCK' for issue in failure.value.details['issues'])


def test_configured_erp_disables_file_apply_without_claiming_ownership(db,monkeypatch):
    from types import SimpleNamespace
    from app import integration
    from app.catalog_imports import apply_import
    p=package(db)
    monkeypatch.setattr(integration,'get_erp_adapter',lambda:SimpleNamespace(is_configured=True))
    with pytest.raises(ApiError) as failure:
        apply_import(db,p.id,'"1"',str(uuid4()),True,'admin-test')
    assert failure.value.code=='ACCOUNTING_OWNER_CONFLICT'


def test_conflicting_upload_replay_and_untrusted_file_are_rejected(client,admin_headers):
    base='/api/admin/catalog/imports'
    headers={**admin_headers,'Idempotency-Key':str(uuid4())}
    fields={'observedAt':(utcnow()-timedelta(minutes=1)).isoformat(),'priceConfirmed':'true'}
    assert client.post(base,files={'file':('a.pdf',b'%PDF-1.4\nx','application/pdf')},data=fields,headers=headers).status_code==202
    assert client.post(base,files={'file':('b.pdf',b'%PDF-1.4\ny','application/pdf')},data=fields,headers=headers).status_code==409
    assert client.post(base,files={'file':('a.pdf',b'not-pdf','application/pdf')},data=fields,headers={**admin_headers,'Idempotency-Key':str(uuid4())}).status_code==422
    assert client.post(base,files={'file':('a.pdf',b'%PDF-1.4\n'+b'x'*(5*1024*1024),'application/pdf')},data=fields,headers={**admin_headers,'Idempotency-Key':str(uuid4())}).status_code==413


def test_apply_failure_rolls_back_products_mappings_and_report(db,monkeypatch):
    import app.catalog_imports as imports
    from app.catalog import Product
    from app.catalog_sources import CatalogSourceRow
    p=package(db,snapshot([CatalogSourceRow(position=1,name='A',stock=Decimal('1'),price_kopecks=100,unit='шт'),CatalogSourceRow(position=2,name='B',stock=Decimal('2'),price_kopecks=200,unit='шт')]))
    def fail_audit(*args,**kwargs):
        raise RuntimeError('Simulated failure after catalog writes')
    monkeypatch.setattr(imports,'record_audit',fail_audit)
    with pytest.raises(RuntimeError):
        imports.apply_import(db,p.id,'"1"',str(uuid4()),True,'admin-test')
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Product))==0
    assert db.scalar(select(func.count()).select_from(imports.CatalogSourceMapping))==0
    db.refresh(p)
    assert p.status=='ready' and p.report is None


def test_unknown_unit_hidden_card_can_later_receive_unit_and_visible_category(client,db,admin_headers,legacy_unknown_unit_product):
    from app.catalog_imports import create_snapshot_import,save_decisions,apply_import,DecisionPatch
    product=legacy_unknown_unit_product
    assert client.get('/api/catalog/'+product.id).status_code==404
    second=create_snapshot_import(db,snapshot(),author_id='admin')
    db.commit()
    save_decisions(db,second.id,DecisionPatch(filter='all',unit='кг'),'"1"')
    apply_import(db,second.id,'"2"',str(uuid4()),True,'admin')
    category=client.post('/api/admin/categories',json={'name':'Разобрано','position':1,'isVisible':True,'showInMenu':True},headers=admin_headers).json()
    response=client.patch('/api/admin/products/'+product.id+'/presentation',json={'categoryId':category['id']},headers={**admin_headers,'If-Match':'"1"'})
    assert response.status_code==200
    response=client.get('/api/catalog/'+product.id)
    assert response.status_code==200 and response.json()['product']['unit']=='кг'


def test_parallel_apply_retry_returns_one_saved_report_without_duplicate_products(db,db_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy.orm import Session
    from app.catalog import Product
    from app.catalog_imports import save_decisions,apply_import,DecisionPatch
    p=package(db)
    save_decisions(db,p.id,DecisionPatch(filter='all',unit='кг'),'"1"')
    package_id=p.id
    db.commit()
    key=str(uuid4());barrier=Barrier(2)
    def apply_once():
        with Session(db_engine) as session:
            barrier.wait(timeout=10)
            return apply_import(session,package_id,'"2"',key,True,'admin-test')
    with ThreadPoolExecutor(max_workers=2) as executor:
        reports=list(executor.map(lambda _:apply_once(),range(2)))
    assert reports[0]==reports[1] and reports[0]['createdCount']==1
    assert db.scalar(select(func.count()).select_from(Product))==1


def test_new_mapping_after_preview_blocks_second_package_and_keeps_one_product(db):
    from app.catalog import Product
    from app.catalog_imports import save_decisions,apply_import,DecisionPatch
    first,second=package(db),package(db)
    for p in (first,second):
        save_decisions(db,p.id,DecisionPatch(filter='all',unit='кг'),'"1"')
    apply_import(db,first.id,'"2"',str(uuid4()),True,'admin-test')
    with pytest.raises(ApiError) as failure:
        apply_import(db,second.id,'"2"',str(uuid4()),True,'admin-test')
    assert failure.value.code=='MAPPING_CONFLICT'
    db.rollback()
    assert db.scalar(select(func.count()).select_from(Product))==1


def test_retention_skips_applying_package_and_preserves_committed_report(db, db_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    from app import catalog_imports as imports

    source = snapshot()
    source.observed_at = utcnow() - timedelta(days=32)
    source.rows[0].unit = 'кг'
    first = package(db, source)
    imports.apply_import(db, first.id, '"1"', str(uuid4()), True, 'admin-test')
    source.observed_at += timedelta(days=1)
    pending = package(db, source)
    pending.created_at = utcnow() - timedelta(days=31)
    pending.raw_pdf = b'%PDF-1.4\n'
    db.commit()
    package_id = pending.id
    db.commit()

    cleanup_selected, resume_cleanup = Event(), Event()

    def pause_after_retention_select(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith('SELECT') and 'catalog_imports' in statement and 'created_at <' in statement:
            cleanup_selected.set()
            assert resume_cleanup.wait(timeout=10), 'Apply did not release the paused retention query'

    def cleanup():
        with Session(db_engine) as pruning:
            connection = pruning.connection()
            event.listen(connection, 'after_cursor_execute', pause_after_retention_select)
            try:
                imports.prune_imports(pruning)
            finally:
                event.remove(connection, 'after_cursor_execute', pause_after_retention_select)

    key = str(uuid4())
    with Session(db_engine) as applying, ThreadPoolExecutor(max_workers=1) as executor:
        # Match apply's lock order before letting cleanup examine the package.
        applying.scalar(select(imports.CatalogAccountingOwner).with_for_update())
        applying.scalar(select(imports.CatalogImport).where(imports.CatalogImport.id == package_id).with_for_update())
        future = executor.submit(cleanup)
        try:
            assert cleanup_selected.wait(timeout=10), 'Retention did not query old packages'
            report = imports.apply_import(applying, package_id, '"1"', key, True, 'admin-test')
            assert report['unchangedCount'] == 1
        finally:
            resume_cleanup.set()
        future.result(timeout=10)

    db.expire_all()
    saved = db.get(imports.CatalogImport, package_id)
    assert saved.status == 'applied' and saved.version == 2
    assert saved.report == report
    # A later pass may remove details, while apply retries still return the saved result.
    imports.prune_imports(db)
    db.refresh(saved)
    assert saved.rows_expired and saved.raw_pdf is None and saved.status == 'applied'
    assert imports.apply_import(db, package_id, '"1"', key, True, 'admin-test') == report


def test_retention_refreshes_cached_package_after_concurrent_apply(db, db_engine):
    from sqlalchemy.orm import Session
    from app import catalog_imports as imports

    source = snapshot()
    source.rows[0].unit = 'кг'
    pending = package(db, source)
    pending.created_at = utcnow() - timedelta(days=31)
    pending.raw_pdf = b'%PDF-1.4\n'
    db.commit()
    package_id = pending.id
    db.commit()

    with Session(db_engine) as pruning:
        cached = pruning.get(imports.CatalogImport, package_id)
        assert cached.status == 'ready'
        report = imports.apply_import(db, package_id, '"1"', str(uuid4()), True, 'admin-test')
        imports.prune_imports(pruning)
        pruning.refresh(cached)
        assert cached.status == 'applied' and cached.version == 2
        assert cached.report == report and cached.rows_expired


@pytest.mark.parametrize(('change_price', 'updated_count', 'unchanged_count'), [(True, 1, 0), (False, 0, 1)])
def test_existing_product_apply_retry_preserves_uuid_and_saved_result(db, change_price, updated_count, unchanged_count):
    from app.catalog import Product
    from app import catalog_imports as imports

    source = snapshot()
    source.rows[0].unit = 'кг'
    first = package(db, source)
    imports.apply_import(db, first.id, '"1"', str(uuid4()), True, 'admin-test')
    repeated_source = snapshot()
    if change_price:
        repeated_source.rows[0].price_kopecks = 39600
    repeated = package(db, repeated_source)
    key = str(uuid4())
    report = imports.apply_import(db, repeated.id, '"1"', key, True, 'admin-test')
    assert (report['createdCount'], report['updatedCount'], report['unchangedCount']) == (0, updated_count, unchanged_count)
    db.refresh(repeated)
    assert repeated.apply_key == key and repeated.version == 2
    product = db.scalar(select(Product))
    before_retry = imports.accounting_snapshot(product)
    assert imports.apply_import(db, repeated.id, '"1"', key, True, 'admin-test') == report
    db.refresh(repeated)
    db.refresh(product)
    assert repeated.version == 2 and imports.accounting_snapshot(product) == before_retry
    assert db.scalar(select(func.count()).select_from(Product)) == 1
    with pytest.raises(ApiError) as failure:
        imports.apply_import(db, repeated.id, '"1"', key, False, 'admin-test')
    assert failure.value.code == 'IDEMPOTENCY_CONFLICT'
    db.rollback()
    db.refresh(repeated)
    assert repeated.report == report and repeated.status == 'applied' and repeated.version == 2


def test_unknown_unit_stays_private_through_real_category_and_presentation_edits(client, db, admin_headers, legacy_unknown_unit_product):
    from app.catalog import Product, ProductPresentation, is_target_available
    from app.catalog_imports import create_snapshot_import, apply_import, save_decisions, DecisionPatch
    product = legacy_unknown_unit_product
    presentation = db.get(ProductPresentation, product.id)
    hidden_category = presentation.category_id
    visible = client.post('/api/admin/categories', json={'name': 'Витрина', 'position': 1, 'isVisible': True, 'showInMenu': True}, headers=admin_headers).json()
    db.add(Product(id='confirmed', external_id='legacy-confirmed', name='Обычный товар', sku='', brand='Confirmed', price_kopecks=100, stock=2, unit='шт'))
    db.add(ProductPresentation(product_id='confirmed', category_id=visible['id']))
    db.commit()
    for category_id in (hidden_category, visible['id']):
        if category_id == hidden_category:
            response = client.patch('/api/admin/categories/' + category_id, json={'isVisible': True}, headers={**admin_headers, 'If-Match': '"1"'})
        else:
            response = client.patch('/api/admin/products/' + product.id + '/presentation', json={'categoryId': category_id}, headers={**admin_headers, 'If-Match': '"1"'})
        assert response.status_code == 200, response.text
        page = client.get('/api/catalog').json()
        assert (page['total'], [item['id'] for item in page['items']]) == (1, ['confirmed'])
        assert {item['brand'] for item in page['items']} == {'Confirmed'}
        assert client.get('/api/catalog', params={'inStock': True}).json()['total'] == 1
        assert client.get('/api/catalog/' + product.id).status_code == 404
        assert not is_target_available(db, {'type': 'product', 'productId': product.id})
        admin = client.get('/api/admin/products', params={'q': product.id}, headers=admin_headers).json()['items'][0]
        assert admin['unit'] is None and admin['isVisible'] is False
        detail = client.get('/api/admin/products/' + product.id + '/presentation', headers=admin_headers)
        assert detail.status_code == 200 and detail.json()['product']['unit'] is None
    second = create_snapshot_import(db, snapshot(), author_id='admin')
    db.commit()
    save_decisions(db, second.id, DecisionPatch(filter='all', unit='кг'), '"1"')
    apply_import(db, second.id, '"2"', str(uuid4()), True, 'admin')
    assert client.get('/api/catalog/' + product.id).json()['product']['unit'] == 'кг'
    assert client.get('/api/catalog').json()['total'] == 2
    db.expire_all()
    product = db.get(Product, product.id)
    product.is_active = False
    db.commit()
    third = create_snapshot_import(db, snapshot(), author_id='admin')
    db.commit()
    apply_import(db, third.id, '"1"', str(uuid4()), True, 'admin')
    assert client.get('/api/catalog/' + product.id).status_code == 404
    db.expire_all()
    product = db.get(Product, product.id)
    product.reserved = Decimal('1')
    db.commit()
    fourth = create_snapshot_import(db, snapshot(), author_id='admin')
    db.commit()
    save_decisions(db, fourth.id, DecisionPatch(filter='all', unit='л'), '"1"')
    with pytest.raises(ApiError) as failure:
        apply_import(db, fourth.id, '"2"', str(uuid4()), True, 'admin')
    assert failure.value.code == 'UNRESOLVED_ROWS'
    assert any(issue['code'] == 'UNIT_RESERVED' for issue in failure.value.details['issues'])
    db.rollback()
    db.refresh(product)
    assert product.unit == 'кг' and product.reserved == 1 and not product.is_active


def test_fake_adapter_http_dtos_pass_actual_shared_client_for_preview_decisions_reports_history(client, db, admin_headers):
    import json
    from pathlib import Path
    import subprocess
    import shutil
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is required only for this backend-to-JavaScript DTO contract regression.')
    from app.catalog_sources import CatalogSourceRow, CatalogSnapshot
    from app.catalog_imports import create_adapter_import
    class FakeAdapter:
        def fetch_snapshot(self):
            return CatalogSnapshot(namespace='adapter:test', mode='adapter', observed_at=utcnow() - timedelta(minutes=1), rows=[CatalogSourceRow(position=1, name='От адаптера', stock=Decimal('2.125'), price_kopecks=101, source_product_id='stable-id', unit='кг')])
    package = create_adapter_import(db, FakeAdapter(), author_id='admin')
    db.commit()
    base = '/api/admin/catalog/imports/' + package.id
    preview = client.get(base, headers=admin_headers)
    assert preview.status_code == 200
    row = preview.json()['rows']['items'][0]
    assert row.get('sourceProductId') == 'stable-id'
    assert row['page'] is None and preview.json()['fileHash'] == ''
    decision = client.patch(base + '/decisions', json={'rowIds': [row['id']], 'selected': True}, headers={**admin_headers, 'If-Match': preview.headers['etag']})
    assert decision.status_code == 200, decision.text
    applied = client.post(base + '/apply', json={'acknowledgeSkipped': True}, headers={**admin_headers, 'If-Match': decision.headers['etag'], 'Idempotency-Key': str(uuid4())})
    assert applied.status_code == 200, applied.text
    history = client.get('/api/admin/catalog/imports', params={'limit': 10}, headers=admin_headers)
    assert history.status_code == 200
    payload = {'preview': preview.json(), 'decision': decision.json(), 'applied': applied.json(), 'history': history.json()}
    script = """
import assert from 'node:assert/strict';
import { createCatalogImportMethods } from './src/data/http-catalog-import-api.js';
let input = ''; for await (const chunk of process.stdin) input += chunk;
const payload = JSON.parse(input);
const responses = [payload.preview, payload.decision, payload.applied, payload.history];
const api = createCatalogImportMethods(async () => { const data = responses.shift(); return { data, etag: data.version ? `"${data.version}"` : null }; });
assert.deepEqual(await api.getCatalogImport(payload.preview.id), payload.preview);
assert.deepEqual(await api.updateCatalogImportDecisions(payload.preview.id, { rowIds: [payload.preview.rows.items[0].id], selected: true }, 1), payload.decision);
assert.deepEqual(await api.applyCatalogImport(payload.preview.id, { acknowledgeSkipped: true }, 2, '12345678-1234-4234-8234-123456789abc'), payload.applied);
assert.deepEqual(await api.getCatalogImports(), payload.history);
"""
    result = subprocess.run([node, '--input-type=module', '-e', script], input=json.dumps(payload), text=True, capture_output=True, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, result.stderr

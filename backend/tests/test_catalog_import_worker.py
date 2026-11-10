from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.common import utcnow


def queued(db, data=b'%PDF-1.4\n'):
    from app.catalog_imports import CatalogImport
    p = CatalogImport(id='worker-package', namespace='file:main-stock:retail', mode='file', author_id='admin', observed_at=utcnow()-timedelta(minutes=1), raw_pdf=data, status='queued', version=1)
    db.add(p)
    db.commit()
    return p


def test_persisted_queue_single_lease_and_interrupted_job_reclaimed(db):
    from app.catalog_import_worker import claim_import, finish_import
    from app.catalog_sources import CatalogSnapshot, CatalogSourceRow
    p = queued(db)
    first = claim_import(db)
    assert first['id'] == p.id and claim_import(db) is None
    db.refresh(p)
    p.lease_until = utcnow()-timedelta(seconds=1)
    db.commit()
    second = claim_import(db)
    assert second['token'] != first['token']
    source = CatalogSnapshot(namespace=p.namespace, mode='file', observed_at=p.observed_at, rows=[CatalogSourceRow(position=1, name='x', stock=Decimal('1'), price_kopecks=100)], page_count=1)
    assert not finish_import(db, first['id'], first['token'], source)
    assert finish_import(db, second['id'], second['token'], source)
    db.refresh(p)
    assert p.status == 'ready' and p.row_count == 1


def test_worker_malformed_pdf_fails_without_product_or_partial_rows(db):
    from app.catalog_import_worker import run_once
    from app.catalog_imports import CatalogImportRow
    from app.catalog import Product
    p = queued(db)
    assert run_once(db)
    db.refresh(p)
    assert p.status == 'failed' and p.error['code'] == 'PDF_UNREADABLE'
    assert db.scalar(select(func.count()).select_from(CatalogImportRow)) == 0
    assert db.scalar(select(func.count()).select_from(Product)) == 0


def test_retention_prunes_raw_and_rows_but_preserves_mapping_and_report(db):
    from app.catalog_imports import CatalogImportRow, prune_imports
    p = queued(db)
    p.status = 'applied'
    p.report = {'createdCount': 1}
    p.created_at = utcnow()-timedelta(days=31)
    db.commit()
    prune_imports(db)
    db.refresh(p)
    assert p.raw_pdf is None and p.rows_expired and p.report == {'createdCount':1}

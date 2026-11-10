"""Run with python -m app.catalog_import_worker; one persisted parser lease at a time."""
from datetime import timedelta
from decimal import Decimal
import json
import logging
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from sqlalchemy import or_, select, text

from .catalog_imports import CatalogImport, prepare_import, prune_imports
from .catalog_sources import CatalogSnapshot, CatalogSourceRow
from .common import ApiError, new_id, utcnow
from .db import SessionLocal

PARSE_SECONDS = 300
LEASE_SECONDS = 360
QUEUE_LOCK = 67139420261102


def claim_import(db):
    # The short transaction serializes the active-lease check across worker processes.
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': QUEUE_LOCK})
    now = utcnow()
    active = db.scalar(select(CatalogImport.id).where(CatalogImport.status == 'parsing', CatalogImport.lease_until > now).limit(1))
    if active:
        db.commit()
        return None
    p = db.scalar(select(CatalogImport).where(or_(CatalogImport.status == 'queued',
                  (CatalogImport.status == 'parsing') & (CatalogImport.lease_until <= now)))
                  .order_by(CatalogImport.created_at, CatalogImport.id).with_for_update(skip_locked=True).limit(1))
    if p is None:
        db.commit()
        return None
    token = new_id()
    p.status, p.lease_token, p.lease_until = 'parsing', token, now + timedelta(seconds=LEASE_SECONDS)
    p.version += 1
    result = {'id': p.id, 'token': token, 'namespace': p.namespace, 'observedAt': p.observed_at, 'rawPdf': p.raw_pdf}
    db.commit()
    return result


def parse_subprocess(raw_pdf):
    if raw_pdf is None:
        raise ApiError(410, 'IMPORT_RAW_EXPIRED', 'Срок хранения исходного PDF истёк. Загрузите файл снова.')
    with tempfile.TemporaryDirectory(prefix='lavr-catalog-') as directory:
        source, output = Path(directory) / 'source.pdf', Path(directory) / 'snapshot.json'
        source.write_bytes(raw_pdf)
        source.chmod(0o600)
        errors = Path(directory) / 'errors.json'
        try:
            with errors.open('wb') as stderr:
                result = subprocess.run([sys.executable, '-m', 'app.catalog_pdf', str(source), str(output)],
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr,
                                        timeout=PARSE_SECONDS, check=False)
        except subprocess.TimeoutExpired:
            raise ApiError(422, 'PDF_TIMEOUT', 'Разбор PDF превысил 5 минут; каталог не изменён.') from None
        if result.returncode:
            try:
                with errors.open('rb') as stderr:
                    stderr.seek(max(0, errors.stat().st_size - 8192))
                    error = json.loads(stderr.read().decode())
                if isinstance(error, dict) and isinstance(error.get('code'), str) and isinstance(error.get('message'), str):
                    raise ApiError(422, error['code'], error['message'], error.get('details'))
            except (ValueError, UnicodeError):
                pass
            raise ApiError(422, 'PDF_PROCESS_FAILED', 'Не удалось разобрать PDF в пределах ресурсов; каталог не изменён.')
        if not output.exists() or output.stat().st_size > 100 * 1024 * 1024:
            raise ApiError(422, 'PDF_PROCESS_FAILED', 'Некорректный результат разбора PDF.')
        payload = json.loads(output.read_text(encoding='utf-8'))
        rows = [CatalogSourceRow(**{**row, 'stock': Decimal(row['stock']) if row['stock'] is not None else None}) for row in payload['rows']]
        return rows, payload['pageCount']


def finish_import(db, package_id, token, snapshot):
    p = db.scalar(select(CatalogImport).where(CatalogImport.id == package_id).with_for_update().execution_options(populate_existing=True))
    if p is None or p.status != 'parsing' or p.lease_token != token:
        db.rollback()
        return False
    # A parser/preparation failure rolls back every normalized row, not just the bad page.
    with db.begin_nested():
        prepare_import(db, p, snapshot)
        p.version += 1
    db.commit()
    return True


def fail_import(db, package_id, token, error):
    db.rollback()
    p = db.scalar(select(CatalogImport).where(CatalogImport.id == package_id).with_for_update())
    if p and p.status == 'parsing' and p.lease_token == token:
        p.status = 'failed'
        p.error = {'code': error.code, 'message': error.message, 'details': error.details}
        p.lease_token = p.lease_until = None
        p.version += 1
        db.commit()
    else:
        db.rollback()


def run_once(db, *, parser=parse_subprocess):
    job = claim_import(db)
    if job is None:
        return False
    try:
        rows, pages = parser(job['rawPdf'])
        snapshot = CatalogSnapshot(namespace=job['namespace'], mode='file', observed_at=job['observedAt'], rows=rows, page_count=pages)
        finish_import(db, job['id'], job['token'], snapshot)
    except ApiError as error:
        fail_import(db, job['id'], job['token'], error)
    except Exception as error:
        logging.getLogger('lavr.catalog.worker').error('Catalog import %s failed: %s', job['id'], type(error).__name__)
        fail_import(db, job['id'], job['token'], ApiError(422, 'IMPORT_PROCESSING_FAILED', 'Не удалось подготовить сверку; каталог не изменён.'))
    return True


def main():
    last_cleanup = 0
    while True:
        try:
            with SessionLocal() as db:
                if time.monotonic() - last_cleanup > 3600:
                    prune_imports(db)
                    last_cleanup = time.monotonic()
                worked = run_once(db)
            if not worked:
                time.sleep(2)
        except KeyboardInterrupt:
            return
        except Exception as error:
            logging.getLogger('lavr.catalog.worker').error('Catalog queue failed: %s', type(error).__name__)
            time.sleep(5)


if __name__ == '__main__':
    main()

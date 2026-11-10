"""Explicit, resumable initializer for one reviewed PDF catalog release."""
import argparse
from collections import Counter
from dataclasses import asdict, replace
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import bindparam, select, text

from .auth import AuditEntry, User, record_audit
from .catalog import Category, CategoryCreate, Product, ProductPresentation
from .catalog_imports import (CatalogAccountingOwner, CatalogImport, CatalogImportRow,
                             _digest, _evaluate, _summarize, apply_import, create_snapshot_import, decimal_text)
from .catalog_pdf import parse_pdf
from .catalog_sources import CatalogSnapshot, FILE_NAMESPACE, UNITS, row_signature, validate_snapshot
from .common import utcnow
from .config import settings
from .content import Media, decode_image, persist_media

POLICY = {'observationTime': 'upload', 'missingStock': 'zero', 'duplicateRows': 'distinct',
          'defaultUnit': 'шт', 'fractionalStock': 'preserve', 'specialRows': 'standalone'}
LOCK_KEY = int(hashlib.sha256(b'catalog-release:main-stock').hexdigest()[:15], 16)
LABELS = {'expired': 'Просрочено', 'used': 'Б/у', 'incomplete': 'Некомплект'}
ILLUSTRATION = 'Изображение — иллюстрация типа товара, не фотография конкретного артикула.'


class ReleaseError(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise ReleaseError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def lock_products(db):
    schema = db.connection().get_execution_options().get('schema_translate_map', {}).get(None)
    quote = db.get_bind().dialect.identifier_preparer
    table = (quote.quote_schema(schema) + '.' if schema else '') + quote.quote('products')
    db.execute(text('LOCK TABLE ' + table + ' IN SHARE ROW EXCLUSIVE MODE'))


def conditions(name):
    name = name.casefold()
    return [key for key, matched in [('expired', 'просроч' in name),
            ('used', re.search(r'(?<!\w)б\s*/\s*у(?!\w)', name)), ('incomplete', 'некомплект' in name)] if matched]


def regular_file(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), 'invalid_file')
    return path.read_bytes()


def validate_inputs(manifest_path, pdf_path, assets_dir):
    manifest_bytes = regular_file(manifest_path)
    m = json.loads(manifest_bytes)
    require(m['schemaVersion'] == 1 and m['policy'] == POLICY, 'invalid_policy')
    require(re.fullmatch(r'pdf-[a-f0-9]{8,64}', m['releaseId']), 'invalid_release_id')
    pdf = regular_file(pdf_path)
    require(pdf.startswith(b'%PDF-') and sha(pdf) == m['source']['sha256'], 'source_hash_mismatch')
    require(0 < len(pdf) <= 5 * 1024 * 1024, 'invalid_pdf_size')
    name = m['source']['fileName']
    require(isinstance(name, str) and 0 < len(name) <= 240 and not re.search(r'[\x00-\x1f\x7f/\\]', name), 'invalid_file_name')
    categories, images, assignments = {}, {}, {}
    for c in m['categories']:
        require(c['key'] not in categories and re.fullmatch(r'(?:existing|new):[a-z0-9-]+', c['key']), 'invalid_category_key')
        validated = CategoryCreate.model_validate({k: c[k] for k in ('name', 'position', 'isVisible', 'showInMenu')})
        require(validated.name == c['name'] and c['action'] in ('reuse', 'create'), 'invalid_category')
        require((c['action'] == 'reuse' and isinstance(c['id'], str) and 0 < len(c['id']) <= 128)
                or (c['action'] == 'create' and c['id'] is None and not c['showInMenu']), 'invalid_category_action')
        categories[c['key']] = c
    require(categories and len({c['name'].casefold() for c in categories.values()}) == len(categories), 'duplicate_category_name')
    assets = Path(assets_dir)
    require(assets.is_dir() and not assets.is_symlink(), 'invalid_assets_directory')
    for image in m['images']:
        key, name = image['key'], image['file']
        require(key not in images and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', key), 'invalid_image_key')
        require(isinstance(name, str) and re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*\.webp', name), 'invalid_asset_name')
        data = regular_file(assets / name)
        require(0 < len(data) <= 5 * 1024 * 1024 and sha(data) == image['sha256'] and len(data) == image['bytes'], 'asset_hash_mismatch')
        width, height, _ = decode_image(data, 'image/webp')
        require((width, height) == (image['width'], image['height']), 'asset_dimensions_mismatch')
        require(isinstance(image['alt'], str) and 0 < len(image['alt']) <= 240 and '\x00' not in image['alt'], 'invalid_image_alt')
        images[key] = image
    # Parse the exact bytes whose digest was checked, even if the input path changes.
    with tempfile.NamedTemporaryFile(suffix='.pdf') as stream:
        stream.write(pdf); stream.flush()
        parsed, pages = parse_pdf(stream.name)
    require(pages == m['source']['pageCount'] and len(parsed) == m['source']['rowCount'], 'source_coverage_mismatch')
    validate_snapshot(CatalogSnapshot(FILE_NAMESPACE, 'file', utcnow(), parsed, page_count=pages))
    originals = {row.position: row for row in parsed}
    for row in m['rows']:
        position = row['position']
        require(type(position) is int and position in originals and position not in assignments, 'invalid_row_coverage')
        source = originals[position]
        require(row['signature'] == row_signature(source), 'row_signature_mismatch')
        require(row['categoryKey'] in categories and row['imageKey'] in images and row['unit'] in UNITS, 'invalid_row_binding')
        require(row['conditionFlags'] == conditions(source.name), 'condition_flags_mismatch')
        require(source.price_kopecks is not None, 'missing_price')
        assignments[position] = row
    require(set(assignments) == set(originals), 'incomplete_row_coverage')
    return m, sha(manifest_bytes), pdf, originals, assignments, images


def save_journal(path, journal):
    path = Path(path)
    require(not path.is_symlink(), 'invalid_receipt_path')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(not path.parent.is_symlink(), 'invalid_receipt_directory')
    os.chmod(path.parent, 0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.receipt-', delete=False) as stream:
            temporary = stream.name
            os.fchmod(stream.fileno(), 0o600)
            stream.write((json.dumps(journal, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n').encode())
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        descriptor = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        if temporary is not None: os.unlink(temporary)


def category_plan(db, manifest, package):
    existing = list(db.scalars(select(Category)))
    by_id, by_name = {c.id: c for c in existing}, {c.normalized_name: c for c in existing}
    owned = set(db.scalars(select(AuditEntry.entity_id).where(AuditEntry.action == 'catalog.release.category.create',
                          AuditEntry.details['importId'].astext == package.id))) if package else set()
    bindings, pending = {}, []
    for definition in manifest['categories']:
        key, name = definition['key'], definition['name']
        if definition['action'] == 'reuse':
            c = by_id.get(definition['id'])
            require(c is not None and (c.name, c.position, c.is_visible, c.show_in_menu) ==
                    (name, definition['position'], definition['isVisible'], definition['showInMenu']), 'existing_category_changed')
        else:
            deterministic_id = 'catalog:' + key.split(':')[-1]
            require(len(deterministic_id) <= 128, 'invalid_category_id')
            collision = by_id.get(deterministic_id)
            require(collision is None or collision.normalized_name == name.casefold(), 'category_id_conflict')
            c = by_name.get(name.casefold())
            if c is None:
                c = Category(id=deterministic_id, name=name, normalized_name=name.casefold(), position=definition['position'],
                             is_visible=False, show_in_menu=False, version=1)
                pending.append(c)
                owned.add(c.id)
            if c.id in owned:
                require(c.position == definition['position'] and not c.show_in_menu and c.is_visible in (False, definition['isVisible']), 'created_category_changed')
        bindings[key] = c.id
    return bindings, pending, owned


def media_index(db):
    found = {}
    for row in db.scalars(select(Media).where(Media.state == 'ready').order_by(Media.id)):
        path = settings.media_dir / row.filename
        if Path(row.filename).name != row.filename or path.is_symlink() or not path.is_file():
            continue
        if path.stat().st_size != row.size_bytes:
            continue
        key = (sha(path.read_bytes()), row.alt, row.width, row.height, row.size_bytes)
        found.setdefault(key, set()).add(row.id)
    return found


def row_map(db, package, journal, positions):
    if package.rows_expired:
        mapping = journal.get('rowProductIds', {})
        require(mapping, 'expired_rows_without_receipt_map')
    else:
        mapping = {str(position): product_id for position, product_id in db.execute(select(CatalogImportRow.position, CatalogImportRow.target_product_id).where(CatalogImportRow.import_id == package.id))}
    require(set(mapping) == {str(p) for p in positions} and all(mapping.values()) and len(set(mapping.values())) == len(positions), 'invalid_applied_row_map')
    require(not journal.get('rowProductIds') or journal['rowProductIds'] == mapping, 'receipt_row_map_conflict')
    return mapping


def planned_decisions(db, package, originals, assignments):
    require(package.status == 'ready' and package.version in (1, 2) and not package.rows_expired, 'modified_ready_package')
    rows = list(db.scalars(select(CatalogImportRow).where(CatalogImportRow.import_id == package.id).order_by(CatalogImportRow.position)))
    require({row.position for row in rows} == set(originals), 'package_row_coverage')
    for row in rows:
        source, planned = originals[row.position], assignments[row.position]
        expected = asdict(source)
        expected['stock'] = decimal_text(source.stock if source.stock is not None else 0)
        require(all(row.source_data.get(k) == v for k, v in expected.items()), 'package_source_changed')
        require(row.target_product_id is None and row.baseline is None and row.mapping_product_id is None, 'unexpected_row_mapping')
        if package.version == 1:
            row.selected, row.unit, row.category_id, row.resolution = True, planned['unit'], None, 'new'
            row.acknowledge_standalone = source.kind != 'ordinary'
            if source.stock is None:
                row.source_data = {**row.source_data, 'releaseOriginalStock': None}
            _evaluate(row, package, {})
        require(row.selected and row.unit == planned['unit'] and row.category_id is None and row.resolution == 'new'
                and row.acknowledge_standalone == (source.kind != 'ordinary') and not row.blocking, 'planned_decisions_changed')
    if package.version == 1:
        package.version = 2
        _summarize(db, package)
        record_audit(db, package_author(db, package.author_id), 'catalog.import.decisions', package.id, 2,
                     {'release': True, 'changedRowCount': len(rows), 'allRowsDistinctStandalone': True})
        db.commit()


def package_author(db, author_id):
    if author_id is None:
        admins = list(db.scalars(select(User).where(User.role == 'admin', User.is_active)))
        require(len(admins) == 1, 'author_id_required')
        return admins[0]
    author = db.get(User, author_id)
    require(author is not None and author.is_active and author.role == 'admin', 'invalid_author')
    return author


def presentation_values(source, planned, categories, media):
    labels = [LABELS[flag] for flag in planned['conditionFlags']]
    title = (', '.join(labels) + ': ' + source.name)[:240] if labels else None
    description = (('Состояние: ' + ', '.join(labels) + '. Исходное наименование: ' + source.name + '. ') if labels else '') + ILLUSTRATION
    return {'category_id': categories[planned['categoryKey']], 'image_ids': [media[planned['imageKey']]],
            'website_title': title, 'website_description': description, 'version': 2}


def publish_or_verify(db, package, originals, assignments, journal, manifest, *, publish):
    mapping = journal['rowProductIds']
    require(set(db.scalars(select(Product.id))) == set(mapping.values()), 'unexpected_inventory')
    categories = {c.id: c for c in db.scalars(select(Category).where(Category.id.in_(journal['categoryBindings'].values()))
                  .order_by(Category.id).with_for_update().execution_options(populate_existing=True))}
    require(len(categories) == len(journal['categoryBindings']), 'category_binding_changed')
    for definition in manifest['categories']:
        category = categories[journal['categoryBindings'][definition['key']]]
        require(category.name == definition['name'], 'category_name_changed')
        if definition['action'] == 'reuse':
            require((category.position, category.is_visible, category.show_in_menu) ==
                    (definition['position'], definition['isVisible'], definition['showInMenu']), 'existing_category_changed')
    media_ids = set(journal['mediaBindings'].values())
    media = {row.id: row for row in db.scalars(select(Media).where(Media.id.in_(media_ids)).order_by(Media.id)
             .with_for_update().execution_options(populate_existing=True))}
    require(set(media) == media_ids, 'publication_media_changed')
    for image in manifest['images']:
        row = media[journal['mediaBindings'][image['key']]]
        require((row.state, row.mime_type, row.alt, row.width, row.height, row.size_bytes) ==
                ('ready', 'image/webp', image['alt'], image['width'], image['height'], image['bytes']) and
                Path(row.filename).name == row.filename, 'publication_media_changed')
        data = regular_file(settings.media_dir / row.filename)
        require(len(data) == image['bytes'] and sha(data) == image['sha256'], 'publication_media_changed')
    states = set()
    positions = sorted(originals)
    for start in range(0, len(positions), 500):
        batch = positions[start:start + 500]
        by_id = {mapping[str(p)]: p for p in batch}
        records = list(db.execute(select(Product, ProductPresentation, Category).join(ProductPresentation, ProductPresentation.product_id == Product.id)
                       .join(Category, ProductPresentation.category_id == Category.id).where(Product.id.in_(by_id)).with_for_update(of=(Product, ProductPresentation))
                       .execution_options(populate_existing=True)))
        require(len(records) == len(batch), 'missing_product_presentation')
        updates = []
        for product, presentation, category in records:
            position = by_id[product.id]; source, planned = originals[position], assignments[position]
            require((product.name, product.sku, product.brand, product.price_kopecks, product.stock, product.unit,
                     product.accounting_source, product.stock_observed_at, product.accounting_version) ==
                    (source.name, source.sku, source.brand, source.price_kopecks, source.stock if source.stock is not None else Decimal(0),
                     planned['unit'], FILE_NAMESPACE, package.observed_at, 1), 'product_data_changed')
            desired = presentation_values(source, planned, journal['categoryBindings'], journal['mediaBindings'])
            final = all(getattr(presentation, k) == value for k, value in desired.items())
            pristine = (presentation.version == 1 and not category.is_visible and presentation.website_title is None
                        and presentation.website_description is None and presentation.image_ids == [])
            require(final or (publish and pristine), 'presentation_baseline_changed')
            states.add('final' if final else 'pristine')
            if not final:
                updates.append({'pid': product.id, **desired})
        if updates:
            table = ProductPresentation.__table__
            db.execute(table.update().where(table.c.product_id == bindparam('pid')).values(
                **{key: bindparam(key) for key in ('category_id', 'image_ids', 'website_title', 'website_description', 'version')}), updates)
    require(len(states) == 1, 'mixed_publication_state')
    if not package.rows_expired:
        saved = dict(db.execute(select(CatalogImportRow.position, CatalogImportRow.category_id)
                     .where(CatalogImportRow.import_id == package.id)).all())
        expected = {position: None if states == {'pristine'} else journal['categoryBindings'][assignments[position]['categoryKey']]
                    for position in originals}
        require(saved == expected, 'report_categories_changed')
        if states == {'pristine'}:
            rows, presentations = CatalogImportRow.__table__, ProductPresentation.__table__
            result = db.execute(rows.update().where(rows.c.import_id == package.id,
                                rows.c.target_product_id == presentations.c.product_id)
                                .values(category_id=presentations.c.category_id))
            require(result.rowcount == len(originals), 'report_row_coverage_changed')
    definitions = {c['key']: c for c in manifest['categories']}
    for key, category_id in journal['categoryBindings'].items():
        if category_id in journal['createdCategoryIds']:
            category = db.scalar(select(Category).where(Category.id == category_id).with_for_update().execution_options(populate_existing=True))
            visible = definitions[key]['isVisible']
            if states == {'pristine'}:
                if category.is_visible != visible:
                    category.is_visible = visible; category.version += 1
            else:
                require(category.is_visible == visible, 'category_publication_changed')
    if states == {'pristine'}:
        record_audit(db, package_author(db, package.author_id), 'catalog.release.publish', package.id,
                     details={'releaseId': manifest['releaseId'], 'manifestSha256': journal['manifestSha256'],
                              'publishedCount': len(mapping), 'reportCategoriesFinalized': not package.rows_expired})
        db.commit()


def execute_release(db, manifest_path, pdf_path, assets_dir, receipt_path, author_id=None):
    try:
        m, manifest_hash, pdf, originals, assignments, images = validate_inputs(manifest_path, pdf_path, assets_dir)
        receipt_path = Path(receipt_path)
        require(not receipt_path.is_symlink(), 'invalid_receipt_path')
        journal = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        identity = {'releaseId': m['releaseId'], 'sourceSha256': m['source']['sha256'], 'manifestSha256': manifest_hash}
        require(not journal or all(journal.get(k) == v for k, v in identity.items()), 'receipt_identity_conflict')
        upload_key = str(uuid5(NAMESPACE_URL, 'catalog-release-upload:' + m['releaseId'] + ':' + m['source']['sha256']))
        apply_key = str(uuid5(NAMESPACE_URL, 'catalog-release-apply:' + upload_key + ':' + manifest_hash))
        # A separate pinned connection retains this SESSION lock across helper commits.
        with db.get_bind().connect() as lock:
            require(lock.scalar(text('SELECT pg_try_advisory_lock(:key)'), {'key': LOCK_KEY}), 'release_already_running')
            lock.commit()
            try:
                package = db.scalar(select(CatalogImport).where(CatalogImport.upload_key == upload_key).execution_options(populate_existing=True))
                author = package_author(db, author_id or (package.author_id if package else None))
                require(not journal or journal.get('authorId') == author.id, 'receipt_author_conflict')
                if package:
                    require((package.upload_digest, package.file_hash, package.author_id, package.mode, package.namespace, package.file_name, package.page_count, package.row_count) ==
                            (manifest_hash, m['source']['sha256'], author.id, 'file', FILE_NAMESPACE, m['source']['fileName'], m['source']['pageCount'], len(originals)), 'upload_identity_conflict')
                    require((package.status == 'ready' and package.version in (1, 2)) or
                            (package.status == 'applied' and package.version == 3), 'invalid_package_state')
                from .integration import get_erp_adapter
                require(not get_erp_adapter().is_configured, 'erp_accounting_owner')
                owner = db.get(CatalogAccountingOwner, 'main-stock')
                require(owner is None or owner.namespace in (None, FILE_NAMESPACE), 'accounting_owner_conflict')
                active = select(CatalogImport.id).where(CatalogImport.status.in_(['queued', 'parsing', 'preparing', 'ready', 'applying']))
                require(not db.scalar(active.where(CatalogImport.id != package.id) if package else active), 'another_import_active')
                expected_ids = set(row_map(db, package, journal, originals).values()) if package and package.status == 'applied' else set()
                require(set(db.scalars(select(Product.id))) == expected_ids, 'unexpected_inventory')
                bindings, pending, owned = category_plan(db, m, package)
                require(not journal.get('categoryBindings') or journal['categoryBindings'] == bindings, 'category_binding_changed')
                if package is None:
                    observed = utcnow()
                    normalized = [replace(row, stock=row.stock if row.stock is not None else Decimal(0)) for row in originals.values()]
                    package = create_snapshot_import(db, CatalogSnapshot(FILE_NAMESPACE, 'file', observed, normalized, page_count=m['source']['pageCount']), author_id=author.id)
                    package.upload_key, package.upload_digest = upload_key, manifest_hash
                    package.file_hash, package.file_name, package.raw_pdf = m['source']['sha256'], m['source']['fileName'], pdf
                    record_audit(db, author, 'catalog.import.upload', package.id, 1, {'fileHash': package.file_hash, 'observedAt': observed.isoformat(), 'releaseId': m['releaseId']})
                    db.commit()
                journal.update(identity, authorId=author.id, importId=package.id, observedAt=package.observed_at.isoformat(),
                               uploadKey=upload_key, applyKey=apply_key, originalApplyETag='"2"', categoryBindings=bindings,
                               createdCategoryIds=sorted(owned), normalizations=[{'position': p, 'from': None, 'to': '0'} for p, row in originals.items() if row.stock is None])
                journal.setdefault('phase', 'prepared')
                save_journal(receipt_path, journal)
                for category in pending:
                    db.add(category)
                    record_audit(db, author, 'catalog.release.category.create', category.id, 1,
                                 {'importId': package.id, 'releaseId': m['releaseId'], 'manifestSha256': manifest_hash})
                if pending: db.commit()
                index = media_index(db)
                media = journal.setdefault('mediaBindings', {})
                for key, image in images.items():
                    fingerprint = (image['sha256'], image['alt'], image['width'], image['height'], image['bytes'])
                    if key in media:
                        require(media[key] in index.get(fingerprint, set()), 'receipt_media_changed')
                    elif fingerprint in index:
                        media[key] = min(index[fingerprint])
                    else:
                        data = regular_file(Path(assets_dir) / image['file'])
                        require(sha(data) == image['sha256'] and len(data) == image['bytes'], 'asset_changed')
                        media[key] = persist_media(db, author, data, 'image/webp', image['alt'])['id']
                        index[fingerprint] = {media[key]}
                    save_journal(receipt_path, journal)
                if package.status == 'ready':
                    package = db.scalar(select(CatalogImport).where(CatalogImport.id == package.id).with_for_update().execution_options(populate_existing=True))
                    planned_decisions(db, package, originals, assignments)
                    journal['phase'] = 'applying'; save_journal(receipt_path, journal)
                    lock_products(db)
                    require(not db.scalar(select(Product.id).limit(1)), 'unexpected_inventory')
                    apply_import(db, package.id, '"2"', apply_key, True, author.id)
                require(package.apply_key == apply_key and package.apply_digest == _digest({'packageId': package.id, 'version': '"2"', 'acknowledgeSkipped': True}), 'apply_identity_conflict')
                journal['rowProductIds'] = row_map(db, package, journal, originals)
                journal['report'] = package.report
                if journal['phase'] != 'complete': journal['phase'] = 'applied'
                save_journal(receipt_path, journal)
                lock_products(db)
                publish_or_verify(db, package, originals, assignments, journal, m, publish=True)
                publish_or_verify(db, package, originals, assignments, journal, m, publish=False)
                journal['counts'] = {'rows': len(originals), 'products': len(journal['rowProductIds']), 'media': len(set(media.values())),
                                     'zeroStock': sum((r.stock or 0) == 0 for r in originals.values()),
                                     'fractionalStock': sum(r.stock is not None and r.stock % 1 != 0 for r in originals.values()),
                                     'duplicateRows': sum(n for n in Counter(row_signature(r) for r in originals.values()).values() if n > 1),
                                     'specialRows': sum(r.kind != 'ordinary' for r in originals.values()),
                                     'units': dict(Counter(row['unit'] for row in assignments.values())),
                                     'conditions': {flag: sum(flag in row['conditionFlags'] for row in assignments.values()) for flag in LABELS}}
                journal['phase'] = 'complete'; save_journal(receipt_path, journal)
                return journal
            finally:
                db.rollback()
                lock.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': LOCK_KEY})
                lock.commit()
    except ReleaseError:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise ReleaseError('release_failed_' + type(error).__name__) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('manifest', 'pdf', 'assets', 'receipt'): parser.add_argument('--' + key, required=True, type=Path)
    parser.add_argument('--author-id')
    args = parser.parse_args()
    from .db import SessionLocal
    try:
        with SessionLocal() as db:
            journal = execute_release(db, args.manifest, args.pdf, args.assets, args.receipt, args.author_id)
        print('status=complete rows=' + str(journal['counts']['rows']) + ' media=' + str(journal['counts']['media']), flush=True)
    except ReleaseError as error:
        print('status=failed reason=' + str(error), flush=True)
        raise SystemExit(1)


if __name__ == '__main__':
    main()

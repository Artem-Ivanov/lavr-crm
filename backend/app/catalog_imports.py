"""Persisted catalog review and atomic source-neutral reconciliation."""
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import json
import re
from types import SimpleNamespace
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, delete, func, or_, select
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from .auth import record_audit, require_admin, require_admin_mutation
from .catalog import Category, Product, ProductPresentation, Id, normalize_search, set_version_headers
from .catalog_classification import category_key, infer_sale_unit, resolve_category_ids
from .catalog_sources import CatalogSnapshot, CatalogSourceRow, DisabledCatalogAdapter, FILE_NAMESPACE, UNITS, row_signature, validate_snapshot
from .common import ApiError, check_version, new_id, page_response, utcnow
from .db import Base, get_db

MAX_BYTES = 5 * 1024 * 1024
EMPTY_SUMMARY = {'new': 0, 'changed': 0, 'unchanged': 0, 'conflict': 0, 'missingUnit': 0, 'bundle': 0, 'selected': 0, 'skipped': 0}
FILTERS = Literal['all', 'ready', 'issues', 'missing_unit', 'bundles', 'selected', 'skipped']


class CatalogImport(Base):
    __tablename__ = 'catalog_imports'
    __table_args__ = (CheckConstraint('version BETWEEN 1 AND 9007199254740991'),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    namespace: Mapped[str] = mapped_column(String(160))
    mode: Mapped[str] = mapped_column(String(16))
    author_id: Mapped[str] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    status: Mapped[str] = mapped_column(String(24), default='queued', index=True)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    file_name: Mapped[str] = mapped_column(String(240), default='')
    file_hash: Mapped[str] = mapped_column(String(64), default='')
    raw_pdf: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    upload_key: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    upload_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    apply_key: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    apply_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rows_expired: Mapped[bool] = mapped_column(Boolean, default=False)


class CatalogImportRow(Base):
    __tablename__ = 'catalog_import_rows'
    __table_args__ = (UniqueConstraint('import_id', 'position'),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True, default=new_id)
    import_id: Mapped[str] = mapped_column(ForeignKey('catalog_imports.id', ondelete='CASCADE'), index=True)
    position: Mapped[int] = mapped_column(Integer)
    source_data: Mapped[dict] = mapped_column(JSONB)
    identity: Mapped[str] = mapped_column(String(300))
    duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    selected: Mapped[bool] = mapped_column(Boolean, default=True)
    unit: Mapped[str | None] = mapped_column(String(8), nullable=True)
    category_id: Mapped[str | None] = mapped_column(ForeignKey('categories.id'), nullable=True)
    resolution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    target_product_id: Mapped[str | None] = mapped_column(ForeignKey('products.id'), nullable=True)
    acknowledge_standalone: Mapped[bool] = mapped_column(Boolean, default=False)
    baseline: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    mapping_product_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    issues: Mapped[list] = mapped_column(JSONB, default=list)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    action: Mapped[str] = mapped_column(String(16), default='new')
    search_text: Mapped[str] = mapped_column(Text)


class CatalogSourceMapping(Base):
    __tablename__ = 'catalog_source_mappings'
    namespace: Mapped[str] = mapped_column(String(160), primary_key=True)
    identity: Mapped[str] = mapped_column(String(300), primary_key=True)
    product_id: Mapped[str] = mapped_column(ForeignKey('products.id'), index=True)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CatalogAccountingOwner(Base):
    __tablename__ = 'catalog_accounting_owners'
    stock_scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    namespace: Mapped[str | None] = mapped_column(String(160), nullable=True)


def decimal_text(value):
    return format(Decimal(str(value)), 'f').rstrip('0').rstrip('.') if Decimal(str(value)) % 1 else str(int(Decimal(str(value))))


def accounting_snapshot(product):
    return {'physicalStock': decimal_text(product.stock), 'reservedStock': decimal_text(product.reserved),
            'availableStock': decimal_text(max(Decimal('0'), product.stock - product.reserved)),
            'stock': decimal_text(product.stock), 'reserved': decimal_text(product.reserved),
            'unit': product.unit, 'priceKopecks': product.price_kopecks,
            'observedAt': product.stock_observed_at.isoformat() if product.stock_observed_at else None,
            'accountingSource': product.accounting_source, 'accountingVersion': product.accounting_version,
            'sku': product.sku, 'brand': product.brand, 'name': product.name, 'isActive': product.is_active}


def identity_for(snapshot, row):
    return 'id:' + row.source_product_id if snapshot.mode == 'adapter' else 'signature:' + row_signature(row)


def _load_package(db, package_id, *, lock=False, editable=False):
    query = select(CatalogImport).where(CatalogImport.id == package_id).execution_options(populate_existing=True)
    p = db.scalar(query.with_for_update() if lock else query)
    if p is None:
        raise ApiError(404, 'NOT_FOUND', 'Пакет импорта не найден.')
    if editable and (p.status != 'ready' or p.rows_expired):
        raise ApiError(409, 'IMPORT_NOT_READY', 'Пакет не готов к изменению или подробные строки уже удалены.')
    return p


def _issue(code, message, blocking=True):
    return {'code': code, 'message': message, 'blocking': blocking}


def _evaluate(row, p, products):
    data = row.source_data
    issues = []
    product = products.get(row.target_product_id)
    if row.resolution == 'skip':
        row.selected = False
    if data['stock'] is None:
        issues.append(_issue('MISSING_STOCK', 'В источнике отсутствует физический остаток.'))
    if data['price_kopecks'] is None:
        issues.append(_issue('MISSING_PRICE', 'В источнике отсутствует цена.'))
    if data.get('candidateProductIds') and row.resolution is None:
        issues.append(_issue('POSSIBLE_MATCH', 'Похожие карточки уже существуют. Явно выберите привязку или создание новой.'))
    if row.resolution not in ('new', 'link', 'skip'):
        issues.append(_issue('IDENTITY_REVIEW', 'Выберите отдельную карточку, привязку или пропуск; строки не объединяются.'))
    if data['kind'] != 'ordinary' and not row.acknowledge_standalone:
        issues.append(_issue('STANDALONE_REQUIRED', 'Подтвердите самостоятельную продаваемую позицию с собственным остатком.'))
    if row.unit is None:
        row.unit = (product.unit if product else None) or (infer_sale_unit(data['name']) if p.mode == 'file' else None) or 'шт'
    if row.resolution == 'link' and product is None:
        issues.append(_issue('TARGET_REQUIRED', 'Выберите существующую карточку.'))
    if product:
        if product.accounting_source not in (None, p.namespace):
            issues.append(_issue('ACCOUNTING_OWNER_CONFLICT', 'Карточка принадлежит другому источнику учётных данных.'))
        if data['stock'] is not None and Decimal(data['stock']) < product.reserved:
            issues.append(_issue('BELOW_RESERVED', 'Физический остаток меньше текущего резерва.'))
        if product.unit != row.unit and product.reserved:
            issues.append(_issue('UNIT_RESERVED', 'Нельзя изменить единицу при ненулевом резерве.'))
        if product.stock_observed_at and p.observed_at < product.stock_observed_at:
            issues.append(_issue('STALE_OBSERVATION', 'Наблюдение старше уже принятого состояния склада.'))
        changed = any((product.price_kopecks != data['price_kopecks'], data['stock'] is None or product.stock != Decimal(data['stock']),
                       product.unit != row.unit, product.sku != data['sku'], product.brand != data['brand'], product.name != data['name']))
        if product.stock_observed_at == p.observed_at and changed:
            issues.append(_issue('OBSERVATION_CONFLICT', 'На это время уже принято другое учётное состояние.'))
        row.action = 'update' if changed else 'unchanged'
    else:
        row.action = 'new'
    row.issues = issues
    row.blocking = any(issue['blocking'] for issue in issues)
    if row.resolution == 'skip' or not row.selected:
        row.action = 'skip'
    elif row.blocking:
        row.action = 'review'


def _summarize(db, p):
    db.flush()
    rows = db.execute(select(CatalogImportRow.action, CatalogImportRow.unit, CatalogImportRow.blocking, CatalogImportRow.selected,
                             CatalogImportRow.source_data['kind'].astext).where(CatalogImportRow.import_id == p.id)).all()
    p.summary = {'new': sum(r.action == 'new' for r in rows), 'changed': sum(r.action == 'update' for r in rows),
                 'unchanged': sum(r.action == 'unchanged' for r in rows), 'conflict': sum(r.blocking for r in rows),
                 'missingUnit': sum(r.unit is None for r in rows), 'bundle': sum(r[4] != 'ordinary' for r in rows),
                 'selected': sum(r.selected for r in rows), 'skipped': sum(not r.selected for r in rows)}
    return p.summary


def prepare_import(db, p, snapshot):
    validate_snapshot(snapshot)
    if snapshot.namespace != p.namespace or snapshot.observed_at != p.observed_at:
        raise ApiError(422, 'SOURCE_MISMATCH', 'Снимок не соответствует пакету импорта.')
    identities = [identity_for(snapshot, row) for row in snapshot.rows]
    counts = Counter(identities)
    mappings = {m.identity: m.product_id for m in db.scalars(select(CatalogSourceMapping).where(CatalogSourceMapping.namespace == p.namespace))}
    products = {product.id: product for product in db.scalars(select(Product).where(Product.id.in_(set(mappings.values()))))} if mappings else {}
    candidates_by_sku, candidates_by_name = defaultdict(list), defaultdict(list)
    category_ids = {}
    if snapshot.mode == 'file':
        category_ids = resolve_category_ids(db.execute(select(Category.id, Category.name)))
        for item in db.execute(select(Product.id, Product.sku, Product.name)):
            if item.sku:
                candidates_by_sku[item.sku].append(item.id)
            candidates_by_name[item.name].append(item.id)
    for source, identity in zip(snapshot.rows, identities):
        target = mappings.get(identity) if counts[identity] == 1 and source.kind == 'ordinary' else None
        product = products.get(target)
        data = asdict(source)
        data['stock'] = decimal_text(source.stock) if source.stock is not None else None
        data['candidateProductIds'] = sorted(set(candidates_by_sku.get(source.sku, []) + candidates_by_name.get(source.name, [])))[:10] if not target else []
        review = counts[identity] > 1 or source.kind != 'ordinary' or bool(data['candidateProductIds'])
        row = CatalogImportRow(id=new_id(), import_id=p.id, position=source.position, source_data=data, identity=identity,
                               duplicate=counts[identity] > 1, selected=not review, unit=source.unit if source.unit is not None else (product.unit if product else None),
                               category_id=category_ids.get(category_key(source.name)) if not product and snapshot.mode == 'file' else None,
                               resolution=None if review else ('link' if product else 'new'), target_product_id=target,
                               mapping_product_id=mappings.get(identity), baseline=accounting_snapshot(product) if product else None,
                               acknowledge_standalone=False, search_text=' '.join([source.sku, source.brand, source.name]).casefold())
        _evaluate(row, p, products)
        db.add(row)
    p.status = 'ready'
    p.page_count = snapshot.page_count
    p.row_count = len(snapshot.rows)
    p.error = None
    p.lease_token = p.lease_until = None
    _summarize(db, p)
    return p


def create_snapshot_import(db, snapshot, *, author_id):
    validate_snapshot(snapshot)
    p = CatalogImport(id=new_id(), namespace=snapshot.namespace, mode=snapshot.mode, author_id=author_id, observed_at=snapshot.observed_at,
                      status='preparing', version=1, created_at=utcnow())
    db.add(p)
    db.flush()
    return prepare_import(db, p, snapshot)


def create_adapter_import(db, adapter, *, author_id):
    snapshot = adapter.fetch_snapshot()
    if snapshot.mode != 'adapter':
        raise ApiError(422, 'SOURCE_MISMATCH', 'Адаптер должен вернуть снимок режима adapter.')
    return create_snapshot_import(db, snapshot, author_id=author_id)


def _scope(statement, filter='all', query=''):
    if query.strip():
        statement = statement.where(CatalogImportRow.search_text.contains(' '.join(query.split()).casefold(), autoescape=True))
    conditions = {'ready': ~CatalogImportRow.blocking & CatalogImportRow.selected,
                  'issues': func.jsonb_array_length(CatalogImportRow.issues) > 0,
                  'missing_unit': CatalogImportRow.unit.is_(None),
                  'bundles': CatalogImportRow.source_data['kind'].astext != 'ordinary',
                  'selected': CatalogImportRow.selected, 'skipped': ~CatalogImportRow.selected}
    return statement.where(conditions[filter]) if filter != 'all' else statement


def row_dto(row):
    data = row.source_data
    reserved = Decimal(row.baseline['reservedStock']) if row.baseline else Decimal(0)
    available = max(Decimal(0), Decimal(data['stock']) - reserved) if data['stock'] is not None else None
    return {'id': row.id, 'position': row.position, 'sourceProductId': data['source_product_id'], 'page': data['page'], 'kind': data['kind'], 'context': data['context'],
            'sku': data['sku'], 'brand': data['brand'], 'name': data['name'], 'unit': row.unit, 'selected': row.selected,
            'action': row.action, 'proposedStock': data['stock'], 'proposedPriceKopecks': data['price_kopecks'],
            'proposedAvailableStock': decimal_text(available) if available is not None else None,
            'before': row.baseline, 'targetProductId': row.target_product_id, 'categoryId': row.category_id,
            'resolution': row.resolution, 'acknowledgeStandalone': row.acknowledge_standalone, 'issues': row.issues,
            'coordinates': data['coordinates'], 'rawCells': data['raw_cells'], 'candidateProductIds': data.get('candidateProductIds', [])}


def package_dto(db, p, *, offset=0, limit=50, filter='all', query='', include_rows=True):
    result = {'id': p.id, 'status': p.status, 'version': p.version, 'sourceNamespace': p.namespace, 'sourceMode': p.mode, 'authorId': p.author_id,
              'fileName': p.file_name, 'fileHash': p.file_hash, 'observedAt': p.observed_at.isoformat(), 'createdAt': p.created_at.isoformat(),
              'pageCount': p.page_count, 'rowCount': p.row_count, 'summary': {**EMPTY_SUMMARY, **(p.summary or {})}, 'error': p.error, 'report': p.report,
              'rowsExpired': p.rows_expired}
    if include_rows:
        statement = _scope(select(CatalogImportRow).where(CatalogImportRow.import_id == p.id), filter, query)
        total = db.scalar(select(func.count()).select_from(statement.subquery()))
        rows = db.scalars(statement.order_by(CatalogImportRow.position).offset(offset).limit(limit))
        result['rows'] = page_response([row_dto(row) for row in rows], total, offset, limit)
    return result


class DecisionPatch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    rowIds: list[Id] | None = Field(default=None, min_length=1, max_length=1000)
    filter: FILTERS | None = None
    query: str = Field(default='', max_length=120, pattern=r'^[^\x00]*$')
    selected: bool | None = None
    unit: Literal['шт', 'кг', 'л', 'м'] | None = None
    categoryId: Id | None = None
    resolution: Literal['new', 'link', 'skip'] | None = None
    productId: Id | None = None
    acknowledgeStandalone: bool | None = None

    @model_validator(mode='after')
    def scope_and_changes(self):
        if (self.rowIds is None) == (self.filter is None):
            raise ValueError('Выберите явные строки или фильтр всего результата.')
        changes = self.model_fields_set - {'rowIds', 'filter', 'query'}
        if not changes or any(getattr(self, key) is None for key in changes if key != 'unit'):
            raise ValueError('Передайте хотя бы одно корректное решение.')
        if self.productId and self.resolution != 'link':
            raise ValueError('Карточка задаётся вместе с решением link.')
        if self.resolution == 'link' and (not self.productId or not self.rowIds or len(self.rowIds) != 1):
            raise ValueError('Привязка задаётся для одной явной строки.')
        return self


def save_decisions(db, package_id, body, if_match, author_id=None):
    p = _load_package(db, package_id, lock=True, editable=True)
    check_version(p, if_match)
    if body.categoryId and db.get(Category, body.categoryId) is None:
        raise ApiError(422, 'INVALID_CATEGORY', 'Категория не найдена.')
    statement = select(CatalogImportRow).where(CatalogImportRow.import_id == p.id)
    if body.rowIds is not None:
        statement = statement.where(CatalogImportRow.id.in_(body.rowIds))
    else:
        statement = _scope(statement, body.filter, body.query)
    rows = list(db.scalars(statement.order_by(CatalogImportRow.position)))
    if body.rowIds and len(rows) != len(set(body.rowIds)):
        raise ApiError(422, 'INVALID_ROW', 'Строка не принадлежит этому пакету.')
    products = {product.id: product for product in db.scalars(select(Product).where(Product.id.in_({r.target_product_id for r in rows} | ({body.productId} if body.productId else set()))))}
    if body.productId and body.productId not in products:
        raise ApiError(422, 'INVALID_PRODUCT', 'Карточка не найдена.')
    for row in rows:
        for key, column in [('selected', 'selected'), ('unit', 'unit'), ('categoryId', 'category_id'), ('resolution', 'resolution'), ('acknowledgeStandalone', 'acknowledge_standalone')]:
            if key in body.model_fields_set:
                setattr(row, column, getattr(body, key))
        if body.resolution == 'new':
            row.target_product_id = None
            row.baseline = None
        elif body.resolution == 'link':
            row.target_product_id = body.productId
            row.baseline = accounting_snapshot(products[body.productId])
            if 'unit' not in body.model_fields_set:
                row.unit = products[body.productId].unit
        _evaluate(row, p, products)
    p.version += 1
    _summarize(db, p)
    if author_id:
        record_audit(db, SimpleNamespace(id=author_id), 'catalog.import.decisions', p.id, p.version, {'changedRowCount': len(rows), 'decisions': body.model_dump(exclude_unset=True)})
    db.commit()
    return p


def refresh_import(db, package_id, if_match, author_id=None):
    p = _load_package(db, package_id, lock=True, editable=True)
    check_version(p, if_match)
    rows = list(db.scalars(select(CatalogImportRow).where(CatalogImportRow.import_id == p.id)))
    products = {product.id: product for product in db.scalars(select(Product).where(Product.id.in_({r.target_product_id for r in rows if r.target_product_id})))}
    mappings = {m.identity: m.product_id for m in db.scalars(select(CatalogSourceMapping).where(CatalogSourceMapping.namespace == p.namespace))}
    for row in rows:
        # Refresh never silently replaces an explicit link with a different mapping.
        row.mapping_product_id = mappings.get(row.identity)
        if row.target_product_id:
            product = products.get(row.target_product_id)
            row.baseline = accounting_snapshot(product) if product else None
        elif row.resolution == 'new' and row.mapping_product_id and not row.duplicate:
            row.resolution = None
            row.selected = False
        _evaluate(row, p, products)
    p.version += 1
    _summarize(db, p)
    if author_id:
        record_audit(db, SimpleNamespace(id=author_id), 'catalog.import.refresh', p.id, p.version)
    db.commit()
    return p


def uuid_key(value):
    try:
        if not isinstance(value, str) or str(UUID(value)) != value.lower():
            raise ValueError
        return str(UUID(value))
    except ValueError:
        raise ApiError(400, 'INVALID_IDEMPOTENCY_KEY', 'Нужен заголовок Idempotency-Key в формате UUID.') from None


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _lock_owner(db):
    db.execute(insert(CatalogAccountingOwner).values(stock_scope='main-stock').on_conflict_do_nothing())
    return db.scalar(select(CatalogAccountingOwner).where(CatalogAccountingOwner.stock_scope == 'main-stock').with_for_update())


def _hidden_category(db):
    name, suffix = 'Неразобранное', 0
    while True:
        category = db.scalar(select(Category).where(Category.normalized_name == name.casefold()))
        if category is None:
            category = Category(id=new_id(), name=name, normalized_name=name.casefold(), position=1000000, is_visible=False, show_in_menu=False, version=1)
            db.add(category)
            db.flush()
        if not category.is_visible:
            return category
        # Visible user categories stay unchanged; new unclassified cards stay private.
        suffix += 1
        name = 'Неразобранное (импорт)' if suffix == 1 else f'Неразобранное (импорт {suffix})'


def apply_import(db, package_id, if_match, idempotency_key, acknowledge_skipped, author_id):
    key = uuid_key(idempotency_key)
    digest = _digest({'packageId': package_id, 'version': if_match, 'acknowledgeSkipped': acknowledge_skipped})
    owner = _lock_owner(db)
    p = _load_package(db, package_id, lock=True)
    replay = db.scalar(select(CatalogImport).where(CatalogImport.apply_key == key))
    if replay:
        if replay.id != p.id or replay.apply_digest != digest:
            raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'Этот ключ уже использован для другого применения.')
        return replay.report
    if p.status != 'ready' or p.rows_expired:
        raise ApiError(409, 'IMPORT_NOT_READY', 'Пакет не готов к применению.')
    check_version(p, if_match)
    from .integration import get_erp_adapter
    if p.mode == 'file' and get_erp_adapter().is_configured:
        raise ApiError(409, 'ACCOUNTING_OWNER_CONFLICT', 'Файловый импорт недоступен при подключённом ERP до явного выбора владельца учётных данных.')
    if owner.namespace not in (None, p.namespace):
        raise ApiError(409, 'ACCOUNTING_OWNER_CONFLICT', 'Основной склад принадлежит другому источнику учётных данных.')
    rows = list(db.scalars(select(CatalogImportRow).where(CatalogImportRow.import_id == p.id).order_by(CatalogImportRow.position)))
    selected = [row for row in rows if row.selected and row.resolution != 'skip']
    skipped = len(rows) - len(selected)
    if skipped and not acknowledge_skipped:
        raise ApiError(422, 'SKIPPED_ACK_REQUIRED', 'Подтвердите число отложенных строк.')
    if not selected:
        raise ApiError(422, 'EMPTY_SELECTION', 'Нет выбранных строк для применения.')
    target_ids = [row.target_product_id for row in selected if row.target_product_id]
    if len(target_ids) != len(set(target_ids)):
        raise ApiError(409, 'DUPLICATE_TARGET', 'Две принятые строки не могут обновить одну карточку.')
    products = {product.id: product for product in db.scalars(select(Product).where(Product.id.in_(sorted(target_ids))).order_by(Product.id).with_for_update().execution_options(populate_existing=True))}
    mapping_rows = {m.identity: m for m in db.scalars(select(CatalogSourceMapping).where(CatalogSourceMapping.namespace == p.namespace).with_for_update())}
    mappings = {identity: mapping.product_id for identity, mapping in mapping_rows.items()}
    for row in selected:
        product = products.get(row.target_product_id)
        if row.baseline != (accounting_snapshot(product) if product else None):
            raise ApiError(409, 'BASELINE_CONFLICT', 'Учётные данные или резерв изменились. Обновите сверку.', {'rowId': row.id})
        if mappings.get(row.identity) != row.mapping_product_id:
            raise ApiError(409, 'MAPPING_CONFLICT', 'Связи источника изменились. Обновите сверку.', {'rowId': row.id})
        _evaluate(row, p, products)
        if row.blocking:
            raise ApiError(422, 'UNRESOLVED_ROWS', 'Выбранная строка требует разбора.', {'rowId': row.id, 'issues': row.issues})
    counts = Counter()
    hidden = _hidden_category(db) if any(not row.target_product_id for row in selected) else None
    new_products, new_presentations, new_mappings = [], [], []

    def insert_batch():
        if new_products:
            db.execute(Product.__table__.insert(), new_products)
            db.execute(ProductPresentation.__table__.insert(), new_presentations)
            new_products.clear()
            new_presentations.clear()
        if new_mappings:
            db.execute(CatalogSourceMapping.__table__.insert(), new_mappings)
            new_mappings.clear()

    # Snapshot validation and _evaluate above enforce the same constraints as ORM writers.
    # Bounded inserts avoid keeping 75k new ORM instances during a full-file apply.
    with db.no_autoflush:
        for row in selected:
            data = row.source_data
            product = products.get(row.target_product_id)
            if product is None:
                product_id = new_id()
                new_products.append({'id': product_id, 'external_id': ('pdf:' if p.mode == 'file' else 'adapter:') + new_id(),
                    'sku': data['sku'], 'brand': data['brand'], 'name': data['name'], 'price_kopecks': data['price_kopecks'],
                    'stock': Decimal(data['stock']), 'reserved': Decimal(0), 'unit': row.unit, 'is_active': True,
                    'delivery_days': 0, 'updated_at': utcnow(), 'stock_observed_at': p.observed_at, 'accounting_source': p.namespace,
                    'accounting_version': 1, 'search_normalized': ' '.join(normalize_search(data[key]) for key in ('sku', 'name', 'brand'))})
                new_presentations.append({'product_id': product_id, 'category_id': row.category_id if row.unit and row.category_id else hidden.id,
                                          'version': 1, 'image_ids': [], 'website_title': None, 'website_description': None})
                counts['created'] += 1
                row.target_product_id = product_id
            else:
                product_id = product.id
                counts['unchanged' if row.action == 'unchanged' else 'updated'] += 1
                for field in ('sku', 'brand', 'name', 'price_kopecks'):
                    setattr(product, field, data[field])
                product.stock = Decimal(data['stock'])
                product.unit = row.unit
                product.stock_observed_at = p.observed_at
                product.accounting_source = p.namespace
                product.accounting_version += 1
                product.updated_at = utcnow()
            if not row.duplicate and data['kind'] == 'ordinary':
                mapping = mapping_rows.get(row.identity)
                if mapping is None:
                    new_mappings.append({'namespace': p.namespace, 'identity': row.identity, 'product_id': product_id, 'confirmed_at': utcnow()})
                elif mapping.product_id != product_id:
                    mapping.product_id = product_id
                    mapping.confirmed_at = utcnow()
            if len(new_products) >= 500 or len(new_mappings) >= 500:
                insert_batch()
        insert_batch()
    _summarize(db, p)
    owner.namespace = p.namespace
    p.status = 'applied'
    p.version += 1
    p.apply_key, p.apply_digest = key, digest
    p.report = {'id': p.id, 'createdCount': counts['created'], 'updatedCount': counts['updated'], 'unchangedCount': counts['unchanged'],
                'skippedCount': skipped, 'appliedAt': utcnow().isoformat(), 'observedAt': p.observed_at.isoformat(),
                'authorId': author_id, 'fileHash': p.file_hash, 'sourceNamespace': p.namespace}
    record_audit(db, SimpleNamespace(id=author_id), 'catalog.import.apply', p.id, p.version, p.report)
    db.commit()
    return p.report


def prune_imports(db, *, now=None):
    now = now or utcnow()
    packages = (select(CatalogImport).where(CatalogImport.created_at < now - timedelta(days=7))
                .with_for_update(skip_locked=True).execution_options(populate_existing=True))
    for p in db.scalars(packages):
        p.raw_pdf = None
        if p.created_at < now - timedelta(days=30) and not p.rows_expired:
            db.execute(delete(CatalogImportRow).where(CatalogImportRow.import_id == p.id))
            p.rows_expired = True
            if p.status in ('queued', 'parsing', 'ready'):
                p.status = 'expired'
                p.version += 1
    db.commit()


class ApplyBody(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    acknowledgeSkipped: bool = False


router = APIRouter(prefix='/api/admin/catalog/imports')


@router.get('/capabilities')
def capabilities(staff=Depends(require_admin)):
    from .integration import get_erp_adapter
    enabled = not get_erp_adapter().is_configured
    return {'file': {'enabled': enabled, 'reason': None if enabled else 'ERP_ACCOUNTING_OWNER', 'maxBytes': MAX_BYTES, 'maxPages': 400, 'maxRows': 50000, 'units': list(UNITS)},
            'adapter': {'enabled': False, 'status': 'not_configured', 'message': 'Адаптер каталога не подключён.'},
            'sourceNamespace': FILE_NAMESPACE, 'rawRetentionDays': 7, 'rowRetentionDays': 30}


@router.get('')
def list_imports(offset: int = Query(default=0, ge=0, le=9007199254740991), limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db), staff=Depends(require_admin)):
    total = db.scalar(select(func.count()).select_from(CatalogImport))
    packages = db.scalars(select(CatalogImport).order_by(CatalogImport.created_at.desc(), CatalogImport.id).offset(offset).limit(limit))
    return page_response([package_dto(db, p, include_rows=False) for p in packages], total, offset, limit)


@router.post('', status_code=202)
async def upload_import(response: Response, file: UploadFile = File(), observedAt: str = Form(max_length=64), priceConfirmed: str = Form(max_length=5),
                        idempotency_key: str | None = Header(default=None, alias='Idempotency-Key'), db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    key = uuid_key(idempotency_key)
    if priceConfirmed != 'true':
        raise ApiError(422, 'PRICE_CONFIRMATION_REQUIRED', 'Подтвердите цену в рублях за назначаемую единицу.')
    try:
        observed = datetime.fromisoformat(observedAt.replace('Z', '+00:00'))
        if observed.tzinfo is None or observed > utcnow():
            raise ValueError
    except ValueError:
        raise ApiError(422, 'INVALID_OBSERVATION', 'Укажите фактическое время состояния склада с часовым поясом, не в будущем.') from None
    data = await file.read(MAX_BYTES + 1)
    await file.close()
    if len(data) > MAX_BYTES:
        raise ApiError(413, 'PDF_TOO_LARGE', 'PDF превышает 5 MiB.')
    if not data.startswith(b'%PDF-') or file.content_type not in ('application/pdf', 'application/octet-stream'):
        raise ApiError(422, 'PDF_UNREADABLE', 'Загрузите исходный PDF отчёта.')
    file_hash = hashlib.sha256(data).hexdigest()
    digest = _digest({'hash': file_hash, 'observedAt': observed.isoformat(), 'source': FILE_NAMESPACE})
    # Serialize key creation without depending on a web-process-local mutex.
    from sqlalchemy import text
    db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': int(hashlib.sha256(('catalog-upload:' + key).encode()).hexdigest()[:15], 16)})
    old = db.scalar(select(CatalogImport).where(CatalogImport.upload_key == key))
    if old:
        if old.upload_digest != digest:
            raise ApiError(409, 'IDEMPOTENCY_CONFLICT', 'Этот ключ уже использован для другой загрузки.')
        set_version_headers(response, old.version)
        return package_dto(db, old)
    name = re.sub(r'[\x00-\x1f\x7f/\\]', '_', file.filename or 'catalog.pdf')[:240]
    p = CatalogImport(id=new_id(), namespace=FILE_NAMESPACE, mode='file', author_id=staff.id, observed_at=observed,
                      created_at=utcnow(), status='queued', version=1, file_name=name, file_hash=file_hash, raw_pdf=data,
                      upload_key=key, upload_digest=digest, summary={})
    db.add(p)
    record_audit(db, staff, 'catalog.import.upload', p.id, 1, {'fileHash': file_hash, 'observedAt': observed.isoformat()})
    db.commit()
    set_version_headers(response, p.version)
    return package_dto(db, p)


@router.get('/{package_id}')
def get_import(package_id: Id, response: Response, offset: int = Query(default=0, ge=0, le=9007199254740991), limit: int = Query(default=50, ge=1, le=100),
               filter: FILTERS = 'all', query: str = Query(default='', max_length=120, pattern=r'^[^\x00]*$'), db: Session = Depends(get_db), staff=Depends(require_admin)):
    p = _load_package(db, package_id)
    set_version_headers(response, p.version)
    return package_dto(db, p, offset=offset, limit=limit, filter=filter, query=query)


@router.patch('/{package_id}/decisions')
def patch_decisions(package_id: Id, body: DecisionPatch, response: Response, if_match: str | None = Header(default=None, alias='If-Match'), db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    p = save_decisions(db, package_id, body, if_match, staff.id)
    set_version_headers(response, p.version)
    return package_dto(db, p)


@router.post('/{package_id}/refresh')
def refresh_preview(package_id: Id, response: Response, if_match: str | None = Header(default=None, alias='If-Match'), db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    p = refresh_import(db, package_id, if_match, staff.id)
    set_version_headers(response, p.version)
    return package_dto(db, p)


@router.post('/{package_id}/apply')
def apply_preview(package_id: Id, body: ApplyBody, response: Response, if_match: str | None = Header(default=None, alias='If-Match'), idempotency_key: str | None = Header(default=None, alias='Idempotency-Key'),
                  db: Session = Depends(get_db), staff=Depends(require_admin_mutation)):
    apply_import(db, package_id, if_match, idempotency_key, body.acknowledgeSkipped, staff.id)
    p = _load_package(db, package_id)
    set_version_headers(response, p.version)
    return package_dto(db, p)

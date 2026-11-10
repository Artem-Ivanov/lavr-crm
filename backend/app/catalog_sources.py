"""Source-neutral catalog snapshots; no source may infer another source's identity."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from typing import Literal, Protocol
import unicodedata

from .common import ApiError, utcnow
from .quantities import quantity_decimal

UNITS = ('шт', 'кг', 'л', 'м')
MAX_ROWS = 50000
FILE_NAMESPACE = 'file:main-stock:retail'


def cell_text(value: str) -> str:
    return ' '.join(unicodedata.normalize('NFC', value).split())


@dataclass
class CatalogSourceRow:
    position: int
    name: str
    stock: Decimal | None
    price_kopecks: int | None
    sku: str = ''
    brand: str = ''
    unit: str | None = None
    source_product_id: str | None = None
    page: int | None = None
    kind: Literal['ordinary', 'bundle', 'component'] = 'ordinary'
    context: str = ''
    raw_cells: list[str] = field(default_factory=list)
    coordinates: list[float] = field(default_factory=list)


@dataclass
class CatalogSnapshot:
    namespace: str
    mode: Literal['file', 'adapter']
    observed_at: datetime
    rows: list[CatalogSourceRow]
    stock_scope: str = 'main-stock'
    price_type: str = 'retail'
    page_count: int = 0


class CatalogSourceAdapter(Protocol):
    def fetch_snapshot(self) -> CatalogSnapshot: ...


class DisabledCatalogAdapter:
    def fetch_snapshot(self) -> CatalogSnapshot:
        raise ApiError(503, 'CATALOG_ADAPTER_NOT_CONFIGURED', 'Адаптер каталога не подключён.')


def row_signature(row: CatalogSourceRow) -> str:
    parts = [cell_text(value) for value in (row.brand, row.sku, row.name, row.kind, row.context)]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def validate_snapshot(snapshot: CatalogSnapshot) -> CatalogSnapshot:
    if snapshot.mode not in ('file', 'adapter') or not isinstance(snapshot.namespace, str) or not snapshot.namespace.startswith(snapshot.mode + ':') or len(snapshot.namespace) > 160 or any(ord(ch) < 33 or ord(ch) == 127 for ch in snapshot.namespace):
        raise ApiError(422, 'INVALID_SOURCE', 'Некорректное пространство имён источника.')
    if snapshot.stock_scope != 'main-stock' or snapshot.price_type != 'retail':
        raise ApiError(422, 'INVALID_SOURCE', 'Поддерживается основной склад и розничная цена.')
    if not isinstance(snapshot.observed_at, datetime) or snapshot.observed_at.tzinfo is None or snapshot.observed_at > utcnow():
        raise ApiError(422, 'INVALID_OBSERVATION', 'Укажите фактическое время состояния склада с часовым поясом, не в будущем.')
    if not snapshot.rows or len(snapshot.rows) > MAX_ROWS:
        raise ApiError(422, 'INVALID_ROW_COUNT', 'Число строк должно быть от 1 до 50 000.')
    positions = set()
    for row in snapshot.rows:
        for key, limit in (('name', 240), ('sku', 80), ('brand', 80)):
            if not isinstance(getattr(row, key), str):
                raise ApiError(422, 'INVALID_SOURCE_ROW', 'Текстовая ячейка должна быть строкой.')
            value = cell_text(getattr(row, key))
            if len(value) > limit or any(ord(ch) < 32 for ch in value) or (key == 'name' and not value):
                raise ApiError(422, 'INVALID_SOURCE_ROW', 'Некорректное значение текстовой ячейки.', {'position': row.position, 'field': key})
            setattr(row, key, value)
        if type(row.position) is not int or row.position < 1 or row.position in positions or row.kind not in ('ordinary', 'bundle', 'component'):
            raise ApiError(422, 'INVALID_SOURCE_ROW', 'Некорректная позиция или тип строки.')
        positions.add(row.position)
        if row.unit is not None and row.unit not in UNITS:
            raise ApiError(422, 'INVALID_UNIT', 'Неизвестная единица измерения.')
        if snapshot.mode == 'adapter' and (not isinstance(row.source_product_id, str) or not row.source_product_id or not row.source_product_id.strip() or len(row.source_product_id) > 240 or any(ord(ch) < 32 for ch in row.source_product_id)):
            raise ApiError(422, 'SOURCE_ID_REQUIRED', 'Адаптер должен передать постоянный код каждой позиции.')
        if row.stock is not None:
            try:
                row.stock = quantity_decimal(row.stock)
            except ValueError:
                raise ApiError(422, 'INVALID_QUANTITY', 'Остаток должен быть неотрицательным числом с точностью до 0,001.') from None
        if row.price_kopecks is not None and (type(row.price_kopecks) is not int or not 0 <= row.price_kopecks <= 9007199254740991):
            raise ApiError(422, 'INVALID_PRICE', 'Цена должна быть целым числом копеек.')
    return snapshot

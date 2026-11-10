from datetime import datetime, timezone
from decimal import Decimal

import pytest


def test_source_identity_normalizes_only_unicode_and_whitespace_and_excludes_values():
    from app.catalog_sources import CatalogSourceRow, row_signature
    a = CatalogSourceRow(position=1, name='  Па\u0301трубок  D=45 / 60 ', sku='001 / АА,Б', stock=Decimal('9.785'), price_kopecks=66000)
    b = CatalogSourceRow(position=77, name='Па́трубок D=45 / 60', sku='001 / АА,Б', stock=Decimal('2'), price_kopecks=56500)
    assert row_signature(a) == row_signature(b)
    assert row_signature(a) != row_signature(CatalogSourceRow(position=1, name=a.name, sku='1 / АА,Б', stock=Decimal('2'), price_kopecks=66000))


def test_snapshot_rejects_unstable_adapter_ids_and_invalid_numbers():
    from app.catalog_sources import CatalogSnapshot, CatalogSourceRow, validate_snapshot
    from app.common import ApiError
    now = datetime.now(timezone.utc)
    for value in [Decimal('0.0001'), Decimal('NaN'), Decimal('-1')]:
        with pytest.raises(ApiError):
            validate_snapshot(CatalogSnapshot(namespace='adapter:test', mode='adapter', observed_at=now, rows=[CatalogSourceRow(position=1, name='x', source_product_id='stable', stock=value, price_kopecks=100)]))
    with pytest.raises(ApiError) as failure:
        validate_snapshot(CatalogSnapshot(namespace='adapter:test', mode='adapter', observed_at=now, rows=[CatalogSourceRow(position=1, name='x', stock=Decimal('1'), price_kopecks=100)]))
    assert failure.value.code == 'SOURCE_ID_REQUIRED'


def test_disabled_adapter_fails_honestly():
    from app.catalog_sources import DisabledCatalogAdapter
    from app.common import ApiError
    with pytest.raises(ApiError) as failure:
        DisabledCatalogAdapter().fetch_snapshot()
    assert failure.value.code == 'CATALOG_ADAPTER_NOT_CONFIGURED'

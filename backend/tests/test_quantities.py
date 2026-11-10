"""Quantity boundaries and legacy request signatures are part of the public contract."""
from decimal import Decimal
import hashlib
import json

import pytest

from app.orders import CreateOrderRequest, request_digest
from app.feedback import payload_digest


def test_legacy_whole_quantity_dump_and_all_digest_versions_are_unchanged():
    raw = {'customer': {'name': 'Покупатель', 'phone': '+79991234567', 'email': ''},
           'fulfillment': {'method': 'pickup', 'address': ''},
           'items': [{'productId': 'legacy', 'quantity': 2, 'expectedUnitPriceKopecks': 101}]}
    body = CreateOrderRequest.model_validate(raw)
    assert body.model_dump() == raw
    assert type(body.model_dump()['items'][0]['quantity']) is int
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert request_digest(body, 'guest', 1) == digest(raw)
    assert request_digest(body, 'guest', 2) == digest({'actor': 'guest', 'body': raw})
    assert request_digest(body, 'buyer', 2) == digest({'actor': 'buyer', 'body': raw})
    assert payload_digest(body) == digest(raw)


@pytest.mark.parametrize('value', [True, False, float('inf'), float('nan'), -1, 0.0001, '1.0001', '1.00000000000000000000000000001', '1e-1000000000', Decimal('2147483647.001')])
def test_quantity_rejects_invalid_precision_and_bounds(value):
    from app.quantities import quantity_decimal
    with pytest.raises(ValueError):
        quantity_decimal(value)


def test_quantity_exact_arithmetic_and_half_up_line_rounding():
    from app.quantities import quantity_decimal, quantity_milli, quantity_json, validate_unit_quantity, line_total_kopecks
    assert quantity_decimal('1.234') == Decimal('1.234')
    assert sum(quantity_milli(0.1) for _ in range(3)) == 300
    assert quantity_json(Decimal('2.000')) == 2
    assert type(quantity_json(Decimal('2.000'))) is int
    assert quantity_json(Decimal('0.125')) == 0.125
    assert validate_unit_quantity(0.001, 'кг') == Decimal('0.001')
    assert line_total_kopecks(101, 0.5) == 51
    assert line_total_kopecks(1, 0.001) == 0
    assert line_total_kopecks(9007199254740991, 0.001) == 9007199254741
    for unit in ['шт', None, 'unknown']:
        with pytest.raises(ValueError):
            validate_unit_quantity(0.5, unit)


@pytest.mark.parametrize('value', ['0.5', True, 0, 1000, 0.0001, float('inf')])
def test_order_quantity_requires_bounded_json_number(value):
    with pytest.raises(ValueError):
        CreateOrderRequest.model_validate({'customer': {'name': 'Buyer', 'phone': '+79991234567'}, 'fulfillment': {'method': 'pickup'}, 'items': [{'productId': 'p', 'quantity': value, 'expectedUnitPriceKopecks': 1}]})


def test_order_schema_advertises_json_numbers_and_omittable_nonnull_units():
    schema = CreateOrderRequest.model_json_schema()['$defs']['OrderItemInput']
    assert schema['properties']['quantity'] == {'title': 'Quantity', 'type': 'number', 'exclusiveMinimum': 0, 'maximum': 999, 'multipleOf': 0.001}
    assert schema['properties']['expectedUnit']['type'] == 'string'
    assert 'expectedUnit' not in schema['required']

"""Exact thousandths in normal sale units; money rounds once per line."""
from decimal import Decimal, InvalidOperation


MAX_QUANTITY = Decimal('2147483647')
UNITS = ('шт', 'кг', 'л', 'м')


def quantity_decimal(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise ValueError('Укажите количество числом.')
    try:
        quantity = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Укажите корректное количество.') from None
    if not quantity.is_finite() or not 0 <= quantity <= MAX_QUANTITY or quantity != quantity.quantize(Decimal('0.001')):
        raise ValueError('Количество должно быть неотрицательным, в допустимых пределах и не более трёх знаков после запятой.')
    return quantity


def quantity_milli(value):
    return int(quantity_decimal(value) * 1000)


def quantity_json(value):
    quantity = quantity_decimal(value)
    return int(quantity) if quantity == quantity.to_integral_value() else float(quantity)


def validate_unit_quantity(value, unit):
    quantity = quantity_decimal(value)
    if unit not in UNITS or unit == 'шт' and quantity != quantity.to_integral_value():
        raise ValueError('Для штук требуется целое количество; выберите подтверждённую единицу измерения.')
    return quantity


def line_total_kopecks(price, quantity):
    if isinstance(price, bool) or not isinstance(price, int) or not 0 <= price <= 9007199254740991:
        raise ValueError('Укажите цену целым числом копеек в допустимых пределах.')
    return (price * quantity_milli(quantity) + 500) // 1000

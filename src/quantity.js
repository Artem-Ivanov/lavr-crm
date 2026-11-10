export const UNITS = Object.freeze(['шт', 'кг', 'л', 'м']);
export const MAX_STOCK = 2147483647;
export const MAX_QUANTITY = 999;
export const productUnit = product => product.unit === undefined ? 'шт' : product.unit;
export const isUnit = value => UNITS.includes(value);

export function quantityMilli(value, { max = MAX_STOCK, input = false } = {}) {
  if (!(typeof value === 'number' && Number.isFinite(value)) && !(input && typeof value === 'string')) throw new Error('Введите корректное количество.');
  const match = /^(\d+)(?:[.,](\d{1,3}))?$/.exec(typeof value === 'string' ? value.trim() : String(value));
  if (!match) throw new Error('Количество: максимум три знака после запятой.');
  const milli = BigInt(match[1]) * 1000n + BigInt((match[2] || '').padEnd(3, '0'));
  if (milli > BigInt(max) * 1000n || milli > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('Количество превышает допустимый предел.');
  return Number(milli);
}
export const fromMilli = value => value / 1000;
export function isQuantity(value, unit = 'шт', { max = MAX_QUANTITY, zero = false } = {}) {
  try { const milli = quantityMilli(value, { max }); return isUnit(unit) && (zero || milli > 0) && (unit !== 'шт' || milli % 1000 === 0); }
  catch { return false; }
}
export function parseQuantity(value, unit = 'шт', { max = MAX_QUANTITY, zero = true } = {}) {
  const quantity = fromMilli(quantityMilli(value, { max, input: true }));
  if (!isQuantity(quantity, unit, { max, zero })) throw new Error(unit === 'шт' ? 'Введите целое неотрицательное количество.' : 'Проверьте количество и единицу измерения.');
  return quantity;
}
export const quantityStep = unit => unit === 'шт' ? 1 : 0.001;
export const formatQuantity = (value, unit) => `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 3 }).format(value)}${unit === undefined ? '' : ` ${unit || 'единица не подтверждена'}`}`;
export function addQuantity(quantity, delta) {
  const milli = quantityMilli(quantity) + quantityMilli(delta);
  if (milli > MAX_STOCK * 1000) throw new Error('Количество превышает допустимый предел.');
  return fromMilli(milli);
}
export function stepQuantity(quantity, unit, direction = 1) {
  return fromMilli(Math.max(0, quantityMilli(quantity) + (unit === 'шт' ? 1000 : 1) * direction));
}
export const expectedUnitFields = unit => unit === 'шт' ? {} : { expectedUnit: unit };
export function quantityMatchesProduct(item, product) {
  const unit = productUnit(product);
  return isUnit(unit) && (item.expectedUnit ?? 'шт') === unit && isQuantity(item.quantity, unit)
    && quantityMilli(item.quantity) <= quantityMilli(product.stock);
}
export function canAddQuantity(product, quantity = 0) {
  const unit = productUnit(product);
  return isUnit(unit) && isQuantity(quantity, unit, { zero: true }) && quantity < MAX_QUANTITY
    && quantityMilli(quantity) + (unit === 'шт' ? 1000 : 1) <= quantityMilli(product.stock);
}
export function lineTotalKopecks(priceKopecks, quantity) {
  if (!Number.isSafeInteger(priceKopecks) || priceKopecks < 0) throw new Error('Некорректная цена.');
  const total = (BigInt(priceKopecks) * BigInt(quantityMilli(quantity)) + 500n) / 1000n;
  if (total > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('Не удалось рассчитать сумму заказа.');
  return Number(total);
}
export function sumKopecks(amounts) {
  let total = 0n;
  for (const amount of amounts) {
    if (!Number.isSafeInteger(amount) || amount < 0) throw new Error('Некорректная сумма заказа.');
    total += BigInt(amount);
  }
  if (total > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('Не удалось рассчитать сумму заказа.');
  return Number(total);
}
export const snapshotLineTotal = item => item.lineTotalKopecks ?? lineTotalKopecks(item.unitPriceKopecks, item.quantity);
export function validSnapshotQuantity(item) {
  try {
    return isQuantity(item.quantity, item.unit === undefined ? 'шт' : item.unit)
      && (item.lineTotalKopecks === undefined ? Number.isInteger(item.quantity) && (item.unit === undefined || item.unit === 'шт') : Number.isSafeInteger(item.lineTotalKopecks) && item.lineTotalKopecks >= 0 && item.lineTotalKopecks === lineTotalKopecks(item.unitPriceKopecks, item.quantity));
  } catch { return false; }
}
export const validRequestQuantity = item => isQuantity(item.quantity, item.expectedUnit === undefined ? 'шт' : item.expectedUnit);

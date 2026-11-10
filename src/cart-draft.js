import { quantityMilli, fromMilli, isUnit, parseQuantity, expectedUnitFields, formatQuantity, addQuantity } from './quantity.js';

export function parseCartDraft(raw) {
  if (!Array.isArray(raw)) return [];
  const entries = new Map();
  for (const item of raw) {
    if (!item || typeof item.productId !== 'string' || !item.productId || [...item.productId].length > 128 || item.expectedUnit !== undefined && !isUnit(item.expectedUnit)) continue;
    try {
      const milli = quantityMilli(item.quantity);
      if (milli <= 0) continue;
      const previous = entries.get(item.productId);
      if (previous && (previous.expectedUnit ?? 'шт') !== (item.expectedUnit ?? 'шт')) continue;
      const quantity = addQuantity(previous?.quantity || 0, item.quantity);
      entries.set(item.productId, { productId: item.productId, quantity, ...(item.expectedUnit === undefined ? {} : { expectedUnit: item.expectedUnit }) });
    } catch { /* Preserve valid unresolved lines without rounding corrupt quantities. */ }
  }
  return [...entries.values()];
}

export function setDraftQuantity(cart, productId, value, stock, unit = 'шт') {
  const quantity = fromMilli(quantityMilli(value, { max: 999, input: true }));
  if (quantity !== 0) parseQuantity(quantity, unit);
  if (quantityMilli(quantity) > quantityMilli(stock)) throw new Error(`Доступно только ${formatQuantity(stock, unit)}.`);
  const next = cart.filter(item => item.productId !== productId);
  if (!quantity) return next;
  const line = { productId, quantity, ...expectedUnitFields(unit) };
  const existing = cart.findIndex(item => item.productId === productId);
  if (existing >= 0) next.splice(existing, 0, line);
  else next.push(line);
  return next;
}

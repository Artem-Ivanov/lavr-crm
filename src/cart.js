import { PRODUCTS } from './catalog.js';
import { setDraftQuantity } from './cart-draft.js';
import { quantityMilli, fromMilli, productUnit, isQuantity, expectedUnitFields, stepQuantity, lineTotalKopecks, sumKopecks } from './quantity.js';

export function sanitizeCart(raw, products = PRODUCTS) {
  if (!Array.isArray(raw)) return [];
  const entries = new Map();
  for (const item of raw) {
    const product = products.find(product => product.id === item?.productId);
    if (!product || !isQuantity(item.quantity, productUnit(product)) || (item.expectedUnit ?? 'шт') !== productUnit(product) || product.stock <= 0) continue;
    const stockMilli = quantityMilli(product.stock);
    const availableMilli = productUnit(product) === 'шт' ? Math.floor(stockMilli / 1000) * 1000 : stockMilli;
    const milli = Math.min(availableMilli, 999000, quantityMilli(entries.get(product.id)?.quantity || 0) + quantityMilli(item.quantity));
    if (milli) entries.set(product.id, { productId: product.id, quantity: fromMilli(milli), ...expectedUnitFields(productUnit(product)) });
  }
  return [...entries.values()];
}
export function setCartQuantity(cart, productId, quantity, products = PRODUCTS) {
  const product = products.find(product => product.id === productId);
  if (!product) throw new Error('Товар не найден.');
  if (typeof quantity !== 'number') throw new Error('Введите корректное количество.');
  return setDraftQuantity(sanitizeCart(cart, products), productId, quantity, product.stock, productUnit(product));
}
export function addCartItem(cart, productId, products = PRODUCTS) {
  const product = products.find(product => product.id === productId);
  if (!product) throw new Error('Товар не найден.');
  const existing = cart.find(item => item.productId === productId);
  if (existing && (existing.expectedUnit ?? 'шт') !== productUnit(product)) throw new Error('Единица измерения изменилась. Проверьте корзину.');
  const result = sanitizeCart(cart, products);
  return setCartQuantity(result, productId, stepQuantity(result.find(item => item.productId === productId)?.quantity || 0, productUnit(product)), products);
}
export const cartTotal = (cart, products = PRODUCTS) => sumKopecks(sanitizeCart(cart, products).map(item => lineTotalKopecks(products.find(product => product.id === item.productId).priceKopecks, item.quantity)));
export const cartCount = cart => cart.length;

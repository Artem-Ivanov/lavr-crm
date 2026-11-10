import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseCartDraft, setDraftQuantity } from '../src/cart-draft.js';
import { addCartItem, cartTotal, cartCount } from '../src/cart.js';
import { createOrder, sanitizeOrders } from '../src/orders.js';
import { validateOwnOrder, isOwnOrder } from '../src/accounts.js';
const product = { id: 'bulk', sku: '', brand: '', name: 'Масло', unit: 'кг', stock: 9.785, priceKopecks: 101 };
const customer = { name: 'Иван', phone: '+79991234567', email: '' };
const fulfillment = { method: 'pickup', address: '' };
const line = quantity => ({ productId: 'bulk', quantity, expectedUnit: 'кг' });

test('draft retains fractional unresolved quantities, merges exactly and preserves legacy shape', () => {
  assert.deepEqual(parseCartDraft([line(0.1), line(0.2), { productId: 'old', quantity: 2 }]), [line(0.3), { productId: 'old', quantity: 2 }]);
  assert.deepEqual(setDraftQuantity([], 'bulk', '0,955', product.stock, 'кг'), [line(0.955)]);
  assert.throws(() => setDraftQuantity([], 'bulk', 9.786, product.stock, 'кг'), /Доступно/);
});

test('cart steps use thousandths and count distinct positions', () => {
  assert.deepEqual(addCartItem([], 'bulk', [product]), [line(0.001)]);
  assert.equal(cartTotal([line(0.5)], [product]), 51);
  assert.equal(cartCount([line(0.955), { productId: 'old', quantity: 5 }]), 2);
});

test('orders round each line half up, persist snapshots and refuse stale units', () => {
  const order = createOrder([line(0.5)], customer, fulfillment, [product]);
  assert.equal(order.totalKopecks, 51);
  assert.equal(order.items[0].unit, 'кг');
  assert.equal(order.items[0].lineTotalKopecks, 51);
  assert.deepEqual(sanitizeOrders([order]), [order]);
  assert.throws(() => createOrder([{ productId: 'bulk', quantity: 1 }], customer, fulfillment, [product]), /единиц/i);
  assert.throws(() => createOrder([line(0.5)], customer, fulfillment, [{ ...product, unit: null }]), /единиц/i);
  assert.equal(createOrder([line(0.001)], customer, fulfillment, [product]).totalKopecks, 0);
});

test('customer DTOs accept fractional unit snapshots but reject fractions without a historical unit', () => {
  const input = { fulfillment, items: [{ ...line(0.955), expectedUnitPriceKopecks: 101 }] };
  assert.deepEqual(validateOwnOrder(input), input);
  const order = createOrder([line(0.955)], customer, fulfillment, [product]);
  const own = Object.fromEntries(['id', 'number', 'createdAt', 'status', 'totalKopecks', 'items', 'fulfillment'].map(key => [key, order[key]]));
  assert.ok(isOwnOrder(own));
  const invalid = structuredClone(own); delete invalid.items[0].unit;
  assert.equal(isOwnOrder(invalid), false);
});

test('quantity arithmetic rejects precision and overflow and uses safe BigInt money intermediates', async () => {
  const { quantityMilli, parseQuantity, addQuantity, lineTotalKopecks, sumKopecks, isQuantity, validSnapshotQuantity } = await import('../src/quantity.js');
  for (const [quantity, milli] of [[0.001, 1], [0.955, 955], [9.785, 9785], [2147483647, 2147483647000]]) assert.equal(quantityMilli(quantity), milli);
  assert.equal(parseQuantity('0,955', 'кг'), 0.955);
  assert.equal(parseQuantity('9.785', 'л'), 9.785);
  assert.equal(addQuantity(addQuantity(0.1, 0.1), 0.1), 0.3);
  for (const quantity of [true, false, Infinity, NaN, -1, 0.0001, 2147483647.001, '0.001']) assert.throws(() => quantityMilli(quantity));
  assert.throws(() => parseQuantity('0.001', 'шт'));
  assert.throws(() => addQuantity(2147483647, 0.001));
  assert.equal(isQuantity(999.001, 'кг'), false);
  assert.equal(lineTotalKopecks(101, 0.5), 51);
  assert.equal(lineTotalKopecks(1, 0.001), 0);
  assert.equal(lineTotalKopecks(Number.MAX_SAFE_INTEGER, 0.5), 4503599627370496);
  assert.equal(sumKopecks([51, 51]), 102);
  assert.throws(() => lineTotalKopecks(Number.MAX_SAFE_INTEGER, 2));
  assert.throws(() => sumKopecks([Number.MAX_SAFE_INTEGER, 1]));
  assert.equal(validSnapshotQuantity({ quantity: 1, unit: 'кг', unitPriceKopecks: 101 }), false);
});

test('removal remains possible after a unit is unconfirmed and stale drafts never change units', async () => {
  const { quantityMatchesProduct } = await import('../src/quantity.js');
  assert.deepEqual(setDraftQuantity([line(0.955)], 'bulk', 0, 0, null), []);
  assert.equal(quantityMatchesProduct({ productId: 'bulk', quantity: 1 }, product), false);
  assert.equal(quantityMatchesProduct(line(0.955), { ...product, unit: 'л' }), false);
  assert.deepEqual(parseCartDraft([line(0.955), { ...line(0.001), expectedUnit: 'л' }]), [line(0.955)]);
});

test('merged domain carts stay inside the per-line limit and zero-priced fractional totals survive restore', async () => {
  const { sanitizeCart } = await import('../src/cart.js');
  assert.equal(sanitizeCart([line(999), line(999)], [{ ...product, stock: 2000 }])[0].quantity, 999);
  const order = createOrder([line(0.001)], customer, fulfillment, [product]);
  assert.deepEqual(sanitizeOrders([order]), [order]);
});

test('adding an existing stale domain cart line requires unit review', () => {
  assert.throws(() => addCartItem([{ productId: 'bulk', quantity: 1 }], 'bulk', [product]), /единиц/i);
});

test('fractional piece stock never turns whole cart quantities into fractional orders', async () => {
  const { sanitizeCart } = await import('../src/cart.js');
  const { canAddQuantity, quantityMatchesProduct } = await import('../src/quantity.js');
  const piece = { ...product, unit: 'шт', stock: 9.785 };
  assert.deepEqual(sanitizeCart([{ productId: 'bulk', quantity: 10 }], [piece]), [{ productId: 'bulk', quantity: 9 }]);
  assert.deepEqual(sanitizeCart([{ productId: 'bulk', quantity: 1 }], [{ ...piece, stock: 0.955 }]), []);
  assert.equal(quantityMatchesProduct({ productId: 'bulk', quantity: 9 }, piece), true);
  assert.equal(canAddQuantity(piece, 8), true);
  assert.equal(canAddQuantity(piece, 9), false);
  assert.throws(() => setDraftQuantity([], 'bulk', 0.5, piece.stock, 'шт'));
  assert.throws(() => createOrder([{ productId: 'bulk', quantity: 0.5 }], customer, fulfillment, [piece]));
  assert.equal(createOrder([{ productId: 'bulk', quantity: 9 }], customer, fulfillment, [piece]).totalKopecks, 909);
});

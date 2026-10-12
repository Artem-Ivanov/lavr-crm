import test from 'node:test';
import assert from 'node:assert/strict';
import { CATEGORIES, PRODUCTS, filterProducts } from '../src/catalog.js';
import { sanitizeCart, setCartQuantity, addCartItem, cartTotal, cartCount } from '../src/cart.js';
import { ORDER_STATUSES, allowedNextStatuses, createOrder, changeOrderStatus, sanitizeOrders } from '../src/orders.js';

const products = [
  { id: 'oil', sku: 'OC-384', brand: 'MANN-FILTER', name: 'Масляный фильтр', category: 'filters', priceKopecks: 129999, stock: 3 },
  { id: 'air', sku: 'C 30 005', brand: 'MANN-FILTER', name: 'Воздушный фильтр', category: 'filters', priceKopecks: 50001, stock: 0 },
  { id: 'disc', sku: '09.9772.11', brand: 'Brembo', name: 'Тормозной диск', category: 'brakes', priceKopecks: 245050, stock: 5 },
];
const cart = [{ productId: 'oil', quantity: 2 }, { productId: 'disc', quantity: 1 }];
const customer = { name: '  Иван Петров  ', phone: '+7 (999) 123-45-67', email: 'ivan@example.ru' };
const pickup = { method: 'pickup', address: '' };

test('demo catalog gives each category usable products and integer prices', () => {
  assert.ok(PRODUCTS.length >= 12);
  assert.ok(PRODUCTS.length <= 16);
  for (const category of CATEGORIES) assert.ok(PRODUCTS.some(product => product.category === category.id));
  for (const product of PRODUCTS) {
    assert.ok(product.id && product.externalId && product.sku && product.name && product.brand);
    assert.ok(Number.isSafeInteger(product.priceKopecks) && product.priceKopecks > 0);
    assert.ok(Number.isSafeInteger(product.stock) && product.stock >= 0);
    assert.ok(product.image.startsWith('/images/') && product.description && Object.keys(product.specs).length);
  }
  assert.equal(new Set(PRODUCTS.map(product => product.id)).size, PRODUCTS.length);
});

test('search ignores case, punctuation and spacing in names, brands and SKUs', () => {
  for (const query of [' oc 384 ', 'OC.384', 'мАсЛяНыЙ фИлЬтР', 'mann filter oc-384']) {
    assert.deepEqual(filterProducts(products, { query }).map(product => product.id), ['oil']);
  }
  assert.deepEqual(filterProducts(products, { query: '09-9772 11' }).map(product => product.id), ['disc']);
});

test('search matches brand and name words in either order without treating text as regex', () => {
  for (const query of ['Bosch фильтр', 'фильтр Bosch']) {
    assert.deepEqual(filterProducts(PRODUCTS, { query }).map(product => product.id), ['filter-bosch-air']);
  }
  for (const query of ['MANN-FILTER масляный', 'масляный MANN-FILTER']) {
    assert.deepEqual(filterProducts(products, { query }).map(product => product.id), ['oil']);
  }
  for (const query of ['W9142', 'W 914 / 2', 'MANN-FILTER W 914/2']) {
    assert.deepEqual(filterProducts(PRODUCTS, { query }).map(product => product.id), ['filter-mann-oil']);
  }
  assert.deepEqual(filterProducts(products, { query: '[Bosch|MANN]' }), []);
});

test('search finds normalized internal and external identifiers without indexing missing values', () => {
  assert.deepEqual(filterProducts(PRODUCTS, { query: 'filter-mann-oil' }).map(product => product.id), ['filter-mann-oil']);
  assert.deepEqual(filterProducts(PRODUCTS, { query: 'DEMO.1C.004' }).map(product => product.id), ['filter-mann-oil']);
  assert.deepEqual(filterProducts(products, { query: 'undefined' }), []);
});

test('catalog combines all filters and reports empty search results', () => {
  assert.deepEqual(filterProducts(products, { query: 'фильтр', category: 'filters', brand: 'MANN-FILTER', inStock: true }).map(product => product.id), ['oil']);
  assert.deepEqual(filterProducts(products, { query: 'несуществующая деталь' }), []);
  assert.deepEqual(filterProducts(products, { category: 'brakes', brand: 'MANN-FILTER' }), []);
});

test('price sorting leaves the catalog source unchanged', () => {
  const before = structuredClone(products);
  assert.deepEqual(filterProducts(products, { sort: 'price_asc' }).map(product => product.id), ['air', 'oil', 'disc']);
  assert.deepEqual(filterProducts(products, { sort: 'price_desc' }).map(product => product.id), ['disc', 'oil', 'air']);
  assert.deepEqual(products, before);
});

test('saved cart merges duplicates, caps stock and discards malformed entries', () => {
  const raw = [
    { productId: 'oil', quantity: 2 }, { productId: 'oil', quantity: 2 },
    { productId: 'disc', quantity: 7 }, { productId: 'missing', quantity: 1 },
    { productId: 'air', quantity: 1 }, { productId: 'disc', quantity: 0 },
    { productId: 'disc', quantity: -1 }, { productId: 'disc', quantity: 1.5 },
    { productId: 'disc', quantity: '2' }, null, 'broken',
  ];
  assert.deepEqual(sanitizeCart(raw, products), [{ productId: 'oil', quantity: 3 }, { productId: 'disc', quantity: 5 }]);
  for (const value of [null, {}, 'broken', 3]) assert.deepEqual(sanitizeCart(value, products), []);
});

test('cart add, update and remove preserve their inputs', () => {
  const empty = [];
  const one = addCartItem(empty, 'oil', products);
  const two = addCartItem(one, 'oil', products);
  const updated = setCartQuantity(two, 'oil', 3, products);
  assert.deepEqual(empty, []);
  assert.deepEqual(one, [{ productId: 'oil', quantity: 1 }]);
  assert.deepEqual(two, [{ productId: 'oil', quantity: 2 }]);
  assert.deepEqual(updated, [{ productId: 'oil', quantity: 3 }]);
  assert.deepEqual(setCartQuantity(updated, 'oil', 0, products), []);
});

test('cart refuses invalid quantities, unknown products and stock overflow', () => {
  for (const quantity of [-1, 1.5, NaN, Infinity, '2', null, Number.MAX_SAFE_INTEGER + 1]) {
    assert.throws(() => setCartQuantity([], 'oil', quantity, products));
  }
  assert.throws(() => setCartQuantity([], 'oil', 4, products));
  assert.throws(() => addCartItem([{ productId: 'oil', quantity: 3 }], 'oil', products));
  assert.throws(() => addCartItem([], 'air', products));
  assert.throws(() => addCartItem([], 'missing', products));
});

test('totals and item count use quantities and exact integer kopecks', () => {
  assert.equal(cartTotal(cart, products), 505048);
  assert.equal(cartCount(cart), 3);
  assert.equal(cartTotal([], products), 0);
});

test('checkout validates the cart instead of silently dropping or capping it', () => {
  for (const invalid of [[], null, {}, [{ productId: 'missing', quantity: 1 }], [{ productId: 'oil', quantity: 0 }], [{ productId: 'oil', quantity: 1.5 }], [{ productId: 'oil', quantity: 4 }], [{ productId: 'oil', quantity: 2 }, { productId: 'oil', quantity: 2 }]]) {
    assert.throws(() => createOrder(invalid, customer, pickup, products));
  }
});

test('checkout requires a name and a Russian eleven-digit telephone number', () => {
  for (const invalid of [{ ...customer, name: '  ' }, { ...customer, phone: '123' }, { ...customer, phone: '+1 999 1234567' }, { ...customer, phone: '+7 999 123 45 67abc' }, null]) {
    assert.throws(() => createOrder(cart, invalid, pickup, products));
  }
  assert.equal(createOrder(cart, { ...customer, phone: '8 (999) 123-45-67' }, pickup, products).customer.phone, '+79991234567');
  for (const phone of [undefined, null, 79991234567]) {
    assert.throws(() => createOrder(cart, { ...customer, phone }, pickup, products), /Укажите российский телефон/);
  }
});

test('checkout accepts an optional email but refuses malformed nonempty email', () => {
  assert.equal(createOrder(cart, { ...customer, email: '   ' }, pickup, products).customer.email, '');
  for (const email of ['broken', 'a@', '@example.ru', 'a b@example.ru', 123, false, []]) {
    assert.throws(() => createOrder(cart, { ...customer, email }, pickup, products));
  }
});

test('delivery requires an address and only known fulfillment methods are accepted', () => {
  assert.throws(() => createOrder(cart, customer, { method: 'delivery', address: ' ' }, products));
  assert.throws(() => createOrder(cart, customer, { method: 'drone', address: 'Москва' }, products));
  assert.throws(() => createOrder(cart, customer, null, products));
  assert.equal(createOrder(cart, customer, { method: 'delivery', address: '  Москва, улица Мира, 1  ' }, products).fulfillment.address, 'Москва, улица Мира, 1');
});

test('checkout accepts contact and delivery fields at their maximum lengths', () => {
  const boundaryCustomer = {
    name: 'И'.repeat(100), phone: `7${' '.repeat(19)}9991234567`, email: `${'a'.repeat(243)}@example.ru`,
  };
  const order = createOrder(cart, boundaryCustomer, { method: 'delivery', address: 'А'.repeat(500) }, products);
  assert.equal(order.customer.name.length, 100);
  assert.equal(order.customer.email.length, 254);
  assert.equal(order.fulfillment.address.length, 500);
});

test('checkout rejects oversized fields before normalizing or saving them', () => {
  for (const invalid of [
    { ...customer, name: 'И'.repeat(101) },
    { ...customer, phone: `7${' '.repeat(20)}9991234567` },
    { ...customer, email: `${'a'.repeat(244)}@example.ru` },
  ]) assert.throws(() => createOrder(cart, invalid, pickup, products));
  assert.throws(() => createOrder(cart, customer, { method: 'delivery', address: 'А'.repeat(501) }, products));
});

test('new order snapshots names, SKUs and current catalog prices', () => {
  const source = structuredClone(products);
  const order = createOrder(cart, customer, pickup, source);
  assert.equal(order.customer.name, 'Иван Петров');
  assert.equal(order.customer.phone, '+79991234567');
  assert.equal(order.totalKopecks, 505048);
  assert.equal(order.status, 'new');
  assert.equal(order.integrationStatus, 'not_connected');
  assert.deepEqual(order.items[0], { productId: 'oil', sku: 'OC-384', brand: 'MANN-FILTER', name: 'Масляный фильтр', quantity: 2, unitPriceKopecks: 129999 });
  assert.ok(order.id && order.number && Number.isFinite(Date.parse(order.createdAt)));
  source[0].priceKopecks = 1;
  source[0].name = 'Изменено';
  assert.equal(order.items[0].unitPriceKopecks, 129999);
  assert.equal(order.items[0].name, 'Масляный фильтр');
  assert.notEqual(createOrder(cart, customer, pickup, source).id, order.id);
});

test('order transitions follow the workflow and do not change the original order', () => {
  const order = createOrder(cart, customer, pickup, products);
  const confirmed = changeOrderStatus(order, 'confirmed');
  const ready = changeOrderStatus(confirmed, 'ready');
  const completed = changeOrderStatus(ready, 'completed');
  assert.equal(order.status, 'new');
  assert.equal(completed.status, 'completed');
  assert.deepEqual(allowedNextStatuses('new'), ['confirmed', 'cancelled']);
  assert.equal(ORDER_STATUSES.ready, 'Готов к выдаче');
  for (const status of ['ready', 'completed', 'new', 'missing']) assert.throws(() => changeOrderStatus(order, status));
  for (const final of [completed, changeOrderStatus(order, 'cancelled')]) {
    assert.deepEqual(allowedNextStatuses(final.status), []);
    for (const status of Object.keys(ORDER_STATUSES)) assert.throws(() => changeOrderStatus(final, status));
  }
});

test('unknown status names always expose an empty transition list', () => {
  for (const status of ['unknown', '__proto__', 'constructor', null]) {
    assert.deepEqual(allowedNextStatuses(status), []);
  }
});

test('saved orders accept valid snapshots independent of the current catalog', () => {
  const order = createOrder(cart, customer, pickup, products);
  assert.deepEqual(sanitizeOrders([order]), [order]);
  assert.deepEqual(sanitizeOrders([{ ...order, items: [{ ...order.items[0], productId: 'discontinued-part' }, order.items[1]] }]).length, 1);
  for (const value of [null, {}, 'broken']) assert.deepEqual(sanitizeOrders(value), []);
});

test('saved orders discard corrupt shape, money, contact, items and statuses', () => {
  const order = createOrder(cart, customer, pickup, products);
  const corrupt = [
    null, {}, { ...order, id: '' }, { ...order, number: 17 }, { ...order, createdAt: 'yesterday' },
    { ...order, totalKopecks: -1 }, { ...order, totalKopecks: 1.5 }, { ...order, totalKopecks: 505049 },
    { ...order, status: 'unknown' }, { ...order, integrationStatus: 'sent' },
    { ...order, customer: null }, { ...order, customer: { ...order.customer, phone: '123' } },
    { ...order, customer: { ...order.customer, email: 'invalid' } },
    { ...order, customer: { ...order.customer, email: 123 } },
    { ...order, customer: { ...order.customer, name: 'И'.repeat(101) } },
    { ...order, customer: { ...order.customer, phone: `7${' '.repeat(20)}9991234567` } },
    { ...order, customer: { ...order.customer, email: `${'a'.repeat(244)}@example.ru` } },
    { ...order, customer: { name: order.customer.name, phone: order.customer.phone } },
    { ...order, fulfillment: { method: 'pickup', address: 123 } },
    { ...order, fulfillment: { method: 'delivery', address: 'А'.repeat(501) } },
    { ...order, fulfillment: { method: 'delivery', address: '' } },
    { ...order, items: [] }, { ...order, items: [null] },
    { ...order, items: [{ ...order.items[0], quantity: -1 }] },
    { ...order, items: [{ ...order.items[0], unitPriceKopecks: '129999' }] },
    { ...order, items: [{ ...order.items[0], name: null }] },
  ];
  assert.deepEqual(sanitizeOrders([...corrupt, order]), [order]);
});

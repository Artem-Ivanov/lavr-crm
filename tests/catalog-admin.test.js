import test from 'node:test';
import assert from 'node:assert/strict';
import { CATEGORIES, PRODUCTS, filterProducts } from '../src/catalog.js';
import { initialCatalog, restoreCatalog, createCategory, createProduct, assignProductCategory } from '../src/catalog-admin.js';
import { addCartItem, setCartQuantity, cartTotal } from '../src/cart.js';
import { createOrder } from '../src/orders.js';

const draft = { name: 'Новый фильтр', sku: 'TEST-42', brand: 'Новый бренд', category: 'filters', price: '1234,56', stock: '3' };

test('initial catalog is fresh and does not share mutable fixture metadata', () => {
  const first = initialCatalog();
  assert.deepEqual(first, { categories: CATEGORIES, products: PRODUCTS });
  first.categories[0].name = 'Изменено';
  first.products[0].specs['Вязкость'] = 'Изменено';
  assert.equal(initialCatalog().categories[0].name, 'Масла и жидкости');
  assert.equal(initialCatalog().products[0].specs['Вязкость'], '5W-30');
  assert.deepEqual(restoreCatalog(null), initialCatalog());
});

test('category creation normalizes spaces and leaves the original catalog unchanged', () => {
  const catalog = initialCatalog();
  const created = createCategory(catalog, { name: '  Кузовные   детали  ' });
  const category = created.categories.at(-1);
  assert.equal(category.name, 'Кузовные детали');
  assert.match(category.id, /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i);
  assert.equal(catalog.categories.length, 6);
  assert.deepEqual(created.products, catalog.products);
  assert.notEqual(createCategory(catalog, { name: 'Кузовные детали' }).categories.at(-1).id, category.id);
});

test('category creation rejects duplicate names, empty names and oversized fields', () => {
  const catalog = createCategory(initialCatalog(), { name: 'Кузовные детали' });
  for (const name of [' кузовные   ДЕТАЛИ ', '  ФИЛЬТРЫ  ', '', ' ', null, 17, 'А'.repeat(81)]) {
    assert.throws(() => createCategory(catalog, { name }));
  }
  assert.equal(createCategory(catalog, { name: 'А'.repeat(80) }).categories.at(-1).name.length, 80);
});

test('a new product belongs to a category and has safe empty optional metadata', () => {
  const catalog = createCategory(initialCatalog(), { name: 'Кузовные детали' });
  const category = catalog.categories.at(-1).id;
  const created = createProduct(catalog, { ...draft, name: '  Новый фильтр  ', category });
  const product = created.products.at(-1);
  assert.deepEqual(product, {
    id: product.id, externalId: '', name: 'Новый фильтр', sku: 'TEST-42', brand: 'Новый бренд', category,
    priceKopecks: 123456, stock: 3, image: '', deliveryDays: 0, description: '', specs: {},
  });
  assert.match(product.id, /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i);
  assert.equal(catalog.products.length, 14);
  assert.deepEqual(created.categories, catalog.categories);
});

test('product prices convert ruble strings into exact bounded integer kopecks', () => {
  for (const [price, expected] of [['0.01', 1], ['19.9', 1990], ['1234,56', 123456], ['10000000.00', 1000000000]]) {
    assert.equal(createProduct(initialCatalog(), { ...draft, price }).products.at(-1).priceKopecks, expected);
  }
  for (const price of ['', ' ', '0', '-1', '.50', '1.001', '1,2.3', '1e2', 'Infinity', 'NaN', '10000000.01', '99999999999999999999999', 10, null]) {
    assert.throws(() => createProduct(initialCatalog(), { ...draft, price }));
  }
});

test('product stock accepts bounded nonnegative integers and integer strings', () => {
  for (const [stock, expected] of [[0, 0], [' 3 ', 3], [100000, 100000]]) {
    assert.equal(createProduct(initialCatalog(), { ...draft, stock }).products.at(-1).stock, expected);
  }
  for (const stock of ['', ' ', '-1', -1, 1.5, '1.5', '1e2', 100001, Infinity, NaN, null, true]) {
    assert.throws(() => createProduct(initialCatalog(), { ...draft, stock }));
  }
});

test('product fields and category links are validated before creation', () => {
  for (const invalid of [
    { name: '' }, { name: null }, { name: 'А'.repeat(161) },
    { sku: '' }, { sku: 123 }, { sku: '---' }, { sku: 'А'.repeat(81) },
    { brand: '' }, { brand: null }, { brand: '---' }, { brand: 'А'.repeat(81) },
    { category: 'missing' }, { category: null },
  ]) assert.throws(() => createProduct(initialCatalog(), { ...draft, ...invalid }));
  const product = createProduct(initialCatalog(), { ...draft, name: 'А'.repeat(160), sku: 'Б'.repeat(80), brand: 'В'.repeat(80) }).products.at(-1);
  assert.equal(product.name.length, 160);
  assert.equal(product.sku.length, 80);
  assert.equal(product.brand.length, 80);
});

test('product uniqueness uses normalized brand and SKU together', () => {
  const catalog = createProduct(initialCatalog(), draft);
  assert.throws(() => createProduct(catalog, { ...draft, sku: 'test 42', brand: 'НОВЫЙ-БРЕНД' }));
  assert.throws(() => createProduct(catalog, { ...draft, sku: 'W9142', brand: 'mann filter' }));
  const created = createProduct(catalog, { ...draft, brand: 'Другой бренд' });
  assert.equal(created.products.at(-1).sku, 'TEST-42');
  assert.notEqual(created.products.at(-1).id, catalog.products.at(-1).id);
});

test('assigning a category preserves identifiers, metadata and the original product', () => {
  const catalog = initialCatalog();
  const created = assignProductCategory(catalog, 'filter-mann-oil', 'oils');
  assert.deepEqual(created.products.find(product => product.id === 'filter-mann-oil'), { ...catalog.products[1], category: 'oils' });
  assert.equal(catalog.products[1].category, 'filters');
  for (const [productId, categoryId] of [['missing', 'oils'], ['filter-mann-oil', 'missing'], [null, 'oils']]) {
    assert.throws(() => assignProductCategory(catalog, productId, categoryId));
  }
});

test('restored custom products work in search, cart totals and order price snapshots', () => {
  const saved = createProduct(initialCatalog(), draft);
  const restored = restoreCatalog(JSON.parse(JSON.stringify(saved)));
  assert.deepEqual(restored, saved);
  const product = restored.products.at(-1);
  assert.equal(filterProducts(restored.products, { query: 'test 42' })[0].id, product.id);
  const cart = setCartQuantity(addCartItem([], product.id, restored.products), product.id, 2, restored.products);
  assert.equal(cartTotal(cart, restored.products), 246912);
  const order = createOrder(cart, { name: 'Иван', phone: '+79991234567', email: '' }, { method: 'pickup', address: '' }, restored.products);
  restored.products.at(-1).priceKopecks = 1;
  restored.products.at(-1).name = 'Изменено';
  assert.equal(order.items[0].unitPriceKopecks, 123456);
  assert.equal(order.items[0].name, 'Новый фильтр');
  assert.equal(order.totalKopecks, 246912);
});

test('restoration whitelists fields and copies valid seed metadata', () => {
  const raw = initialCatalog();
  raw.extra = true;
  raw.categories[0].extra = true;
  raw.products[0].extra = true;
  const restored = restoreCatalog(raw);
  assert.deepEqual(restored, initialCatalog());
  raw.products[0].specs['Вязкость'] = 'Изменено';
  assert.equal(restored.products[0].specs['Вязкость'], '5W-30');
});

test('restoration rejects malformed snapshots and duplicate identities instead of dropping entries', () => {
  for (const invalid of [undefined, 'broken', [], 1, {}, { categories: [], products: null }]) {
    assert.throws(() => restoreCatalog(invalid));
  }
  const original = initialCatalog();
  for (const change of [
    raw => raw.categories.push({ ...raw.categories[0], name: 'Ещё одна категория' }),
    raw => raw.categories.push({ id: 'another', name: '  МАСЛА И   ЖИДКОСТИ  ' }),
    raw => raw.products.push({ ...raw.products[0], sku: 'another' }),
    raw => raw.products.push({ ...raw.products[1], id: 'another', sku: 'w9142', brand: 'mann filter' }),
    raw => { raw.products[0].category = 'missing'; },
    raw => { raw.categories[0].id = ''; },
    raw => { raw.categories[0].name = 'А'.repeat(81); },
    raw => { raw.products[0].id = ''; },
  ]) {
    const raw = structuredClone(original);
    change(raw);
    assert.throws(() => restoreCatalog(raw));
  }
});

test('restoration rejects invalid money, inventory, metadata and untrusted image URLs', () => {
  const original = initialCatalog();
  for (const invalid of [
    { priceKopecks: '389000' }, { priceKopecks: 0 }, { priceKopecks: 1.5 }, { priceKopecks: 1000000001 },
    { stock: '12' }, { stock: -1 }, { stock: 1.5 }, { stock: 100001 },
    { name: null }, { name: 'А'.repeat(161) }, { sku: '' }, { brand: '' }, { externalId: 123 },
    { image: 'https://example.com/track.jpg' }, { image: '//example.com/track.jpg' }, { image: '/images/untrusted.svg' },
    { image: '/images/../private.jpg' }, { image: 'data:image/svg+xml,broken' },
    { deliveryDays: -1 }, { deliveryDays: '1' }, { description: {} }, { specs: [] }, { specs: { Тип: {} } },
  ]) {
    const raw = structuredClone(original);
    raw.products[0] = { ...raw.products[0], ...invalid };
    assert.throws(() => restoreCatalog(raw));
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { createDemoApi } from '../src/data/demo-api.js';
import { initialCatalog } from '../src/catalog-admin.js';

const CATALOG_KEY = 'lavr.catalog.v1';
const ORDERS_KEY = 'lavr.orders.v1';
const CART_KEY = 'lavr.cart.v1';
class Storage {
  values = new Map();
  failRead = false;
  failWrite = false;
  getItem(key) {
    if (this.failRead) throw new Error('Storage unavailable');
    return this.values.get(key) ?? null;
  }
  setItem(key, value) {
    if (this.failWrite) throw new Error('Storage quota exceeded');
    this.values.set(key, String(value));
  }
}
function setup() {
  const localStorage = new Storage();
  const sessionStorage = new Storage();
  return { api: createDemoApi({ localStorage, sessionStorage }), localStorage, sessionStorage };
}
const orderBody = () => ({
  customer: { name: 'Демо Покупатель', phone: '+7 (999) 123-45-67', email: '' },
  fulfillment: { method: 'pickup', address: '' },
  items: [{ productId: 'filter-mann-oil', quantity: 1, expectedUnitPriceKopecks: 89000 }],
});

test('demo catalog queries filter before paging and count all products per category', async () => {
  const { api } = setup();
  assert.equal(api.mode, 'demo');
  assert.equal(api.canCreateProduct, true);
  const result = await api.getCatalog({ category: 'oils', sort: 'price_asc' }, { offset: 1, limit: 1 });
  assert.equal(result.total, 3);
  assert.equal(result.items[0].id, 'oil-shell-5w30');
  assert.equal(result.offset, 1);
  assert.equal(result.limit, 1);
  assert.equal(result.readOnly, false);
  assert.equal(result.issue, '');
  assert.equal(result.stale, false);
  assert.equal((await api.getCategories()).find(category => category.id === 'filters').productCount, 3);
  assert.equal((await api.getAdminProducts({ query: 'w9142' })).items[0].id, 'filter-mann-oil');
  assert.equal((await api.getProduct('filter-mann-oil')).product.priceKopecks, 89000);
  await assert.rejects(api.getProduct('missing'), /Товар не найден/);
  for (const paging of [{ offset: -1 }, { limit: 0 }, { limit: 1.5 }]) {
    await assert.rejects(api.getCatalog({}, paging), /пагинац/i);
  }
});

test('demo site owns the original hero and all three promotion fixtures', async () => {
  const { api } = setup();
  const site = await api.getSite();
  assert.equal(site.hero.title, 'Всё для вашего\nавтомобиля.\nВ одном месте.');
  assert.equal(site.hero.description, 'Найдите запчасть по артикулу\nили начните с нужной категории.');
  assert.equal(site.hero.image.path, '/images/hero-car.jpg');
  assert.deepEqual(site.hero.target, { type: 'catalog' });
  assert.deepEqual(site.publications.map(publication => publication.title), [
    'Свежий фильтр.\nСпокойная дорога.', 'Время заменить\nмасло?', 'Знаете артикул?\nНачните с него.',
  ]);
  assert.deepEqual(site.publications[0].images.map(image => image.path), ['/images/air-filter.jpg', '/images/oil-filter.jpg']);
  assert.deepEqual(site.publications[1].target, { type: 'category', categoryId: 'oils' });
  assert.deepEqual(site.publications.map(publication => publication.style), ['service', 'oil', 'notice']);
  site.hero.title = 'Изменено';
  assert.equal((await api.getSite()).hero.title, 'Всё для вашего\nавтомобиля.\nВ одном месте.');
});

test('a corrupt saved catalog is shown read only and never overwritten by mutations or checkout', async () => {
  const { api, localStorage, sessionStorage } = setup();
  const damaged = '{"categories":';
  localStorage.setItem(CATALOG_KEY, damaged);
  const result = await api.getCatalog();
  assert.equal(result.total, 14);
  assert.equal(result.readOnly, true);
  assert.match(result.issue, /Сохранённый каталог повреждён/);
  assert.equal((await api.getCategories()).length, 6);
  await assert.rejects(api.createCategory({ name: 'Электрика' }), /каталог повреждён/);
  await assert.rejects(api.createProduct({}), /каталог повреждён/);
  await assert.rejects(api.assignProductCategory('filter-mann-oil', 'oils'), /каталог повреждён/);
  await assert.rejects(api.createOrder(orderBody(), 'blocked'), /каталог повреждён/);
  assert.equal(localStorage.getItem(CATALOG_KEY), damaged);
  assert.equal(sessionStorage.getItem(ORDERS_KEY), null);
});

test('unavailable storage displays demo data and catalog write failure leaves prior data intact', async () => {
  const { api, localStorage } = setup();
  localStorage.failRead = true;
  assert.equal((await api.getCatalog()).readOnly, true);
  await assert.rejects(api.createCategory({ name: 'Электрика' }), /каталог повреждён/);
  localStorage.failRead = false;
  const saved = JSON.stringify(initialCatalog());
  localStorage.setItem(CATALOG_KEY, saved);
  localStorage.failWrite = true;
  await assert.rejects(api.createCategory({ name: 'Электрика' }), /Не удалось сохранить каталог/);
  assert.equal(localStorage.getItem(CATALOG_KEY), saved);
  assert.equal((await api.getCategories()).length, 6);
});

test('empty and structurally invalid saved data are preserved as corruption rather than missing data', async () => {
  const { api, localStorage, sessionStorage } = setup();
  for (const damaged of ['', '{"categories":[],"products":[{}]}']) {
    localStorage.setItem(CATALOG_KEY, damaged);
    assert.equal((await api.getCatalog()).readOnly, true);
    await assert.rejects(api.createCategory({ name: 'Электрика' }), /каталог повреждён/);
    assert.equal(localStorage.getItem(CATALOG_KEY), damaged);
  }
  localStorage.values.delete(CATALOG_KEY);
  for (const damaged of ['', '{}']) {
    sessionStorage.setItem(ORDERS_KEY, damaged);
    assert.equal((await api.getOrders()).readOnly, true);
    await assert.rejects(api.createOrder(orderBody(), 'damaged-orders'), /прочитать/);
    assert.equal(sessionStorage.getItem(ORDERS_KEY), damaged);
  }
});

test('catalog mutations reread sibling changes and persist before returning the created records', async () => {
  const { api, localStorage } = setup();
  const sibling = createDemoApi({ localStorage, sessionStorage: new Storage() });
  const category = await sibling.createCategory({ name: 'Электрика' });
  assert.equal(category.name, 'Электрика');
  const product = await api.createProduct({ name: 'Датчик', sku: 'SAFE-001', brand: 'Demo', category: category.id, price: '12,34', stock: '4' });
  assert.equal(product.priceKopecks, 1234);
  assert.equal((await sibling.getCategories()).find(item => item.id === category.id).productCount, 1);
  const moved = await api.assignProductCategory('filter-mann-oil', category.id);
  assert.equal(moved.category, category.id);
  assert.equal(JSON.parse(localStorage.getItem(CATALOG_KEY)).products.find(item => item.id === moved.id).category, category.id);
  assert.equal((await sibling.getCategories()).find(item => item.id === category.id).productCount, 2);
  await assert.rejects(api.createCategory({ name: ' электрика ' }), /уже существует/);
  await assert.rejects(api.createProduct({ name: 'Повтор', sku: 'SAFE001', brand: 'Demo', category: category.id, price: '12,34', stock: '4' }), /уже существует/);
});

test('checkout persists snapshots and retries a key without creating a second order', async () => {
  const { api, localStorage, sessionStorage } = setup();
  localStorage.setItem(CART_KEY, '[{"productId":"filter-mann-oil","quantity":1}]');
  const body = orderBody();
  const [order, retry] = await Promise.all([api.createOrder(body, 'request-1'), api.createOrder(structuredClone(body), 'request-1')]);
  assert.equal(retry.id, order.id);
  assert.equal(order.customer.phone, '+79991234567');
  assert.equal(order.totalKopecks, 89000);
  assert.equal(order.status, 'new');
  assert.equal(order.integrationStatus, 'not_connected');
  assert.equal(JSON.parse(sessionStorage.getItem(ORDERS_KEY)).length, 1);
  assert.equal(localStorage.getItem(CART_KEY), '[{"productId":"filter-mann-oil","quantity":1}]');
  assert.equal(localStorage.getItem(CATALOG_KEY), null);
  const changed = orderBody();
  changed.items[0].quantity = 2;
  await assert.rejects(api.createOrder(changed, 'request-1'), /ключ|повтор/i);
  assert.equal(JSON.parse(sessionStorage.getItem(ORDERS_KEY)).length, 1);
});

test('checkout fails safely on storage quota and the same key can be retried after recovery', async () => {
  const { api, localStorage, sessionStorage } = setup();
  localStorage.setItem(CART_KEY, 'original cart');
  sessionStorage.failWrite = true;
  await assert.rejects(api.createOrder(orderBody(), 'quota'), /Корзина сохранена/);
  assert.equal(sessionStorage.getItem(ORDERS_KEY), null);
  assert.equal(localStorage.getItem(CART_KEY), 'original cart');
  sessionStorage.failWrite = false;
  const order = await api.createOrder(orderBody(), 'quota');
  assert.equal(order.totalKopecks, 89000);
  assert.equal(JSON.parse(sessionStorage.getItem(ORDERS_KEY)).length, 1);
});

test('retrying the same order with reordered object fields returns the original receipt', async () => {
  const { api, sessionStorage } = setup();
  const original = await api.createOrder(orderBody(), 'same-body');
  const retry = await api.createOrder({
    items: [{ expectedUnitPriceKopecks: 89000, quantity: 1, productId: 'filter-mann-oil' }],
    fulfillment: { address: '', method: 'pickup' },
    customer: { email: '', phone: '+7 (999) 123-45-67', name: 'Демо Покупатель' },
  }, 'same-body');
  assert.equal(retry.id, original.id);
  assert.equal(JSON.parse(sessionStorage.getItem(ORDERS_KEY)).length, 1);
});

test('checkout rejects changed expected prices and keeps domain stock and contact validation', async () => {
  const { api, sessionStorage } = setup();
  const body = orderBody();
  body.items[0].expectedUnitPriceKopecks = 88000;
  await assert.rejects(api.createOrder(body, 'old-price'), /Цена|цена/);
  delete body.items[0].expectedUnitPriceKopecks;
  await assert.rejects(api.createOrder(body, 'missing-price'), /цен/i);
  body.items[0].expectedUnitPriceKopecks = 89000;
  body.items[0].quantity = 25;
  await assert.rejects(api.createOrder(body, 'stock'), /Недостаточно товара/);
  body.items[0].quantity = 1;
  body.customer.phone = '123';
  await assert.rejects(api.createOrder(body, 'contact'), /российский телефон/);
  assert.equal(sessionStorage.getItem(ORDERS_KEY), null);
});

test('order search and paging use sanitized persisted orders without rewriting damaged entries', async () => {
  const { api, sessionStorage } = setup();
  const first = await api.createOrder(orderBody(), 'first');
  const secondBody = orderBody();
  secondBody.customer.name = 'Анна Демо';
  const second = await api.createOrder(secondBody, 'second');
  await api.updateOrderStatus(second, 'confirmed');
  const raw = JSON.stringify([null, ...JSON.parse(sessionStorage.getItem(ORDERS_KEY))]);
  sessionStorage.setItem(ORDERS_KEY, raw);
  const result = await api.getOrders({ query: 'анна', status: 'confirmed' }, { offset: 0, limit: 1 });
  assert.equal(result.total, 1);
  assert.equal(result.items[0].id, second.id);
  const page = await api.getOrders({}, { offset: 1, limit: 1 });
  assert.equal(page.total, 2);
  assert.equal(page.items[0].id, first.id);
  assert.equal((await api.getOrder(second.id)).status, 'confirmed');
  await assert.rejects(api.getOrder('missing'), /Заказ не найден/);
  assert.equal(sessionStorage.getItem(ORDERS_KEY), raw);
});

test('corrupt or unreadable saved orders never get overwritten by checkout', async () => {
  const { api, sessionStorage } = setup();
  const damaged = '{"orders":';
  sessionStorage.setItem(ORDERS_KEY, damaged);
  const result = await api.getOrders();
  assert.deepEqual(result.items, []);
  assert.equal(result.readOnly, true);
  assert.match(result.issue, /прочитать/);
  await assert.rejects(api.createOrder(orderBody(), 'corrupt'), /прочитать/);
  assert.equal(sessionStorage.getItem(ORDERS_KEY), damaged);
  sessionStorage.failRead = true;
  await assert.rejects(api.createOrder(orderBody(), 'unavailable'), /прочитать/);
  sessionStorage.failRead = false;
  assert.equal(sessionStorage.getItem(ORDERS_KEY), damaged);
});

test('status mutations use persisted status and leave the saved order unchanged when persistence fails', async () => {
  const { api, sessionStorage } = setup();
  const original = await api.createOrder(orderBody(), 'status');
  const confirmed = await api.updateOrderStatus(original, 'confirmed');
  assert.equal(confirmed.status, 'confirmed');
  await assert.rejects(api.updateOrderStatus(original, 'confirmed'), /переход/);
  sessionStorage.failWrite = true;
  await assert.rejects(api.updateOrderStatus(confirmed, 'ready'), /сохранить/);
  assert.equal((await api.getOrder(original.id)).status, 'confirmed');
  sessionStorage.failWrite = false;
  assert.equal((await api.updateOrderStatus(original.id, 'ready')).status, 'ready');
});

test('demo session is identified as a demonstration', async () => {
  const { api } = setup();
  assert.equal((await api.getSession()).demo, true);
  assert.equal((await api.login({ username: 'anything', password: 'anything' })).demo, true);
  await api.logout();
});

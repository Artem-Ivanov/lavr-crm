import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpApi } from '../src/data/http-api.js';

const product = {
  id: 'oil', externalId: '1c-oil', sku: 'OIL-42', brand: 'LAVR', name: 'Моторное масло', category: 'oils',
  priceKopecks: 389000, stock: 12, image: '/images/motor-oil.jpg', deliveryDays: 0,
};
const category = { id: 'oils', name: 'Масла', position: 0, showInMenu: true };
const adminCategory = { ...category, isVisible: true, version: 1 };
const session = { user: { id: 'manager', name: 'Ольга', role: 'admin' }, csrfToken: 'a'.repeat(32), expiresAt: '2026-10-12T10:00:00Z' };
const receipt = { id: 'order-1', number: 'L-1', createdAt: '2026-10-11T10:00:00Z', totalKopecks: 389000, status: 'new', integrationStatus: 'pending_sync' };
const order = {
  ...receipt, customer: { name: 'Иван', phone: '+79991234567', email: '' }, fulfillment: { method: 'pickup', address: '' },
  items: [{ productId: 'oil', sku: 'OIL-42', brand: 'LAVR', name: 'Моторное масло', quantity: 1, unitPriceKopecks: 389000 }],
  version: 3, canCancel: true, externalDocumentId: null,
};
const presentation = { productId: 'oil', categoryId: 'oils', websiteTitle: null, websiteDescription: null, imageIds: [], version: 7 };
const media = { id: 'hero', path: '/api/media/hero', alt: 'Автомобиль', width: 1200, height: 800 };
const site = {
  hero: { title: 'Автозапчасти', description: 'Для вашего авто', image: media, buttonText: 'В каталог', target: { type: 'catalog' } },
  settings: { storeName: 'LAVR', phone: '+79991234567', address: 'Москва', hours: '9–18', categoriesTitle: 'Категории', promotionsTitle: 'Акции', catalogTitle: 'Каталог' },
  menu: [{ label: 'Масла', target: { type: 'category', categoryId: 'oils' }, position: 0, isVisible: true }],
  publications: [{ id: 'offer', kind: 'offer', title: 'Акция', description: 'Описание', images: [media], target: { type: 'product', productId: 'oil' }, buttonText: 'Открыть', position: 0 }],
};
const page = (items, extra = {}) => ({ items, total: items.length, offset: 0, limit: 24, ...extra });
const catalogPage = (items = [product], extra = {}) => page(items, { catalogUpdatedAt: '2026-10-11T10:00:00Z', stale: false, ...extra });
const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', ...headers } });

function setup(responses, options = {}) {
  const calls = [];
  const api = createHttpApi({ ...options, fetchImpl: async (url, init) => {
    calls.push({ url, ...init });
    assert.ok(responses.length, `Unexpected request: ${url}`);
    const response = responses.shift();
    if (response instanceof Error) throw response;
    return response;
  } });
  return { api, calls };
}

test('API uses only a same-origin path and declares server-owned product creation', () => {
  const { api } = setup([]);
  assert.equal(api.mode, 'api');
  assert.equal(api.canCreateProduct, false);
  for (const baseUrl of ['https://example.com/api', '//example.com/api', 'api', '/\\example.com', '/api?token=secret', '/api#fragment', '/api/../private', '/%2f%2fexample.com', '/api\n']) {
    assert.throws(() => createHttpApi({ baseUrl }), /API|адрес|origin/i);
  }
});

test('catalog filters map to the contract and product detail responses preserve freshness', async () => {
  const expectedPage = catalogPage([product], { total: 25, offset: 24 });
  const { api, calls } = setup([json(expectedPage), json({ product, catalogUpdatedAt: '2026-10-11T10:00:00Z', stale: false })], { baseUrl: '/backend/api/' });
  assert.deepEqual(await api.getCatalog({ query: 'масло & фильтр', category: 'oils', brand: 'LAVR', inStock: true, sort: 'price_asc' }, { offset: 24, limit: 24 }), expectedPage);
  assert.equal(calls[0].url, '/backend/api/catalog?q=%D0%BC%D0%B0%D1%81%D0%BB%D0%BE+%26+%D1%84%D0%B8%D0%BB%D1%8C%D1%82%D1%80&category=oils&brand=LAVR&inStock=true&sort=price_asc&offset=24&limit=24');
  assert.deepEqual(await api.getProduct('oil'), { product, catalogUpdatedAt: '2026-10-11T10:00:00Z', stale: false });
  assert.equal(calls[1].url, '/backend/api/catalog/oil');
  for (const call of calls) {
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.cache, 'no-store');
    assert.equal(call.headers.Accept, 'application/json');
    assert.equal(call.headers['Content-Type'], undefined);
    assert.equal(call.headers['X-CSRF-Token'], undefined);
  }
});

test('admin categories fetch every page while public categories remain an array', async () => {
  const first = Array.from({ length: 100 }, (_, index) => ({ ...adminCategory, id: `category-${index}`, name: `Категория ${index}`, position: index }));
  const last = { ...adminCategory, id: 'category-100', name: 'Последняя', position: 100 };
  const { api, calls } = setup([json([category]), json(page(first, { total: 101, limit: 100 })), json(page([last], { total: 101, offset: 100, limit: 100 }))]);
  assert.deepEqual(await api.getCategories(), [category]);
  assert.deepEqual(await api.getCategories({ admin: true }), [...first, last]);
  assert.deepEqual(calls.map(call => call.url), ['/api/categories', '/api/admin/categories?offset=0&limit=100', '/api/admin/categories?offset=100&limit=100']);
});

test('incomplete category pagination rejects instead of silently omitting categories', async () => {
  const { api } = setup([json(page([], { total: 5, limit: 100 }))]);
  await assert.rejects(api.getCategories({ admin: true }), /ответ|пагинац|категори/i);
});

test('manager and admin searches use server pagination and protected endpoints', async () => {
  const summary = { id: 'oil', sku: 'OIL-42', brand: 'LAVR', name: 'Моторное масло', category: 'oils', priceKopecks: 389000, stock: 12, isVisible: false };
  const { api, calls } = setup([json(page([summary])), json(page([order])), json(order)]);
  assert.deepEqual(await api.getAdminProducts({ query: 'OIL', category: 'oils' }), page([summary]));
  assert.deepEqual(await api.getOrders({ query: 'Иван', status: 'new' }), page([order]));
  assert.deepEqual(await api.getOrder('order-1'), order);
  assert.deepEqual(calls.map(call => call.url), ['/api/admin/products?q=OIL&category=oils&offset=0&limit=24', '/api/manager/orders?q=%D0%98%D0%B2%D0%B0%D0%BD&status=new&offset=0&limit=24', '/api/manager/orders/order-1']);
});

test('checkout sends the price agreement and idempotency key once without a session token', async () => {
  const body = { customer: order.customer, fulfillment: order.fulfillment, items: [{ productId: 'oil', quantity: 1, expectedUnitPriceKopecks: 389000 }] };
  const { api, calls } = setup([json(receipt, 201)]);
  assert.deepEqual(await api.createOrder(body, '9a55cbe9-3743-4f32-9c83-4c01955ade11'), receipt);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/orders');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].headers['Idempotency-Key'], '9a55cbe9-3743-4f32-9c83-4c01955ade11');
  assert.equal(calls[0].headers['X-CSRF-Token'], undefined);
  assert.equal(calls[0].headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(calls[0].body), body);
});

test('session mutations send CSRF and optimistic versions without changing accounting fields', async () => {
  const saved = { ...presentation, categoryId: 'filters', version: 8 };
  const updatedOrder = { ...order, status: 'cancelled', version: 4 };
  const { api, calls } = setup([json(session), json(adminCategory, 201), json(updatedOrder), json({ product, presentation }, 200, { ETag: '"7"' }), json(saved), new Response(null, { status: 204 })]);
  assert.deepEqual(await api.login({ login: 'olga', password: 'secret' }), session);
  assert.deepEqual(await api.createCategory({ name: 'Масла', position: 0, isVisible: true, showInMenu: true }), adminCategory);
  assert.deepEqual(await api.updateOrderStatus(order, 'cancelled'), updatedOrder);
  assert.deepEqual(await api.assignProductCategory('oil', 'filters'), saved);
  await api.logout();
  assert.equal(calls[0].headers['X-CSRF-Token'], undefined);
  assert.deepEqual(JSON.parse(calls[0].body), { login: 'olga', password: 'secret' });
  assert.deepEqual(JSON.parse(calls[1].body), { name: 'Масла', position: 0, isVisible: true, showInMenu: true });
  assert.equal(calls[2].url, '/api/manager/orders/order-1/status');
  assert.equal(calls[2].method, 'PATCH');
  assert.equal(calls[2].headers['If-Match'], '"3"');
  assert.deepEqual(JSON.parse(calls[2].body), { status: 'cancelled' });
  assert.equal(calls[3].url, '/api/admin/products/oil/presentation');
  assert.equal(calls[3].headers['X-CSRF-Token'], undefined);
  assert.equal(calls[4].method, 'PATCH');
  assert.equal(calls[4].headers['If-Match'], '"7"');
  assert.deepEqual(JSON.parse(calls[4].body), { categoryId: 'filters' });
  for (const index of [1, 2, 4, 5]) assert.equal(calls[index].headers['X-CSRF-Token'], session.csrfToken);
  assert.equal(calls[5].method, 'DELETE');
  assert.equal(calls[5].body, undefined);
  await assert.rejects(api.createCategory({ name: 'Масла', position: 0, isVisible: true, showInMenu: true }), /вход|сесси|войдите/i);
  assert.equal(calls.length, 6);
});

test('presentation version is used when GET does not include an ETag', async () => {
  const { api, calls } = setup([json(session), json({ product, presentation }), json({ ...presentation, categoryId: 'filters', version: 8 })]);
  await api.getSession();
  await api.assignProductCategory('oil', 'filters');
  assert.equal(calls[2].headers['If-Match'], '"7"');
});

test('401 clears the token and errors expose the safe API message, code and status', async () => {
  const { api, calls } = setup([json(session), json({ code: 'SESSION_EXPIRED', message: 'Войдите снова', requestId: 'r1' }, 401)]);
  await api.getSession();
  await assert.rejects(api.getOrders(), error => error.message === 'Войдите снова' && error.status === 401 && error.code === 'SESSION_EXPIRED');
  await assert.rejects(api.updateOrderStatus(order, 'cancelled'), /вход|сесси|войдите/i);
  assert.equal(calls.length, 2);
});

test('failed mutations are not retried and preserve server conflict details', async () => {
  const { api, calls } = setup([json(session), json({ code: 'VERSION_CONFLICT', message: 'Заказ изменился', requestId: 'r2' }, 412)]);
  await api.getSession();
  await assert.rejects(api.updateOrderStatus(order, 'cancelled'), error => error.status === 412 && error.code === 'VERSION_CONFLICT' && error.message === 'Заказ изменился');
  assert.equal(calls.length, 2);
});

test('non-JSON and broken success responses reject instead of falling back to demo data', async () => {
  for (const response of [new Response('<html>Vite fallback</html>', { headers: { 'Content-Type': 'text/html' } }), new Response('{broken', { headers: { 'Content-Type': 'application/json' } }), json({}), json({ items: [], total: 0, offset: 0, limit: 24 })]) {
    const { api } = setup([response]);
    await assert.rejects(api.getCatalog(), /ответ|JSON/i);
  }
  const { api } = setup([new Response('<html>private stack trace</html>', { status: 500, headers: { 'Content-Type': 'text/html' } })]);
  await assert.rejects(api.getSite(), error => error.status === 500 && !error.message.includes('private stack trace'));
});

test('malformed products, money and page metadata never reach the UI', async () => {
  for (const change of [
    { id: '' }, { externalId: null }, { category: null }, { name: {} }, { priceKopecks: '389000' }, { priceKopecks: -1 },
    { priceKopecks: Number.MAX_SAFE_INTEGER + 1 }, { stock: 1.5 }, { stock: -1 }, { stock: 2147483648 },
    { image: 'https://tracker.example/pixel.jpg' }, { image: '//tracker.example/pixel.jpg' }, { image: '/images/../private.jpg' },
    { image: '/images/untrusted.svg' }, { image: '/api/admin/media/private/file' }, { deliveryDays: -1 },
    { images: [{ ...media, path: 'data:image/svg+xml,broken' }] },
  ]) {
    const { api } = setup([json(catalogPage([{ ...product, ...change }]))]);
    await assert.rejects(api.getCatalog(), /ответ/i);
  }
  for (const change of [{ items: null }, { total: -1 }, { total: 0 }, { total: 1.5 }, { total: Number.MAX_SAFE_INTEGER + 1 }, { offset: -1 }, { limit: 101 }, { stale: 'false' }]) {
    const { api } = setup([json(catalogPage([product], change))]);
    await assert.rejects(api.getCatalog(), /ответ/i);
  }
  const { api } = setup([json(catalogPage([{ ...product, priceKopecks: 0, image: '/api/media/oil-new', images: [media] }]))]);
  assert.equal((await api.getCatalog()).items[0].priceKopecks, 0);
});

test('site content validates public media and internal targets while preserving escaped text', async () => {
  const textSite = { ...site, hero: { ...site.hero, title: '<strong>Текст</strong>', target: null, image: null } };
  const { api } = setup([json(textSite)]);
  assert.deepEqual(await api.getSite(), textSite);
  for (const invalid of [
    { ...site, menu: null }, { ...site, publications: {} },
    { ...site, hero: { ...site.hero, target: { type: 'url', url: 'https://example.com' } } },
    { ...site, hero: { ...site.hero, image: { ...media, path: '//example.com/pixel.jpg' } } },
    { ...site, publications: [{ ...site.publications[0], images: [{ ...media, path: '/api/media/../private' }] }] },
  ]) {
    const { api: invalidApi } = setup([json(invalid)]);
    await assert.rejects(invalidApi.getSite(), /ответ/i);
  }
});

test('invalid session roles and tokens cannot authorize subsequent changes', async () => {
  for (const invalid of [{ ...session, csrfToken: 'short' }, { ...session, user: { ...session.user, role: 'customer' } }, { ...session, user: { ...session.user, name: null } }]) {
    const { api, calls } = setup([json(invalid)]);
    await assert.rejects(api.getSession(), /ответ/i);
    await assert.rejects(api.updateOrderStatus(order, 'cancelled'), /вход|сесси|войдите/i);
    assert.equal(calls.length, 1);
  }
});

test('uncontracted product creation and invalid mutation inputs never send requests', async () => {
  const { api, calls } = setup([]);
  await assert.rejects(api.createProduct({ name: 'Новый товар' }), /номенклатуры.*адаптера 1С/i);
  await assert.rejects(api.getProduct('..'), /идентификатор/i);
  await assert.rejects(api.getCatalog({}, { offset: -1, limit: 24 }), /пагинац|страниц/i);
  await assert.rejects(api.getCatalog({}, { offset: 0, limit: 101 }), /пагинац|страниц/i);
  await assert.rejects(api.createOrder({}, 'bad-key'), /ключ|заказ/i);
  assert.equal(calls.length, 0);
});

test('an older session 401 cannot erase the token from a newer successful login', async () => {
  let resolveOld;
  const calls = [];
  const api = createHttpApi({ fetchImpl: async (url, init) => {
    calls.push({ url, ...init });
    if (calls.length === 1) return new Promise(resolve => { resolveOld = resolve; });
    return json(init.method === 'POST' && url.endsWith('/session') ? session : adminCategory);
  } });
  const old = api.getSession();
  await api.login({ login: 'admin', password: 'test' });
  resolveOld(json({ code: 'EXPIRED', message: 'Expired' }, 401));
  await assert.rejects(old, error => error.status === 401);
  await api.createCategory({ name: 'Масла', position: 0, isVisible: true, showInMenu: true });
  assert.equal(calls.at(-1).headers['X-CSRF-Token'], session.csrfToken);
});

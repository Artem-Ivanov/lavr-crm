import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHttpApi } from '../src/data/http-api.js';
import { createDemoApi } from '../src/data/demo-api.js';
import { initialCatalog, createProduct, restoreCatalog } from '../src/catalog-admin.js';
import { checkoutKey } from '../src/checkout-attempt.js';
import { feedbackKey } from '../src/feedback-attempt.js';
import { setDraftQuantity } from '../src/cart-draft.js';
const key = '9a55cbe9-3743-4f32-9c83-4c01955ade11';
const customer = { name: 'Иван', phone: '+79991234567', email: '' };
const fulfillment = { method: 'pickup', address: '' };
const product = { ...initialCatalog().products[0], id: 'bulk', sku: '', brand: '', unit: 'кг', priceKopecks: 101, stock: 9.785 };
const body = { customer, fulfillment, items: [{ productId: product.id, quantity: 0.955, expectedUnitPriceKopecks: 101, expectedUnit: 'кг' }] };
const json = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const page = item => ({ items: [item], total: 1, limit: 24, offset: 0 });
class Storage {
  values = new Map();
  getItem(key) { return this.values.get(key) ?? null; }
  setItem(key, value) { this.values.set(key, String(value)); }
  removeItem(key) { this.values.delete(key); }
}
function demo() {
  const localStorage = new Storage(); const sessionStorage = new Storage();
  const catalog = initialCatalog(); catalog.products.push(product);
  localStorage.setItem('lavr.catalog.v1', JSON.stringify(catalog));
  return { api: createDemoApi({ localStorage, sessionStorage }), localStorage, sessionStorage };
}

test('HTTP product/admin/order validators accept exact fractional values and empty source codes', async () => {
  const managerOrder = { id: 'order', number: 'L-1', createdAt: '2026-11-02T00:00:00Z', status: 'new', integrationStatus: 'pending_sync', totalKopecks: 96, customer, fulfillment, version: 1, canCancel: true, externalDocumentId: null, items: [{ productId: product.id, name: product.name, sku: '', brand: '', quantity: 0.955, unit: 'кг', unitPriceKopecks: 101, lineTotalKopecks: 96 }] };
  const responses = [json({ ...page(product), catalogUpdatedAt: managerOrder.createdAt, stale: false }), json(page({ ...product, unit: null, isVisible: false, physicalStock: 10.785, reservedStock: 1 })), json(managerOrder), json(managerOrder)];
  const calls = [];
  const api = createHttpApi({ fetchImpl: async (...args) => { calls.push(args); return responses.shift(); } });
  assert.equal((await api.getCatalog()).items[0].stock, 9.785);
  assert.equal((await api.getAdminProducts()).items[0].unit, null);
  assert.equal((await api.getOrder('order')).items[0].lineTotalKopecks, 96);
  await api.createOrder(body, key);
  assert.deepEqual(JSON.parse(calls.at(-1)[1].body), body);
  for (const item of [{ ...body.items[0], quantity: 0.0001 }, { ...body.items[0], quantity: true }, { ...body.items[0], expectedUnit: null }, { ...body.items[0], quantity: 999.001 }]) await assert.rejects(api.createOrder({ ...body, items: [item] }, key));
  assert.equal(calls.length, 4);
});

test('HTTP unit conflict preserves validated current units for explicit review', async () => {
  const details = [{ productId: 'bulk', code: 'UNIT_CHANGED', currentUnit: 'л' }];
  const api = createHttpApi({ fetchImpl: async () => json({ code: 'UNIT_CHANGED', message: 'Единица изменилась.', details }, 409) });
  await assert.rejects(api.createOrder(body, key), error => error.code === 'UNIT_CHANGED' && error.status === 409 && JSON.stringify(error.details) === JSON.stringify(details));
});

test('HTTP catalog accepts fractional piece stock while reservations and ordered pieces stay whole', async () => {
  const piece = { ...product, unit: 'шт', stock: 9.785 };
  const responses = [json({ ...page(piece), catalogUpdatedAt: '2026-11-02T00:00:00Z', stale: false }),
    json(page({ ...piece, isVisible: true, physicalStock: 10.785, reservedStock: 1 })),
    json(page({ ...piece, isVisible: true, physicalStock: 10.785, reservedStock: 1.5 }))];
  const calls = [];
  const api = createHttpApi({ fetchImpl: async (...args) => { calls.push(args); return responses.shift(); } });
  assert.equal((await api.getCatalog()).items[0].stock, 9.785);
  assert.equal((await api.getAdminProducts()).items[0].physicalStock, 10.785);
  await assert.rejects(api.getAdminProducts(), { code: 'INVALID_RESPONSE' });
  await assert.rejects(api.createOrder({ ...body, items: [{ productId: 'bulk', quantity: 0.5, expectedUnitPriceKopecks: 101 }] }, key));
  assert.equal(calls.length, 3);
});

test('demo guest/customer/conversion orders share fractional line rounding and retry signatures', async () => {
  const { api } = demo();
  const first = await api.createOrder(body, key);
  assert.equal(first.totalKopecks, 96);
  assert.equal(first.items[0].unit, 'кг');
  assert.deepEqual(await api.createOrder(body, key), first);
  await assert.rejects(api.createOrder({ ...body, items: [{ ...body.items[0], expectedUnit: 'л' }] }, key), /ключ|Ключ/);
  const legacy = { ...body, items: [{ productId: 'bulk', quantity: 1, expectedUnitPriceKopecks: 101 }] };
  await assert.rejects(api.createOrder(legacy, crypto.randomUUID()), /единиц/i);
  await api.loginUser({ email: 'partner@example.test', password: 'demonstration' });
  const own = await api.createOwnOrder({ fulfillment, items: body.items }, crypto.randomUUID());
  assert.equal(own.items[0].lineTotalKopecks, 96);
  await api.loginUser({ email: 'admin@example.test', password: 'demonstration' });
  const receipt = await api.submitFeedback({ type: 'request', ...customer, message: 'Заказ', messenger: { service: '', address: '' } }, crypto.randomUUID());
  let feedback = await api.getFeedbackDetail(receipt.id);
  feedback = await api.updateFeedbackStatus(feedback, { status: 'in_progress', resolution: '' });
  const conversionKey = crypto.randomUUID();
  const result = await api.createFeedbackOrder(feedback, body, conversionKey);
  assert.equal(result.order.totalKopecks, 96);
  assert.deepEqual(await api.createFeedbackOrder(feedback, body, conversionKey), result);
  await assert.rejects(api.createFeedbackOrder(feedback, { ...body, items: [{ ...body.items[0], expectedUnit: 'л' }] }, conversionKey), /ключ|Ключ/);
});

test('demo catalog restores units, preserves old piece catalogs and never publishes unconfirmed units', async () => {
  const old = initialCatalog(); old.products.forEach(product => { delete product.unit; });
  assert.ok(restoreCatalog(old).products.every(product => product.unit === 'шт'));
  const draft = { name: 'Без артикула', sku: '', brand: '', category: 'oils', price: '1,01', stock: '9,785', unit: 'кг' };
  const once = createProduct(initialCatalog(), draft); const twice = createProduct(once, draft);
  assert.equal(twice.products.length, once.products.length + 1);
  assert.equal(twice.products.at(-1).stock, 9.785);
  const { api, localStorage } = demo();
  const saved = JSON.parse(localStorage.getItem('lavr.catalog.v1')); saved.products.at(-1).unit = null;
  localStorage.setItem('lavr.catalog.v1', JSON.stringify(saved));
  assert.ok(!(await api.getCatalog()).items.some(product => product.id === 'bulk'));
  assert.equal((await api.getAdminProducts()).items.find(product => product.id === 'bulk').unit, null);
  await assert.rejects(api.getProduct('bulk'), /Товар/);
});

test('legacy piece drafts retain the identical checkout/conversion body and saved retry digests', async () => {
  const items = setDraftQuantity([], 'old', 2, 10).map(item => ({ ...item, expectedUnitPriceKopecks: 101 }));
  const oldBody = { customer, fulfillment, items: [{ productId: 'old', quantity: 2, expectedUnitPriceKopecks: 101 }] };
  const restoredBody = { customer, fulfillment, items };
  assert.equal(JSON.stringify(restoredBody), JSON.stringify(oldBody));
  const digest = Buffer.from(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(JSON.stringify(oldBody)))).toString('hex');
  const storage = new Storage();
  storage.setItem('lavr.checkout-attempt.v1', JSON.stringify({ digest, key }));
  storage.setItem('lavr.feedback-attempt.v1:order:case:actor', JSON.stringify({ digest, key }));
  assert.equal(await checkoutKey(restoredBody, storage), key);
  assert.equal(await feedbackKey(restoredBody, 'order:case:actor', storage), key);
});


test('HTTP category assignment can read a null-unit presentation while public responses remain strict', async () => {
  const unconfirmed = { ...product, unit: null, externalId: 'pdf:local-id' };
  const presentation = { productId: product.id, categoryId: product.category, websiteTitle: null, websiteDescription: null, imageIds: [], version: 3 };
  const calls = [];
  const session = { user: { id: 'admin', name: 'Админ', email: null, phone: null, emailVerified: false, phoneVerified: false, profileComplete: false }, capabilities: { operate: true, editSite: true, manageUsers: true }, csrfToken: 'a'.repeat(32), expiresAt: '2026-11-03T00:00:00Z' };
  const responses = [json(session), json({ product: unconfirmed, presentation }), json({ ...presentation, categoryId: 'visible', version: 4 })];
  const api = createHttpApi({ fetchImpl: async (url, init) => { calls.push({ url, ...init }); return responses.shift(); } });
  await api.getSession();
  assert.equal((await api.assignProductCategory(product.id, 'visible')).categoryId, 'visible');
  assert.equal(calls.length, 3);
  assert.equal(calls[2].headers['If-Match'], '"3"');
  assert.deepEqual(JSON.parse(calls[2].body), { categoryId: 'visible' });
  const publicApi = createHttpApi({ fetchImpl: async () => json({ ...page(unconfirmed), catalogUpdatedAt: '2026-11-02T00:00:00Z', stale: false }) });
  await assert.rejects(publicApi.getCatalog(), { code: 'INVALID_RESPONSE' });
});

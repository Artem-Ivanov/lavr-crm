import test from 'node:test';
import assert from 'node:assert/strict';
import { createDemoApi } from '../src/data/demo-api.js';
import { createDemoFeedbackApi } from '../src/data/demo-feedback-api.js';
import { isFeedbackCase } from '../src/feedback.js';

const key = '9a55cbe9-3743-4f32-9c83-4c01955ade11';
const otherKey = 'bc67f0d1-0615-48bc-9255-c534baefc974';
const body = { type: 'request', name: 'Иван', email: 'ivan@example.org', message: 'Нужен фильтр', messenger: { service: '', address: '' }, phone: '' };
const session = { user: { id: 'manager-1', name: 'Ольга', role: 'manager' } };
function storage() {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
async function claimed(api) {
  const receipt = await api.submitFeedback(body, key);
  return api.updateFeedbackStatus(await api.getFeedbackDetail(receipt.id), { status: 'in_progress' });
}

test('demo feedback keeps contacts only in memory and public replay stays an accepted immutable receipt', async () => {
  const local = storage(); const session = storage();
  const api = createDemoApi({ localStorage: local, sessionStorage: session });
  const receipt = await api.submitFeedback(body, key);
  assert.equal(receipt.state, 'accepted');
  assert.equal(receipt.demo, true);
  assert.equal(Object.isFrozen(receipt), true);
  const detail = await api.getFeedbackDetail(receipt.id);
  assert.equal(isFeedbackCase(detail), true);
  assert.equal(detail.demo, true);
  assert.equal(detail.notification.state, 'not_configured');
  detail.email = 'mutated@example.org';
  assert.equal((await api.getFeedbackDetail(receipt.id)).email, 'ivan@example.org');
  await api.updateFeedbackStatus(await api.getFeedbackDetail(receipt.id), { status: 'closed', resolution: 'Вопрос решён' });
  assert.deepEqual(await api.submitFeedback(body, key), receipt);
  assert.equal(local.values.size, 0);
  assert.equal(session.values.size, 0);
  await assert.rejects(api.submitFeedback({ ...body, message: 'changed' }, key), /ключ/i);
  assert.equal((await createDemoApi({ localStorage: local, sessionStorage: session }).getFeedback()).total, 0);
});

test('demo enforces owner/version/terminal states and searchable paged cases with note history', async () => {
  const api = createDemoFeedbackApi({ getSession: async () => session, createOrder: async () => { throw Error('unused'); } });
  const current = await claimed(api);
  assert.equal(current.assignedTo.id, 'manager-1');
  assert.equal(current.version, 2);
  const listed = await api.getFeedback({ query: 'IVAN@', status: 'in_progress' }, { offset: 0, limit: 1 });
  assert.equal(listed.total, 1);
  assert.equal(listed.items.length, 1);
  await assert.rejects(api.updateFeedbackStatus({ ...current, version: 1 }, { status: 'closed', resolution: 'Решено' }), error => error.status === 412);
  session.user = { id: 'manager-2', name: 'Игорь', role: 'manager' };
  await assert.rejects(api.addFeedbackNote(current, { text: 'Чужая заметка' }), error => error.status === 403);
  session.user = { id: 'manager-1', name: 'Ольга', role: 'manager' };
  const noted = await api.addFeedbackNote(current, { text: ' Написали клиенту ', contactMethod: 'email' });
  assert.equal(noted.history.at(-1).text, 'Написали клиенту');
  assert.equal(noted.history.at(-1).contactMethod, 'email');
  assert.equal(noted.version, 3);
  const closed = await api.updateFeedbackStatus(noted, { status: 'closed', resolution: 'Вопрос решён' });
  await assert.rejects(api.addFeedbackNote(closed, { text: 'Поздно' }), /заверш|закры|недоступ/i);
  assert.equal((await api.getFeedback({ status: 'closed' })).total, 1);
});

test('demo conversion creates one normal linked order and replays its original manager response', async () => {
  const local = storage(); const saved = storage();
  const api = createDemoApi({ localStorage: local, sessionStorage: saved });
  const current = await claimed(api);
  const product = (await api.getCatalog()).items[0];
  const orderBody = { customer: { name: current.name, phone: '8 (999) 123-45-67', email: current.email }, fulfillment: { method: 'pickup', address: '' }, items: [{ productId: product.id, quantity: 1, expectedUnitPriceKopecks: product.priceKopecks }] };
  await assert.rejects(api.createFeedbackOrder(current, { ...orderBody, items: [{ ...orderBody.items[0], expectedUnitPriceKopecks: 1 }] }, otherKey), /цена/i);
  assert.equal((await api.getFeedbackDetail(current.id)).status, 'in_progress');
  const converted = await api.createFeedbackOrder(current, orderBody, otherKey);
  assert.equal(converted.status, 'converted');
  assert.equal(isFeedbackCase(converted), true);
  assert.equal((await api.getOrders()).total, 1);
  const linked = await api.getOrder(converted.order.id);
  assert.equal(linked.customer.phone, '+79991234567');
  assert.equal(linked.items[0].productId, product.id);
  assert.deepEqual(await api.createFeedbackOrder(current, orderBody, otherKey), converted);
  await assert.rejects(api.createFeedbackOrder(current, { ...orderBody, customer: { ...orderBody.customer, name: 'Другой' } }, otherKey), /ключ/i);
  await assert.rejects(api.createFeedbackOrder(converted, orderBody, crypto.randomUUID()), /заверш|недоступ|создан/i);
  assert.equal((await api.getOrders()).total, 1);
});

test('demo reserves conversion while the order is being created and preserves the case on failure', async () => {
  let complete;
  const api = createDemoFeedbackApi({ getSession: async () => session, createOrder: () => new Promise((resolve, reject) => { complete = { resolve, reject }; }) });
  const current = await claimed(api);
  const pending = api.createFeedbackOrder(current, { customer: { name: 'Иван', phone: '+79991234567' } }, otherKey);
  await new Promise(resolve => setTimeout(resolve, 0));
  await assert.rejects(api.updateFeedbackStatus(current, { status: 'closed', resolution: 'Закрыть' }), /созда|обраб|измен/i);
  complete.reject(Error('ERP недоступна'));
  await assert.rejects(pending, /ERP/);
  assert.equal((await api.getFeedbackDetail(current.id)).status, 'in_progress');
  assert.equal((await api.getFeedbackDetail(current.id)).version, current.version);
});

test('demo conversion replay retains ownership checks and accepts reordered order fields', async () => {
  let user = { id: 'manager-1', name: 'Ольга', role: 'manager' };
  const api = createDemoFeedbackApi({ getSession: async () => ({ user }), createOrder: async () => ({ id: 'order-1', number: 'L-1', createdAt: '2026-10-20T10:00:00Z', totalKopecks: 100, status: 'new', integrationStatus: 'not_connected' }) });
  const current = await claimed(api);
  const orderBody = { customer: { name: 'Иван', phone: '+79991234567' }, items: [{ productId: 'oil', quantity: 1, expectedUnitPriceKopecks: 100 }], fulfillment: { method: 'pickup', address: '' } };
  const converted = await api.createFeedbackOrder(current, orderBody, otherKey);
  const reordered = { fulfillment: { address: '', method: 'pickup' }, items: [{ quantity: 1, expectedUnitPriceKopecks: 100, productId: 'oil' }], customer: { phone: '+79991234567', name: 'Иван' } };
  assert.deepEqual(await api.createFeedbackOrder(current, reordered, otherKey), converted);
  user = { id: 'manager-2', name: 'Игорь', role: 'manager' };
  await assert.rejects(api.createFeedbackOrder(current, orderBody, otherKey), error => error.status === 403);
});

test('demo conversion keys cannot replay an unrelated public checkout order', async () => {
  const api = createDemoApi({ localStorage: storage(), sessionStorage: storage() });
  const current = await claimed(api);
  const product = (await api.getCatalog()).items[0];
  const orderBody = { customer: { name: 'Иван', phone: '+79991234567', email: 'ivan@example.org' }, fulfillment: { method: 'pickup', address: '' }, items: [{ productId: product.id, quantity: 1, expectedUnitPriceKopecks: product.priceKopecks }] };
  const checkout = await api.createOrder(orderBody, otherKey);
  const converted = await api.createFeedbackOrder(current, orderBody, otherKey);
  assert.notEqual(converted.order.id, checkout.id);
  assert.equal((await api.getOrders()).total, 2);
});

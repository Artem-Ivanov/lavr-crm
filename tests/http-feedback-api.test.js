import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpApi } from '../src/data/http-api.js';

const key = '9a55cbe9-3743-4f32-9c83-4c01955ade11';
const body = { type: 'contact', name: 'Иван', phone: '', email: 'ivan@example.org', messenger: { service: '', address: '' }, message: '' };
const receipt = { id: key, number: 'F-1', createdAt: '2026-10-20T10:00:00Z', state: 'accepted' };
const session = { user: { id: 'manager', name: 'Ольга', role: 'manager' }, csrfToken: 'a'.repeat(32), expiresAt: '2026-10-21T10:00:00Z' };
const feedback = { ...body, id: key, number: receipt.number, createdAt: receipt.createdAt, updatedAt: receipt.createdAt, status: 'new', assignedTo: null, version: 1, resolution: '', order: null, history: [{ id: 'event-1', createdAt: receipt.createdAt, staff: null, action: 'feedback.created', text: '', contactMethod: '' }], notification: { state: 'queued', code: null } };
const claimed = { ...feedback, status: 'in_progress', assignedTo: { id: 'manager', name: 'Ольга' }, version: 2 };
const orderBody = { customer: { name: 'Иван', phone: '+79991234567', email: 'ivan@example.org' }, fulfillment: { method: 'pickup', address: '' }, items: [{ productId: 'oil', quantity: 1, expectedUnitPriceKopecks: 389000 }] };
const order = { id: 'order-1', number: 'L-1', createdAt: receipt.createdAt, totalKopecks: 389000, status: 'new', integrationStatus: 'pending_sync' };
const json = (value, status = 200, headers = {}) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json', ...headers } });
function setup(responses) {
  const calls = [];
  return { calls, api: createHttpApi({ fetchImpl: async (url, options) => { calls.push({ url, ...options }); assert.ok(responses.length, `Unexpected ${url}`); return responses.shift(); } }) };
}

test('public feedback normalizes input and sends only its own idempotency key without requiring a session', async () => {
  const { api, calls } = setup([json(receipt, 201)]);
  assert.deepEqual(await api.submitFeedback({ ...body, name: ' Иван ' }, key), receipt);
  assert.equal(calls[0].url, '/api/feedback');
  assert.equal(calls[0].method, 'POST');
  assert.deepEqual(JSON.parse(calls[0].body), body);
  assert.equal(calls[0].headers['Idempotency-Key'], key);
  assert.equal(calls[0].headers['X-CSRF-Token'], undefined);
  assert.equal(calls[0].credentials, 'same-origin');
  await assert.rejects(api.submitFeedback(body, 'bad'), /ключ/i);
  assert.equal(calls.length, 1);
});

test('feedback list and detail validate complete cases and ETags', async () => {
  const page = { items: [feedback], total: 25, offset: 24, limit: 24 };
  const { api, calls } = setup([json(page), json(feedback, 200, { ETag: '"1"' })]);
  assert.deepEqual(await api.getFeedback({ query: 'F & Иван', status: 'new' }, { offset: 24, limit: 24 }), page);
  assert.deepEqual(await api.getFeedbackDetail(key), feedback);
  assert.equal(calls[0].url, '/api/manager/feedback?q=F+%26+%D0%98%D0%B2%D0%B0%D0%BD&status=new&offset=24&limit=24');
  assert.equal(calls[1].url, `/api/manager/feedback/${key}`);
});

test('manager list and detail accept a server case at the Unicode message limit', async () => {
  const serverCase = { ...feedback, type: 'request', message: '😀'.repeat(2000) };
  const page = { items: [serverCase], total: 1, offset: 0, limit: 24 };
  const { api } = setup([json(page), json(serverCase)]);
  assert.deepEqual(await api.getFeedback(), page);
  assert.deepEqual(await api.getFeedbackDetail(key), serverCase);
});

test('feedback mutations require CSRF, If-Match and a separate conversion key', async () => {
  const noted = { ...claimed, version: 3 };
  const converted = { ...noted, status: 'converted', version: 4, order };
  const { api, calls } = setup([json(session), json(claimed), json(noted), json(converted)]);
  await assert.rejects(api.updateFeedbackStatus(feedback, { status: 'in_progress' }), error => error.status === 401);
  assert.equal(calls.length, 0);
  await api.getSession();
  assert.deepEqual(await api.updateFeedbackStatus(feedback, { status: 'in_progress' }), claimed);
  assert.deepEqual(await api.addFeedbackNote(claimed, { text: ' Позвонили ', contactMethod: 'phone' }), noted);
  assert.deepEqual(await api.createFeedbackOrder(noted, orderBody, key), converted);
  assert.deepEqual(calls.slice(1).map(call => [call.method, call.url]), [
    ['PATCH', `/api/manager/feedback/${key}/status`], ['POST', `/api/manager/feedback/${key}/notes`], ['POST', `/api/manager/feedback/${key}/order`],
  ]);
  assert.deepEqual(calls.slice(1).map(call => call.headers['If-Match']), ['"1"', '"2"', '"3"']);
  assert.ok(calls.slice(1).every(call => call.headers['X-CSRF-Token'] === session.csrfToken));
  assert.deepEqual(JSON.parse(calls[1].body), { status: 'in_progress', resolution: '' });
  assert.deepEqual(JSON.parse(calls[2].body), { text: 'Позвонили', contactMethod: 'phone' });
  assert.deepEqual(JSON.parse(calls[3].body), orderBody);
  assert.equal(calls[3].headers['Idempotency-Key'], key);
});

test('feedback API blocks malformed payloads, nested responses, private public receipts and conflicting versions', async () => {
  for (const invoke of [
    api => api.submitFeedback({ ...body, phone: 'bad' }, key),
    api => api.getFeedback({ status: 'bogus' }),
    api => api.getFeedback({ query: 'a'.repeat(121) }),
    api => api.updateFeedbackStatus(feedback, { status: 'converted' }),
    api => api.updateFeedbackStatus(feedback, { status: 'closed', resolution: ' ' }),
    api => api.addFeedbackNote(claimed, { text: ' ' }),
    api => api.createFeedbackOrder(claimed, { ...orderBody, customer: { name: 'Иван', phone: '', email: 'ivan@example.org' } }, key),
  ]) {
    const { api, calls } = setup([]);
    await assert.rejects(invoke(api));
    assert.equal(calls.length, 0);
  }
  for (const response of [json({ ...receipt, email: 'private@example.org' }), json({ ...receipt, state: 'converted' })]) {
    await assert.rejects(setup([response]).api.submitFeedback(body, key), error => error.code === 'INVALID_RESPONSE');
  }
  for (const response of [json({ ...feedback, history: [{ ...feedback.history[0], staff: { id: 'staff', name: 12 } }] }), json(feedback, 200, { ETag: '"2"' })]) {
    await assert.rejects(setup([response]).api.getFeedbackDetail(key), error => error.code === 'INVALID_RESPONSE');
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpApi } from '../src/data/http-api.js';
import { isUserSession, isOwnOrder, isOwnFeedback, isAdminUser } from '../src/accounts.js';

const session = { user: { id: 'customer-1', name: '', email: 'buyer@example.org', phone: '+79991234567', emailVerified: true, phoneVerified: false, profileComplete: true }, capabilities: { operate: false, editSite: false, manageUsers: false }, csrfToken: 'a'.repeat(32), expiresAt: '2027-01-01T00:00:00Z' };
const key = '9a55cbe9-3743-4f32-9c83-4c01955ade11';
const flow = { flowId: 'opaque-flow', resendAfterSeconds: 60 };
const resetBody = { flowId: '9a55cbe9-3743-4f32-9c83-4c01955ade11', token: 'a'.repeat(43), newPassword: 'another password', confirmPassword: 'another password' };
const order = { id: 'order-1', number: 'L-1', createdAt: '2026-10-28T10:00:00Z', status: 'new', totalKopecks: 100, fulfillment: { method: 'pickup', address: '' }, items: [{ productId: 'part', sku: 'P-1', brand: 'A', name: 'Деталь', quantity: 1, unitPriceKopecks: 100 }] };
const feedback = { id: 'feedback-1', number: 1, createdAt: order.createdAt, type: 'request', message: 'Вопрос', status: 'new', order: null };
const json = (value, status = 200, headers = {}) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json', ...headers } });
const page = value => ({ items: [value], total: 1, offset: 0, limit: 24 });
function setup(responses) {
  const calls = [];
  const api = createHttpApi({ fetchImpl: async (url, options) => { calls.push({ url, ...options }); assert.ok(responses.length, `Unexpected ${url}`); return responses.shift(); } });
  return { api, calls };
}

test('both email login adapters normalize credentials and share session endpoint and CSRF', async () => {
  const { api, calls } = setup([json(session), json(session), json(session), json(feedback, 201)]);
  assert.deepEqual(await api.getUserSession(), session);
  assert.deepEqual(await api.login({ login: ' Buyer@Example.org ', password: ' password ' }), session);
  assert.deepEqual(await api.loginUser({ email: ' Buyer@Example.org ', password: ' password ' }), session);
  await api.createOwnFeedback({ type: 'request', message: 'Вопрос' }, key);
  assert.deepEqual(calls.slice(0, 3).map(call => call.url), Array(3).fill('/api/customer/session'));
  assert.deepEqual(JSON.parse(calls[1].body), JSON.parse(calls[2].body));
  assert.deepEqual(JSON.parse(calls[1].body), { email: 'buyer@example.org', password: ' password ' });
  assert.equal(calls[3].headers['X-CSRF-Token'], session.csrfToken);
});

test('both login adapters reject usernames and missing passwords before making a request', async () => {
  const { api, calls } = setup([]);
  for (const method of ['login', 'loginUser']) {
    for (const body of [{ email: 'admin', password: 'password' }, { email: 'admin@example.test', password: '' }]) await assert.rejects(api[method](body));
  }
  await assert.rejects(api.login({ login: 'admin', password: 'password' }));
  assert.equal(calls.length, 0);
});

test('canonical sessions reject role leaks, missing capabilities and inconsistent verification', async () => {
  for (const invalid of [{ ...session, user: { ...session.user, role: 'admin' } }, { ...session, capabilities: undefined }, { ...session, user: { ...session.user, phoneVerified: true } }, { ...session, user: { ...session.user, emailVerified: false } }]) {
    const { api } = setup([json(invalid)]);
    await assert.rejects(api.getUserSession(), error => error.code === 'INVALID_RESPONSE');
  }
});

test('signup activation resend recovery and profile completion follow exact routes', async () => {
  const { api, calls } = setup([json(flow, 202), json(flow, 202), json(session), json(flow, 202), new Response(null, { status: 204 }), json(session), json(flow, 202), json(session)]);
  await api.signup({ email: ' Buyer@Example.org ', phone: '8 (999) 123-45-67', password: ' password1234 ' });
  await api.resendCode({ flowId: flow.flowId });
  await api.activate({ flowId: flow.flowId, code: '123456' });
  await api.requestPasswordReset({ email: 'buyer@example.org' });
  await api.confirmPasswordReset(resetBody);
  await api.loginUser({ email: 'buyer@example.org', password: ' password1234 ' });
  await api.startProfileActivation({ email: 'buyer@example.org', phone: '+79991234567' });
  await api.confirmProfileActivation({ flowId: flow.flowId, code: '123456' });
  assert.deepEqual(calls.map(call => call.url), ['/api/customer/auth/signup', '/api/customer/auth/resend', '/api/customer/auth/activate', '/api/customer/auth/password-reset', '/api/customer/auth/password-reset/confirm', '/api/customer/session', '/api/customer/profile/activation', '/api/customer/profile/activation/confirm']);
  assert.deepEqual(JSON.parse(calls[0].body), { email: 'buyer@example.org', phone: '+79991234567', password: ' password1234 ' });
  assert.deepEqual(JSON.parse(calls[4].body), resetBody);
  assert.equal(calls[4].headers['X-CSRF-Token'], undefined);
  assert.equal(calls[6].headers['X-CSRF-Token'], session.csrfToken);
  assert.equal(calls[7].headers['X-CSRF-Token'], session.csrfToken);
});

test('reset links reject invalid tokens, mismatched passwords and legacy codes before requests', async () => {
  const { api, calls } = setup([]);
  for (const patch of [{ flowId: 'opaque-flow' }, { token: 'a'.repeat(42) }, { token: 'a'.repeat(44) }, { token: '!'.repeat(43) }, { token: null }, { code: '123456' }, { newPassword: 'too short', confirmPassword: 'too short' }, { newPassword: 'a'.repeat(257), confirmPassword: 'a'.repeat(257) }, { newPassword: 'password\0secret', confirmPassword: 'password\0secret' }, { confirmPassword: 'different password' }, { confirmPassword: 123 }]) {
    await assert.rejects(api.confirmPasswordReset({ ...resetBody, ...patch }));
  }
  await assert.rejects(api.confirmPasswordReset({ flowId: resetBody.flowId, code: '123456', newPassword: resetBody.newPassword }));
  assert.equal(calls.length, 0);
});

test('reset links preserve matching password bytes and clear the old session only after success', async () => {
  const { api, calls } = setup([json(session), new Response(null, { status: 204 })]);
  await api.getUserSession();
  const body = { ...resetBody, newPassword: '  password1234  ', confirmPassword: '  password1234  ' };
  await api.confirmPasswordReset(body);
  assert.deepEqual(JSON.parse(calls[1].body), body);
  await assert.rejects(api.createOwnFeedback({ type: 'request', message: 'Вопрос' }, key), error => error.code === 'SESSION_REQUIRED');
  assert.equal(calls.length, 2);
});

test('reset confirmation requires its final 204 response before clearing the session', async () => {
  for (const response of [json(flow, 202), json({ accepted: true })]) {
    const { api, calls } = setup([json(session), response, json(feedback, 201)]);
    await api.getUserSession();
    await assert.rejects(api.confirmPasswordReset(resetBody), error => error.code === 'INVALID_RESPONSE');
    await api.createOwnFeedback({ type: 'request', message: 'Вопрос' }, key);
    assert.equal(calls.at(-1).headers['X-CSRF-Token'], session.csrfToken);
  }
});

for (const status of [400, 401]) test(`a failed reset link (${status}) keeps the signed-in session usable`, async () => {
  const { api, calls } = setup([json(session), json({ code: 'INVALID_RESET_LINK', message: 'Ссылка истекла' }, status), json(feedback, 201)]);
  await api.getUserSession();
  await assert.rejects(api.confirmPasswordReset(resetBody), error => error.code === 'INVALID_RESET_LINK');
  await api.createOwnFeedback({ type: 'request', message: 'Вопрос' }, key);
  assert.equal(calls[2].headers['X-CSRF-Token'], session.csrfToken);
});

test('a reset response from an older identity cannot clear a newer login', async () => {
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  const calls = [];
  const api = createHttpApi({ fetchImpl: async (url, options) => { calls.push({ url, ...options }); return url.endsWith('/confirm') ? pending : url.endsWith('/feedback') ? json(feedback, 201) : json(session); } });
  await api.getUserSession();
  const reset = api.confirmPasswordReset(resetBody);
  await api.loginUser({ email: 'next@example.org', password: 'long password' });
  release(new Response(null, { status: 204 }));
  await assert.rejects(reset, error => error.code === 'SESSION_CHANGED');
  await api.createOwnFeedback({ type: 'request', message: 'Вопрос' }, key);
  assert.equal(calls.at(-1).headers['X-CSRF-Token'], session.csrfToken);
});

test('own endpoints use profile fallbacks and never accept owner or role input', async () => {
  const { api, calls } = setup([json(session), json(page(order)), json(order), json(order, 201), json(page(feedback)), json(feedback), json(feedback, 201)]);
  await api.getUserSession();
  await api.getOwnOrders(); await api.getOwnOrder(order.id);
  const body = { fulfillment: order.fulfillment, items: [{ productId: 'part', quantity: 1, expectedUnitPriceKopecks: 100 }] };
  await api.createOwnOrder(body, key);
  await api.getOwnFeedback(); await api.getOwnFeedbackCase(feedback.id); await api.createOwnFeedback({ type: 'request', message: 'Вопрос' }, key);
  assert.deepEqual(JSON.parse(calls[3].body), body);
  assert.equal(calls[3].url, '/api/customer/orders');
  assert.equal(calls[6].url, '/api/customer/feedback');
  assert.equal(calls[3].headers['Idempotency-Key'], key);
  await assert.rejects(api.createOwnOrder({ ...body, customer: { email: 'other@example.org' } }, key));
  await assert.rejects(api.createOwnFeedback({ type: 'request', message: 'Вопрос', role: 'admin' }, key));
  assert.equal(calls.length, 7);
});

test('own checkout sends explicit contacts for an unverified incomplete account with session and CSRF intact', async () => {
  const incomplete = { ...session, user: { ...session.user, name: 'Имя профиля', phone: null, email: null, emailVerified: false, profileComplete: false } };
  const { api, calls } = setup([json(incomplete), json(order, 201)]);
  const before = await api.getUserSession();
  const body = { fulfillment: order.fulfillment, items: [{ productId: 'part', quantity: 1, expectedUnitPriceKopecks: 100 }], phone: '8 (999) 123-45-67', email: '' };
  assert.deepEqual(await api.createOwnOrder(body, key), order);
  assert.equal(calls[1].url, '/api/customer/orders');
  assert.deepEqual(JSON.parse(calls[1].body), { ...body, phone: '+79991234567' });
  assert.equal(calls[1].headers['X-CSRF-Token'], incomplete.csrfToken);
  assert.equal(calls[1].headers['Idempotency-Key'], key);
  assert.deepEqual(before, incomplete);
  for (const patch of [{ phone: '' }, { phone: null }, { email: null }, { email: 'bad' }, { customerId: 'other' }]) await assert.rejects(api.createOwnOrder({ ...body, ...patch }, key));
  assert.equal(calls.length, 2);
});

test('customer DTOs reject leaked operational fields', async () => {
  for (const value of [{ ...order, externalDocumentId: 'private' }, { ...order, customer: {} }, { ...order, items: [{ ...order.items[0], quantity: -1 }] }]) {
    await assert.rejects(setup([json(value)]).api.getOwnOrder(order.id), error => error.code === 'INVALID_RESPONSE');
  }
  for (const value of [{ ...feedback, resolution: 'private' }, { ...feedback, history: [] }, { ...feedback, order: { id: 'other' } }]) {
    await assert.rejects(setup([json(value)]).api.getOwnFeedbackCase(feedback.id), error => error.code === 'INVALID_RESPONSE');
  }
});

test('late private data after another identity transition is discarded', async () => {
  let resolveOld;
  let calls = 0;
  const api = createHttpApi({ fetchImpl: async url => { calls++; if (url.includes('/orders')) return new Promise(resolve => { resolveOld = resolve; }); return json(session); } });
  await api.getUserSession();
  const old = api.getOwnOrders();
  await api.loginUser({ email: 'second@example.org', password: 'long password' });
  resolveOld(json(page(order)));
  await assert.rejects(old, error => error.code === 'SESSION_CHANGED');
  assert.equal(calls, 3);
});

test('public account inputs reject unknown fields and weak passwords without network calls', async () => {
  const { api, calls } = setup([]);
  for (const body of [{ email: 'a@b.co', phone: '+79991234567', password: 'long password', role: 'admin' }, { email: 'a@b.co', phone: '+79991234567', password: 'short' }]) await assert.rejects(api.signup(body));
  await assert.rejects(api.activate({ flowId: 'flow', code: '12345' }));
  assert.equal(calls.length, 0);
});

const adminUser = { id: 'customer-1', name: '', login: 'buyer@example.org', email: 'buyer@example.org', phone: '+79991234567', emailVerified: true, phoneVerified: false, isActive: true, role: 'partner', version: 1 };
test('admin creation normalizes contacts, preserves password and checks the returned version', async () => {
  const created = { ...adminUser, name: 'Новый администратор', role: 'admin' };
  const { api, calls } = setup([json(session), json(created, 201, { ETag: '"1"' })]);
  await api.getUserSession();
  assert.deepEqual(await api.createUser({ name: ' Новый администратор ', email: ' Buyer@Example.org ', phone: '8 (999) 123-45-67', password: ' password1234 ', role: 'admin' }), created);
  assert.equal(calls[1].url, '/api/admin/users');
  assert.equal(calls[1].method, 'POST');
  assert.equal(calls[1].headers['X-CSRF-Token'], session.csrfToken);
  assert.equal(calls[1].headers['If-Match'], undefined);
  assert.deepEqual(JSON.parse(calls[1].body), { name: 'Новый администратор', email: 'buyer@example.org', phone: '+79991234567', password: ' password1234 ', role: 'admin' });
});

test('admin creation rejects invalid input without a request and requires a session', async () => {
  const body = { email: 'new@example.test', password: 'new password123', role: 'partner' };
  const { api, calls } = setup([json(session)]); await api.getUserSession();
  for (const change of [{ role: 'manager' }, { email: 'bad' }, { phone: '123' }, { password: 'short' }, { password: 'long\0password' }, { name: '\nInvalid' }, { isActive: true }, { version: 1 }])
    await assert.rejects(api.createUser({ ...body, ...change }));
  assert.equal(calls.length, 1);
  await assert.rejects(setup([]).api.createUser(body), error => error.code === 'SESSION_REQUIRED');
});

test('admin creation accepts omitted optional fields but rejects leaked secrets and mismatched ETag', async () => {
  const created = { ...adminUser, name: '', phone: null };
  const { api, calls } = setup([json(session), json(created, 201, { ETag: '"1"' })]);
  await api.getUserSession(); await api.createUser({ email: created.email, password: 'new password123' });
  assert.deepEqual(JSON.parse(calls[1].body), { name: '', email: created.email, phone: '', password: 'new password123', role: 'partner' });
  for (const response of [json({ ...created, password: 'new password123' }, 201), json(created, 201, { ETag: '"2"' })]) {
    const { api } = setup([json(session), response]); await api.getUserSession();
    await assert.rejects(api.createUser({ email: created.email, password: 'new password123' }), error => error.code === 'INVALID_RESPONSE');
  }
});

test('only admin user endpoints expose roles and use versioned exact mutations', async () => {
  const changed = { ...adminUser, role: 'operator', version: 2 };
  const blocked = { ...changed, isActive: false, version: 3 };
  const { api, calls } = setup([json(session), json(page(adminUser)), json(adminUser, 200, { ETag: '"1"' }), json(changed, 200, { ETag: '"2"' }), json(blocked, 200, { ETag: '"3"' })]);
  await api.getUserSession();
  assert.deepEqual(await api.listUsers({ query: 'buyer@example.org' }), page(adminUser));
  await api.getUser(adminUser.id); await api.updateUserRole(adminUser.id, 'operator', 1); await api.updateUserStatus(adminUser.id, false, 2);
  assert.equal(calls[1].url, '/api/admin/users?q=buyer%40example.org&offset=0&limit=24');
  assert.equal(calls[3].headers['If-Match'], '"1"');
  assert.deepEqual(JSON.parse(calls[3].body), { role: 'operator' });
  assert.deepEqual(JSON.parse(calls[4].body), { isActive: false });
  await assert.rejects(api.updateUserRole(adminUser.id, 'manager', 3));
  await assert.rejects(setup([json({ ...adminUser, passwordHash: 'secret' })]).api.getUser(adminUser.id), error => error.code === 'INVALID_RESPONSE');
});

test('an identity change while parsing JSON cannot release a stale private response', async () => {
  let finishJson; let parsing;
  const started = new Promise(resolve => { parsing = resolve; });
  const api = createHttpApi({ fetchImpl: async url => url.includes('/orders') ? { ok: true, status: 200, headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => { parsing(); return new Promise(resolve => { finishJson = resolve; }); } } : json(session) });
  await api.getUserSession(); const old = api.getOwnOrders(); await started;
  await api.loginUser({ email: 'second@example.org', password: 'long password' }); finishJson(page(order));
  await assert.rejects(old, error => error.code === 'SESSION_CHANGED');
});

test('manager customer projections accept orders from accounts with no display name', async () => {
  const operational = { ...order, customer: { name: '', phone: '+79991234567', email: 'buyer@example.org' }, integrationStatus: 'pending_sync', externalDocumentId: null, version: 1, canCancel: false };
  assert.deepEqual(await setup([json(operational)]).api.getOrder(order.id), operational);
});

test('account DTO IDs accept 128 Unicode code points and retain invalid-ID boundaries', () => {
  const opaqueId = '🚗'.repeat(128);
  const adminUser = { id: opaqueId, name: '', login: null, email: null, phone: null, emailVerified: false, phoneVerified: false, isActive: true, role: 'partner', version: 1 };
  assert.equal(isUserSession({ ...session, user: { ...session.user, id: opaqueId } }), true);
  assert.equal(isOwnOrder({ ...order, id: opaqueId, items: [{ ...order.items[0], productId: opaqueId }] }), true);
  assert.equal(isOwnFeedback({ ...feedback, id: opaqueId, order: { id: opaqueId, number: order.number, createdAt: order.createdAt, status: order.status, totalKopecks: order.totalKopecks } }), true);
  assert.equal(isAdminUser(adminUser), true);
  for (const id of ['', '.', '..', 'bad\nID', 'bad\u007fID', '🚗'.repeat(129)]) {
    assert.equal(isUserSession({ ...session, user: { ...session.user, id } }), false);
    assert.equal(isOwnOrder({ ...order, id }), false);
    assert.equal(isOwnOrder({ ...order, items: [{ ...order.items[0], productId: id }] }), false);
    assert.equal(isOwnFeedback({ ...feedback, id }), false);
    assert.equal(isAdminUser({ ...adminUser, id }), false);
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { createDemoApi } from '../src/data/demo-api.js';
import { createDemoFeedbackApi } from '../src/data/demo-feedback-api.js';
const storage = () => { const data = new Map(); return { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key) }; };
const create = () => createDemoApi({ localStorage: storage(), sessionStorage: storage() });
test('demo customer identities are explicitly simulated and own lists stay separate', async () => {
 const api = create();
 await assert.rejects(api.getUserSession(), error => error.status === 401);
 const first = await api.loginUser({ email: 'partner@example.test', password: 'demonstration' });
 assert.equal(first.demo,true); assert.equal(first.capabilities.operate,false); assert.equal(first.user.role,undefined);
 const feedback = await api.createOwnFeedback({ type:'request',message:'Демо' },crypto.randomUUID());
 const product = (await api.getCatalog()).items[0];
 const body = { fulfillment:{ method:'pickup',address:'' },items:[{ productId:product.id,quantity:1,expectedUnitPriceKopecks:product.priceKopecks }] };
 const order = await api.createOwnOrder(body,crypto.randomUUID());
 assert.equal((await api.getOwnOrders()).items[0].id,order.id);
 assert.equal(order.customer,undefined);
 await api.logout();
 await api.loginUser({ email:'operator@example.test',password:'demonstration' });
 assert.equal((await api.getOwnFeedback()).total,0); assert.equal((await api.getOwnOrders()).total,0);
 await assert.rejects(api.getOwnFeedbackCase(feedback.id),error=>error.status===404);
});
test('demo never claims that registration or recovery sends a real email', async () => {
 const api = create();
 await assert.rejects(api.signup({ email:'a@example.test',phone:'+79991234567',password:'long password' }),/демонстрац|симуляц/i);
 await assert.rejects(api.requestPasswordReset({ email:'a@example.test' }),/демонстрац|симуляц/i);
});
test('demo admin creates every role without retaining passwords or overwriting duplicate email', async () => {
 const api = create(); await api.loginUser({ email: 'admin@example.test', password: 'demonstration' });
 for (const role of ['partner', 'operator', 'admin']) {
  const user = await api.createUser({ name: ' Новый пользователь ', email: ` NEW-${role}@Example.test `, password: 'test password123', role });
  assert.equal(user.role, role); assert.equal(user.name, 'Новый пользователь'); assert.equal(user.email, `new-${role}@example.test`);
  assert.equal(user.phone, null); assert.equal(user.emailVerified, true); assert.equal(user.isActive, true); assert.equal(user.version, 1);
  assert.equal(user.password, undefined); assert.equal(user.passwordHash, undefined);
  assert.deepEqual(await api.getUser(user.id), user);
  await assert.rejects(api.createUser({ email: user.email.toUpperCase(), password: 'other password123', role: 'partner' }), error => error.status === 409);
  assert.deepEqual(await api.getUser(user.id), user);
 }
 assert.equal((await api.listUsers()).total, 6);
 await api.logout(); await api.loginUser({ email: 'new-admin@example.test', password: 'demonstration' });
 assert.equal((await api.getUserSession()).capabilities.manageUsers, true);
 assert.equal((await api.getUserSession()).user.profileComplete, false);
});
test('demo refuses user creation without administrator access and validates input', async () => {
 const api = create(), body = { email: 'new@example.test', password: 'test password123', role: 'admin' };
 await assert.rejects(api.createUser(body), error => error.status === 401);
 for (const role of ['partner', 'operator']) {
  await api.loginUser({ email: `${role}@example.test`, password: 'demonstration' });
  await assert.rejects(api.createUser(body), error => error.status === 403);
 }
 await api.loginUser({ email: 'admin@example.test', password: 'demonstration' });
 await assert.rejects(api.createUser({ ...body, role: 'manager' }));
 assert.equal((await api.listUsers()).total, 3);
});
test('demo operator conversion keeps the original customer owner', async () => {
 const api = create(); await api.loginUser({ email: 'partner@example.test', password: 'demonstration' });
 const own = await api.createOwnFeedback({ type: 'request', message: 'Демо' }, crypto.randomUUID());
 await api.loginUser({ email: 'operator@example.test', password: 'demonstration' });
 const current = await api.updateFeedbackStatus(await api.getFeedbackDetail(own.id), { status: 'in_progress' });
 const product = (await api.getCatalog()).items[0];
 const converted = await api.createFeedbackOrder(current, { customer: { name: 'Тест', phone: '+79991234567', email: 'partner@example.test' }, fulfillment: { method: 'pickup', address: '' }, items: [{ productId: product.id, quantity: 1, expectedUnitPriceKopecks: product.priceKopecks }] }, crypto.randomUUID());
 assert.equal((await api.getOwnOrders()).total, 0);
 await api.loginUser({ email: 'partner@example.test', password: 'demonstration' });
 assert.equal((await api.getOwnOrder(converted.order.id)).id, converted.order.id);
});

for (const [label, contacts] of [
 ['email only', { email: 'legacy@example.test', phone: null }],
 ['phone only', { email: null, phone: '+79991234567' }],
 ['unverified contacts', { email: 'legacy@example.test', phone: '+79991234567' }],
 ['null legacy contacts', { email: null, phone: null }],
 ['empty legacy contacts', { email: '', phone: '' }],
]) test(`demo own feedback accepts ${label} without profile completion`, async () => {
 const user = { id: 'legacy', name: '', ...contacts, emailVerified: false, phoneVerified: false, profileComplete: false };
 const api = createDemoFeedbackApi({ getCustomerSession: async () => ({ user }), getSession: async () => ({ user: { id: 'staff', name: 'Оператор' }, capabilities: { operate: true, manageUsers: true } }) });
 const key = crypto.randomUUID();
 const feedback = await api.createOwnFeedback({ type: 'request', message: ' Вопрос из аккаунта ' }, key);
 assert.equal(feedback.type, 'request'); assert.equal(feedback.message, 'Вопрос из аккаунта');
 assert.equal(feedback.email, undefined); assert.equal(feedback.phone, undefined);
 assert.deepEqual(await api.createOwnFeedback({ type: 'request', message: 'Вопрос из аккаунта' }, key), feedback);
 assert.equal((await api.getOwnFeedback()).total, 1);
 const detail = await api.getFeedbackDetail(feedback.id);
 assert.equal(detail.name, ''); assert.equal(detail.email, contacts.email ?? ''); assert.equal(detail.phone, contacts.phone ?? '');
 assert.deepEqual(detail.messenger, { service: '', address: '' });
});

test('demo own feedback keeps validation, owner isolation and retries separate from guest submissions', async () => {
 let user = null;
 const api = createDemoFeedbackApi({ getCustomerSession: async () => { if (!user) throw Object.assign(new Error('Войдите'), { status: 401 }); return { user }; } });
 const body = { type: 'contact', message: '' }; const key = crypto.randomUUID();
 await assert.rejects(api.createOwnFeedback(body, key), error => error.status === 401);
 user = { id: 'first', name: '', email: null, phone: null, emailVerified: false, phoneVerified: false, profileComplete: false };
 await assert.rejects(api.createOwnFeedback({ type: 'request', message: '' }, key));
 await assert.rejects(api.createOwnFeedback({ ...body, email: 'other@example.test' }, key));
 await assert.rejects(api.createOwnFeedback(body, 'bad-key'));
 await assert.rejects(api.submitFeedback(body, key), /способ связи/);
 const guest = await api.submitFeedback({ ...body, email: 'guest@example.test' }, key);
 const own = await api.createOwnFeedback(body, key);
 assert.notEqual(own.id, guest.id);
 assert.deepEqual(await api.createOwnFeedback(body, key), own);
 await assert.rejects(api.createOwnFeedback({ type: 'request', message: 'Другой запрос' }, key), error => error.code === 'IDEMPOTENCY_CONFLICT');
 user = { ...user, id: 'second' };
 assert.equal((await api.getOwnFeedback()).total, 0);
 await assert.rejects(api.getOwnFeedbackCase(own.id), error => error.status === 404);
 await assert.rejects(api.createOwnFeedback(body, key), error => error.code === 'IDEMPOTENCY_CONFLICT');
 user = { ...user, id: 'first' };
 assert.deepEqual(await api.getOwnFeedbackCase(own.id), own);
 assert.equal((await api.getOwnFeedback()).total, 1);
});

for (const [label, profileName] of [['normal', 'Иван Петров'], ['long', 'Я'.repeat(240)]]) test(`demo own orders use the ${label} account name unless a name is supplied`, async () => {
 const localStorage = storage(), sessionStorage = storage(); const api = createDemoApi({ localStorage, sessionStorage });
 await api.loginUser({ email: 'admin@example.test', password: 'demonstration' });
 await api.createUser({ name: profileName, email: 'buyer@example.test', phone: '+79991234567', password: 'test password123', role: 'partner' });
 await api.loginUser({ email: 'buyer@example.test', password: 'demonstration' });
 const product = (await api.getCatalog()).items[0];
 const body = { fulfillment: { method: 'pickup', address: '' }, items: [{ productId: product.id, quantity: 1, expectedUnitPriceKopecks: product.priceKopecks }] };
 for (const [extra, expected] of [[{}, profileName], [{ name: '' }, profileName], [{ name: '   ' }, profileName], [{ name: ' Другой получатель ' }, 'Другой получатель']]) {
  const input = { ...body, ...extra }; const key = crypto.randomUUID();
  const own = await api.createOwnOrder(input, key);
  assert.equal((await api.getOrder(own.id)).customer.name, expected);
  assert.equal(own.customer, undefined);
  assert.deepEqual(await api.getOwnOrder(own.id), own);
  assert.equal((await api.getOrders()).items.find(order => order.id === own.id).customer.name, expected);
  assert.deepEqual(await api.createOwnOrder(input, key), own);
  assert.equal((await api.updateOrderStatus(own.id, 'confirmed')).customer.name, expected);
  assert.equal((await api.getOrder(own.id)).customer.name, expected);
  assert.equal((await api.getOwnOrder(own.id)).status, 'confirmed');
 }
 assert.equal((await api.getOwnOrders()).total, 4);
 await assert.rejects(api.createOwnOrder({ ...body, name: 'Я'.repeat(101) }, crypto.randomUUID()));
 for (const name of ['', 'Я'.repeat(101)]) await assert.rejects(api.createOrder({ ...body, customer: { name, phone: '+79991234567', email: 'guest@example.test' } }, crypto.randomUUID()));
 const guest = await api.createOrder({ ...body, customer: { name: 'Г'.repeat(100), phone: '+79991234567', email: 'guest@example.test' } }, crypto.randomUUID());
 const restored = createDemoApi({ localStorage, sessionStorage });
 assert.equal((await restored.getOrder(guest.id)).customer.name, 'Г'.repeat(100));
 assert.equal((await restored.getOrders()).total, 1);
});

test('demo own order profile names and retries stay bound to the signed-in identity', async () => {
 const api = create(); await api.loginUser({ email: 'partner@example.test', password: 'demonstration' });
 const product = (await api.getCatalog()).items[0];
 const body = { fulfillment: { method: 'pickup', address: '' }, items: [{ productId: product.id, quantity: 1, expectedUnitPriceKopecks: product.priceKopecks }] };
 const key = crypto.randomUUID(); const first = await api.createOwnOrder(body, key);
 assert.equal((await api.getOrder(first.id)).customer.name, 'Демо partner');
 await api.loginUser({ email: 'operator@example.test', password: 'demonstration' });
 await assert.rejects(api.getOwnOrder(first.id), error => error.status === 404);
 await assert.rejects(api.createOwnOrder(body, key), error => error.status === 409);
 const second = await api.createOwnOrder(body, crypto.randomUUID());
 assert.equal((await api.getOrder(second.id)).customer.name, 'Демо operator');
 await api.loginUser({ email: 'partner@example.test', password: 'demonstration' });
 assert.deepEqual(await api.createOwnOrder(body, key), first);
 assert.equal((await api.getOrder(first.id)).customer.name, 'Демо partner');
 assert.equal((await api.getOwnOrders()).total, 1);
});

test('demo incomplete checkout uses supplied contacts without changing the profile or weakening retries', async () => {
 const api = create(); await api.loginUser({ email: 'admin@example.test', password: 'demonstration' });
 const profileName = 'Я'.repeat(240);
 await api.createUser({ name: profileName, email: 'incomplete@example.test', password: 'test password123', role: 'partner' });
 const before = await api.loginUser({ email: 'incomplete@example.test', password: 'demonstration' });
 assert.equal(before.user.phone, null); assert.equal(before.user.profileComplete, false);
 const product = (await api.getCatalog()).items[0];
 const body = { fulfillment: { method: 'pickup', address: '' }, items: [{ productId: product.id, quantity: 1, expectedUnitPriceKopecks: product.priceKopecks }] };
 const key = crypto.randomUUID();
 const own = await api.createOwnOrder({ ...body, phone: '8 (999) 123-45-67', email: '' }, key);
 assert.deepEqual((await api.getOrder(own.id)).customer, { name: profileName, phone: '+79991234567', email: '' });
 assert.deepEqual(await api.createOwnOrder({ ...body, phone: '+79991234567', email: '' }, key), own);
 await assert.rejects(api.createOwnOrder({ ...body, phone: '+79990000000', email: '' }, key), error => error.status === 409);
 for (const extra of [{}, { phone: '' }, { phone: null }, { phone: '123' }, { phone: '+79991234567', email: 'bad' }]) await assert.rejects(api.createOwnOrder({ ...body, ...extra }, crypto.randomUUID()));
 const fallback = await api.createOwnOrder({ ...body, name: ' Другой получатель ', phone: '+79991234567' }, crypto.randomUUID());
 assert.deepEqual((await api.getOrder(fallback.id)).customer, { name: 'Другой получатель', phone: '+79991234567', email: 'incomplete@example.test' });
 assert.deepEqual(await api.getUserSession(), before);
 assert.equal((await api.getOwnOrders()).total, 2);
 for (const items of [[{ ...body.items[0], quantity: product.stock + 1 }], [{ ...body.items[0], expectedUnitPriceKopecks: product.priceKopecks + 1 }], [{ ...body.items[0], expectedUnit: 'кг' }]]) {
  await assert.rejects(api.createOwnOrder({ ...body, phone: '+79991234567', items }, crypto.randomUUID()));
 }
 assert.equal((await api.getOwnOrders()).total, 2);
});

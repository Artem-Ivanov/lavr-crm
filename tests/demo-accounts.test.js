import test from 'node:test';
import assert from 'node:assert/strict';
import { createDemoApi } from '../src/data/demo-api.js';
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

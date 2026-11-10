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

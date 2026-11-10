import test from 'node:test';
import assert from 'node:assert/strict';
import { checkoutKey, clearCheckoutKey } from '../src/checkout-attempt.js';
import { feedbackKey } from '../src/feedback-attempt.js';
const storage = () => { const data = new Map(); return { getItem: key => data.get(key), setItem: (key, value) => data.set(key, value), removeItem: key => data.delete(key), data }; };
test('order retries are scoped to the identity without storing personal fields', async () => {
 const s = storage(); const body = { items: [1] };
 const a = await checkoutKey(body, s, 'user-a');
 const b = await checkoutKey(body, s, 'user-b');
 assert.notEqual(a,b); assert.equal(await checkoutKey(body,s,'user-a'),a);
 clearCheckoutKey(s,'user-a'); assert.equal(await checkoutKey(body,s,'user-b'),b);
});
test('feedback retries for two users have independent stable keys', async () => {
 const s = storage(); const body = { type: 'contact', message: '' };
 const a = await feedbackKey(body,'customer:user-a',s); const b = await feedbackKey(body,'customer:user-b',s);
 assert.notEqual(a,b); assert.equal(await feedbackKey(body,'customer:user-a',s),a);
});

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { checkoutKey, clearCheckoutKey } from '../src/checkout-attempt.js';

function storage() {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key), values };
}
test('checkout retry key survives a fresh caller without storing contacts', async () => {
  const saved = storage();
  const body = { customer: { name: 'Иван', phone: '+79991234567' }, items: [{ productId: 'part', quantity: 1 }] };
  const key = await checkoutKey(body, saved);
  assert.equal(await checkoutKey(structuredClone(body), saved), key);
  assert.ok(![...saved.values.values()].join('').includes('Иван'));
  assert.ok(![...saved.values.values()].join('').includes('+7999'));
  assert.notEqual(await checkoutKey({ ...body, items: [{ productId: 'part', quantity: 2 }] }, saved), key);
  clearCheckoutKey(saved);
  assert.equal(saved.values.size, 0);
});
test('unavailable retry storage blocks checkout instead of sending an unprotected attempt', async () => {
  await assert.rejects(checkoutKey({}, { getItem() { return null; }, setItem() { throw new Error('blocked'); } }), /ключ/);
});

test('cleanup leaves a tombstone when removal fails so a new order gets a new key', async () => {
  const saved = storage();
  const body = { items: [{ productId: 'part', quantity: 1 }] };
  const previous = await checkoutKey(body, saved);
  saved.removeItem = () => { throw new Error('remove blocked'); };
  clearCheckoutKey(saved);
  assert.notEqual(await checkoutKey(body, saved), previous);
});

test('a stored retry key is not reused when the technical storage becomes unwritable', async () => {
  const saved = storage();
  await checkoutKey({}, saved);
  saved.setItem = () => { throw new Error('write blocked'); };
  await assert.rejects(checkoutKey({}, saved), /ключ/);
});

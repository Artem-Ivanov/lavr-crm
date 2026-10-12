import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseCartDraft, setDraftQuantity } from '../src/cart-draft.js';

test('cart draft keeps unknown product IDs until the API can resolve them', () => {
  assert.deepEqual(parseCartDraft([{ productId: 'not-loaded', quantity: 2 }, { productId: 'other', quantity: 1 }, { productId: 'bad', quantity: -1 }]), [
    { productId: 'not-loaded', quantity: 2 }, { productId: 'other', quantity: 1 },
  ]);
});

test('changing one draft line preserves unresolved neighbours and validates quantity', () => {
  const draft = [{ productId: 'not-loaded', quantity: 2 }, { productId: 'known', quantity: 1 }];
  assert.deepEqual(setDraftQuantity(draft, 'known', 3, 5), [{ productId: 'not-loaded', quantity: 2 }, { productId: 'known', quantity: 3 }]);
  assert.deepEqual(setDraftQuantity(draft, 'not-loaded', 0, 0), [{ productId: 'known', quantity: 1 }]);
  assert.throws(() => setDraftQuantity(draft, 'known', 6, 5), /Доступно/);
  assert.throws(() => setDraftQuantity(draft, 'known', 1.5, 5), /целое/);
  assert.deepEqual(draft[1], { productId: 'known', quantity: 1 });
});

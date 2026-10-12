import { test } from 'node:test';
import assert from 'node:assert/strict';
import { orderActions } from '../src/order-labels.js';

test('real CRM never confirms an order before 1C and obeys server cancellation flag', () => {
  assert.deepEqual(orderActions({ status: 'new', canCancel: false }), []);
  assert.deepEqual(orderActions({ status: 'new', canCancel: true }), ['cancelled']);
  assert.deepEqual(orderActions({ status: 'confirmed' }), ['ready']);
  assert.deepEqual(orderActions({ status: 'ready' }), ['completed']);
  assert.deepEqual(orderActions({ status: 'completed' }), []);
  assert.deepEqual(orderActions({ status: 'new' }, true), ['confirmed', 'cancelled']);
});

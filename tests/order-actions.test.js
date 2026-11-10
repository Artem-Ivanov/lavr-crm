import { test } from 'node:test';
import assert from 'node:assert/strict';
import { orderActions } from '../src/order-labels.js';

test('ERP orders keep confirmation in 1C and obey server cancellation flag', () => {
  assert.deepEqual(orderActions({ status: 'new', canCancel: false }), []);
  assert.deepEqual(orderActions({ status: 'new', canCancel: true }), ['cancelled']);
  assert.deepEqual(orderActions({ status: 'confirmed' }), ['ready']);
  assert.deepEqual(orderActions({ status: 'ready' }), ['completed']);
  assert.deepEqual(orderActions({ status: 'completed' }), []);
  assert.deepEqual(orderActions({ status: 'new' }, true), ['confirmed', 'cancelled']);
});

test('manual CRM orders can progress without 1C and obey server cancellation flag', () => {
  const manual = { integrationStatus: 'not_connected', externalDocumentId: null, canCancel: true };
  assert.deepEqual(orderActions({ ...manual, status: 'new' }), ['confirmed', 'cancelled']);
  assert.deepEqual(orderActions({ ...manual, status: 'confirmed' }), ['ready', 'cancelled']);
  assert.deepEqual(orderActions({ ...manual, status: 'ready' }), ['completed', 'cancelled']);
  assert.deepEqual(orderActions({ ...manual, status: 'new', canCancel: false }), ['confirmed']);
  for (const status of ['completed', 'cancelled']) assert.deepEqual(orderActions({ ...manual, status }), []);
});

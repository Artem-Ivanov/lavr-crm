import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeFeedbackInput, normalizeFeedbackNote, normalizeFeedbackStatus, isFeedbackReceipt, isFeedbackCase } from '../src/feedback.js';

const empty = { type: 'contact', name: '', phone: '', email: '', messenger: { service: '', address: '' }, message: '' };
const receipt = { id: 'a0f2d7e0-5af2-40c7-8bc0-de61ed274ed9', number: 'F-1', createdAt: '2026-10-20T10:00:00Z', state: 'accepted' };
const feedback = { ...empty, ...receipt, updatedAt: receipt.createdAt, email: 'ivan@example.org', status: 'new', assignedTo: null, version: 1, resolution: '', order: null, history: [], notification: { state: 'queued', code: null } };
delete feedback.state;

test('feedback accepts each contact alternative and normalizes every field', () => {
  assert.deepEqual(normalizeFeedbackInput({ type: 'contact', phone: ' 8 (999) 123-45-67 ', name: ' Иван ' }), { ...empty, name: 'Иван', phone: '+79991234567' });
  assert.deepEqual(normalizeFeedbackInput({ type: 'request', email: ' ivan@example.org ', message: ' Нужен фильтр\nдля авто ' }), { ...empty, type: 'request', email: 'ivan@example.org', message: 'Нужен фильтр\nдля авто' });
  assert.deepEqual(normalizeFeedbackInput({ type: 'contact', messenger: { service: ' Telegram ', address: ' @ivan ' } }), { ...empty, messenger: { service: 'Telegram', address: '@ivan' } });
});

test('feedback rejects missing contacts and validates every supplied contact and size', () => {
  for (const body of [
    empty, { ...empty, email: 'ivan@example.org', phone: 'wrong' }, { ...empty, phone: '+79991234567', email: 'bad' },
    { ...empty, email: 'ivan@example.org?subject=oops' }, { ...empty, email: 'ivan@example.org', messenger: { service: 'Telegram', address: '' } },
    { ...empty, email: 'ivan@example.org', name: 'a'.repeat(101) }, { ...empty, email: 'ivan@example.org', message: 'a'.repeat(2001) },
    { ...empty, email: 'ivan@example.org', name: 'Иван\u0085' }, { ...empty, email: 'ivan@example.org', message: 'x\tfoo' },
    { ...empty, type: 'request', email: 'ivan@example.org', message: '  ' },
    { ...empty, email: 'ivan@example.org', messenger: { service: 'T'.repeat(41), address: '@ivan' } },
    { ...empty, email: 'ivan@example.org', admin: true }, { ...empty, email: 12 },
  ]) assert.throws(() => normalizeFeedbackInput(body), Error);
});

test('public receipt never admits manager fields and manager case validates nested trust boundaries', () => {
  assert.equal(isFeedbackReceipt(receipt), true);
  assert.equal(isFeedbackReceipt({ ...receipt, demo: true }), true);
  assert.equal(isFeedbackReceipt({ ...receipt, email: 'private@example.org' }), false);
  assert.equal(isFeedbackReceipt({ ...receipt, state: 'converted' }), false);
  assert.equal(isFeedbackCase(feedback), true);
  assert.equal(isFeedbackCase({ ...feedback, messenger: { address: '', service: '' } }), true);
  for (const patch of [
    { status: 'accepted' }, { version: 0 }, { updatedAt: 'bad' }, { phone: '79991234567' },
    { assignedTo: { id: 'staff', name: 12 } }, { notification: { state: 'error', code: 'token:secret' } },
    { history: [{ id: 'n1', createdAt: receipt.createdAt, staff: null, action: 'note', text: 'Hi', contactMethod: 'sms' }] },
    { status: 'converted', order: null }, { status: 'closed', resolution: '' },
  ]) assert.equal(isFeedbackCase({ ...feedback, ...patch }), false, JSON.stringify(patch));
});

test('feedback notes reject an explicitly invalid contact method', () => {
  assert.throws(() => normalizeFeedbackNote({ text: 'Позвонили', contactMethod: null }), /способ/i);
});

test('feedback Unicode limits count code points like the backend for input and nested manager data', () => {
  const input = { ...empty, type: 'request', name: '😀'.repeat(100), email: 'ivan@example.org', messenger: { service: '😀'.repeat(40), address: '😀'.repeat(254) }, message: '😀'.repeat(2000) };
  assert.deepEqual(normalizeFeedbackInput(input), input);
  assert.throws(() => normalizeFeedbackInput({ ...input, message: '😀'.repeat(2001) }), /2000/);
  assert.equal(normalizeFeedbackNote({ text: '😀'.repeat(2000) }).text, '😀'.repeat(2000));
  assert.throws(() => normalizeFeedbackNote({ text: '😀'.repeat(2001) }), /2000/);
  assert.equal(normalizeFeedbackStatus({ status: 'closed', resolution: '😀'.repeat(2000) }).resolution, '😀'.repeat(2000));
  assert.throws(() => normalizeFeedbackStatus({ status: 'closed', resolution: '😀'.repeat(2001) }), /2000/);
  const serverCase = { ...feedback, ...input, status: 'closed', resolution: '😀'.repeat(2000), history: [{ id: 'note-1', createdAt: receipt.createdAt, staff: { id: 'staff', name: '😀'.repeat(240) }, action: 'feedback.note_added', text: '😀'.repeat(2000), contactMethod: '' }] };
  assert.equal(isFeedbackCase(serverCase), true);
  assert.equal(isFeedbackCase({ ...serverCase, message: '😀'.repeat(2001) }), false);
});

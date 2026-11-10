import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeFeedbackInput, normalizeFeedbackNote, normalizeFeedbackStatus, isFeedbackReceipt, isFeedbackCase } from '../src/feedback.js';
import * as feedbackContract from '../src/feedback.js';
import { isOwnFeedback } from '../src/accounts.js';

const empty = { type: 'contact', name: '', phone: '', email: '', messenger: { service: '', address: '' }, message: '' };
const receipt = { id: 'a0f2d7e0-5af2-40c7-8bc0-de61ed274ed9', number: 'F-1', createdAt: '2026-10-20T10:00:00Z', state: 'accepted' };
const feedback = { ...empty, ...receipt, updatedAt: receipt.createdAt, email: 'ivan@example.org', status: 'new', assignedTo: null, version: 1, resolution: '', order: null, history: [], canManage: true, notification: { state: 'queued', code: null } };
delete feedback.state;

test('feedback references use positive safe integers and Moscow time with date padding', () => {
  assert.equal(typeof feedbackContract.isFeedbackNumber, 'function');
  for (const number of [1, 12, Number.MAX_SAFE_INTEGER]) assert.equal(feedbackContract.isFeedbackNumber(number), true);
  for (const number of [0, -1, 1.2, '12', null, true, Number.MAX_SAFE_INTEGER + 1]) assert.equal(feedbackContract.isFeedbackNumber(number), false);
  assert.equal(feedbackContract.formatFeedbackReference(12, '2026-11-02T10:45:00Z'), '№12 от 02-11-2026 13:45');
  assert.equal(feedbackContract.formatFeedbackReference(1, '2026-01-01T22:05:00Z'), '№1 от 02-01-2026 01:05');
  assert.equal(isFeedbackReceipt({ ...receipt, number: 12 }), true);
  assert.equal(isFeedbackReceipt(receipt), true); // Immutable historical public receipt.
  assert.equal(isFeedbackCase({ ...feedback, number: 12 }), true);
  for (const number of [0, -1, 1.2, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(isFeedbackReceipt({ ...receipt, number }), false);
    assert.equal(isFeedbackCase({ ...feedback, number }), false);
  }
  assert.deepEqual(Object.keys(feedbackContract.FEEDBACK_STATUS_DESCRIPTIONS), ['new', 'in_progress', 'converted', 'closed']);
  const own = { id: receipt.id, number: 12, createdAt: receipt.createdAt, type: 'contact', message: '', status: 'new', order: null };
  assert.equal(isOwnFeedback(own), true);
  for (const number of [0, -1, 1.2, '12', Number.MAX_SAFE_INTEGER + 1]) assert.equal(isOwnFeedback({ ...own, number }), false);
});

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
    { assignedTo: { id: 'staff', name: 12 } }, { canManage: true, notification: { state: 'error', code: 'token:secret' } },
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

test('promoted customer accounts can appear in assignments without a display name', () => {
  assert.equal(isFeedbackCase({ ...feedback, status: 'in_progress', assignedTo: { id: 'staff', name: '' }, history: [{ id: 'event', createdAt: receipt.createdAt, staff: { id: 'staff', name: '' }, action: 'feedback.claimed', text: '', contactMethod: '' }] }), true);
});

test('staff feedback DTO IDs use Unicode code points for case, assignment, order and history', () => {
  const id = '🚗'.repeat(128);
  const serverCase = { ...feedback, id, status: 'converted', assignedTo: { id, name: '' }, order: { id, number: 'L-1', createdAt: receipt.createdAt, totalKopecks: 100, status: 'new', integrationStatus: 'pending_sync' }, history: [{ id, createdAt: receipt.createdAt, staff: { id, name: '' }, action: 'feedback.note_added', text: '', contactMethod: '' }] };
  assert.equal(isFeedbackReceipt({ ...receipt, id }), true);
  assert.equal(isFeedbackCase(serverCase), true);
  for (const invalid of ['', '.', '..', 'bad\nID', 'bad\u007fID', '🚗'.repeat(129)]) {
    assert.equal(isFeedbackReceipt({ ...receipt, id: invalid }), false);
    assert.equal(isFeedbackCase({ ...serverCase, id: invalid }), false);
    assert.equal(isFeedbackCase({ ...serverCase, assignedTo: { id: invalid, name: '' } }), false);
    assert.equal(isFeedbackCase({ ...serverCase, order: { ...serverCase.order, id: invalid } }), false);
    assert.equal(isFeedbackCase({ ...serverCase, history: [{ ...serverCase.history[0], id: invalid }] }), false);
  }
});

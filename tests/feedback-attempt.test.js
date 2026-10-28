import test from 'node:test';
import assert from 'node:assert/strict';
import { feedbackKey, clearFeedbackKey } from '../src/feedback-attempt.js';
import { checkoutKey } from '../src/checkout-attempt.js';

function storage() {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) };
}
test('feedback retry keys are scoped separately from checkout and store technical data only', async () => {
  const saved = storage();
  const body = { name: 'Иван', phone: '+79991234567', message: 'private message' };
  const submission = await feedbackKey(body, 'submission', saved);
  assert.equal(await feedbackKey(structuredClone(body), 'submission', saved), submission);
  const conversion = await feedbackKey(body, 'order:case-1', saved);
  assert.notEqual(conversion, submission);
  assert.notEqual(await checkoutKey(body, saved), submission);
  assert.notEqual(await feedbackKey({ ...body, message: 'changed' }, 'order:case-1', saved), conversion);
  for (const value of saved.values.values()) {
    assert.ok(!value.includes('Иван') && !value.includes('+7999') && !value.includes('private'));
    assert.deepEqual(Object.keys(JSON.parse(value)).sort(), ['digest', 'key']);
  }
  clearFeedbackKey('submission', saved);
  assert.ok(!saved.values.has('lavr.feedback-attempt.v1:submission'));
  assert.ok(saved.values.has('lavr.feedback-attempt.v1:order:case-1'));
  assert.ok(saved.values.has('lavr.checkout-attempt.v1'));
});
test('failed retry storage blocks sending and failed deletion leaves a safe tombstone', async () => {
  const saved = storage();
  const first = await feedbackKey({}, 'submission', saved);
  saved.removeItem = () => { throw Error('blocked'); };
  clearFeedbackKey('submission', saved);
  assert.notEqual(await feedbackKey({}, 'submission', saved), first);
  saved.setItem = () => { throw Error('blocked'); };
  await assert.rejects(feedbackKey({}, 'submission', saved), /ключ/i);
});

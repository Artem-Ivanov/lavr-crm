import test from 'node:test';
import assert from 'node:assert/strict';
import { isAdminUser, isUserSession, normalizeAccountInput, validateOwnOrder } from '../src/accounts.js';

test('admin login is the same normalized email alias, including full length addresses', () => {
  const email = `${'a'.repeat(64)}@${'b'.repeat(63)}.${'c'.repeat(63)}.${'d'.repeat(61)}`;
  assert.equal(email.length, 254);
  const user = { id: 'staff', name: '', login: email, email, phone: null, emailVerified: true, phoneVerified: false, isActive: true, role: 'operator', version: 1 };
  assert.equal(isAdminUser(user), true);
  assert.equal(isAdminUser({ ...user, login: null, email: null, emailVerified: false }), true);
  for (const login of [null, 'staff', 'different@example.test']) assert.equal(isAdminUser({ ...user, login }), false);
  assert.equal(isAdminUser({ ...user, email: null }), false);
});

for (const separator of ['#', '?']) test(`backend-supported ${separator} email works for login, session and admin identity`, () => {
  const email = `owner${separator}ops@example.test`;
  assert.equal(normalizeAccountInput({ email: ` ${email.toUpperCase()} `, password: 'test-password' }, 'login').email, email);
  const user = { id: 'staff', name: '', email, phone: null, emailVerified: true, phoneVerified: false, profileComplete: false };
  assert.equal(isUserSession({ user, capabilities: { operate: true, editSite: false, manageUsers: false }, csrfToken: 'x'.repeat(40), expiresAt: '2027-01-01T00:00:00Z' }), true);
  const { profileComplete, ...adminFields } = user;
  assert.equal(isAdminUser({ ...adminFields, login: email, isActive: true, role: 'operator', version: 1 }), true);
});

test('account identity still rejects whitespace, controls, duplicate @ and missing email domain', () => {
  for (const email of ['owner ops@example.test', 'owner\u0000@example.test', 'owner\u007f@example.test', 'owner@@example.test', 'owner@example']) {
    assert.throws(() => normalizeAccountInput({ email, password: 'test-password' }, 'login'), /email/);
  }
});

test('password-reset links require UUID and token plus exactly matching unmodified passwords', () => {
  const link = { flowId: '9a55cbe9-3743-4f32-9c83-4c01955ade11', token: '_'.repeat(42) + '-' };
  for (const password of ['x'.repeat(12), 'x'.repeat(256), '  password1234  ']) {
    const body = { ...link, newPassword: password, confirmPassword: password };
    assert.deepEqual(normalizeAccountInput(body, 'password'), body);
  }
  const body = { ...link, newPassword: 'valid password', confirmPassword: 'valid password' };
  for (const patch of [{ flowId: '' }, { token: 'a'.repeat(43) + '\n' }, { token: 'a'.repeat(42) + '+' }, { confirmPassword: 'valid password ' }, { newPassword: 'nul\0password123', confirmPassword: 'nul\0password123' }]) assert.throws(() => normalizeAccountInput({ ...body, ...patch }, 'password'));
});

test('own checkout normalizes only supplied contacts and leaves profile fallbacks omitted', () => {
  const body = { fulfillment: { method: 'pickup', address: '' }, items: [{ productId: 'part', quantity: 1, expectedUnitPriceKopecks: 100 }] };
  assert.deepEqual(validateOwnOrder(body), body);
  const input = { ...body, name: ' Получатель ', phone: ' 8 (999) 123-45-67 ', email: ' Buyer@Example.Test ' };
  assert.deepEqual(validateOwnOrder(input), { ...body, name: 'Получатель', phone: '+79991234567', email: 'Buyer@Example.Test' });
  assert.equal(input.phone, ' 8 (999) 123-45-67 ');
  assert.deepEqual(validateOwnOrder({ ...body, email: '' }), { ...body, email: '' });
  for (const patch of [{ phone: '' }, { phone: '123' }, { phone: null }, { email: null }, { email: 'missing-domain' }, { email: 'a\n@example.test' }, { name: 'x'.repeat(101) }, { customerId: 'other' }, { role: 'admin' }]) {
    assert.throws(() => validateOwnOrder({ ...body, ...patch }), undefined, JSON.stringify(patch));
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { isAdminUser, isUserSession, normalizeAccountInput } from '../src/accounts.js';

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

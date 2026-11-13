import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';
import { createRenderer, h, nextTick, reactive } from 'vue';

const componentUrl = new URL('../src/components/AccountAccess.vue', import.meta.url);
const { descriptor } = parse(await readFile(componentUrl, 'utf8'));
const script = compileScript(descriptor, { id: 'account-access-regression' });
const imports = source => source.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) => `from ${JSON.stringify(specifier.startsWith('.') ? new URL(specifier, componentUrl).href : import.meta.resolve(specifier))}`);
const { default: AccountAccess } = await import(`data:text/javascript;base64,${Buffer.from(imports(script.content)).toString('base64')}`);
const template = compileTemplate({ source: descriptor.template.content, filename: componentUrl.pathname, id: 'account-access-regression', compilerOptions: { bindingMetadata: script.bindings } });
assert.deepEqual(template.errors, []);
AccountAccess.render = (await import(`data:text/javascript;base64,${Buffer.from(imports(template.code)).toString('base64')}`)).render;
const node = tag => ({ tag, tagName: tag.toUpperCase(), children: [], props: {}, getRootNode: () => globalThis.document, addEventListener() {}, removeEventListener() {} });
const renderer = createRenderer({
  createElement: node, createComment: text => ({ text }), createText: text => ({ text }),
  setText: (element, text) => { element.text = text; }, setElementText: (element, text) => { element.text = text; element.children = []; },
  patchProp: (element, key, prior, value) => { element.props[key] = value; },
  insert(element, parent, anchor) { if (element.parent) element.parent.children.splice(element.parent.children.indexOf(element), 1); const index = parent.children.indexOf(anchor); parent.children.splice(index < 0 ? parent.children.length : index, 0, element); element.parent = parent; },
  remove(element) { if (element.parent) element.parent.children.splice(element.parent.children.indexOf(element), 1); },
  parentNode: element => element.parent, nextSibling: element => element.parent?.children[element.parent.children.indexOf(element) + 1] || null,
});
const walk = (root, predicate) => [root, ...(root.children || []).flatMap(child => walk(child, predicate))].filter(predicate);
const text = root => [root.text || '', ...(root.children || []).map(text)].join(' ');
const link = { flowId: '9a55cbe9-3743-4f32-9c83-4c01955ade11', token: 'a'.repeat(43) };
const password = 'new password123';
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function setup(t, { page = 'password-reset', resetLink = null, api = {} } = {}) {
  const priorDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const priorDocumentClass = Object.getOwnPropertyDescriptor(globalThis, 'Document');
  Object.defineProperty(globalThis, 'Document', { configurable: true, value: class Document {} });
  Object.defineProperty(globalThis, 'document', { configurable: true, value: Object.assign(new globalThis.Document(), { activeElement: null }) });
  const events = []; const calls = []; let instance;
  const props = reactive({ page, resetLink, api: { mode: 'api', requestPasswordReset: async body => { calls.push(body); return { flowId: link.flowId, resendAfterSeconds: 60 }; }, confirmPasswordReset: async body => { calls.push(body); }, ...api } });
  const root = node('root'); const app = renderer.createApp({ render: () => h(AccountAccess, { ...props, ref: value => { instance = value; }, onPasswordReset: () => events.push('password-reset'), onIdentityChanging: () => events.push('identity-changing'), onAuthenticated: value => events.push(['authenticated', value]), onNavigate: value => events.push(['navigate', value]) }) });
  app.mount(root); const state = instance.$.setupState;
  let mounted = true;
  const unmount = () => { if (mounted) { app.unmount(); mounted = false; } };
  t.after(() => { unmount(); if (priorDocument) Object.defineProperty(globalThis, 'document', priorDocument); else delete globalThis.document; if (priorDocumentClass) Object.defineProperty(globalThis, 'Document', priorDocumentClass); else delete globalThis.Document; });
  return { props, state, calls, events, root, unmount, inputs: () => walk(root, element => element.tag === 'input'), find: predicate => walk(root, predicate)[0] };
}

test('plain password recovery requests an email link and never renders code or password inputs', async t => {
  const fixture = setup(t);
  assert.deepEqual(fixture.inputs().map(input => input.props.name), ['email']);
  assert.ok(fixture.find(element => element.tag === 'button' && text(element) === 'Отправить ссылку'));
  fixture.state.draft.email = 'buyer@example.test'; await fixture.state.submit(); await nextTick();
  assert.deepEqual(fixture.calls, [{ email: 'buyer@example.test' }]);
  assert.match(text(fixture.root), /Если.*ссылк/);
  assert.deepEqual(fixture.inputs().map(input => input.props.name), ['email']);
  assert.doesNotMatch(text(fixture.root), /Код из письма|Отправить код повторно/);
});

test('reset link renders two password fields, blocks mismatch and emits success without changing identity first', async t => {
  const fixture = setup(t, { resetLink: link });
  assert.deepEqual(fixture.inputs().map(input => input.props.name), ['newPassword', 'confirmPassword']);
  assert.deepEqual(walk(fixture.root, element => element.tag === 'label').map(label => text(label).trim()), ['Новый пароль', 'Подтвердите пароль']);
  for (const input of fixture.inputs()) assert.deepEqual([input.props.type, input.props.minlength, input.props.maxlength, input.props.autocomplete], ['password', '12', '256', 'new-password']);
  assert.match(text(fixture.root), /Новый пароль/); assert.match(text(fixture.root), /Подтвердите пароль/);
  Object.assign(fixture.state.draft, { password, confirmPassword: 'different password' });
  await fixture.state.submit(); await nextTick();
  assert.equal(fixture.calls.length, 0); assert.match(text(fixture.root), /Пароли не совпадают/); assert.deepEqual(fixture.events, []);
  fixture.state.draft.confirmPassword = password; await fixture.state.submit();
  assert.deepEqual(fixture.calls, [{ ...link, newPassword: password, confirmPassword: password }]);
  assert.deepEqual(fixture.events, ['password-reset']);
  assert.equal(fixture.state.draft.password, ''); assert.equal(fixture.state.draft.confirmPassword, '');
});

test('a malformed link offers a fresh email request without password or code entry', async t => {
  const fixture = setup(t, { resetLink: { invalid: true } });
  assert.equal(fixture.inputs().length, 0); assert.match(text(fixture.root), /Ссылка.*некорректна/);
  const restart = fixture.find(element => element.tag === 'button' && text(element) === 'Запросить новую ссылку');
  assert.ok(restart); restart.props.onClick(); await nextTick();
  assert.deepEqual(fixture.inputs().map(input => input.props.name), ['email']);
});

test('an expired or reused link offers requesting a new link while keeping signup code separate', async t => {
  const fixture = setup(t, { resetLink: link, api: { confirmPasswordReset: async () => { throw new Error('Ссылка истекла или уже использована.'); } } });
  Object.assign(fixture.state.draft, { password, confirmPassword: password }); await fixture.state.submit(); await nextTick();
  assert.match(text(fixture.root), /Ссылка истекла/);
  const restart = fixture.find(element => element.tag === 'button' && text(element) === 'Запросить новую ссылку');
  assert.ok(restart); restart.props.onClick(); await nextTick();
  assert.equal(fixture.state.draft.password, ''); assert.equal(fixture.state.draft.confirmPassword, '');
  assert.deepEqual(fixture.inputs().map(input => input.props.name), ['email']);
});

for (const change of ['route', 'returnTo', 'link', 'unmount']) test(`a late reset response is ignored after ${change} and clears password fields`, async t => {
  const pending = deferred();
  const fixture = setup(t, { resetLink: link, api: { confirmPasswordReset: () => pending.promise } });
  Object.assign(fixture.state.draft, { password, confirmPassword: password }); const submitting = fixture.state.submit();
  if (change === 'route') fixture.props.page = 'login';
  if (change === 'returnTo') fixture.props.returnTo = '#account';
  if (change === 'link') fixture.props.resetLink = { ...link, token: 'b'.repeat(43) };
  if (change === 'unmount') fixture.unmount();
  await nextTick();
  assert.equal(fixture.state.draft.password, ''); assert.equal(fixture.state.draft.confirmPassword, '');
  pending.resolve(); await submitting; assert.deepEqual(fixture.events, []);
});

test('signup activation continues to use its six digit code and emits the authenticated session', async t => {
  const activated = { user: { id: 'buyer' } }; const calls = [];
  const fixture = setup(t, { page: 'signup', api: { signup: async () => ({ flowId: 'signup-flow', resendAfterSeconds: 60 }), activate: async body => { calls.push(body); return activated; } } });
  Object.assign(fixture.state.draft, { email: 'buyer@example.test', phone: '+79991234567', password });
  await fixture.state.submit(); fixture.props.page = 'activate'; await nextTick();
  assert.deepEqual(fixture.inputs().map(input => input.props.name), ['code']);
  assert.equal(fixture.inputs()[0].props.pattern, '[0-9]{6}');
  fixture.state.draft.code = '123456'; await fixture.state.submit();
  assert.deepEqual(calls, [{ flowId: 'signup-flow', code: '123456' }]);
  assert.deepEqual(fixture.events.slice(-2), ['identity-changing', ['authenticated', activated]]);
});

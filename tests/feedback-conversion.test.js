import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { setImmediate as flush } from 'node:timers/promises';
import { parse, compileScript } from '@vue/compiler-sfc';
import { createRenderer, reactive } from 'vue';

const componentUrl = new URL('../src/components/FeedbackManager.vue', import.meta.url);
const { descriptor } = parse(await readFile(componentUrl, 'utf8'));
const compiled = compileScript(descriptor, { id: 'feedback-conversion-regression' }).content.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) => `from ${JSON.stringify(specifier.startsWith('.') ? new URL(specifier, componentUrl).href : import.meta.resolve(specifier))}`);
const { default: FeedbackManager } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
FeedbackManager.render = () => null;
const renderer = createRenderer({
  createComment: () => ({}), insert() {}, remove() {}, parentNode: () => null, nextSibling: () => null,
});
const feedback = { id: 'feedback-1', name: 'Тест', phone: '+79991234567', email: '', status: 'in_progress', version: 2 };
const product = { id: 'part-1', priceKopecks: 100, stock: 3 };

async function setup(t, { delayDigest = false } = {}) {
  const values = new Map();
  const priorStorage = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage');
  const priorDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  Object.defineProperty(globalThis, 'document', { configurable: true, value: { querySelector: () => null } });
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), removeItem: key => values.delete(key) } });
  const digest = crypto.subtle.digest;
  let release;
  let started;
  let finished;
  let sent;
  const pending = new Promise(resolve => { release = resolve; });
  const hashing = new Promise(resolve => { started = resolve; });
  const hashed = new Promise(resolve => { finished = resolve; });
  const sending = new Promise(resolve => { sent = resolve; });
  if (delayDigest) crypto.subtle.digest = async (...args) => { started(); await pending; const result = await digest.apply(crypto.subtle, args); finished(); return result; };
  let actor = 'operator-1';
  const calls = [];
  const session = reactive({ user: { id: actor }, capabilities: { operate: true, editSite: false, manageUsers: false } });
  const api = {
    getFeedback: async () => ({ items: [], total: 0 }), getFeedbackDetail: async () => ({ ...feedback }),
    createFeedbackOrder: async (item, body, key) => { calls.push({ actor, item, body, key }); sent(); return { ...feedback, status: 'converted' }; },
  };
  const app = renderer.createApp(FeedbackManager, { api, session, caseId: feedback.id });
  const state = app.mount({}).$.setupState;
  await flush();
  Object.assign(state.orderDraft, { name: 'Тест', phone: '+79991234567', email: '', method: 'pickup', address: '' });
  state.orderItems = [{ product, quantity: 1 }];
  t.after(() => {
    release(); app.unmount(); crypto.subtle.digest = digest;
    if (priorStorage) Object.defineProperty(globalThis, 'sessionStorage', priorStorage); else delete globalThis.sessionStorage;
    if (priorDocument) Object.defineProperty(globalThis, 'document', priorDocument); else delete globalThis.document;
  });
  return { state, calls, values, hashing, hashed, sending, release, unmount: () => app.unmount(), switchActor(id) { actor = id; session.user.id = id; } };
}

test('feedback conversion still sends the normal order and clears its captured retry scope', async t => {
  const fixture = await setup(t);
  fixture.state.createOrder();
  await fixture.sending;
  await flush();
  assert.equal(fixture.calls.length, 1);
  assert.equal(fixture.calls[0].actor, 'operator-1');
  assert.equal(fixture.calls[0].item.id, feedback.id);
  assert.deepEqual(fixture.calls[0].body.items, [{ productId: product.id, quantity: 1, expectedUnitPriceKopecks: 100 }]);
  assert.match(fixture.calls[0].key, /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i);
  assert.equal(fixture.values.has('lavr.feedback-attempt.v1:order:feedback-1:operator-1'), false);
});

test('a conversion awaiting its retry digest sends no mutation after unmount and a new login', async t => {
  const fixture = await setup(t, { delayDigest: true });
  fixture.state.createOrder(); await fixture.hashing;
  fixture.unmount(); fixture.switchActor('admin-2'); fixture.release();
  await fixture.hashed; await flush();
  assert.equal(fixture.calls.length, 0);
});

test('a conversion awaiting its retry digest sends no mutation after the actor changes', async t => {
  const fixture = await setup(t, { delayDigest: true });
  fixture.state.createOrder(); await fixture.hashing;
  fixture.switchActor('admin-2'); fixture.release();
  await fixture.hashed; await flush();
  assert.equal(fixture.calls.length, 0);
});

test('feedback conversion parses comma quantities, rounds subtotal and includes the agreed sale unit', async t => {
  const fixture = await setup(t);
  fixture.state.orderItems = [];
  const bulk = { ...product, unit: 'кг', stock: 9.785, priceKopecks: 101 };
  fixture.state.addProduct(bulk);
  assert.equal(fixture.state.orderItems[0].quantity, 0.001);
  fixture.state.changeOrderQuantity(fixture.state.orderItems[0], '0,955');
  assert.equal(fixture.state.orderTotal, 96);
  fixture.state.createOrder();
  await fixture.sending; await flush();
  assert.deepEqual(fixture.calls[0].body.items, [{ productId: bulk.id, quantity: 0.955, expectedUnitPriceKopecks: 101, expectedUnit: 'кг' }]);
});

test('feedback conversion blocks a changed unit until explicit quantity selection', async t => {
  const fixture = await setup(t);
  fixture.state.orderItems = [{ product: { ...product, unit: 'л' }, quantity: 0.955, expectedUnit: 'кг' }];
  fixture.state.createOrder(); await flush();
  assert.equal(fixture.calls.length, 0);
  assert.match(fixture.state.actionError, /количество|остаток/);
  fixture.state.changeOrderQuantity(fixture.state.orderItems[0], '0,001', true);
  fixture.state.createOrder(); await fixture.sending; await flush();
  assert.equal(fixture.calls[0].body.items[0].expectedUnit, 'л');
  assert.equal(fixture.calls[0].body.items[0].quantity, 0.001);
});

test('feedback conversion confirms a change from kilograms to pieces and omits the legacy piece unit', async t => {
  const fixture = await setup(t);
  fixture.state.orderItems = [{ product: { ...product, unit: 'шт' }, quantity: 0.955, expectedUnit: 'кг' }];
  const item = fixture.state.orderItems[0];
  fixture.state.changeOrderQuantity(item, 1, true);
  assert.equal(item.quantity, 1);
  assert.equal(Object.hasOwn(item, 'expectedUnit'), false);
  assert.equal(fixture.state.actionError, '');
  fixture.state.createOrder(); await fixture.sending; await flush();
  assert.deepEqual(fixture.calls[0].body.items, [{ productId: product.id, quantity: 1, expectedUnitPriceKopecks: 100 }]);
});

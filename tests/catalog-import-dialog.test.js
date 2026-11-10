import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc';
import { createRenderer, nextTick } from 'vue';
import { createCatalogImportMethods } from '../src/data/http-catalog-import-api.js';
import { formatQuantity, productUnit } from '../src/quantity.js';

const componentUrl = new URL('../src/components/CatalogImportDialog.vue', import.meta.url);
const source = (await readFile(componentUrl, 'utf8')).replace("import AppIcon from './AppIcon.vue';", 'const AppIcon = { render: () => null };');
const { descriptor } = parse(source);
const script = compileScript(descriptor, { id: 'catalog-import-regression' });
const absoluteImports = source => source.replace(/from (['"])([^'"]+)\1/g, (_, quote, specifier) => `from ${JSON.stringify(specifier.startsWith('.') ? new URL(specifier, componentUrl).href : import.meta.resolve(specifier))}`);
const { default: CatalogImportDialog } = await import(`data:text/javascript;base64,${Buffer.from(absoluteImports(script.content)).toString('base64')}`);
const template = compileTemplate({ source: descriptor.template.content, filename: componentUrl.pathname, id: 'catalog-import-regression', compilerOptions: { bindingMetadata: script.bindings } });
assert.deepEqual(template.errors, []);
CatalogImportDialog.render = (await import(`data:text/javascript;base64,${Buffer.from(absoluteImports(template.code)).toString('base64')}`)).render;
const node = tag => ({ tag, tagName: tag.toUpperCase(), children: [], props: {}, get options() { return this.children.filter(child => child.tag === 'option'); }, getRootNode: () => globalThis.document, addEventListener() {}, removeEventListener() {}, setAttribute() {}, removeAttribute() {}, open: false, querySelector: () => null, showModal() { this.open = true; }, close() { this.open = false; } });
const renderer = createRenderer({
  createElement: node, createComment: text => ({ text }), createText: text => ({ text }),
  setText: (el, text) => { el.text = text; }, setElementText: (el, text) => { el.text = text; el.children = []; },
  patchProp: (el, key, prior, value) => { el.props[key] = value; },
  insert(el, parent, anchor) { if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1); const index = parent.children.indexOf(anchor); parent.children.splice(index < 0 ? parent.children.length : index, 0, el); el.parent = parent; },
  remove(el) { if (el.parent) el.parent.children.splice(el.parent.children.indexOf(el), 1); },
  parentNode: el => el.parent, nextSibling: el => el.parent?.children[el.parent.children.indexOf(el) + 1] || null,
});
const stamp = '2026-11-02T09:00:00Z';
const row = (id, extra = {}) => ({ id, name: `Товар ${id}`, sku: id, brand: '', sourceProductId: null, page: 1, position: 1, unit: 'кг', selected: true, action: 'update', kind: 'ordinary', targetProductId: `product-${id}`, categoryId: null, resolution: 'link', acknowledgeStandalone: false, issues: [], proposedStock: '1', proposedAvailableStock: '1', proposedPriceKopecks: 100, before: { stock: '1', reserved: '0', availableStock: '1', priceKopecks: 100, unit: 'кг' }, ...extra });
const batch = (id = 'A', extra = {}) => ({ id, status: 'ready', version: 1, sourceMode: 'file', authorId: 'admin-1', fileHash: 'a'.repeat(64), pageCount: 1, fileName: `${id}.pdf`, observedAt: stamp, createdAt: stamp, sourceNamespace: 'pdf:main', error: null, report: null, summary: { new: 0, changed: 3, unchanged: 0, conflict: 0, missingUnit: 0, bundle: 0, selected: 3, skipped: 0 }, rows: { items: [row('1'), row('2'), row('3')], total: 50, offset: 0, limit: 50 }, ...extra });
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
function walk(root, predicate) { if (predicate(root)) return root; for (const child of root.children || []) { const found = walk(child, predicate); if (found) return found; } }
function textContent(root) { return [root.text || '', ...(root.children || []).map(textContent)].join(' '); }
async function setup(t, overrides = {}) {
  const calls = []; let saved = batch();
  const api = {
    getCatalogImportCapabilities: async () => ({ file: { enabled: true }, adapter: { message: 'Недоступен' } }),
    getCatalogImports: async () => ({ items: [] }), uploadCatalogImport: async () => saved,
    getCatalogImport: async (id, options) => { calls.push({ kind: 'detail', id, options }); return { ...saved, id }; },
    updateCatalogImportDecisions: async (id, body) => { calls.push({ kind: 'decision', id, body }); saved = { ...saved, version: saved.version + 1, rows: { ...saved.rows, items: saved.rows.items.map(item => body.rowIds?.includes(item.id) ? { ...item, ...body, selected: (body.resolution || item.resolution) === 'skip' ? false : body.selected ?? item.selected } : item) } }; return saved; },
    applyCatalogImport: async () => { calls.push({ kind: 'apply' }); return saved; },
    ...overrides,
  };
  const priorDocument = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const priorDocumentClass = Object.getOwnPropertyDescriptor(globalThis, 'Document');
  Object.defineProperty(globalThis, 'Document', { configurable: true, value: class Document {} });
  Object.defineProperty(globalThis, 'document', { configurable: true, value: Object.assign(new globalThis.Document(), { activeElement: null }) });
  const root = node('root'); const app = renderer.createApp(CatalogImportDialog, { api, categories: [] });
  const state = app.mount(root).$.setupState;
  t.after(() => { app.unmount(); if (priorDocument) Object.defineProperty(globalThis, 'document', priorDocument); else delete globalThis.document; if (priorDocumentClass) Object.defineProperty(globalThis, 'Document', priorDocumentClass); else delete globalThis.Document; });
  await state.open(); await state.selectBatch('A'); await nextTick();
  return { state, root, calls, find: predicate => walk(root, predicate) };
}

test('import offers kilograms and metres in both bulk and individual unit choices', async t => {
  const { find } = await setup(t);
  const choices = el => el.children.filter(child => child.tag === 'option').map(child => child.props.value);
  assert.deepEqual(choices(find(el => el.props?.['data-testid'] === 'catalog-import-bulk-unit')), ['', 'шт', 'кг', 'л', 'м']);
  assert.deepEqual(choices(find(el => el.props?.['aria-label'] === 'Единица Товар 1')), ['', 'шт', 'кг', 'л', 'м']);
});

test('file observation uses upload time and keeps the same timestamp and key on retry', async t => {
  const uploads = [];
  const { state, find } = await setup(t, { uploadCatalogImport: async (body, key) => {
    uploads.push({ body, key });
    if (uploads.length < 3) throw new Error('Connection interrupted');
    return batch();
  } });
  state.resetView();
  state.chooseFile({ target: { files: [{ name: 'first.pdf' }] } });
  state.priceConfirmed = true;
  const before = Date.now();
  await state.upload();
  await state.upload();
  assert.ok(Date.parse(uploads[0].body.observedAt) >= before);
  assert.ok(Date.parse(uploads[0].body.observedAt) <= Date.now());
  assert.equal(uploads[1].body.observedAt, uploads[0].body.observedAt);
  assert.equal(uploads[1].key, uploads[0].key);
  await nextTick();
  assert.equal(find(el => el.props?.type === 'datetime-local'), undefined);
  state.chooseFile({ target: { files: [{ name: 'second.pdf' }] } });
  await state.upload();
  assert.notEqual(uploads[2].key, uploads[0].key);
  assert.ok(Date.parse(uploads[2].body.observedAt) >= Date.parse(uploads[0].body.observedAt));
});

test('unsent search and an in-flight preview cannot mutate the displayed filter result', async t => {
  const preview = deferred(); let delay = false;
  const { state, calls, find } = await setup(t, { getCatalogImport: async (id, options) => delay ? preview.promise : batch(id) });
  state.scope = 'filter'; state.bulkConfirmed = true; state.query = 'another unsent query'; await nextTick();
  await state.bulk('unit', 'л');
  assert.equal(calls.filter(call => call.kind === 'decision').length, 0, 'unsent query must not change any row');
  assert.equal(find(el => el.tag === 'fieldset')?.props.disabled, true);
  state.query = ''; await nextTick(); state.bulkConfirmed = true; delay = true;
  const loading = state.loadBatch('A');
  await state.bulk('unit', 'л'); await state.decide(state.rows[0], { selected: false }); await state.apply();
  assert.equal(calls.filter(call => ['decision', 'apply'].includes(call.kind)).length, 0, 'stale preview must not allow programmatic mutations either');
  preview.resolve(batch()); await loading;
});

test('inclusion checkbox deselects and reselects a mapped row without discarding its identity', async t => {
  const { state, find } = await setup(t);
  await find(el => el.props?.['aria-label'] === 'Включить Товар 1').props.onChange({ target: { checked: false } }); await nextTick();
  assert.equal(state.rows[0].selected, false);
  assert.equal(state.rows[0].resolution, 'link');
  await find(el => el.props?.['aria-label'] === 'Включить Товар 1').props.onChange({ target: { checked: true } }); await nextTick();
  assert.equal(state.rows[0].selected, true);
  assert.equal(state.rows[0].targetProductId, 'product-1');
});

test('late upload history cannot reload package A after close, reopen and selecting B', async t => {
  const history = deferred(); let delay = false;
  const { state, calls } = await setup(t, { getCatalogImports: async () => delay ? history.promise : { items: [] } });
  delay = true; const uploading = state.upload(); await nextTick(); await nextTick();
  await state.close(); delay = false; await state.open(); await state.selectBatch('B');
  history.resolve({ items: [] }); await uploading;
  assert.equal(state.batch.id, 'B');
  assert.equal(calls.filter(call => call.kind === 'detail').at(-1).id, 'B');
});

test('bulk marks are separate from inclusion and only marked rows on the displayed page are assigned', async t => {
  const { state, root, calls, find } = await setup(t);
  const mark1 = find(el => el.props?.['aria-label'] === 'Отметить для назначения Товар 1');
  const mark3 = find(el => el.props?.['aria-label'] === 'Отметить для назначения Товар 3');
  assert.ok(mark1 && mark3, 'each row needs its own bulk mark');
  mark1.props.onChange({ target: { checked: true } });
  mark3.props.onChange({ target: { checked: true } }); await nextTick();
  assert.match(textContent(root), /Отмеченные на текущей странице.*2/);
  assert.equal(state.rows.every(item => item.selected), true);
  await state.bulk('unit', 'л');
  assert.deepEqual(calls.find(call => call.kind === 'decision').body, { rowIds: ['1', '3'], unit: 'л' });
  assert.match(textContent(root), /Весь результат фильтра.*50/);
});

test('saved report renders stored author, hash, source and observation time', async t => {
  const report = { appliedAt: stamp, observedAt: '2026-10-01T09:00:00Z', authorId: 'saved-author', fileHash: 'a'.repeat(64), sourceNamespace: 'saved-source', createdCount: 1, updatedCount: 2, unchangedCount: 3, skippedCount: 4 };
  const { root } = await setup(t, { getCatalogImport: async () => batch('A', { status: 'applied', report }) });
  const text = textContent(walk(root, el => el.props?.['data-testid'] === 'catalog-import-report'));
  assert.match(text, /saved-author/); assert.match(text, /a{64}/); assert.match(text, /saved-source/); assert.match(text, /01\.10\.2026/);
});

test('manual defer and re-inclusion preserve a new row decision and do not resolve ambiguous identities', async t => {
  const { state, find, calls } = await setup(t);
  state.batch.rows.items[0] = row('1', { before: null, targetProductId: null, resolution: 'new', action: 'create' });
  state.batch.rows.items[1] = row('2', { before: null, targetProductId: null, resolution: null, action: 'review', issues: [{ code: 'AMBIGUOUS', message: 'Выберите товар' }] });
  // The API is external here: keep the fixture response in sync with the server-owned identity fields.
  const decisions = [];
  state.props.api.updateCatalogImportDecisions = async (id, body) => { decisions.push(body); state.batch.rows.items = state.rows.map(item => body.rowIds.includes(item.id) ? { ...item, selected: body.selected } : item); return state.batch; };
  state.props.api.getCatalogImport = async () => state.batch;
  await nextTick();
  await find(el => el.tag === 'button' && el.text === 'Отложить строку').props.onClick(); await nextTick();
  assert.deepEqual(decisions[0], { rowIds: ['1'], selected: false });
  assert.equal(state.rows[0].resolution, 'new');
  await find(el => el.props?.['aria-label'] === 'Включить Товар 1').props.onChange({ target: { checked: true } }); await nextTick();
  assert.equal(state.rows[0].selected, true); assert.equal(state.rows[0].resolution, 'new');
  await find(el => el.props?.['aria-label'] === 'Включить Товар 2').props.onChange({ target: { checked: false } }); await nextTick();
  await find(el => el.props?.['aria-label'] === 'Включить Товар 2').props.onChange({ target: { checked: true } }); await nextTick();
  assert.equal(state.rows[1].resolution, null); assert.equal(state.rows[1].action, 'review'); assert.equal(state.rows[1].issues[0].code, 'AMBIGUOUS');
  assert.equal(decisions.every(body => !Object.hasOwn(body, 'resolution')), true);
  assert.equal(calls.filter(call => call.kind === 'apply').length, 0);
});

test('only the latest submitted preview sets the confirmed bulk scope and stale marks never reach another page', async t => {
  const a = deferred(); const b = deferred(); let mode = '';
  const { state, calls } = await setup(t, { getCatalogImport: async (id, options) => mode ? (options.query === 'A' ? a.promise : b.promise) : batch(id) });
  state.markRow(state.rows[0], true); mode = 'delay';
  state.query = 'A'; const first = state.changeFilter();
  state.query = 'B'; const second = state.changeFilter();
  b.resolve(batch('A', { rows: { items: [row('B1')], total: 7, limit: 50, offset: 0 } })); await second;
  a.resolve(batch('A', { rows: { items: [row('A1')], total: 80, limit: 50, offset: 0 } })); await first;
  assert.equal(state.rows[0].id, 'B1'); assert.equal(state.bulkCount, 0);
  state.scope = 'filter'; state.bulkConfirmed = true; mode = '';
  await state.bulk('unit', 'л');
  assert.deepEqual(calls.find(call => call.kind === 'decision').body, { filter: 'all', query: 'B', unit: 'л' });
});

test('late bulk, mapping and apply continuations cannot clear or focus a reopened dialog', async t => {
  const mutation = deferred(); const history = deferred(); let delayHistory = false;
  const { state } = await setup(t, { updateCatalogImportDecisions: async () => mutation.promise, getCatalogImports: async () => delayHistory ? history.promise : { items: [] } });
  state.scope = 'filter'; state.bulkConfirmed = true;
  const bulk = state.bulk('unit', 'л');
  await state.close(); await state.open(); state.bulkConfirmed = true;
  mutation.resolve(batch()); await bulk;
  assert.equal(state.bulkConfirmed, true);
  const mappingMutation = deferred(); state.props.api.updateCatalogImportDecisions = async () => mappingMutation.promise;
  state.mapping.rowId = '1'; state.mapping.productId = 'product-1';
  const mapping = state.linkRow(state.rows[0]);
  await state.close(); await state.open(); state.mapping.rowId = 'new-mapping';
  mappingMutation.resolve(batch()); await mapping;
  assert.equal(state.mapping.rowId, 'new-mapping');
  delayHistory = true; const applying = state.apply(); await nextTick(); await nextTick();
  await state.close(); delayHistory = false; await state.open();
  let focused = false; state.dialog.querySelector = () => ({ focus() { focused = true; } });
  history.resolve({ items: [] }); await applying;
  assert.equal(focused, false);
});


test('adapter preview and saved report render actual shared DTOs in dialog and mixed history', async t => {
  const adapter = batch('A', { sourceMode: 'adapter', sourceNamespace: 'adapter:test', authorId: 'saved-author', fileName: '', fileHash: '', pageCount: 0, rows: { items: [row('1', { page: null, sourceProductId: 'stable-source-id' })], total: 1, offset: 0, limit: 50 } });
  const applied = { ...adapter, status: 'applied', report: { appliedAt: stamp, observedAt: stamp, authorId: 'saved-author', fileHash: '', sourceNamespace: 'adapter:test', createdCount: 1, updatedCount: 0, unchangedCount: 0, skippedCount: 0 } };
  let saved = adapter;
  const methods = createCatalogImportMethods(async path => {
    const data = path.includes('/A?') ? saved : { items: [saved, batch('PDF')], total: 2, offset: 0, limit: 10 };
    return { data, etag: data.version ? `"${data.version}"` : null };
  });
  const { root, state } = await setup(t, { getCatalogImport: methods.getCatalogImport, getCatalogImports: methods.getCatalogImports });
  let text = textContent(root);
  assert.match(text, /adapter:test/);
  assert.match(text, /stable-source-id/);
  assert.match(text, /Без исходного файла/);
  assert.doesNotMatch(text, /Страница\s+[,·]|Страница\s+null/);
  saved = applied; await state.selectBatch('A'); await nextTick();
  text = textContent(walk(root, el => el.props?.['data-testid'] === 'catalog-import-report'));
  assert.match(text, /saved-author/);
  assert.match(text, /adapter:test/);
  assert.match(text, /Без исходного файла/);
  assert.match(text, /02\.11\.2026/);
  assert.doesNotMatch(text, /SHA-256/);
  const historyText = textContent(walk(root, el => el.props?.['aria-label'] === 'История импорта'));
  assert.match(historyText, /adapter:test/);
  assert.match(historyText, /PDF\.pdf/);
});

test('compiled App product details label a published local PDF identifier neutrally', t => {
  return readFile(new URL('../src/App.vue', import.meta.url), 'utf8').then(appSource => {
    const { descriptor } = parse(appSource);
    const specs = descriptor.template.content.match(/<dl class="product-specs">[\s\S]*?<\/dl>/)[0];
    const compiled = compileTemplate({ source: specs, filename: 'App.vue', id: 'catalog-id-regression' });
    assert.deepEqual(compiled.errors, []);
    return import(`data:text/javascript;base64,${Buffer.from(absoluteImports(compiled.code)).toString('base64')}`).then(({ render }) => {
      const component = { render, setup: () => ({ modalProduct: { id: 'local-id', externalId: 'pdf:local-catalog-id', specs: {}, stock: 1, unit: 'шт' }, isDemo: false, formatQuantity, productUnit }) };
      const root = node('root'); const app = renderer.createApp(component); app.mount(root); t.after(() => app.unmount());
      assert.match(textContent(root), /Код каталога/);
      assert.match(textContent(root), /pdf:local-catalog-id/);
      assert.doesNotMatch(textContent(root), /ID из 1С/);
    });
  });
});

test('unavailable file import is labelled unavailable and disables file selection', async t => {
  const { state, find } = await setup(t, { getCatalogImportCapabilities: async () => ({ file: { enabled: false }, adapter: { message: 'Адаптер не подключён' } }) });
  state.resetView(); await nextTick();
  assert.match(textContent(find(el => el.props?.class === 'import-source-active')), /недоступен/);
  assert.equal(find(el => el.props?.['data-testid'] === 'catalog-import-file').props.disabled, true);
});

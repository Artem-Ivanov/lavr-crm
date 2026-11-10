import test from 'node:test';
import assert from 'node:assert/strict';
import { createCatalogImportMethods } from '../src/data/http-catalog-import-api.js';

const key = '12345678-1234-4234-8234-123456789abc';
const stamp = '2026-11-02T09:00:00Z';
const counts = { new: 1, changed: 0, unchanged: 0, conflict: 0, missingUnit: 0, bundle: 0, selected: 1, skipped: 0 };
const row = { id: 'row-1', sourceProductId: null, page: 1, position: 1, sku: '', brand: '', name: 'Болт', unit: 'кг', selected: true, action: 'create', kind: 'ordinary', proposedStock: '0.955', proposedPriceKopecks: 10000, proposedAvailableStock: '0.955', before: null, targetProductId: null, categoryId: null, resolution: 'new', acknowledgeStandalone: false, issues: [] };
const batch = { id: 'batch-1', status: 'ready', version: 2, sourceMode: 'file', authorId: 'admin-1', fileHash: 'a'.repeat(64), pageCount: 1, fileName: 'catalog.pdf', sourceNamespace: 'pdf:main', observedAt: stamp, createdAt: stamp, summary: counts, error: null, report: null, rows: { items: [row], total: 1, offset: 0, limit: 50 } };
const capabilities = { file: { enabled: true, maxBytes: 5242880, maxPages: 400, maxRows: 50000, units: ['шт', 'кг', 'л', 'м'] }, adapter: { enabled: false, status: 'not_configured', message: 'Адаптер каталога не подключён.' }, sourceNamespace: 'file:main-stock:retail', rawRetentionDays: 7, rowRetentionDays: 30 };

function setup(responses) {
  const calls = [];
  const api = createCatalogImportMethods(async (path, options = {}) => {
    calls.push({ path, ...options });
    const data = responses.shift();
    return { data, etag: data?.version ? `"${data.version}"` : null };
  });
  return { api, calls };
}

test('PDF upload keeps multipart boundaries and sends explicit stock observation, price acknowledgement and attempt key', async () => {
  const { api, calls } = setup([batch]);
  const file = new File(['%PDF-1.7\nexample'], 'catalog.pdf', { type: 'application/pdf' });
  assert.deepEqual(await api.uploadCatalogImport({ file, observedAt: stamp, priceConfirmed: true }, key), batch);
  assert.equal(calls[0].path, '/admin/catalog/imports');
  assert.equal(calls[0].method, 'POST');
  assert.equal(calls[0].protectedMutation, true);
  assert.equal(calls[0].headers['Idempotency-Key'], key);
  assert.equal(calls[0].headers['Content-Type'], undefined);
  assert.equal(calls[0].body.get('file').name, 'catalog.pdf');
  assert.equal(calls[0].body.get('observedAt'), stamp);
  assert.equal(calls[0].body.get('priceConfirmed'), 'true');
});

test('bounded preview preserves filter/page and accepts empty source metadata with exact decimal stocks', async () => {
  const page = { ...batch, rows: { ...batch.rows, offset: 50, total: 51 } };
  const { api, calls } = setup([capabilities, page]);
  assert.deepEqual(await api.getCatalogImportCapabilities(), capabilities);
  assert.deepEqual(await api.getCatalogImport('batch-1', { offset: 50, limit: 50, filter: 'missing_unit', query: 'Болт 1' }), page);
  const url = new URL(calls[1].path, 'https://example.test');
  assert.equal(url.searchParams.get('offset'), '50');
  assert.equal(url.searchParams.get('filter'), 'missing_unit');
  assert.equal(url.searchParams.get('query'), 'Болт 1');
});

test('decisions and apply carry optimistic version and mutation protection; apply preserves retry key', async () => {
  const { api, calls } = setup([batch, batch]);
  await api.updateCatalogImportDecisions('batch-1', { rowIds: ['row-1'], unit: 'кг', selected: true }, 1);
  await api.applyCatalogImport('batch-1', { acknowledgeSkipped: true }, 2, key);
  assert.equal(calls[0].method, 'PATCH');
  assert.equal(calls[0].headers['If-Match'], '"1"');
  assert.equal(calls[0].protectedMutation, true);
  assert.deepEqual(calls[0].body, { rowIds: ['row-1'], unit: 'кг', selected: true });
  assert.equal(calls[1].headers['Idempotency-Key'], key);
  assert.equal(calls[1].headers['If-Match'], '"2"');
  assert.equal(calls[1].protectedMutation, true);
});

test('invalid files, dates, scope, ids, versions and overflowing previews never reach protected mutations', async () => {
  const { api, calls } = setup([]);
  const file = new File(['x'], 'catalog.pdf', { type: 'application/pdf' });
  await assert.rejects(api.uploadCatalogImport({ file, observedAt: '2026-11-02T12:00', priceConfirmed: true }, key));
  await assert.rejects(api.uploadCatalogImport({ file, observedAt: stamp, priceConfirmed: false }, key));
  await assert.rejects(api.uploadCatalogImport({ file: new File([], 'catalog.pdf'), observedAt: stamp, priceConfirmed: true }, key));
  await assert.rejects(api.uploadCatalogImport({ file: new File([new Uint8Array(5242881)], 'catalog.pdf'), observedAt: stamp, priceConfirmed: true }, key));
  await assert.rejects(api.updateCatalogImportDecisions('batch-1', { rowIds: ['row-1'], filter: 'all', unit: 'кг' }, 1));
  await assert.rejects(api.updateCatalogImportDecisions('batch-1', { rowIds: ['row-1'], unit: 'тонна' }, 1));
  await assert.rejects(api.applyCatalogImport('../other', { acknowledgeSkipped: false }, 1, key));
  await assert.rejects(api.refreshCatalogImport('batch-1', 0));
  await assert.rejects(api.getCatalogImport('batch-1', { limit: 50001 }));
  assert.equal(calls.length, 0);
});

test('malformed successful response or mismatched ETag is rejected instead of rendering trusted preview', async () => {
  for (const altered of [
    { ...batch, summary: { ...counts, selected: -1 } },
    { ...batch, rows: { ...batch.rows, items: [{ ...row, proposedStock: '0.9555' }] } },
    { ...batch, rows: { ...batch.rows, items: [{ ...row, proposedPriceKopecks: Number.MAX_SAFE_INTEGER + 1 }] } },
  ]) {
    const { api } = setup([altered]);
    await assert.rejects(api.getCatalogImport('batch-1'), { code: 'INVALID_RESPONSE' });
  }
  const api = createCatalogImportMethods(async () => ({ data: batch, etag: '"999"' }));
  await assert.rejects(api.getCatalogImport('batch-1'), { code: 'INVALID_RESPONSE' });
});

test('saved report provenance is validated before rendering or reopening history', async () => {
  const provenance = { appliedAt: stamp, observedAt: stamp, authorId: 'admin-1', fileHash: 'a'.repeat(64), sourceNamespace: 'pdf:main', createdCount: 1, updatedCount: 0, unchangedCount: 0, skippedCount: 0 };
  const applied = { ...batch, status: 'applied', report: provenance };
  const { api } = setup([applied]);
  assert.deepEqual(await api.getCatalogImport('batch-1'), applied);
  const longNamespace = { ...applied, sourceNamespace: 's'.repeat(160), report: { ...provenance, sourceNamespace: 's'.repeat(160) } };
  for (const invalid of [{ authorId: null }, { authorId: '' }, { fileHash: '' }, { fileHash: 'z'.repeat(64) }, { fileHash: 'a'.repeat(65) }, { sourceNamespace: null }, { sourceNamespace: 's'.repeat(161) }]) {
    const malformed = { ...batch, report: { ...provenance, ...invalid } };
    const { api } = setup([malformed, { items: [malformed], total: 1, offset: 0, limit: 10 }]);
    await assert.rejects(api.getCatalogImport('batch-1'), { code: 'INVALID_RESPONSE' });
    await assert.rejects(api.getCatalogImports(), { code: 'INVALID_RESPONSE' });
  }
  assert.deepEqual(await setup([longNamespace]).api.getCatalogImport('batch-1'), longNamespace);
});


test('source-neutral adapter previews, decisions, reports and mixed history retain absent PDF provenance', async () => {
  const adapter = { ...batch, id: 'adapter-1', sourceMode: 'adapter', sourceNamespace: 'adapter:test', fileName: '', fileHash: '', pageCount: 0, rows: { ...batch.rows, items: [{ ...row, page: null, sourceProductId: 'stable-id' }] } };
  const applied = { ...adapter, status: 'applied', report: { appliedAt: stamp, observedAt: stamp, authorId: 'admin-1', fileHash: '', sourceNamespace: 'adapter:test', createdCount: 1, updatedCount: 0, unchangedCount: 0, skippedCount: 0 } };
  const history = { items: [applied, batch], total: 2, offset: 0, limit: 10 };
  const { api } = setup([adapter, adapter, applied, history]);
  assert.deepEqual(await api.getCatalogImport(adapter.id), adapter);
  assert.deepEqual(await api.updateCatalogImportDecisions(adapter.id, { rowIds: [row.id], unit: 'кг' }, 1), adapter);
  assert.deepEqual(await api.applyCatalogImport(adapter.id, { acknowledgeSkipped: true }, 2, key), applied);
  assert.deepEqual(await api.getCatalogImports(), history);
  for (const altered of [
    { ...batch, fileHash: '' },
    { ...batch, rows: { ...batch.rows, items: [{ ...row, page: null }] } },
    { ...batch, rows: { ...batch.rows, items: [{ ...row, page: 2 }] } },
    { ...adapter, sourceMode: 'unknown' },
    { ...adapter, sourceNamespace: 'a'.repeat(161) },
    { ...adapter, rows: { ...adapter.rows, items: [{ ...adapter.rows.items[0], unit: 'тонна' }] } },
    { ...adapter, rows: { ...adapter.rows, items: [{ ...adapter.rows.items[0], proposedPriceKopecks: 1.5 }] } },
  ]) await assert.rejects(setup([altered]).api.getCatalogImport(altered.id), { code: 'INVALID_RESPONSE' });
});

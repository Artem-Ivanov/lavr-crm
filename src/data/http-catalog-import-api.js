import { UNITS, MAX_STOCK, quantityMilli } from '../quantity.js';

const BASE = '/admin/catalog/imports';
const MAX_BYTES = 5 * 1024 * 1024;
export const CATALOG_IMPORT_FILTERS = Object.freeze(['all', 'ready', 'issues', 'missing_unit', 'bundles', 'selected', 'skipped']);
const STATES = ['queued', 'processing', 'parsing', 'preparing', 'ready', 'applied', 'error', 'failed', 'expired'];
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value, max = 240) => typeof value === 'string' && value.length <= max;
const integer = (value, min = 0, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && value >= min && value <= max;
const id = value => text(value, 128) && value.length > 0 && !/[\u0000-\u001f\u007f/\\]/.test(value) && value !== '.' && value !== '..';
const nullableId = value => value === null || id(value);
const date = value => text(value, 80) && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const unit = value => value === null || UNITS.includes(value);
const decimal = value => {
  if (typeof value !== 'string' || !/^\d+(?:\.\d{1,3})?$/.test(value)) return false;
  try { quantityMilli(value, { max: MAX_STOCK, input: true }); return true; } catch { return false; }
};
const list = (value, check, max) => Array.isArray(value) && value.length <= max && value.every(check);
const issue = value => object(value) && text(value.code, 100) && text(value.message, 2000);
const before = value => value === null || object(value) && decimal(value.stock) && decimal(value.reserved)
  && decimal(value.availableStock) && unit(value.unit) && integer(value.priceKopecks);
const importRow = (value, sourceMode, pageCount) => object(value) && id(value.id)
  && (sourceMode === 'file' ? integer(value.page, 1, pageCount) : value.page === null)
  && (sourceMode === 'adapter' ? text(value.sourceProductId, 240) && value.sourceProductId.length > 0 : value.sourceProductId === null) && integer(value.position, 1, 50000)
  && text(value.sku, 1000) && text(value.brand, 1000) && text(value.name, 4000) && unit(value.unit)
  && typeof value.selected === 'boolean' && ['create', 'new', 'update', 'unchanged', 'review', 'skip'].includes(value.action)
  && ['ordinary', 'bundle', 'component'].includes(value.kind) && (value.proposedStock === null || decimal(value.proposedStock))
  && (value.proposedPriceKopecks === null || integer(value.proposedPriceKopecks))
  && (value.proposedAvailableStock === null || decimal(value.proposedAvailableStock)) && before(value.before)
  && nullableId(value.targetProductId) && nullableId(value.categoryId)
  && ['new', 'link', 'skip', 'auto', null].includes(value.resolution)
  && typeof value.acknowledgeStandalone === 'boolean' && list(value.issues, issue, 100);
const summary = value => object(value) && ['new', 'changed', 'unchanged', 'conflict', 'missingUnit', 'bundle', 'selected', 'skipped'].every(key => integer(value[key], 0, 50000));
const fileHash = (value, sourceMode) => typeof value === 'string' && (sourceMode === 'file' ? /^[a-f0-9]{64}$/.test(value) : value === '');
const report = (value, sourceMode) => value === null || object(value) && ['createdCount', 'updatedCount', 'unchangedCount', 'skippedCount'].every(key => integer(value[key], 0, 50000)) && date(value.appliedAt) && date(value.observedAt)
  && id(value.authorId) && fileHash(value.fileHash, sourceMode) && text(value.sourceNamespace, 160);
const packageMeta = value => object(value) && id(value.id) && STATES.includes(value.status) && integer(value.version, 1)
  && ['file', 'adapter'].includes(value.sourceMode) && id(value.authorId) && fileHash(value.fileHash, value.sourceMode)
  && integer(value.pageCount, 0, value.sourceMode === 'file' ? 400 : 0)
  && text(value.fileName, 255) && text(value.sourceNamespace, 160) && date(value.observedAt) && date(value.createdAt)
  && summary(value.summary) && (value.error === null || issue(value.error)) && report(value.report, value.sourceMode);

function invalid() { return Object.assign(new Error('API вернул некорректные данные импорта. Повторите загрузку.'), { code: 'INVALID_RESPONSE' }); }
function checked({ data, etag }, check) {
  if (!check(data) || etag != null && data.version !== undefined && etag !== `"${data.version}"`) throw invalid();
  return data;
}
function pathId(value) { if (!id(value)) throw new Error('Некорректный идентификатор импорта.'); return encodeURIComponent(value); }
function versionHeader(version) { if (!integer(version, 1)) throw new Error('Обновите версию предварительного просмотра.'); return { 'If-Match': `"${version}"` }; }
function attemptHeader(key) {
  if (typeof key !== 'string' || !/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(key)) throw new Error('Некорректный ключ попытки импорта.');
  return { 'Idempotency-Key': key };
}
function pageOptions({ offset = 0, limit = 50 } = {}) {
  if (!integer(offset) || !integer(limit, 1, 100)) throw new Error('Некорректная страница импорта.');
  return { offset, limit };
}
function detailsCheck(data) {
  return packageMeta(data) && object(data.rows) && integer(data.rows.total, 0, 50000) && integer(data.rows.offset)
    && integer(data.rows.limit, 1, 100) && list(data.rows.items, row => importRow(row, data.sourceMode, data.pageCount), data.rows.limit)
    && data.rows.items.length <= Math.max(0, data.rows.total - data.rows.offset);
}

export function createCatalogImportMethods(request) {
  return {
    async getCatalogImportCapabilities() {
      return checked(await request(`${BASE}/capabilities`), value => object(value) && object(value.file) && typeof value.file.enabled === 'boolean'
        && integer(value.file.maxBytes, 1, MAX_BYTES) && integer(value.file.maxPages, 1, 400) && integer(value.file.maxRows, 1, 50000)
        && list(value.file.units, candidate => UNITS.includes(candidate), UNITS.length) && value.file.units.length > 0
        && object(value.adapter) && typeof value.adapter.enabled === 'boolean' && text(value.adapter.status, 100) && text(value.adapter.message, 2000)
        && text(value.sourceNamespace, 160) && integer(value.rawRetentionDays, 1) && integer(value.rowRetentionDays, 1));
    },
    async getCatalogImports(options = {}) {
      const { offset, limit } = pageOptions({ limit: 10, ...options });
      const params = new URLSearchParams({ offset, limit });
      return checked(await request(`${BASE}?${params}`), value => object(value) && integer(value.total) && value.offset === offset && value.limit === limit && list(value.items, packageMeta, limit));
    },
    async uploadCatalogImport({ file, observedAt, priceConfirmed }, key) {
      if (!(file instanceof Blob) || !text(file.name, 255) || !/\.pdf$/i.test(file.name) || !file.size || file.size > MAX_BYTES) throw new Error('Выберите PDF размером до 5 МиБ.');
      if (!date(observedAt)) throw new Error('Укажите дату и время состояния склада.');
      if (priceConfirmed !== true) throw new Error('Подтвердите цену в рублях за выбранную единицу.');
      const body = new FormData();
      body.append('file', file); body.append('observedAt', observedAt); body.append('priceConfirmed', 'true');
      return checked(await request(BASE, { method: 'POST', body, headers: attemptHeader(key), protectedMutation: true }), detailsCheck);
    },
    async getCatalogImport(batchId, { offset = 0, limit = 50, filter = 'all', query = '' } = {}) {
      pageOptions({ offset, limit });
      if (!CATALOG_IMPORT_FILTERS.includes(filter) || !text(query, 120)) throw new Error('Некорректный фильтр импорта.');
      const params = new URLSearchParams({ offset, limit, filter, query });
      const result = checked(await request(`${BASE}/${pathId(batchId)}?${params}`), detailsCheck);
      if (result.rows.offset !== offset || result.rows.limit !== limit) throw invalid();
      return result;
    },
    async updateCatalogImportDecisions(batchId, decisions, version) {
      const keys = ['rowIds', 'filter', 'query', 'selected', 'unit', 'categoryId', 'resolution', 'productId', 'acknowledgeStandalone'];
      if (!object(decisions) || Object.keys(decisions).some(key => !keys.includes(key))) throw new Error('Некорректное решение по строкам.');
      const explicit = Array.isArray(decisions.rowIds);
      if (explicit ? !list(decisions.rowIds, id, 1000) || !decisions.rowIds.length || new Set(decisions.rowIds).size !== decisions.rowIds.length || decisions.filter !== undefined
        : !CATALOG_IMPORT_FILTERS.includes(decisions.filter)) throw new Error('Выберите строки либо весь результат фильтра.');
      if (decisions.query !== undefined && !text(decisions.query, 120)
        || decisions.unit !== undefined && !unit(decisions.unit)
        || decisions.categoryId !== undefined && !nullableId(decisions.categoryId)
        || decisions.productId !== undefined && !nullableId(decisions.productId)
        || decisions.selected !== undefined && typeof decisions.selected !== 'boolean'
        || decisions.acknowledgeStandalone !== undefined && typeof decisions.acknowledgeStandalone !== 'boolean'
        || decisions.resolution !== undefined && !['new', 'link', 'skip'].includes(decisions.resolution)) throw new Error('Проверьте параметры решения.');
      return checked(await request(`${BASE}/${pathId(batchId)}/decisions`, { method: 'PATCH', body: decisions, headers: versionHeader(version), protectedMutation: true }), detailsCheck);
    },
    async refreshCatalogImport(batchId, version) {
      return checked(await request(`${BASE}/${pathId(batchId)}/refresh`, { method: 'POST', body: {}, headers: versionHeader(version), protectedMutation: true }), detailsCheck);
    },
    async applyCatalogImport(batchId, { acknowledgeSkipped = false } = {}, version, key) {
      if (typeof acknowledgeSkipped !== 'boolean') throw new Error('Подтвердите отложенные строки.');
      return checked(await request(`${BASE}/${pathId(batchId)}/apply`, { method: 'POST', body: { acknowledgeSkipped }, headers: { ...versionHeader(version), ...attemptHeader(key) }, protectedMutation: true }), detailsCheck);
    },
  };
}

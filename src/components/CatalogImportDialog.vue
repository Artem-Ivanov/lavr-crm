<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue';
import AppIcon from './AppIcon.vue';
import { UNITS } from '../quantity.js';

const props = defineProps({ api: { type: Object, required: true }, categories: { type: Array, default: () => [] } });
const emit = defineEmits(['changed', 'staff-error']);
const dialog = ref(null);
const capabilities = ref(null);
const history = ref([]);
const batch = ref(null);
const file = ref(null);
const observedAt = ref('');
const priceConfirmed = ref(false);
const error = ref('');
const busy = ref(false);
const loading = ref(false);
const filter = ref('all');
const query = ref('');
const offset = ref(0);
const scope = ref('selected');
const appliedView = ref(null);
const markedRows = ref([]);
const bulkUnit = ref('');
const bulkCategory = ref('');
const bulkConfirmed = ref(false);
const skipsConfirmed = ref(false);
const mapping = reactive({ rowId: '', query: '', productId: '', items: [], busy: false });
let opener;
let generation = 0;
let detailRequest = 0;
let poll;
let uploadKey;
let applyKey;
let mappingRequest = 0;
const filters = [['all', 'Все строки'], ['ready', 'Подготовленные'], ['issues', 'Требует разбора'], ['missing_unit', 'Без единицы'], ['bundles', 'Наборы и компоненты'], ['selected', 'Включённые'], ['skipped', 'Отложенные']];
const stateLabels = { queued: 'Ожидает разбора', processing: 'Разбираем PDF', parsing: 'Разбираем PDF', preparing: 'Готовим сверку', ready: 'Готов к сверке', applied: 'Применён', failed: 'Ошибка разбора', error: 'Ошибка разбора', expired: 'Срок хранения предпросмотра истёк' };
const actionLabels = { create: 'Новая карточка', new: 'Новая карточка', update: 'Обновление', unchanged: 'Без изменений', review: 'Нужна проверка', skip: 'Отложено' };
const ready = computed(() => batch.value?.status === 'ready');
const processing = computed(() => ['queued', 'processing', 'parsing', 'preparing'].includes(batch.value?.status));
const rows = computed(() => batch.value?.rows?.items || []);
const total = computed(() => batch.value?.rows?.total || 0);
const pageLimit = computed(() => batch.value?.rows?.limit || 50);
const markedIds = computed(() => rows.value.filter(row => markedRows.value.includes(row.id)).map(row => row.id));
const bulkCount = computed(() => scope.value === 'selected' ? markedIds.value.length : total.value);
const viewStale = computed(() => !appliedView.value || appliedView.value.filter !== filter.value || appliedView.value.query !== query.value.trim());
const mutationsDisabled = computed(() => busy.value || loading.value || viewStale.value);
watch([filter, query], () => { bulkConfirmed.value = false; }, { flush: 'sync' });
const money = value => value == null ? 'Не указана' : new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 2 }).format(value / 100);
const quantity = value => value == null ? 'Не указан' : String(value).replace('.', ',');
const date = value => new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'short', timeZone: 'Europe/Moscow' }).format(new Date(value));
const categoryName = id => props.categories.find(category => category.id === id)?.name || 'Неразобранное';
function stopPoll() { clearTimeout(poll); poll = undefined; }
function setError(issue) {
  error.value = issue?.message || 'Не удалось выполнить импорт. Повторите запрос.';
  if ([401, 403].includes(issue?.status)) emit('staff-error', issue);
  nextTick(() => dialog.value?.querySelector('[role="alert"]')?.focus());
}
function changedUpload() { uploadKey = undefined; }
function chooseFile(event) { file.value = event.target.files?.[0] || null; changedUpload(); }
function options(nextOffset = offset.value, view = appliedView.value || { filter: 'all', query: '' }) { return { offset: nextOffset, limit: 50, ...view }; }
function markRow(row, checked) { markedRows.value = checked ? [...markedRows.value, row.id] : markedRows.value.filter(id => id !== row.id); }

async function loadBatch(id, nextOffset = offset.value, view) {
  const requested = options(nextOffset, view);
  const current = generation;
  const request = ++detailRequest;
  loading.value = true;
  stopPoll();
  try {
    const result = await props.api.getCatalogImport(id, requested);
    if (current !== generation || request !== detailRequest) return;
    if (result.status === 'applied' && batch.value?.status !== 'applied') emit('changed');
    batch.value = result; offset.value = result.rows.offset;
    appliedView.value = { filter: requested.filter, query: requested.query }; markedRows.value = []; bulkConfirmed.value = false;
    if (processing.value && dialog.value?.open) poll = setTimeout(() => loadBatch(id), 1800);
  } catch (issue) { if (current === generation && request === detailRequest) setError(issue); }
  finally { if (current === generation && request === detailRequest) loading.value = false; }
}
async function loadHistory() {
  const current = generation;
  try {
    const result = await props.api.getCatalogImports({ limit: 10 });
    if (current === generation) history.value = result.items;
  } catch (issue) { if (current === generation) setError(issue); }
}
async function open(event) {
  opener = event?.currentTarget || document.activeElement;
  const current = ++generation;
  error.value = ''; loading.value = true;
  await nextTick();
  if (current !== generation) return;
  if (!dialog.value.open) dialog.value.showModal();
  dialog.value.querySelector('.dialog-close')?.focus();
  try {
    const caps = await props.api.getCatalogImportCapabilities();
    if (current !== generation) return;
    capabilities.value = caps;
    await loadHistory();
    if (current !== generation) return;
    if (batch.value) await loadBatch(batch.value.id);
  } catch (issue) { if (current === generation) setError(issue); }
  finally { if (current === generation) loading.value = false; }
}
async function close() {
  ++generation; ++detailRequest; ++mappingRequest; stopPoll();
  busy.value = false; loading.value = false; mapping.busy = false;
  dialog.value?.close();
  await nextTick();
  if (opener?.isConnected) opener.focus({ preventScroll: true });
}
function resetView() {
  stopPoll(); ++detailRequest; appliedView.value = null; markedRows.value = []; batch.value = null; filter.value = 'all'; query.value = ''; offset.value = 0;
  skipsConfirmed.value = false; bulkConfirmed.value = false; applyKey = undefined; mapping.rowId = '';
}
async function selectBatch(id) { resetView(); await loadBatch(id, 0); }
async function upload() {
  if (busy.value) return;
  const current = generation;
  busy.value = true; error.value = '';
  try {
    uploadKey ||= crypto.randomUUID();
    const result = await props.api.uploadCatalogImport({ file: file.value, observedAt: observedAt.value ? `${observedAt.value}:00+03:00` : '', priceConfirmed: priceConfirmed.value }, uploadKey);
    if (current !== generation) return;
    resetView(); batch.value = result; uploadKey = undefined;
    await loadHistory();
    if (current !== generation) return;
    await loadBatch(result.id, 0);
  } catch (issue) { if (current === generation) setError(issue); }
  finally { if (current === generation) busy.value = false; }
}
async function mutate(run) {
  if (mutationsDisabled.value || !ready.value) return;
  const current = generation;
  busy.value = true; error.value = ''; stopPoll(); ++detailRequest;
  try {
    const result = await run();
    if (current !== generation) return;
    batch.value = result; applyKey = undefined; skipsConfirmed.value = false;
    await loadBatch(result.id, offset.value);
  } catch (issue) {
    if (current === generation) {
      setError(issue);
      if ([409, 412].includes(issue?.status)) error.value += ' Обновите сверку перед применением.';
    }
  } finally { if (current === generation) busy.value = false; }
}
function decide(row, fields) { return mutate(() => props.api.updateCatalogImportDecisions(batch.value.id, { rowIds: [row.id], ...fields }, batch.value.version)); }
async function bulk(field, value) {
  if (mutationsDisabled.value || !value || !bulkCount.value) return;
  const current = generation;
  if (scope.value === 'filter' && !bulkConfirmed.value) { setError(new Error('Подтвердите изменение всех строк текущего фильтра.')); return; }
  const selection = scope.value === 'selected' ? { rowIds: markedIds.value } : { ...appliedView.value };
  await mutate(() => props.api.updateCatalogImportDecisions(batch.value.id, { ...selection, [field]: value }, batch.value.version));
  if (current === generation) bulkConfirmed.value = false;
}
async function refresh() { await mutate(() => props.api.refreshCatalogImport(batch.value.id, batch.value.version)); }
async function apply() {
  if (mutationsDisabled.value || !ready.value) return;
  const current = generation;
  busy.value = true; error.value = ''; stopPoll(); ++detailRequest;
  try {
    applyKey ||= crypto.randomUUID();
    const result = await props.api.applyCatalogImport(batch.value.id, { acknowledgeSkipped: skipsConfirmed.value }, batch.value.version, applyKey);
    if (current !== generation) return;
    batch.value = result; applyKey = undefined; emit('changed'); await loadHistory();
    if (current !== generation) return;
    await nextTick();
    if (current !== generation) return;
    dialog.value?.querySelector('[data-testid="catalog-import-report"]')?.focus();
  } catch (issue) { if (current === generation) setError(issue); }
  finally { if (current === generation) busy.value = false; }
}
function changeFilter() { offset.value = 0; bulkConfirmed.value = false; if (batch.value) return loadBatch(batch.value.id, 0, { filter: filter.value, query: query.value.trim() }); }
function startMapping(row) { mapping.rowId = row.id; mapping.query = (row.sku || row.name).slice(0, 120); mapping.productId = row.targetProductId || ''; mapping.items = []; searchMapping(); }
async function searchMapping() {
  const current = generation; const request = ++mappingRequest;
  mapping.busy = true; error.value = '';
  try {
    const result = await props.api.getAdminProducts({ query: mapping.query }, { limit: 10, offset: 0 });
    if (current === generation && request === mappingRequest) mapping.items = result.items;
  } catch (issue) { if (current === generation && request === mappingRequest) setError(issue); }
  finally { if (current === generation && request === mappingRequest) mapping.busy = false; }
}
async function linkRow(row) {
  if (mutationsDisabled.value || !mapping.productId) return;
  const current = generation;
  const rowId = mapping.rowId;
  await decide(row, { resolution: 'link', productId: mapping.productId, selected: true });
  if (current === generation && mapping.rowId === rowId && !error.value) mapping.rowId = '';
}
onBeforeUnmount(() => { ++generation; ++detailRequest; ++mappingRequest; stopPoll(); });
defineExpose({ open, close });
</script>

<template>
  <dialog ref="dialog" class="detail-dialog catalog-import-dialog" aria-labelledby="catalog-import-title" @cancel.prevent="close">
    <button type="button" class="dialog-close" aria-label="Закрыть импорт" @click="close"><AppIcon name="close" /></button>
    <span class="eyebrow">КАТАЛОГ / ИМПОРТ</span>
    <h2 id="catalog-import-title">Импорт номенклатуры<span class="red-period">.</span></h2>
    <p class="import-intro">Загрузите PDF, проверьте цены и физические остатки, затем примените выбранные строки. Резервы заказов сохраняются.</p>
    <div v-if="capabilities" class="import-sources" aria-label="Источники импорта">
      <span class="import-source-active"><AppIcon name="box" /> Файл PDF · {{ capabilities.file.enabled ? 'доступен' : 'недоступен' }}</span>
      <span>{{ capabilities.adapter.message }}</span>
    </div>
    <p v-if="error" class="form-error import-error" role="alert" tabindex="-1">{{ error }}</p>
    <p v-if="loading && !batch" role="status">Загружаем данные импорта…</p>

    <form v-if="!batch" class="import-upload" :aria-busy="busy" @submit.prevent="upload">
      <label>Отчёт номенклатуры, PDF<input data-testid="catalog-import-file" type="file" accept=".pdf,application/pdf" required :disabled="busy || !capabilities?.file.enabled" @change="chooseFile"></label>
      <p>До 5 МиБ, 400 страниц и 50 000 строк. Нужен текстовый PDF с колонками «Артикул», «Номенклатура», «Цена», «Остаток».</p>
      <label>Состояние склада на дату и время, Москва<input v-model="observedAt" data-testid="catalog-import-observed" type="datetime-local" required :disabled="busy" @input="changedUpload"></label>
      <label class="import-check"><input v-model="priceConfirmed" data-testid="catalog-import-price-confirmed" type="checkbox" :disabled="busy" @change="changedUpload"><span>Цена указана в рублях за единицу учёта. Если единица не задана, для нового товара применяется «шт».</span></label>
      <button type="submit" class="btn red" data-testid="catalog-import-upload" :disabled="busy || !capabilities?.file.enabled">{{ busy ? 'Загружаем…' : 'Загрузить и сверить' }}</button>
    </form>

    <template v-if="batch">
      <div class="import-package-heading"><div><h3>{{ batch.fileName || batch.sourceNamespace }}</h3><p v-if="batch.sourceMode === 'adapter'">Без исходного файла · Автор: {{ batch.authorId }}</p><p>Состояние склада: {{ date(batch.observedAt) }} МСК · {{ stateLabels[batch.status] }}</p></div><button type="button" class="btn outline" :disabled="busy" @click="resetView">Другой файл</button></div>
      <p v-if="processing" class="import-progress" role="status">Разбираем страницы и готовим сверку. Можно закрыть окно и вернуться к пакету из истории.</p>
      <div v-if="batch.error" class="form-error import-error" role="alert"><strong>Не удалось подготовить импорт.</strong> {{ batch.error.message }}</div>
      <div v-if="batch.report" class="import-report" data-testid="catalog-import-report" role="status" tabindex="-1">
        <h3>Импорт применён</h3><p>Автор: {{ batch.report.authorId }} · <template v-if="batch.sourceMode === 'file'">SHA-256: <code>{{ batch.report.fileHash }}</code></template><template v-else>Без исходного файла</template></p><p>Источник: {{ batch.report.sourceNamespace }} · Состояние склада: {{ date(batch.report.observedAt) }} МСК</p><p>{{ date(batch.report.appliedAt) }} МСК · добавлено {{ batch.report.createdCount }}, обновлено {{ batch.report.updatedCount }}, без изменений {{ batch.report.unchangedCount }}, отложено {{ batch.report.skippedCount }}.</p>
      </div>
      <div v-if="ready || batch.report" class="import-summary" aria-label="Итоги сверки">
        <span><strong>{{ batch.summary.new }}</strong> новые</span><span><strong>{{ batch.summary.changed }}</strong> изменения</span><span><strong>{{ batch.summary.unchanged }}</strong> без изменений</span><span><strong>{{ batch.summary.conflict }}</strong> требуют разбора</span><span><strong>{{ batch.summary.missingUnit }}</strong> без единицы</span><span><strong>{{ batch.summary.selected }}</strong> включены</span>
      </div>
      <template v-if="ready || batch.report">
        <form class="import-filters" @submit.prevent="changeFilter">
          <label>Показывать<select v-model="filter" :disabled="busy" data-testid="catalog-import-filter" @change="changeFilter"><option v-for="[value, label] in filters" :key="value" :value="value">{{ label }}</option></select></label>
          <label>Найти строку<input v-model="query" type="search" maxlength="120" :disabled="busy" placeholder="Артикул, название или бренд"></label>
          <button type="submit" class="btn outline" :disabled="busy">Найти</button>
        </form>
        <p v-if="viewStale && !loading" role="status">Нажмите «Найти», чтобы загрузить строки и подтвердить область назначения.</p>
        <fieldset v-if="ready" class="import-bulk" :disabled="mutationsDisabled || !rows.length">
          <legend>Массовое назначение</legend>
          <label>Область<select v-model="scope" @change="bulkConfirmed = false"><option value="selected">Отмеченные на текущей странице — {{ markedIds.length }} строк</option><option value="filter">Весь результат фильтра — {{ total }} строк</option></select></label>
          <label>Единица<select v-model="bulkUnit" data-testid="catalog-import-bulk-unit"><option value="">Выберите</option><option v-for="unit in UNITS" :key="unit" :value="unit">{{ unit }}</option></select></label>
          <button type="button" class="btn outline" :disabled="!bulkUnit || !bulkCount" data-testid="catalog-import-assign-unit" @click="bulk('unit', bulkUnit)">Назначить единицу</button>
          <label>Категория новых карточек<select v-model="bulkCategory"><option value="">Выберите</option><option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}</option></select></label>
          <button type="button" class="btn outline" :disabled="!bulkCategory || !bulkCount" @click="bulk('categoryId', bulkCategory)">Назначить категорию</button>
          <label v-if="scope === 'filter'" class="import-check import-wide"><input v-model="bulkConfirmed" type="checkbox"><span>Применить назначение ко всем {{ total }} строкам текущего фильтра, включая другие страницы.</span></label>
          <p class="import-wide">Отметки для назначения действуют только на текущей странице и сбрасываются после загрузки сверки. Они не меняют включение строк в импорт.</p>
          <p class="import-wide">При обновлении сверки и применении для строк без единицы сохраняется единица существующего товара, для новых применяется «шт». Для дробного остатка выберите «кг», «л» или «м» по фактической единице учёта. Фасовка в названии её не определяет.</p>
        </fieldset>
        <p id="catalog-import-scroll-help" class="import-scroll-help">Прокрутите таблицу по горизонтали, чтобы увидеть сверку и действия.</p>
        <div class="import-table-wrap" tabindex="0" role="region" aria-label="Сверка номенклатуры" aria-describedby="catalog-import-scroll-help" :aria-busy="loading || busy">
          <table class="import-table" data-testid="catalog-import-table">
            <colgroup><col class="col-check"><col class="col-check"><col class="col-product"><col class="col-unit"><col class="col-price"><col class="col-stock"><col class="col-resolution"></colgroup>
            <thead><tr><th scope="col">Для назначения</th><th scope="col">В импорт</th><th scope="col">Номенклатура</th><th scope="col">Единица</th><th scope="col">Цена, ₽/ед.</th><th scope="col">Остатки до → после</th><th scope="col">Проверка</th></tr></thead>
            <tbody><tr v-for="row in rows" :key="row.id" :data-import-row="row.id">
              <td><input type="checkbox" :aria-label="`Отметить для назначения ${row.name}`" :checked="markedRows.includes(row.id)" :disabled="!ready || mutationsDisabled" @change="markRow(row, $event.target.checked)"></td>
              <td><input type="checkbox" :aria-label="`Включить ${row.name}`" :checked="row.selected" :disabled="!ready || mutationsDisabled" @change="decide(row, { selected: $event.target.checked })"></td>
              <td><strong>{{ row.name }}</strong><small>Артикул: {{ row.sku || 'не указан' }} · Бренд: {{ row.brand || 'не указан' }}</small><small><template v-if="batch.sourceMode === 'file'">Страница {{ row.page }}, строка {{ row.position }}</template><template v-else>Строка {{ row.position }} · Код источника: {{ row.sourceProductId }}</template> · {{ actionLabels[row.action] }}</small><small>{{ row.before ? 'Категория существующей карточки сохранится' : `Категория: ${categoryName(row.categoryId)}` }}</small><small v-if="row.targetProductId">ID: {{ row.targetProductId }}</small></td>
              <td><select :value="row.unit || ''" :aria-label="`Единица ${row.name}`" :disabled="!ready || mutationsDisabled" @change="decide(row, { unit: $event.target.value || null })"><option value="">По умолчанию</option><option v-for="unit in UNITS" :key="unit" :value="unit">{{ unit }}</option></select></td>
              <td><span>{{ row.before ? money(row.before.priceKopecks) : 'Новая' }}</span><strong>→ {{ money(row.proposedPriceKopecks) }}</strong></td>
              <td><span>На складе: {{ row.before ? quantity(row.before.stock) : '—' }} → <b>{{ quantity(row.proposedStock) }}</b></span><small>Резерв: {{ row.before ? quantity(row.before.reserved) : '0' }} · сохраняется</small><small>Доступно: {{ row.before ? quantity(row.before.availableStock) : '—' }} → {{ quantity(row.proposedAvailableStock) }}</small></td>
              <td><ul v-if="row.issues.length" class="import-issues"><li v-for="issue in row.issues" :key="issue.code">{{ issue.message }}</li></ul><span v-else class="import-valid">Проверено</span>
                <details v-if="ready" class="import-resolution"><summary>Решение по строке</summary>
                  <p v-if="row.kind !== 'ordinary'">{{ row.kind === 'bundle' ? 'Набор' : 'Компонент набора' }}. Состав автоматически не списывается.</p>
                  <label v-if="row.kind !== 'ordinary'" class="import-check"><input type="checkbox" :checked="row.acknowledgeStandalone" :disabled="mutationsDisabled" @change="decide(row, { acknowledgeStandalone: $event.target.checked })"><span>Это самостоятельный товар с собственным остатком</span></label>
                  <button type="button" :disabled="mutationsDisabled" @click="decide(row, { resolution: 'new', selected: true })">Создать отдельную карточку</button>
                  <button type="button" :disabled="mutationsDisabled" @click="startMapping(row)">Связать с товаром</button>
                  <button type="button" :disabled="mutationsDisabled" @click="decide(row, { selected: false })">Отложить строку</button>
                  <form v-if="mapping.rowId === row.id" class="import-mapping" @submit.prevent="searchMapping"><label>Поиск товара<input v-model="mapping.query" maxlength="120" :disabled="mapping.busy" placeholder="Артикул или название"></label><button type="submit" :disabled="mapping.busy">Найти</button><label>Существующая карточка<select v-model="mapping.productId" :disabled="mapping.busy"><option value="">Выберите товар</option><option v-for="product in mapping.items" :key="product.id" :value="product.id">{{ product.sku || 'Без артикула' }} · {{ product.name }}</option></select></label><button type="button" :disabled="mutationsDisabled || !mapping.productId" @click="linkRow(row)">Подтвердить связь</button></form>
                </details>
              </td>
            </tr><tr v-if="!rows.length"><td colspan="7">По текущему фильтру строк нет.</td></tr></tbody>
          </table>
        </div>
        <nav v-if="total > pageLimit" class="import-pagination" aria-label="Страницы сверки"><button type="button" class="btn outline" :disabled="busy || loading || offset === 0" @click="loadBatch(batch.id, Math.max(0, offset - pageLimit))">Назад</button><span>{{ offset + 1 }}–{{ offset + rows.length }} из {{ total }}</span><button type="button" class="btn outline" :disabled="busy || loading || offset + pageLimit >= total" @click="loadBatch(batch.id, offset + pageLimit)">Далее</button></nav>
        <div v-if="ready" class="import-apply">
          <p>Включено {{ batch.summary.selected }} строк. Отложено {{ batch.summary.skipped }}. Резервы, фотографии и тексты существующих карточек сохранятся.</p>
          <label v-if="batch.summary.skipped" class="import-check"><input v-model="skipsConfirmed" type="checkbox" :disabled="busy" data-testid="catalog-import-ack-skips"><span>Отложенные строки сейчас не применяются; прежние цены и остатки этих товаров сохраняются.</span></label>
          <div><button type="button" class="btn outline" :disabled="mutationsDisabled" @click="refresh">Обновить сверку</button><button type="button" class="btn red" data-testid="catalog-import-apply" :disabled="mutationsDisabled || !batch.summary.selected || !!batch.summary.skipped && !skipsConfirmed" @click="apply">{{ busy ? 'Сохраняем…' : `Применить ${batch.summary.selected} строк` }}</button></div>
        </div>
      </template>
    </template>

    <section v-if="history.length" class="import-history" aria-label="История импорта"><h3>Последние пакеты</h3><ul><li v-for="item in history" :key="item.id"><button type="button" :disabled="busy || batch?.id === item.id" @click="selectBatch(item.id)"><strong>{{ item.fileName || item.sourceNamespace }}</strong><span>{{ date(item.createdAt) }} МСК · {{ stateLabels[item.status] }}</span></button></li></ul></section>
  </dialog>
</template>

<style scoped>
.catalog-import-dialog { width: min(1280px, calc(100% - 32px)); max-width: none; }
.catalog-import-dialog > .eyebrow { display: block; margin-bottom: 10px; }
.catalog-import-dialog h3 { font-size: 17px; font-weight: 600; overflow-wrap: anywhere; }
.catalog-import-dialog button { cursor: pointer; }
.catalog-import-dialog input, .catalog-import-dialog select { min-width: 0; max-width: 100%; min-height: 42px; padding: 9px 11px; border: 1px solid var(--line); background: white; font: inherit; color: var(--ink); }
.catalog-import-dialog input[type="checkbox"] { flex: 0 0 auto; width: 18px; min-height: 18px; accent-color: var(--red); }
.catalog-import-dialog label { display: grid; gap: 7px; font-size: 12px; line-height: 1.6; }
.catalog-import-dialog .import-check { display: flex; align-items: start; gap: 10px; }
.import-sources { display: flex; gap: 14px 24px; flex-wrap: wrap; margin: 19px 0; padding: 13px 16px; background: #f5f7f8; font-size: 12px; color: var(--muted); }
.import-source-active { display: inline-flex; align-items: center; gap: 7px; color: var(--ink); }
.import-sources .icon { width: 17px; height: 17px; }
.import-upload { display: grid; gap: 17px; max-width: 650px; margin: 24px 0; }
.import-upload .btn { justify-self: start; }
.import-error { margin: 16px 0; padding: 13px 16px; border-left: 3px solid #b4212b; background: #fff5f5; }
.catalog-import-dialog .import-error { color: #b4212b; }
.import-package-heading { display: flex; gap: 16px; align-items: center; justify-content: space-between; padding: 14px 0; }
.import-package-heading .btn, .import-pagination .btn, .import-filters .btn, .import-bulk .btn, .import-apply .btn { font-size: 12px; min-height: 42px; white-space: normal; }
.import-report { overflow-wrap: anywhere; }
.import-progress, .import-report { margin: 15px 0; padding: 16px; background: #eef7f1; border: 1px solid #d3e5d9; }
.import-summary { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; margin: 20px 0; }
.import-summary > span { display: grid; gap: 5px; padding: 12px; background: #f5f7f8; font-size: 11px; color: var(--muted); }
.import-summary strong { font-size: 22px; color: var(--ink); }
.import-filters { display: grid; grid-template-columns: 230px minmax(160px, 1fr) auto; gap: 13px; align-items: end; margin: 17px 0; }
.import-bulk { display: grid; grid-template-columns: minmax(160px, 1.3fr) minmax(80px, .5fr) auto minmax(130px, 1fr) auto; align-items: end; gap: 12px; border: 1px solid var(--line); padding: 16px; margin-bottom: 15px; }
.import-bulk legend { padding: 0 7px; font-size: 12px; font-weight: 600; }
.import-wide { grid-column: 1 / -1; }
.import-table-wrap { overflow: auto; width: 100%; max-height: 56dvh; border: 1px solid var(--line); }
.import-table { width: 100%; min-width: 1050px; table-layout: fixed; border-collapse: collapse; font-size: 11px; line-height: 1.6; }
.import-table .col-check { width: 65px; }.import-table .col-product { width: 27%; }.import-table .col-unit { width: 105px; }.import-table .col-price { width: 130px; }.import-table .col-stock { width: 195px; }.import-table .col-resolution { width: auto; }
.import-table th { position: sticky; top: 0; background: #f3f5f7; z-index: 1; text-align: left; font-weight: 600; }
.import-table th, .import-table td { padding: 12px 10px; border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: anywhere; }
.import-table td > strong, .import-table td > small, .import-table td > span { display: block; }
.import-table small { color: var(--muted); margin-top: 5px; }
.import-table select { width: 100%; font-size: 11px; }
.import-issues { padding-left: 15px; color: #9c512c; }
.import-valid { color: #357447; }
.import-resolution { margin-top: 9px; }
.import-resolution summary { cursor: pointer; color: var(--red); }
.import-resolution button { display: block; padding: 6px 0; margin-top: 5px; color: var(--ink); background: transparent; border: 0; text-align: left; text-decoration: underline; font-size: 11px; }
.import-resolution .import-check { margin-top: 9px; font-size: 11px; }
.import-mapping { margin-top: 12px; }
.import-mapping input { width: 100%; }
.import-pagination { display: flex; align-items: center; justify-content: space-between; gap: 15px; margin: 16px 0; font-size: 12px; }
.import-apply { margin-top: 20px; padding: 20px; border: 1px solid var(--line); }
.import-apply .import-check { margin: 13px 0; }
.import-apply > div { display: flex; gap: 12px; justify-content: end; margin-top: 17px; }
.import-history { margin-top: 30px; }
.import-history ul { list-style: none; padding: 0; margin-top: 10px; }
.import-history button { display: flex; justify-content: space-between; gap: 15px; width: 100%; padding: 12px 0; text-align: left; font-size: 12px; background: white; color: var(--ink); border: 0; border-bottom: 1px solid var(--line); }
.import-history button span { color: var(--muted); }
@media (max-width: 1000px) { .import-bulk { grid-template-columns: 1fr 1fr; }.import-bulk > label:first-of-type { grid-column: 1 / -1; }.import-summary { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 600px) { .catalog-import-dialog { width: calc(100% - 16px); padding: 22px 16px; }.catalog-import-dialog input, .catalog-import-dialog select { font-size: 16px; min-height: 44px; }.catalog-import-dialog input[type="checkbox"] { min-height: 18px; }.import-filters, .import-bulk { grid-template-columns: 1fr; }.import-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }.import-package-heading { align-items: start; flex-direction: column; }.import-apply { padding: 14px; }.import-apply > div { flex-direction: column; }.import-history button { flex-direction: column; gap: 3px; }.import-table-wrap { max-height: 60dvh; } }
</style>

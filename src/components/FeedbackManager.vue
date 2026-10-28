<script setup>
import { computed, nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { FEEDBACK_STATUSES } from '../feedback.js';
import { feedbackKey, clearFeedbackKey } from '../feedback-attempt.js';

const props = defineProps({ api: { type: Object, required: true }, session: { type: Object, required: true }, caseId: { type: String, default: '' } });
const emit = defineEmits(['staff-error', 'dirty', 'open-case']);
const items = ref([]);
const total = ref(0);
const offset = ref(0);
const limit = 24;
const query = ref('');
const queryDraft = ref('');
const status = ref('new');
const listLoading = ref(false);
const listError = ref('');
const selected = ref(null);
const detailLoading = ref(false);
const detailError = ref('');
const busy = ref(false);
const actionError = ref('');
const conflict = ref(false);
const note = ref('');
const contactMethod = ref('');
const resolution = ref('');
const preparing = ref(false);
const orderDraft = reactive({ name: '', phone: '', email: '', method: 'pickup', address: '' });
const orderBaseline = ref('');
const orderItems = ref([]);
const productQuery = ref('');
const products = ref([]);
const catalogLoading = ref(false);
const catalogError = ref('');
const catalogStale = ref(false);
const catalogOffset = ref(0);
const catalogTotal = ref(0);
let active = true;
let listRequest = 0;
let detailRequest = 0;
let catalogRequest = 0;
let actionRequest = 0;
const date = value => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(value));
const money = value => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 2 }).format(value / 100);
const isOwner = computed(() => selected.value && (props.session.user.role === 'admin' || selected.value.assignedTo?.id === props.session.user.id));
const editable = computed(() => selected.value?.status === 'in_progress' && isOwner.value);
const canClose = computed(() => selected.value?.status === 'new' || editable.value);
const orderTotal = computed(() => orderItems.value.reduce((sum, item) => sum + item.product.priceKopecks * item.quantity, 0));
const orderDirty = computed(() => preparing.value && (orderItems.value.length > 0 || JSON.stringify(orderDraft) !== orderBaseline.value));
const dirty = computed(() => busy.value || !!note.value || !!resolution.value || orderDirty.value);
const notificationLabel = computed(() => ({ queued: 'Ожидает отправки', running: 'Отправляется', done: 'Отправлено', error: 'Требует внимания', uncertain: 'Доставка не подтверждена', not_configured: 'Ожидает настройки Telegram' })[selected.value?.notification.state]);
const historyLabel = action => ({ 'feedback.created': 'Обращение принято', 'feedback.claimed': 'Взято в работу', 'feedback.closed': 'Закрыто без заказа', 'feedback.converted': 'Создан заказ', 'feedback.note_added': 'Заметка', note: 'Заметка' })[action] || action;
const methodLabel = method => ({ phone: 'Телефон', email: 'Email', messenger: 'Мессенджер' })[method] || '';
watch(dirty, value => emit('dirty', value), { immediate: true });
function staffError(error) { if (active && error.status === 401) emit('staff-error', error); }
async function loadList() {
  const request = ++listRequest;
  listLoading.value = true; listError.value = '';
  try {
    const result = await props.api.getFeedback({ query: query.value, status: status.value }, { offset: offset.value, limit });
    if (active && request === listRequest) { items.value = result.items; total.value = result.total; }
  } catch (error) { if (active && request === listRequest) { listError.value = error.message; items.value = []; staffError(error); } }
  finally { if (active && request === listRequest) listLoading.value = false; }
}
async function loadDetail() {
  const id = props.caseId;
  const request = ++detailRequest;
  if (!id) return;
  detailLoading.value = true; detailError.value = '';
  try {
    const result = await props.api.getFeedbackDetail(id);
    if (active && request === detailRequest && id === props.caseId) { selected.value = result; conflict.value = false; actionError.value = ''; }
  } catch (error) { if (active && request === detailRequest) { detailError.value = error.message; selected.value = null; staffError(error); } }
  finally { if (active && request === detailRequest) detailLoading.value = false; }
}
watch(() => props.caseId, () => {
  detailRequest++; actionRequest++; catalogRequest++;
  selected.value = null; detailLoading.value = false; detailError.value = ''; actionError.value = ''; conflict.value = false; busy.value = false;
  note.value = ''; contactMethod.value = ''; resolution.value = ''; preparing.value = false; orderItems.value = []; products.value = []; catalogLoading.value = false;
  if (props.caseId) loadDetail();
}, { immediate: true });
watch([query, status], () => { offset.value = 0; loadList(); });
loadList();
function search() { query.value = queryDraft.value.trim(); }
function page(delta) { offset.value += delta * limit; loadList(); }
async function mutate(operation, success) {
  if (busy.value || !selected.value || conflict.value) return;
  const id = selected.value.id;
  const request = ++actionRequest;
  busy.value = true; actionError.value = '';
  try {
    const result = await operation(selected.value);
    if (!active || request !== actionRequest || id !== props.caseId) return;
    selected.value = result;
    success?.();
    loadList();
  } catch (error) {
    if (!active || request !== actionRequest || id !== props.caseId) return;
    conflict.value = error.status === 412 || error.status === 409;
    actionError.value = conflict.value ? `${error.message} Ваши данные остались в форме. Загрузите актуальную карточку перед повторной попыткой.` : error.message;
    staffError(error);
    await nextTick(); document.querySelector('#feedback-action-error')?.focus();
  } finally { if (active && request === actionRequest) busy.value = false; }
}
function claim() { mutate(item => props.api.updateFeedbackStatus(item, { status: 'in_progress', resolution: '' })); }
function closeCase() { mutate(item => props.api.updateFeedbackStatus(item, { status: 'closed', resolution: resolution.value }), () => { resolution.value = ''; note.value = ''; preparing.value = false; }); }
function addNote() { mutate(item => props.api.addFeedbackNote(item, { text: note.value, contactMethod: contactMethod.value }), () => { note.value = ''; contactMethod.value = ''; }); }
function prepare() {
  Object.assign(orderDraft, { name: selected.value.name, phone: selected.value.phone, email: selected.value.email, method: 'pickup', address: '' });
  orderBaseline.value = JSON.stringify(orderDraft);
  preparing.value = true; catalogOffset.value = 0; productQuery.value = ''; loadProducts();
  nextTick(() => document.querySelector('[data-testid="feedback-order-name"]')?.focus());
}
async function loadProducts(reset = false) {
  if (reset) catalogOffset.value = 0;
  const request = ++catalogRequest;
  catalogLoading.value = true; catalogError.value = '';
  try {
    const result = await props.api.getCatalog({ query: productQuery.value.trim(), category: '', brand: '', inStock: true, sort: 'relevance' }, { offset: catalogOffset.value, limit });
    if (!active || request !== catalogRequest) return;
    products.value = result.items; catalogTotal.value = result.total; catalogStale.value = !!result.stale || !!result.readOnly;
    catalogError.value = result.issue || '';
  } catch (error) { if (active && request === catalogRequest) { catalogError.value = error.message; products.value = []; } }
  finally { if (active && request === catalogRequest) catalogLoading.value = false; }
}
function productsPage(delta) { catalogOffset.value += delta * limit; loadProducts(); }
function addProduct(product) {
  const found = orderItems.value.find(item => item.product.id === product.id);
  if (found) { if (found.quantity < Math.min(product.stock, 999)) found.quantity++; }
  else orderItems.value.push({ product, quantity: 1 });
}
async function refreshOrderProducts() {
  if (catalogLoading.value || busy.value) return;
  const request = ++catalogRequest;
  catalogLoading.value = true; catalogError.value = '';
  try {
    const results = await Promise.all(orderItems.value.map(item => props.api.getProduct(item.product.id)));
    if (!active || request !== catalogRequest) return;
    catalogStale.value = results.some(result => result.stale);
    const refreshed = new Map(results.map(result => [result.product.id, result.product]));
    orderItems.value = orderItems.value.map(item => ({ ...item, product: refreshed.get(item.product.id) || item.product }));
  } catch (error) { if (active && request === catalogRequest) { catalogError.value = error.message; catalogStale.value = true; } }
  finally { if (active && request === catalogRequest) catalogLoading.value = false; }
}
function createOrder() {
  const id = selected.value.id;
  const body = { customer: { name: orderDraft.name, phone: orderDraft.phone, email: orderDraft.email }, fulfillment: { method: orderDraft.method, address: orderDraft.method === 'delivery' ? orderDraft.address : '' }, items: orderItems.value.map(item => ({ productId: item.product.id, quantity: item.quantity, expectedUnitPriceKopecks: item.product.priceKopecks })) };
  mutate(async item => {
    if (catalogStale.value || catalogLoading.value || !body.items.length || !Number.isSafeInteger(orderTotal.value)) throw new Error('Добавьте товары и проверьте цены и остатки перед созданием заказа.');
    if (orderItems.value.some(entry => !Number.isSafeInteger(entry.quantity) || entry.quantity < 1 || entry.quantity > Math.min(entry.product.stock, 999))) throw new Error('Проверьте количество и остаток выбранных товаров.');
    const result = await props.api.createFeedbackOrder(item, body, await feedbackKey(body, `order:${id}`));
    try { clearFeedbackKey(`order:${id}`); } catch { /* Successful conversion is also protected by the linked order. */ }
    return result;
  }, () => { preparing.value = false; orderItems.value = []; note.value = ''; resolution.value = ''; });
}
onBeforeUnmount(() => { active = false; listRequest++; detailRequest++; catalogRequest++; actionRequest++; emit('dirty', false); });
</script>

<template>
  <div class="feedback-manager">
    <p v-if="api.mode === 'demo'" class="api-feedback">Демонстрационные обращения хранятся в памяти до перезагрузки страницы и не отправляются магазину.</p>
    <template v-if="caseId">
      <button type="button" class="text-link feedback-back" @click="emit('open-case', '')">← Все обращения</button>
      <p v-if="detailLoading" class="api-feedback" role="status">Загружаем обращение…</p>
      <div v-else-if="detailError" class="api-feedback" role="alert"><p>{{ detailError }}</p><button type="button" class="btn outline" @click="loadDetail">Повторить загрузку карточки</button></div>
      <article v-else-if="selected" class="feedback-detail" data-testid="feedback-detail" :aria-busy="busy">
        <div class="section-heading"><div><span class="eyebrow">{{ selected.type === 'request' ? 'ЗАПРОС ПОКУПАТЕЛЯ' : 'СВЯЖИТЕСЬ СО МНОЙ' }}</span><h2>Обращение № {{ selected.number }}</h2><p class="feedback-meta">{{ date(selected.createdAt) }} · МСК · {{ FEEDBACK_STATUSES[selected.status] }}</p></div><button type="button" class="btn outline" :disabled="busy || detailLoading" @click="loadDetail">Загрузить актуальную карточку</button></div>
        <div class="feedback-case-grid">
          <section class="feedback-panel" aria-label="Контакты обращения"><h3>{{ selected.name || 'Имя не указано' }}</h3><p v-if="selected.phone"><a :href="/^\+7\d{10}$/.test(selected.phone) ? `tel:${selected.phone}` : undefined">{{ selected.phone }}</a></p><p v-if="selected.email"><a :href="`mailto:${encodeURIComponent(selected.email)}`">{{ selected.email }}</a></p><label v-if="selected.messenger.service" class="feedback-messenger">{{ selected.messenger.service }} · скопируйте контакт<input :value="selected.messenger.address" readonly aria-label="Контакт мессенджера" @focus="$event.target.select()"></label><p class="feedback-message">{{ selected.message || 'Комментарий не указан.' }}</p></section>
          <section class="feedback-panel"><h3>Обработка</h3><p>Ответственный: <strong>{{ selected.assignedTo?.name || 'Не назначен' }}</strong></p><p>Telegram: {{ notificationLabel }}<span v-if="selected.notification.code"> ({{ selected.notification.code }})</span></p><p v-if="selected.resolution" class="feedback-message">Результат: {{ selected.resolution }}</p><div class="feedback-actions"><button v-if="selected.status === 'new'" type="button" class="btn red" :disabled="busy || conflict" @click="claim">Взять в работу</button><button v-if="editable && !preparing" type="button" class="btn outline" :disabled="busy || conflict" @click="prepare">Подготовить заказ</button><a v-if="selected.order" class="btn red" :href="`#crm?order=${encodeURIComponent(selected.order.id)}`">Открыть заказ № {{ selected.order.number }}</a></div><p v-if="selected.status === 'in_progress' && !isOwner" class="feedback-help">Изменения доступны ответственному сотруднику и администратору.</p></section>
        </div>
        <p v-if="actionError" id="feedback-action-error" tabindex="-1" class="form-error" role="alert">{{ actionError }}</p>
        <form v-if="editable" class="feedback-panel feedback-note" @submit.prevent="addNote"><label>Заметка<textarea v-model="note" rows="3" maxlength="2000" required :disabled="busy"></textarea></label><label>Способ связи<select v-model="contactMethod" aria-label="Способ связи" :disabled="busy"><option value="">Не указан</option><option v-if="selected.phone" value="phone">Телефон</option><option v-if="selected.email" value="email">Email</option><option v-if="selected.messenger.service" value="messenger">Мессенджер</option></select></label><button type="submit" class="btn outline" :disabled="busy || conflict">Добавить заметку</button></form>
        <section v-if="preparing && editable" class="feedback-panel feedback-order"><h3>Подготовка заказа</h3><p class="feedback-help">Укажите имя и российский телефон, даже если клиент оставил только email или мессенджер. Цена и остаток будут проверены при создании.</p><form @submit.prevent="createOrder"><fieldset :disabled="busy"><legend class="sr-only">Данные заказа из обращения</legend><div class="form-grid"><label>Имя покупателя<input v-model="orderDraft.name" data-testid="feedback-order-name" required maxlength="100" autocomplete="off"></label><label>Телефон покупателя<input v-model="orderDraft.phone" data-testid="feedback-order-phone" type="tel" required maxlength="30" autocomplete="off"></label><label class="full">Email покупателя<input v-model="orderDraft.email" type="email" maxlength="254" autocomplete="off"></label><label>Получение<select v-model="orderDraft.method" aria-label="Получение"><option value="pickup">Самовывоз</option><option value="delivery">Доставка</option></select></label><label v-if="orderDraft.method === 'delivery'">Адрес доставки<input v-model="orderDraft.address" required maxlength="500"></label></div></fieldset>
          <div class="feedback-selected-products"><div v-for="item in orderItems" :key="item.product.id" class="feedback-selected-product"><span><strong>{{ item.product.name }}</strong><small>{{ item.product.sku }} · {{ money(item.product.priceKopecks) }} · доступно {{ item.product.stock }}</small></span><label>Количество<input v-model.number="item.quantity" type="number" min="1" :max="Math.min(item.product.stock, 999)" required :disabled="busy || catalogLoading" :aria-label="`Количество ${item.product.name}`"></label><button type="button" class="btn outline" :disabled="busy || catalogLoading" :aria-label="`Удалить ${item.product.name}`" @click="orderItems = orderItems.filter(entry => entry !== item)">Удалить</button></div></div>
          <div class="feedback-actions"><strong>Товары: {{ money(orderTotal) }}</strong><button type="button" class="btn outline" :disabled="busy || catalogLoading || !orderItems.length" @click="refreshOrderProducts">Проверить цены и остатки</button><button type="submit" class="btn red" data-testid="feedback-order-submit" :disabled="busy || conflict || catalogLoading || catalogStale || !orderItems.length">{{ busy ? 'Создаём…' : api.mode === 'demo' ? 'Создать демозаказ из обращения' : 'Создать заказ из обращения' }}</button></div>
        </form>
        <form class="feedback-product-search" @submit.prevent="loadProducts(true)"><label>Найти товар в каталоге<input v-model="productQuery" maxlength="120" type="search" :disabled="busy"></label><button type="submit" class="btn outline" :disabled="busy || catalogLoading">Найти товары</button></form><p v-if="catalogLoading" role="status">Загружаем товары…</p><p v-if="catalogError" class="form-error" role="alert">{{ catalogError }}</p><p v-if="catalogStale" class="form-error">Каталог ожидает обновления. Проверьте цены и остатки перед созданием заказа.</p><div v-if="!catalogLoading" class="feedback-catalog"><div v-for="product in products" :key="product.id"><span><strong>{{ product.websiteTitle || product.name }}</strong><small>{{ product.brand }} · {{ product.sku }} · {{ money(product.priceKopecks) }} · {{ product.stock }} шт.</small></span><button type="button" class="btn outline" :disabled="busy || catalogStale || !product.stock || orderItems.some(item => item.product.id === product.id && item.quantity >= Math.min(product.stock, 999))" :aria-label="`Добавить ${product.name}`" @click="addProduct(product)">Добавить</button></div><p v-if="!products.length">Товары не найдены.</p></div><nav v-if="catalogTotal > limit" class="pagination" aria-label="Страницы товаров обращения"><button type="button" class="btn outline" :disabled="busy || catalogLoading || !catalogOffset" @click="productsPage(-1)">Назад</button><span>{{ catalogOffset + 1 }}–{{ Math.min(catalogOffset + limit, catalogTotal) }} из {{ catalogTotal }}</span><button type="button" class="btn outline" :disabled="busy || catalogLoading || catalogOffset + limit >= catalogTotal" @click="productsPage(1)">Далее</button></nav>
        </section>
        <form v-if="canClose" class="feedback-panel feedback-closure" @submit.prevent="closeCase"><label>Результат закрытия<textarea v-model="resolution" rows="2" required maxlength="2000" :disabled="busy" placeholder="Например, вопрос решён или клиент отказался"></textarea></label><button type="submit" class="btn outline danger" :disabled="busy || conflict">Закрыть без заказа</button></form>
        <section class="feedback-panel"><h3>История обращения</h3><ol v-if="selected.history.length" class="feedback-history"><li v-for="entry in selected.history" :key="entry.id"><small>{{ date(entry.createdAt) }} · МСК · {{ entry.staff?.name || 'Сайт' }}</small><strong>{{ historyLabel(entry.action) }}<span v-if="entry.contactMethod"> · {{ methodLabel(entry.contactMethod) }}</span></strong><p v-if="entry.text" class="feedback-message">{{ entry.text }}</p></li></ol><p v-else>Новых записей в истории пока нет.</p></section>
      </article>
    </template>
    <section v-else class="crm-orders"><div class="crm-toolbar"><h2>Обращения <span class="heading-count">{{ listLoading ? '—' : total }}</span></h2><form class="crm-search" @submit.prevent="search"><input v-model="queryDraft" type="search" maxlength="120" aria-label="Поиск обращений" placeholder="Номер или контакт"><button type="submit" aria-label="Найти обращение">→</button></form></div><div class="status-filters" role="group" aria-label="Статус обращения"><button v-for="[value, label] in [['', 'Все обращения'], ...Object.entries(FEEDBACK_STATUSES)]" :key="value" type="button" class="status-filter" :class="{ selected: value === status }" :aria-pressed="value === status" @click="status = value">{{ label }}</button></div><p v-if="listLoading" class="api-feedback" role="status">Загружаем обращения…</p><div v-else-if="listError" class="api-feedback" role="alert"><p>{{ listError }}</p><button type="button" class="btn outline" @click="loadList">Повторить загрузку обращений</button></div><div v-else-if="items.length" class="feedback-list"><a v-for="item in items" :key="item.id" :href="`#crm-feedback?case=${encodeURIComponent(item.id)}`"><span><strong>№ {{ item.number }} · {{ item.name || 'Имя не указано' }}</strong><small>{{ date(item.createdAt) }} · МСК · {{ item.type === 'request' ? 'Запрос' : 'Свяжитесь со мной' }}</small><span>{{ [item.phone, item.email, item.messenger.service ? `${item.messenger.service} / ${item.messenger.address}` : ''].filter(Boolean).join(' · ') }}</span><span class="feedback-list-message">{{ item.message || 'Без комментария' }}</span></span><span><strong>{{ FEEDBACK_STATUSES[item.status] }}</strong><small>{{ item.assignedTo?.name || 'Без ответственного' }}</small><small>{{ item.order ? `Заказ № ${item.order.number}` : item.resolution }}</small></span></a></div><p v-else class="api-feedback">Обращения не найдены. Измените поиск или дождитесь первого обращения.</p><nav v-if="total > limit" class="pagination" aria-label="Страницы обращений"><button type="button" class="btn outline" :disabled="listLoading || !offset" @click="page(-1)">Назад</button><span>{{ offset + 1 }}–{{ Math.min(offset + limit, total) }} из {{ total }}</span><button type="button" class="btn outline" :disabled="listLoading || offset + limit >= total" @click="page(1)">Далее</button></nav></section>
  </div>
</template>

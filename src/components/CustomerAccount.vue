<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { routeHash } from '../routes.js';
import { formatQuantity, snapshotLineTotal } from '../quantity.js';
import { ORDER_STATUSES } from '../order-labels.js';
import { FEEDBACK_STATUSES, FEEDBACK_STATUS_DESCRIPTIONS, formatFeedbackReference } from '../feedback.js';
const props = defineProps({ api: { type: Object, required: true }, session: { type: Object, required: true }, page: { type: String, required: true }, recordId: { type: String, default: '' }, embedded: Boolean });
const emit = defineEmits(['staff-error', 'feedback']);
const isOrders = computed(() => props.page !== 'account-feedback');
const items = ref([]); const selected = ref(null); const total = ref(0); const offset = ref(0); const busy = ref(false); const error = ref('');
let request = 0; let active = true;
const money = value => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 2 }).format(value / 100);
const date = value => new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeZone: 'Europe/Moscow' }).format(new Date(value));
async function load() {
  const current = ++request; items.value = []; selected.value = null; error.value = '';
  busy.value = true;
  try {
    const orders = isOrders.value;
    const result = await (props.recordId ? orders ? props.api.getOwnOrder(props.recordId) : props.api.getOwnFeedbackCase(props.recordId) : orders ? props.api.getOwnOrders({ offset: offset.value, limit: 24 }) : props.api.getOwnFeedback({ offset: offset.value, limit: 24 }));
    if (!active || current !== request) return;
    if (props.recordId) selected.value = result; else { items.value = result.items; total.value = result.total; }
  } catch (issue) { if (active && current === request) { error.value = issue.message; emit('staff-error', issue); } }
  finally { if (active && current === request) busy.value = false; }
}
watch(() => [props.page, props.recordId, props.session.user.id], (current, previous = []) => { if (current[0] !== previous[0] || current[2] !== previous[2]) offset.value = 0; load(); }, { immediate: true });
function nextPage(delta) { offset.value += delta * 24; load(); }
const url = id => routeHash({ page: isOrders.value ? 'account-orders' : 'account-feedback', [isOrders.value ? 'orderId' : 'caseId']: id });
onBeforeUnmount(() => { active = false; request++; items.value = []; selected.value = null; });
</script>
<template>
  <section :class="embedded ? 'account-panel' : 'container account-section'"><h1 v-if="!embedded">{{ isOrders ? 'Мои заказы' : 'Мои обращения' }}</h1>
    <button v-if="page === 'account-feedback' && !recordId" type="button" class="btn red" @click="emit('feedback', 'request', $event)">Новое обращение</button>
    <a v-if="recordId" :href="`#${page}`" class="text-link">← К списку</a>
    <p v-if="busy" class="api-feedback" role="status">Загружаем ваши данные…</p>
    <div v-else-if="error" class="api-feedback" role="alert"><p>{{ error }}</p><button type="button" class="btn outline" @click="load">Повторить загрузку</button></div>
    <article v-else-if="selected" class="account-card" data-testid="own-detail"><h2>{{ isOrders ? `№ ${selected.number}` : formatFeedbackReference(selected.number, selected.createdAt) }}</h2><p><template v-if="isOrders">{{ date(selected.createdAt) }} · </template>{{ isOrders ? ORDER_STATUSES[selected.status] : FEEDBACK_STATUSES[selected.status] }}</p><p v-if="page === 'account-feedback'" class="feedback-help">{{ FEEDBACK_STATUS_DESCRIPTIONS[selected.status] }}</p>
      <template v-if="isOrders"><p>{{ selected.fulfillment.method === 'delivery' ? `Доставка: ${selected.fulfillment.address}` : 'Самовывоз' }}</p><ul class="account-items"><li v-for="item in selected.items" :key="item.productId"><span>{{ item.name }} · {{ item.sku }} · {{ formatQuantity(item.quantity, item.unit ?? 'шт') }}</span><strong>{{ money(snapshotLineTotal(item)) }}</strong></li></ul><p>Сумма товаров: <strong>{{ money(selected.totalKopecks) }}</strong></p></template>
      <template v-else><p class="feedback-message">{{ selected.message || 'Без комментария' }}</p><a v-if="selected.order" :href="`#account-orders?order=${encodeURIComponent(selected.order.id)}`">Заказ № {{ selected.order.number }}</a></template>
    </article>
    <div v-else class="feedback-list"><a v-for="item in items" :key="item.id" :href="url(item.id)"><span><strong>{{ isOrders ? `№ ${item.number}` : formatFeedbackReference(item.number, item.createdAt) }}</strong><small v-if="isOrders">{{ date(item.createdAt) }}</small><span v-if="page === 'account-feedback'" class="feedback-list-message">{{ item.message || 'Без комментария' }}</span></span><span>{{ isOrders ? ORDER_STATUSES[item.status] : FEEDBACK_STATUSES[item.status] }}<small v-if="page === 'account-feedback'">{{ FEEDBACK_STATUS_DESCRIPTIONS[item.status] }}</small><strong v-if="isOrders">{{ money(item.totalKopecks) }}</strong></span></a><p v-if="!items.length" class="api-feedback">{{ isOrders ? 'У вас пока нет заказов.' : 'У вас пока нет обращений.' }}</p></div>
    <nav v-if="!busy && !error && !recordId && total > 24" class="pagination" aria-label="Страницы личных записей"><button type="button" class="btn outline" :disabled="!offset" @click="nextPage(-1)">Назад</button><span>{{ offset + 1 }}–{{ Math.min(offset + 24, total) }} из {{ total }}</span><button type="button" class="btn outline" :disabled="offset + 24 >= total" @click="nextPage(1)">Далее</button></nav>
  </section>
</template>

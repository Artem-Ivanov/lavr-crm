<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { USER_ROLES } from '../accounts.js';
import { routeHash } from '../routes.js';
const props = defineProps({ api: { type: Object, required: true }, session: { type: Object, required: true }, selectedUserId: { type: String, default: '' } });
const emit = defineEmits(['staff-error', 'select-user']);
const items = ref([]); const total = ref(0); const offset = ref(0); const query = ref(''); const selected = ref(null); const role = ref('partner'); const loading = ref(false); const busy = ref(false); const error = ref(''); const conflict = ref(false);
const userDetail = ref(null); const userRows = ref([]);
let active = true; let request = 0; let detailRequest = 0;
async function list(reset = false) {
  if (reset) offset.value = 0;
  const current = ++request; loading.value = true; error.value = '';
  try { const result = await props.api.listUsers({ query: query.value.trim() }, { offset: offset.value, limit: 24 }); if (active && current === request) { items.value = result.items; total.value = result.total; } }
  catch (issue) { if (active && current === request) { error.value = issue.message; emit('staff-error', issue); } }
  finally { if (active && current === request) loading.value = false; }
}
async function select(id) {
  const current = ++detailRequest; selected.value = null; busy.value = true; error.value = '';
  try { const result = await props.api.getUser(id); if (active && current === detailRequest) { selected.value = result; role.value = result.role; conflict.value = false; } }
  catch (issue) { if (active && current === detailRequest) { error.value = issue.message; emit('staff-error', issue); } }
  finally { if (active && current === detailRequest) busy.value = false; }
  await nextTick();
  if (active && current === detailRequest) userDetail.value?.focus();
}
async function update(kind) {
  if (busy.value || conflict.value || !selected.value) return;
  const current = ++detailRequest; const user = selected.value; busy.value = true; error.value = '';
  try {
    const result = await (kind === 'role' ? props.api.updateUserRole(user.id, role.value, user.version) : props.api.updateUserStatus(user.id, !user.isActive, user.version));
    if (!active || current !== detailRequest) return;
    selected.value = result; role.value = result.role;
    if (user.id === props.session.user.id) { emit('staff-error', { status: 401 }); return; }
    await list();
  } catch (issue) { if (active && current === detailRequest) { conflict.value = issue.status === 412; error.value = conflict.value ? `${issue.message} Загрузите актуальную карточку перед изменением.` : issue.message; emit('staff-error', issue); } }
  finally { if (active && current === detailRequest) busy.value = false; }
}
function page(delta) { offset.value += delta * 24; list(); }
function selectUser(event, id) {
  if (id && busy.value) { event.preventDefault(); return; }
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault(); emit('select-user', id);
}
watch(() => props.selectedUserId, async (id, previousId) => {
  if (id) select(id);
  else {
    const current = ++detailRequest; selected.value = null; busy.value = false; error.value = ''; conflict.value = false;
    if (previousId) {
      await nextTick();
      if (active && current === detailRequest) userRows.value.find(row => row.getAttribute('href') === routeHash({ page: 'admin-users', userId: previousId }))?.focus();
    }
  }
}, { immediate: true });
list();
onBeforeUnmount(() => { active = false; request++; detailRequest++; selected.value = null; items.value = []; });
</script>
<template>
  <div class="account-admin"><h2>Пользователи</h2><form v-if="!selectedUserId" class="crm-search" @submit.prevent="list(true)"><input v-model="query" type="search" maxlength="254" aria-label="Поиск пользователей" placeholder="Полный email или телефон"><button type="submit" :disabled="loading">Найти</button></form>
    <p v-if="api.mode === 'demo'" class="api-feedback">Симуляция управления тестовыми пользователями текущей вкладки.</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="!selectedUserId && loading" role="status">Загружаем пользователей…</p>
    <div v-else-if="!selectedUserId" class="feedback-list"><a v-for="user in items" ref="userRows" :key="user.id" :href="routeHash({ page: 'admin-users', userId: user.id })" class="account-user-row" :aria-disabled="busy || undefined" @click="selectUser($event, user.id)"><span><strong>{{ user.name || 'Пользователь' }}</strong><small>{{ user.email || 'Email не указан' }} · {{ user.phone || 'Телефон не указан' }}</small></span><span>{{ USER_ROLES[user.role] }} · {{ user.isActive ? 'Активен' : 'Заблокирован' }}</span></a><p v-if="!items.length">Пользователи не найдены.</p></div>
    <a v-if="selectedUserId" :href="routeHash({ page: 'admin-users' })" class="text-link" @click="selectUser($event, '')">К списку пользователей</a>
    <p v-if="selectedUserId && busy && !selected" class="api-feedback" role="status">Загружаем карточку пользователя…</p>
    <button v-if="error && !selected" type="button" class="btn outline" @click="selectedUserId ? select(selectedUserId) : list()">Повторить загрузку</button>
    <nav v-if="!selectedUserId && total > 24" class="pagination" aria-label="Страницы пользователей"><button type="button" class="btn outline" :disabled="loading || !offset" @click="page(-1)">Назад</button><span>{{ offset + 1 }}–{{ Math.min(offset + 24, total) }} из {{ total }}</span><button type="button" class="btn outline" :disabled="loading || offset + 24 >= total" @click="page(1)">Далее</button></nav>
    <section v-if="selected" ref="userDetail" tabindex="-1" class="account-card" data-testid="user-detail" :aria-busy="busy"><h3>{{ selected.name || 'Пользователь' }}</h3><p>ID: {{ selected.id }}</p><p>Email: {{ selected.email || 'Не указан' }} · {{ selected.emailVerified ? 'Подтверждён' : 'Не подтверждён' }}</p><p>Телефон: {{ selected.phone || 'Не указан' }} · Не подтверждён</p><p>{{ selected.isActive ? 'Активен' : 'Заблокирован' }} · Версия {{ selected.version }}</p><form class="account-form" @submit.prevent="update('role')"><label>Роль<select v-model="role" :disabled="busy || conflict"><option v-for="(label, value) in USER_ROLES" :key="value" :value="value">{{ label }} ({{ value }})</option></select></label><div class="account-actions"><button type="submit" class="btn red" :disabled="busy || conflict || role === selected.role">Сохранить роль</button><button type="button" class="btn outline" :disabled="busy || conflict" @click="update('status')">{{ selected.isActive ? 'Заблокировать' : 'Разблокировать' }}</button><button type="button" class="text-link" :disabled="busy" @click="select(selected.id)">Загрузить актуальную карточку</button></div></form><p v-if="selected.id === session.user.id">Изменение своих прав завершит текущую сессию.</p></section>
  </div>
</template>

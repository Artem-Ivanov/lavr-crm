<script setup>
import { nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { USER_ROLES } from '../accounts.js';
import { routeHash } from '../routes.js';
const props = defineProps({ api: { type: Object, required: true }, session: { type: Object, required: true }, selectedUserId: { type: String, default: '' } });
const emit = defineEmits(['staff-error', 'select-user']);
const items = ref([]); const total = ref(0); const offset = ref(0); const query = ref(''); const selected = ref(null); const role = ref('partner'); const loading = ref(false); const busy = ref(false); const error = ref(''); const conflict = ref(false);
const userDetail = ref(null); const userRows = ref([]);
const creating = ref(false); const createBusy = ref(false); const createError = ref(''); const createForm = ref(null); const createButton = ref(null);
const emptyDraft = () => ({ name: '', email: '', phone: '', password: '', role: 'partner' });
const draft = ref(emptyDraft());
let active = true; let request = 0; let detailRequest = 0; let createRequest = 0;
async function openCreate() {
  if (createBusy.value) return;
  creating.value = true; createError.value = ''; await nextTick(); createForm.value?.querySelector('input')?.focus();
}
async function closeCreate() {
  if (createBusy.value) return;
  creating.value = false; createError.value = ''; draft.value = emptyDraft();
  await nextTick(); createButton.value?.focus();
}
async function create() {
  if (createBusy.value) return;
  const current = ++createRequest; createBusy.value = true; createError.value = '';
  try {
    const user = await props.api.createUser({ ...draft.value });
    if (!active || current !== createRequest) return;
    draft.value = emptyDraft(); creating.value = false;
    emit('select-user', user.id); await list(true);
  } catch (issue) {
    if (active && current === createRequest) { createError.value = issue.message; emit('staff-error', issue); }
  } finally { if (active && current === createRequest) createBusy.value = false; }
}
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
function page(delta) { if (createBusy.value) return; offset.value += delta * 24; list(); }
function selectUser(event, id) {
  if (id && (busy.value || createBusy.value)) { event.preventDefault(); return; }
  if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  event.preventDefault(); emit('select-user', id);
}
watch(() => props.selectedUserId, async (id, previousId) => {
  createRequest++; creating.value = false; createBusy.value = false; createError.value = ''; draft.value = emptyDraft();
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
onBeforeUnmount(() => { active = false; request++; detailRequest++; createRequest++; draft.value = emptyDraft(); selected.value = null; items.value = []; });
</script>
<template>
  <div class="account-admin"><div class="account-actions"><h2>Пользователи</h2><button v-if="!selectedUserId && !creating" ref="createButton" type="button" class="btn red" @click="openCreate">Создать пользователя</button></div>
    <section v-if="!selectedUserId && creating" class="account-card" data-testid="user-create" :aria-busy="createBusy">
      <h3>Новый пользователь</h3>
      <p class="muted">Пользователь сможет войти сразу. Вы подтверждаете правильность email; передайте пароль пользователю лично.</p>
      <form ref="createForm" class="account-form" data-testid="user-create-form" @submit.prevent="create">
        <label>Имя <small>необязательно</small><input v-model="draft.name" name="name" maxlength="240" autocomplete="name" :disabled="createBusy"></label>
        <label>Email<input v-model="draft.email" name="email" type="email" maxlength="254" autocomplete="off" required :disabled="createBusy"></label>
        <label>Телефон <small>необязательно</small><input v-model="draft.phone" name="phone" type="tel" maxlength="30" autocomplete="off" placeholder="+7 (999) 123-45-67" :disabled="createBusy"></label>
        <label>Пароль<input v-model="draft.password" name="password" type="password" minlength="12" maxlength="256" autocomplete="new-password" required :disabled="createBusy"><small>От 12 до 256 символов</small></label>
        <label>Роль<select v-model="draft.role" name="role" :disabled="createBusy"><option v-for="(label, value) in USER_ROLES" :key="value" :value="value">{{ label }}</option></select></label>
        <p v-if="createError" class="form-error" role="alert">{{ createError }}</p>
        <div class="account-actions"><button type="submit" class="btn red" :disabled="createBusy">{{ createBusy ? 'Создаём…' : 'Создать пользователя' }}</button><button type="button" class="btn outline" :disabled="createBusy" @click="closeCreate">Отмена</button></div>
      </form>
    </section>
    <form v-if="!selectedUserId" class="crm-search" @submit.prevent="!createBusy && list(true)"><input v-model="query" type="search" maxlength="254" aria-label="Поиск пользователей" placeholder="Полный email или телефон" :disabled="createBusy"><button type="submit" :disabled="loading || createBusy">Найти</button></form>
    <p v-if="api.mode === 'demo'" class="api-feedback">Симуляция управления тестовыми пользователями текущей вкладки.</p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p v-if="!selectedUserId && loading" role="status">Загружаем пользователей…</p>
    <div v-else-if="!selectedUserId" class="feedback-list"><a v-for="user in items" ref="userRows" :key="user.id" :href="routeHash({ page: 'admin-users', userId: user.id })" class="account-user-row" :aria-disabled="busy || createBusy || undefined" @click="selectUser($event, user.id)"><span><strong>{{ user.name || 'Пользователь' }}</strong><small>{{ user.email || 'Email не указан' }} · {{ user.phone || 'Телефон не указан' }}</small></span><span>{{ USER_ROLES[user.role] }} · {{ user.isActive ? 'Активен' : 'Заблокирован' }}</span></a><p v-if="!items.length">Пользователи не найдены.</p></div>
    <a v-if="selectedUserId" :href="routeHash({ page: 'admin-users' })" class="text-link" @click="selectUser($event, '')">К списку пользователей</a>
    <p v-if="selectedUserId && busy && !selected" class="api-feedback" role="status">Загружаем карточку пользователя…</p>
    <button v-if="error && !selected" type="button" class="btn outline" @click="selectedUserId ? select(selectedUserId) : list()">Повторить загрузку</button>
    <nav v-if="!selectedUserId && total > 24" class="pagination" aria-label="Страницы пользователей"><button type="button" class="btn outline" :disabled="loading || createBusy || !offset" @click="page(-1)">Назад</button><span>{{ offset + 1 }}–{{ Math.min(offset + 24, total) }} из {{ total }}</span><button type="button" class="btn outline" :disabled="loading || createBusy || offset + 24 >= total" @click="page(1)">Далее</button></nav>
    <section v-if="selected" ref="userDetail" tabindex="-1" class="account-card" data-testid="user-detail" :aria-busy="busy"><h3>{{ selected.name || 'Пользователь' }}</h3><p>ID: {{ selected.id }}</p><p>Email: {{ selected.email || 'Не указан' }} · {{ selected.emailVerified ? 'Подтверждён' : 'Не подтверждён' }}</p><p>Телефон: {{ selected.phone || 'Не указан' }} · Не подтверждён</p><p>{{ selected.isActive ? 'Активен' : 'Заблокирован' }} · Версия {{ selected.version }}</p><form class="account-form" @submit.prevent="update('role')"><label>Роль<select v-model="role" :disabled="busy || conflict"><option v-for="(label, value) in USER_ROLES" :key="value" :value="value">{{ label }} ({{ value }})</option></select></label><div class="account-actions"><button type="submit" class="btn red" :disabled="busy || conflict || role === selected.role">Сохранить роль</button><button type="button" class="btn outline" :disabled="busy || conflict" @click="update('status')">{{ selected.isActive ? 'Заблокировать' : 'Разблокировать' }}</button><button type="button" class="text-link" :disabled="busy" @click="select(selected.id)">Загрузить актуальную карточку</button></div></form><p v-if="selected.id === session.user.id">Изменение своих прав завершит текущую сессию.</p></section>
  </div>
</template>

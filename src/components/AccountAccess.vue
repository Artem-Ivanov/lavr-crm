<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue';
import { routeHash, safeReturnTo } from '../routes.js';
const props = defineProps({ api: { type: Object, required: true }, page: { type: String, required: true }, session: { type: Object, default: null }, returnTo: { type: String, default: '' }, embedded: Boolean });
const emit = defineEmits(['authenticated', 'identity-changing', 'staff-error', 'navigate']);
const authHash = page => routeHash({ page, returnTo: safeReturnTo(props.returnTo) });
const draft = reactive({ email: '', phone: '', password: '', code: '' });
const busy = ref(false);
const error = ref('');
const notice = ref('');
const flow = ref(null);
const remaining = ref(0);
let deadline = 0;
let active = true;
let generation = 0;
const profile = computed(() => props.page === 'account' && props.session && !props.session.user.profileComplete);
const confirming = computed(() => !!flow.value && (props.page === 'activate' || props.page === 'password-reset' || profile.value));
const title = computed(() => profile.value ? 'Завершите профиль' : confirming.value ? props.page === 'password-reset' ? 'Новый пароль' : 'Подтвердите email' : ({ login: 'Войти в личный кабинет', signup: 'Регистрация', activate: 'Подтвердите email', 'password-reset': 'Восстановить пароль' })[props.page]);
watch(() => props.session?.user.id, (current, previous) => { if (previous && current !== previous) { draft.password = ''; draft.code = ''; } });
const timer = setInterval(() => { remaining.value = Math.max(0, Math.ceil((deadline - Date.now()) / 1000)); }, 1000);
watch(() => props.page, () => { generation++; busy.value = false; error.value = ''; draft.password = ''; draft.code = ''; if (!['activate', 'password-reset', 'account'].includes(props.page)) { flow.value = null; notice.value = ''; } });
function setFlow(result) {
  flow.value = result;
  deadline = Date.now() + result.resendAfterSeconds * 1000;
  remaining.value = result.resendAfterSeconds;
  notice.value = 'Если адрес доступен для этой операции, письмо с кодом будет отправлено. Проверьте входящие и спам.';
}
async function perform(operation) {
  if (busy.value) return;
  const current = ++generation;
  busy.value = true; error.value = '';
  try { await operation(current); }
  catch (issue) { if (active && current === generation) { error.value = issue.message; if (issue.status === 401 && profile.value) emit('staff-error', issue); } }
  finally { if (active && current === generation) busy.value = false; }
}
function authenticated(result, current) { if (active && current === generation) { draft.password = ''; draft.code = ''; flow.value = null; emit('authenticated', result); } }
function submit() {
  perform(async current => {
    if (confirming.value) {
      const body = { flowId: flow.value.flowId, code: draft.code };
      if (props.page === 'password-reset') {
        emit('identity-changing');
        await props.api.confirmPasswordReset({ ...body, newPassword: draft.password });
        if (active && current === generation) { flow.value = null; draft.password = ''; draft.code = ''; notice.value = 'Пароль изменён. Войдите с новым паролем.'; }
      } else {
        if (!profile.value) emit('identity-changing');
        authenticated(await (profile.value ? props.api.confirmProfileActivation(body) : props.api.activate(body)), current);
      }
    } else if (props.page === 'signup' || profile.value) {
      const body = { email: draft.email, phone: draft.phone };
      const result = await (profile.value ? props.api.startProfileActivation(body) : props.api.signup({ ...body, password: draft.password }));
      if (active && current === generation) { setFlow(result); draft.password = ''; if (!profile.value) emit('navigate', authHash('activate')); }
    } else if (props.page === 'password-reset') {
      const result = await props.api.requestPasswordReset({ email: draft.email });
      if (active && current === generation) setFlow(result);
    } else if (props.page === 'login') {
      emit('identity-changing');
      authenticated(await props.api.loginUser({ email: draft.email, password: draft.password }), current);
    }
  });
}
function resend() { perform(async current => { const result = await props.api.resendCode({ flowId: flow.value.flowId }); if (active && current === generation) setFlow(result); }); }
function restart() { flow.value = null; notice.value = ''; error.value = ''; draft.code = ''; draft.password = ''; if (props.page === 'activate') emit('navigate', authHash('signup')); }
onBeforeUnmount(() => { active = false; generation++; clearInterval(timer); draft.password = ''; draft.code = ''; });
</script>

<template>
  <section :class="embedded ? 'account-panel' : 'container account-section'">
    <div class="account-card"><component :is="embedded ? 'h2' : 'h1'">{{ title }}</component>
      <p v-if="api.mode === 'demo'" class="api-feedback">Симуляция: настоящие аккаунты и письма не создаются. Для просмотра используйте email admin@example.test, operator@example.test или partner@example.test и любой непустой пароль.</p>
      <p v-if="profile">Укажите телефон и email, затем подтвердите email. Служебные разделы уже доступны по вашим правам.</p>
      <p v-if="notice" role="status">{{ notice }}</p>
      <template v-if="page === 'activate' && !flow"><p>Подтверждение доступно в браузере, где начата регистрация. Начните заново, если страница была перезагружена.</p><a :href="authHash('signup')" class="btn red">Начать регистрацию</a></template>
      <form v-else class="account-form" :aria-busy="busy" @submit.prevent="submit">
        <fieldset :disabled="busy"><legend class="sr-only">{{ title }}</legend>
          <template v-if="confirming">
            <label>Код из письма<input v-model="draft.code" name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" minlength="6" maxlength="6" required></label>
            <label v-if="page === 'password-reset'">Новый пароль<input v-model="draft.password" name="newPassword" type="password" minlength="12" maxlength="256" autocomplete="new-password" required><small>От 12 до 256 символов.</small></label>
          </template>
          <template v-else>
            <label>Email<input v-model="draft.email" name="email" type="email" autocomplete="email" maxlength="254" required></label>
            <label v-if="page === 'signup' || profile">Телефон<input v-model="draft.phone" name="phone" type="tel" autocomplete="tel" maxlength="30" placeholder="+7 (999) 123-45-67" required><small>Телефон пока не подтверждается и не используется для входа.</small></label>
            <label v-if="page === 'login' || page === 'signup'">Пароль<input v-model="draft.password" name="password" type="password" :minlength="page === 'signup' ? 12 : 1" maxlength="256" :autocomplete="page === 'signup' ? 'new-password' : 'current-password'" required><small v-if="page === 'signup'">От 12 до 256 символов.</small></label>
          </template>
        </fieldset>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button type="submit" class="btn red" :disabled="busy">{{ busy ? 'Подождите…' : confirming ? page === 'password-reset' ? 'Сохранить новый пароль' : 'Подтвердить код' : page === 'login' ? 'Войти' : 'Получить код' }}</button>
        <div v-if="confirming" class="account-actions"><button type="button" class="btn outline" :disabled="busy || remaining > 0" @click="resend">{{ remaining ? `Повторить через ${remaining} с` : 'Отправить код повторно' }}</button><button type="button" class="text-link" :disabled="busy" @click="restart">Начать заново</button></div>
      </form>
      <nav v-if="!profile" class="account-actions" aria-label="Вход и регистрация"><a v-if="page !== 'login'" :href="authHash('login')">Войти</a><a v-if="page !== 'signup'" :href="authHash('signup')">Зарегистрироваться</a><a v-if="page === 'login'" :href="authHash('password-reset')">Забыли пароль?</a></nav>
    </div>
  </section>
</template>

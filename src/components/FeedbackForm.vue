<script setup>
import { nextTick, onBeforeUnmount, reactive, ref, watch } from 'vue';
import AppIcon from './AppIcon.vue';
import { normalizeFeedbackInput } from '../feedback.js';
import { feedbackKey, clearFeedbackKey } from '../feedback-attempt.js';

const props = defineProps({ api: { type: Object, required: true }, session: { type: Object, default: null }, needsRelogin: Boolean, authLoading: Boolean });
const emit = defineEmits(['staff-error']);
let generation = 0;
const dialog = ref(null);
const type = ref('contact');
const draft = reactive({ name: '', phone: '', email: '', message: '' });
const busy = ref(false);
const error = ref('');
const receipt = ref(null);
let opener;
let cleanupPending = false;
let previousActor = props.session?.user.id || null;
function clearDraft() { Object.assign(draft, { name: '', phone: '', email: '', message: '' }); }
watch([() => props.session?.user.id, () => props.needsRelogin], ([actor, reauth]) => {
  generation++; receipt.value = null; error.value = ''; busy.value = false;
  if (actor ? actor !== previousActor : !reauth) { clearDraft(); cleanupPending = false; }
  if (actor || !reauth) previousActor = actor || null;
});
async function open(variant = 'contact', event) {
  if (receipt.value) { receipt.value = null; error.value = ''; }
  type.value = variant;
  opener = event?.currentTarget || document.activeElement;
  await nextTick();
  if (!dialog.value.open) dialog.value.showModal();
  await nextTick();
  (dialog.value?.querySelector('[data-feedback-first]') || dialog.value?.querySelector('.dialog-close'))?.focus();
}
async function close(restoreFocus = true) {
  if (!dialog.value?.open) return;
  dialog.value.close();
  await nextTick();
  if (restoreFocus && opener?.isConnected) opener.focus({ preventScroll: true });
}
async function submit() {
  if (busy.value) return;
  const current = generation;
  const scope = props.session ? `customer:${props.session.user.id}` : 'submission';
  busy.value = true; error.value = '';
  try {
    if (props.authLoading || props.needsRelogin) throw new Error('Войдите снова перед отправкой обращения.');
    if (!props.session && !draft.phone.trim() && !draft.email.trim()) throw new Error('Укажите телефон или email.');
    const body = props.session ? { type: type.value, message: draft.message } : normalizeFeedbackInput({ type: type.value, ...draft });
    if (cleanupPending) { clearFeedbackKey(scope); cleanupPending = false; }
    const key = await feedbackKey(body, scope);
    if (current !== generation) return;
    const result = await (props.session ? props.api.createOwnFeedback(body, key) : props.api.submitFeedback(body, key));
    if (current !== generation) return;
    receipt.value = result;
    clearDraft();
    try { clearFeedbackKey(scope); } catch { cleanupPending = true; }
    await nextTick();
    dialog.value?.querySelector('[data-testid="feedback-receipt"]')?.focus();
  } catch (issue) {
    if (current !== generation) return;
    error.value = issue.message; emit('staff-error', issue);
    await nextTick(); dialog.value?.querySelector('[role="alert"]')?.focus();
  } finally { if (current === generation) busy.value = false; }
}
function another() { receipt.value = null; error.value = ''; nextTick(() => dialog.value?.querySelector('[data-feedback-first]')?.focus()); }
onBeforeUnmount(() => { generation++; clearDraft(); });
defineExpose({ open, close });
</script>

<template>
  <dialog ref="dialog" class="detail-dialog feedback-dialog" aria-labelledby="feedback-title" @cancel.prevent="close">
    <button type="button" class="dialog-close" aria-label="Закрыть" @click="close"><AppIcon name="close" /></button>
    <template v-if="receipt">
      <div data-testid="feedback-receipt" tabindex="-1" role="status"><span class="eyebrow">{{ api.mode === 'demo' ? 'ДЕМОНСТРАЦИЯ' : 'ОБРАЩЕНИЕ ПРИНЯТО' }}</span><h2 id="feedback-title">{{ api.mode === 'demo' ? 'Демообращение сохранено' : 'Обращение принято' }}</h2><p>{{ api.mode === 'demo' ? 'Данные доступны только до перезагрузки страницы. Магазину они не отправлены.' : 'Менеджер рассмотрит ваше обращение.' }}</p></div>
      <div class="feedback-actions"><button type="button" class="btn red" @click="close">Готово</button><button type="button" class="btn outline" @click="another">Отправить ещё обращение</button></div>
    </template>
    <template v-else>
      <h2 id="feedback-title">{{ type === 'request' ? 'Оставить запрос' : 'Свяжитесь со мной' }}</h2>
      <div class="feedback-variants" role="group" aria-label="Вариант обращения"><button type="button" :aria-pressed="type === 'contact'" :disabled="busy" @click="type = 'contact'">Свяжитесь со мной</button><button type="button" :aria-pressed="type === 'request'" :disabled="busy" @click="type = 'request'">Оставить запрос</button></div>
      <p v-if="session && (session.user.email || session.user.phone)">Контакты для связи: {{ [session.user.email, session.user.phone].filter(Boolean).join(' · ') }}.</p>
      <p v-if="needsRelogin" class="form-error">Сессия завершена. <a href="#login" @click="close">Войти снова</a></p>
      <p v-if="!session" id="feedback-contact-help">Укажите телефон или email. Достаточно одного способа связи.</p>
      <p v-if="api.mode === 'demo'" class="feedback-demo">Демонстрация: используйте тестовые контакты. Данные исчезнут при перезагрузке.</p>
      <form class="feedback-form" novalidate :aria-busy="busy" @submit.prevent="submit">
        <fieldset :disabled="busy"><legend class="sr-only">Контакты и текст обращения</legend>
          <div class="form-grid">
            <template v-if="!session"><label class="full">Ваше имя<input v-model="draft.name" data-feedback-first name="feedbackName" autocomplete="name" maxlength="100"></label>
            <label>Телефон<input v-model="draft.phone" name="feedbackPhone" type="tel" autocomplete="tel" maxlength="30" aria-describedby="feedback-contact-help" placeholder="+7 (999) 123-45-67"></label>
            <label>Email<input v-model="draft.email" name="feedbackEmail" type="email" autocomplete="email" maxlength="254" aria-describedby="feedback-contact-help"></label>
            </template>
            <label class="full">{{ type === 'request' ? 'Ваш запрос' : 'Комментарий' }}<textarea v-model="draft.message" name="feedbackMessage" maxlength="2000" rows="4" :required="type === 'request'"></textarea></label>
          </div>
        </fieldset>
        <p v-if="error" class="form-error" role="alert" tabindex="-1">{{ error }}</p>
        <button type="submit" data-testid="feedback-submit" class="btn red" :disabled="busy || authLoading || needsRelogin">{{ busy ? 'Отправляем…' : 'Отправить обращение' }}</button>
        <p class="feedback-help">Контакты используются для ответа на ваше обращение.</p>
      </form>
    </template>
  </dialog>
</template>

<script setup>
import { nextTick, reactive, ref } from 'vue';
import AppIcon from './AppIcon.vue';
import { normalizeFeedbackInput } from '../feedback.js';
import { feedbackKey, clearFeedbackKey } from '../feedback-attempt.js';

const props = defineProps({ api: { type: Object, required: true } });
const dialog = ref(null);
const type = ref('contact');
const draft = reactive({ name: '', phone: '', email: '', messenger: { service: '', address: '' }, message: '' });
const busy = ref(false);
const error = ref('');
const receipt = ref(null);
let opener;
let cleanupPending = false;
async function open(variant = 'contact', event) {
  if (receipt.value) { receipt.value = null; error.value = ''; }
  type.value = variant;
  opener = event?.currentTarget || document.activeElement;
  await nextTick();
  if (!dialog.value.open) dialog.value.showModal();
  await nextTick();
  (dialog.value.querySelector('[data-feedback-first]') || dialog.value.querySelector('.dialog-close'))?.focus();
}
async function close(restoreFocus = true) {
  if (!dialog.value?.open) return;
  dialog.value.close();
  await nextTick();
  if (restoreFocus && opener?.isConnected) opener.focus({ preventScroll: true });
}
async function submit() {
  if (busy.value) return;
  busy.value = true; error.value = '';
  try {
    const body = normalizeFeedbackInput({ type: type.value, ...draft, messenger: { ...draft.messenger } });
    if (cleanupPending) { clearFeedbackKey(); cleanupPending = false; }
    receipt.value = await props.api.submitFeedback(body, await feedbackKey(body));
    Object.assign(draft, { name: '', phone: '', email: '', messenger: { service: '', address: '' }, message: '' });
    try { clearFeedbackKey(); } catch { cleanupPending = true; }
    await nextTick();
    dialog.value.querySelector('[data-testid="feedback-receipt"]')?.focus();
  } catch (issue) {
    error.value = issue.message;
    await nextTick(); dialog.value.querySelector('[role="alert"]')?.focus();
  } finally { busy.value = false; }
}
function another() { receipt.value = null; error.value = ''; nextTick(() => dialog.value.querySelector('[data-feedback-first]')?.focus()); }
defineExpose({ open, close });
</script>

<template>
  <dialog ref="dialog" class="detail-dialog feedback-dialog" aria-labelledby="feedback-title" @cancel.prevent="close">
    <button type="button" class="dialog-close" aria-label="Закрыть" @click="close"><AppIcon name="close" /></button>
    <template v-if="receipt">
      <div data-testid="feedback-receipt" tabindex="-1" role="status"><span class="eyebrow">{{ receipt.demo ? 'ДЕМОНСТРАЦИЯ' : 'ОБРАЩЕНИЕ ПРИНЯТО' }}</span><h2 id="feedback-title">{{ receipt.demo ? 'Демообращение сохранено' : 'Спасибо, мы свяжемся с вами' }}</h2><p>{{ receipt.demo ? 'Данные доступны только до перезагрузки страницы. Магазину они не отправлены.' : 'Менеджер свяжется с вами по указанным контактам. Онлайн-оплата не требуется.' }}</p></div>
      <div class="feedback-actions"><button type="button" class="btn red" @click="close">Готово</button><button type="button" class="btn outline" @click="another">Отправить ещё обращение</button></div>
    </template>
    <template v-else>
      <h2 id="feedback-title">{{ type === 'request' ? 'Оставить запрос' : 'Свяжитесь со мной' }}</h2>
      <div class="feedback-variants" role="group" aria-label="Вариант обращения"><button type="button" :aria-pressed="type === 'contact'" :disabled="busy" @click="type = 'contact'">Свяжитесь со мной</button><button type="button" :aria-pressed="type === 'request'" :disabled="busy" @click="type = 'request'">Оставить запрос</button></div>
      <p id="feedback-contact-help">Укажите хотя бы один способ связи: телефон, email или мессенджер.</p>
      <p v-if="api.mode === 'demo'" class="feedback-demo">Демонстрация: используйте тестовые контакты. Данные исчезнут при перезагрузке.</p>
      <form class="feedback-form" novalidate :aria-busy="busy" @submit.prevent="submit">
        <fieldset :disabled="busy"><legend class="sr-only">Контакты и текст обращения</legend>
          <div class="form-grid">
            <label class="full">Ваше имя<input v-model="draft.name" data-feedback-first name="feedbackName" autocomplete="name" maxlength="100"></label>
            <label>Телефон<input v-model="draft.phone" name="feedbackPhone" type="tel" autocomplete="tel" maxlength="30" aria-describedby="feedback-contact-help" placeholder="+7 (999) 123-45-67"></label>
            <label>Email<input v-model="draft.email" name="feedbackEmail" type="email" autocomplete="email" maxlength="254" aria-describedby="feedback-contact-help"></label>
            <label>Мессенджер<input v-model="draft.messenger.service" name="feedbackMessengerService" maxlength="40" placeholder="Telegram, MAX…" aria-describedby="feedback-messenger-help"></label>
            <label>Контакт в мессенджере<input v-model="draft.messenger.address" name="feedbackMessengerAddress" maxlength="254" placeholder="@имя, телефон или ссылка" aria-describedby="feedback-messenger-help"></label>
            <p id="feedback-messenger-help" class="full feedback-help">Для мессенджера заполните оба поля: сервис и контакт.</p>
            <label class="full">{{ type === 'request' ? 'Ваш запрос' : 'Комментарий' }}<textarea v-model="draft.message" name="feedbackMessage" maxlength="2000" rows="4" :required="type === 'request'"></textarea></label>
          </div>
        </fieldset>
        <p v-if="error" class="form-error" role="alert" tabindex="-1">{{ error }}</p>
        <button type="submit" data-testid="feedback-submit" class="btn red" :disabled="busy">{{ busy ? 'Отправляем…' : 'Отправить обращение' }}</button>
        <p class="feedback-help">Контакты используются для ответа на ваше обращение.</p>
      </form>
    </template>
  </dialog>
</template>

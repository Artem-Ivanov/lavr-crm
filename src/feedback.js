export const FEEDBACK_STATUSES = Object.freeze({
  new: 'Новое', in_progress: 'В работе', converted: 'Создан заказ', closed: 'Закрыто без заказа',
});

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const controls = /[\u0000-\u0009\u000b\u000c\u000e-\u001f\u007f-\u009f]/;
const singleLineControls = /[\u0000-\u001f\u007f-\u009f]/;
const string = (value, max, multiline = false) => typeof value === 'string' && [...value].length <= max && !(multiline ? controls : singleLineControls).test(value);
const exact = (value, keys, optional = []) => object(value) && keys.every(key => Object.hasOwn(value, key))
  && Object.keys(value).every(key => keys.includes(key) || optional.includes(key));
const identifier = value => string(value, 128) && value.length > 0 && value !== '.' && value !== '..';
const date = value => string(value, 80) && value.length > 0 && Number.isFinite(Date.parse(value));
const staff = value => exact(value, ['id', 'name']) && identifier(value.id) && string(value.name, 240) && value.name.trim().length > 0;
const optionalDemo = value => value.demo === undefined || value.demo === true;
const inputKeys = ['type', 'name', 'phone', 'email', 'messenger', 'message'];

function field(value, max, message, multiline = false) {
  if (value === undefined) return '';
  if (!string(value, max, multiline)) throw new Error(message);
  return value.trim();
}

export function normalizeFeedbackInput(input) {
  if (!object(input) || Object.keys(input).some(key => !inputKeys.includes(key)) || !['contact', 'request'].includes(input.type)) {
    throw new Error('Выберите обращение: связаться с вами или оставить запрос.');
  }
  const name = field(input.name, 100, 'Имя должно быть не длиннее 100 символов и без управляющих символов.');
  const rawPhone = field(input.phone, 30, 'Укажите российский телефон: +7 и 10 цифр.');
  const digits = rawPhone.replace(/[+\s()-]/g, '');
  if (rawPhone && !/^[78][0-9]{10}$/.test(digits)) throw new Error('Укажите российский телефон: +7 и 10 цифр.');
  const phone = rawPhone ? `+7${digits.slice(1)}` : '';
  const email = field(input.email, 254, 'Проверьте адрес электронной почты.');
  if (email && !/^[^\s@?#]+@[^\s@?#]+\.[^\s@?#]+$/.test(email)) throw new Error('Проверьте адрес электронной почты.');
  const messengerInput = input.messenger === undefined ? { service: '', address: '' } : input.messenger;
  if (!exact(messengerInput, ['service', 'address'])) throw new Error('Укажите сервис мессенджера и контакт в нём.');
  const messenger = {
    service: field(messengerInput.service, 40, 'Название мессенджера должно быть не длиннее 40 символов.'),
    address: field(messengerInput.address, 254, 'Контакт в мессенджере должен быть не длиннее 254 символов.'),
  };
  if (Boolean(messenger.service) !== Boolean(messenger.address)) throw new Error('Укажите сервис мессенджера и контакт в нём.');
  if (!phone && !email && !messenger.address) throw new Error('Укажите хотя бы один способ связи: телефон, email или мессенджер.');
  const message = field(input.message, 2000, 'Текст обращения должен быть не длиннее 2000 символов и без управляющих символов.', true);
  if (input.type === 'request' && !message) throw new Error('Напишите ваш запрос.');
  return { type: input.type, name, phone, email, messenger, message };
}

export function normalizeFeedbackStatus(input) {
  if (!object(input) || Object.keys(input).some(key => !['status', 'resolution'].includes(key)) || !['in_progress', 'closed'].includes(input.status)) {
    throw new Error('Выберите допустимый статус обращения.');
  }
  const resolution = field(input.resolution, 2000, 'Результат должен быть не длиннее 2000 символов.', true);
  if (input.status === 'closed' && !resolution) throw new Error('Укажите результат закрытия обращения.');
  if (input.status === 'in_progress' && resolution) throw new Error('Результат указывается при закрытии обращения.');
  return { status: input.status, resolution };
}

export function normalizeFeedbackNote(input) {
  if (!object(input) || Object.keys(input).some(key => !['text', 'contactMethod'].includes(key))) throw new Error('Проверьте заметку.');
  const text = field(input.text, 2000, 'Заметка должна быть не длиннее 2000 символов.', true);
  const contactMethod = input.contactMethod === undefined ? '' : input.contactMethod;
  if (!text) throw new Error('Напишите заметку.');
  if (!['', 'phone', 'email', 'messenger'].includes(contactMethod)) throw new Error('Выберите способ связи.');
  return { text, contactMethod };
}

export function isFeedbackReceipt(value) {
  return exact(value, ['id', 'number', 'createdAt', 'state'], ['demo']) && identifier(value.id)
    && string(value.number, 80) && value.number.trim().length > 0 && date(value.createdAt)
    && value.state === 'accepted' && optionalDemo(value);
}

const orderReceipt = value => object(value) && identifier(value.id) && string(value.number, 80) && value.number.length > 0 && date(value.createdAt)
  && Number.isSafeInteger(value.totalKopecks) && value.totalKopecks >= 0
  && ['new', 'confirmed', 'ready', 'completed', 'cancelled'].includes(value.status)
  && ['not_connected', 'pending_sync', 'exporting', 'exported', 'error', 'cancelled_before_sync'].includes(value.integrationStatus);

export function isFeedbackCase(value) {
  if (!exact(value, ['id', 'number', 'createdAt', 'updatedAt', ...inputKeys, 'status', 'assignedTo', 'version', 'resolution', 'order', 'history', 'notification'], ['demo'])
    || !identifier(value.id) || !string(value.number, 80) || !value.number.trim() || !date(value.createdAt) || !date(value.updatedAt)
    || !Object.hasOwn(FEEDBACK_STATUSES, value.status) || !Number.isSafeInteger(value.version) || value.version < 1
    || !(value.assignedTo === null || staff(value.assignedTo)) || !string(value.resolution, 2000, true) || !optionalDemo(value)) return false;
  try {
    const normalized = normalizeFeedbackInput(Object.fromEntries(inputKeys.map(key => [key, value[key]])));
    if (inputKeys.filter(key => key !== 'messenger').some(key => normalized[key] !== value[key])
      || normalized.messenger.service !== value.messenger.service || normalized.messenger.address !== value.messenger.address) return false;
  } catch { return false; }
  if (value.status === 'new' && value.assignedTo !== null
    || ['in_progress', 'converted'].includes(value.status) && value.assignedTo === null
    || value.status === 'closed' && !value.resolution.trim()
    || (value.status === 'converted' ? !orderReceipt(value.order) : value.order !== null)) return false;
  return Array.isArray(value.history) && value.history.every(item => exact(item, ['id', 'createdAt', 'staff', 'action', 'text', 'contactMethod'])
    && identifier(item.id) && date(item.createdAt) && (item.staff === null || staff(item.staff))
    && string(item.action, 80) && /^[a-z][a-z0-9_.]*$/.test(item.action) && string(item.text, 2000, true)
    && ['', 'phone', 'email', 'messenger'].includes(item.contactMethod))
    && exact(value.notification, ['state', 'code'])
    && ['queued', 'running', 'done', 'error', 'uncertain', 'not_configured'].includes(value.notification.state)
    && (value.notification.code === null || typeof value.notification.code === 'string' && /^[A-Z][A-Z0-9_]{0,79}$/.test(value.notification.code));
}

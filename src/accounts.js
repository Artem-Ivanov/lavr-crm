import { validRequestQuantity, validSnapshotQuantity } from './quantity.js';
import { isFeedbackNumber } from './feedback.js';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
export const exact = (value, keys, optional = []) => object(value) && keys.every(key => Object.hasOwn(value, key)) && Object.keys(value).every(key => keys.includes(key) || optional.includes(key));
const text = (value, max, min = 0) => typeof value === 'string' && value.length >= min && value.length <= max && !/[\u0000-\u001f\u007f]/.test(value);
const id = value => typeof value === 'string' && [...value].length >= 1 && [...value].length <= 128 && !/[\u0000-\u001f\u007f]/.test(value) && value !== '.' && value !== '..';
const date = value => text(value, 80, 1) && Number.isFinite(Date.parse(value));
const integer = (value, min = 0) => Number.isSafeInteger(value) && value >= min;
const bool = value => typeof value === 'boolean';
const email = value => text(value, 254, 3) && /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
const phone = value => typeof value === 'string' && /^\+7\d{10}$/.test(value);
export const USER_ROLES = Object.freeze({ partner: 'Партнёр', operator: 'Оператор', admin: 'Администратор' });
export function isUserSession(value) {
  if (!exact(value, ['user', 'capabilities', 'csrfToken', 'expiresAt']) || !exact(value.user, ['id', 'name', 'email', 'phone', 'emailVerified', 'phoneVerified', 'profileComplete'])
    || !id(value.user.id) || !text(value.user.name, 240) || !(value.user.email === null || text(value.user.email, 254)) || !(value.user.phone === null || text(value.user.phone, 30))
    || !bool(value.user.emailVerified) || value.user.phoneVerified !== false || !bool(value.user.profileComplete)
    || value.user.emailVerified && !email(value.user.email)
    || value.user.profileComplete !== (value.user.emailVerified && email(value.user.email) && phone(value.user.phone))) return false;
  return exact(value.capabilities, ['operate', 'editSite', 'manageUsers']) && Object.values(value.capabilities).every(bool)
    && typeof value.csrfToken === 'string' && /^[\x21-\x7e]{32,256}$/.test(value.csrfToken) && date(value.expiresAt);
}
export const isAccountFlow = value => exact(value, ['flowId', 'resendAfterSeconds']) && id(value.flowId) && integer(value.resendAfterSeconds) && value.resendAfterSeconds <= 3600;
const orderBaseKeys = ['id', 'number', 'createdAt', 'status', 'totalKopecks'];
const baseOrder = value => object(value) && id(value.id) && text(value.number, 80, 1) && date(value.createdAt) && integer(value.totalKopecks) && ['new', 'confirmed', 'ready', 'completed', 'cancelled'].includes(value.status);
export const isOwnOrderReceipt = value => exact(value, orderBaseKeys) && baseOrder(value);
export const isOwnFulfillment = value => exact(value, ['method', 'address']) && ['pickup', 'delivery'].includes(value.method) && text(value.address, 500) && (value.method !== 'delivery' || value.address.trim().length > 0);
export const isOwnOrder = value => exact(value, [...orderBaseKeys, 'items', 'fulfillment']) && baseOrder(value) && isOwnFulfillment(value.fulfillment)
  && Array.isArray(value.items) && value.items.length > 0 && value.items.length <= 100 && value.items.every(item => exact(item, ['productId', 'sku', 'brand', 'name', 'quantity', 'unitPriceKopecks'], ['unit', 'lineTotalKopecks']) && id(item.productId) && text(item.sku, 80) && text(item.brand, 80) && text(item.name, 240, 1) && validSnapshotQuantity(item) && integer(item.unitPriceKopecks));
export const isOwnFeedback = value => exact(value, ['id', 'number', 'createdAt', 'type', 'message', 'status', 'order']) && id(value.id) && isFeedbackNumber(value.number) && date(value.createdAt)
  && ['contact', 'request'].includes(value.type) && typeof value.message === 'string' && [...value.message].length <= 2000 && !/[\u0000-\u0009\u000b\u000c\u000e-\u001f\u007f]/.test(value.message)
  && (value.type !== 'request' || value.message.trim().length > 0) && ['new', 'in_progress', 'converted', 'closed'].includes(value.status) && (value.order === null || isOwnOrderReceipt(value.order));
export function normalizeAccountInput(input, kind) {
  const keys = { signup: ['email', 'phone', 'password'], login: ['email', 'password'], reset: ['email'], confirm: ['flowId', 'code'], resend: ['flowId'], password: ['flowId', 'code', 'newPassword'], profile: ['email', 'phone'] }[kind];
  if (!keys || !exact(input, keys)) throw new Error('Проверьте поля формы.');
  const body = { ...input };
  if ('email' in body) { if (typeof body.email !== 'string') throw new Error('Проверьте email.'); body.email = body.email.trim().toLowerCase(); if (!email(body.email)) throw new Error('Проверьте email.'); }
  if ('phone' in body) { if (typeof body.phone !== 'string') throw new Error('Укажите российский телефон.'); const digits = body.phone.trim().replace(/[+\s()-]/g, ''); if (!/^[78][0-9]{10}$/.test(digits)) throw new Error('Укажите российский телефон: +7 и 10 цифр.'); body.phone = `+7${digits.slice(1)}`; }
  for (const key of ['password', 'newPassword']) if (key in body && (typeof body[key] !== 'string' || body[key].length < (kind === 'login' ? 1 : 12) || body[key].length > 256)) throw new Error('Пароль должен содержать от 12 до 256 символов.');
  if ('flowId' in body && !id(body.flowId)) throw new Error('Начните подтверждение заново.');
  if ('code' in body && (typeof body.code !== 'string' || !/^\d{6}$/.test(body.code))) throw new Error('Введите шестизначный код из письма.');
  return body;
}
export function normalizeOwnFeedback(input) {
  if (!exact(input, ['type', 'message']) || !['contact', 'request'].includes(input.type) || typeof input.message !== 'string' || [...input.message].length > 2000 || /[\u0000-\u0009\u000b\u000c\u000e-\u001f\u007f]/.test(input.message) || input.type === 'request' && !input.message.trim()) throw new Error('Проверьте тип и текст обращения.');
  return { type: input.type, message: input.message.trim() };
}
export function validateOwnOrder(input) {
  if (!exact(input, ['fulfillment', 'items'], ['name']) || !isOwnFulfillment(input.fulfillment) || input.name !== undefined && !text(input.name, 100)
    || !Array.isArray(input.items) || !input.items.length || input.items.length > 100 || !input.items.every(item => exact(item, ['productId', 'quantity', 'expectedUnitPriceKopecks'], ['expectedUnit']) && id(item.productId) && validRequestQuantity(item) && integer(item.expectedUnitPriceKopecks))) throw new Error('Проверьте данные заказа.');
  return input;
}
export const isAdminUser = value => exact(value, ['id', 'name', 'login', 'email', 'phone', 'emailVerified', 'phoneVerified', 'isActive', 'role', 'version']) && id(value.id) && text(value.name, 240)
  && (value.login === null || email(value.login)) && value.login === value.email && (value.email === null || text(value.email, 254)) && (value.phone === null || text(value.phone, 30))
  && bool(value.emailVerified) && value.phoneVerified === false && bool(value.isActive) && Object.hasOwn(USER_ROLES, value.role) && integer(value.version, 1);

import { PRODUCTS } from './catalog.js';

import { ORDER_STATUSES } from './order-labels.js';
export { ORDER_STATUSES };

export function allowedNextStatuses(status) {
  const transitions = { new: ['confirmed', 'cancelled'], confirmed: ['ready', 'cancelled'], ready: ['completed', 'cancelled'] };
  return Object.hasOwn(transitions, status) ? transitions[status] : [];
}

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function text(value) {
  return typeof value === 'string' ? value.trim() : '';
}

function validateCustomer(customer) {
  if (!isRecord(customer) || !text(customer.name)) throw new Error('Укажите имя покупателя.');
  if (customer.name.length > 100) throw new Error('Имя должно быть не длиннее 100 символов.');
  const phone = text(customer.phone);
  const digits = phone.replace(/[+\s()-]/g, '');
  if (typeof customer.phone !== 'string' || customer.phone.length > 30 || !/^[78]\d{10}$/.test(digits)) throw new Error('Укажите российский телефон: +7 и 10 цифр.');
  if (customer.email !== undefined && typeof customer.email !== 'string') throw new Error('Проверьте адрес электронной почты.');
  const email = text(customer.email);
  if (customer.email?.length > 254 || (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email))) throw new Error('Проверьте адрес электронной почты.');
  return { name: customer.name.trim(), phone: `+7${digits.slice(1)}`, email };
}

function validateFulfillment(fulfillment) {
  if (!isRecord(fulfillment) || !['pickup', 'delivery'].includes(fulfillment.method)) throw new Error('Выберите самовывоз или доставку.');
  const address = fulfillment.method === 'delivery' ? text(fulfillment.address) : '';
  if (fulfillment.method === 'delivery' && !address) throw new Error('Укажите адрес доставки.');
  if (fulfillment.method === 'delivery' && fulfillment.address.length > 500) throw new Error('Адрес должен быть не длиннее 500 символов.');
  return { method: fulfillment.method, address };
}

function totalItems(items) {
  const total = items.reduce((sum, item) => sum + item.unitPriceKopecks * item.quantity, 0);
  if (!Number.isSafeInteger(total) || total <= 0) throw new Error('Некорректная сумма заказа.');
  return total;
}

export function createOrder(cart, customer, fulfillment, products = PRODUCTS) {
  const checkedCustomer = validateCustomer(customer);
  const checkedFulfillment = validateFulfillment(fulfillment);
  if (!Array.isArray(cart) || cart.length === 0) throw new Error('Добавьте товары в корзину.');
  const items = [];
  for (const entry of cart) {
    if (!isRecord(entry) || !Number.isSafeInteger(entry.quantity) || entry.quantity <= 0) throw new Error('Проверьте количество товаров в корзине.');
    const product = products.find(product => product.id === entry.productId);
    if (!product) throw new Error('Товар из корзины больше недоступен.');
    const previous = items.find(item => item.productId === product.id);
    const quantity = (previous?.quantity || 0) + entry.quantity;
    if (!Number.isSafeInteger(quantity) || quantity > product.stock) throw new Error(`Недостаточно товара «${product.name}»: доступно ${product.stock} шт.`);
    if (previous) previous.quantity = quantity;
    else items.push({ productId: product.id, sku: product.sku, brand: product.brand, name: product.name, quantity, unitPriceKopecks: product.priceKopecks });
  }
  const id = crypto.randomUUID();
  const createdAt = new Date().toISOString();
  return {
    id,
    number: `L-${Date.now()}-${id.slice(0, 6).toUpperCase()}`,
    createdAt,
    customer: checkedCustomer,
    fulfillment: checkedFulfillment,
    items,
    totalKopecks: totalItems(items),
    status: 'new',
    integrationStatus: 'not_connected',
  };
}

export function changeOrderStatus(order, status) {
  if (!allowedNextStatuses(order.status).includes(status)) throw new Error('Этот переход статуса недоступен.');
  return { ...order, status };
}

export function sanitizeOrders(raw) {
  if (!Array.isArray(raw)) return [];
  const orders = [];
  for (const order of raw) {
    try {
      if (!isRecord(order) || !text(order.id) || !text(order.number) || !text(order.createdAt) || !Number.isFinite(Date.parse(order.createdAt))) continue;
      if (!Object.hasOwn(ORDER_STATUSES, order.status) || order.integrationStatus !== 'not_connected') continue;
      if (!isRecord(order.customer) || typeof order.customer.email !== 'string' || !isRecord(order.fulfillment) || typeof order.fulfillment.address !== 'string') continue;
      if (!Array.isArray(order.items) || order.items.length === 0 || !Number.isSafeInteger(order.totalKopecks)) continue;
      if (order.items.some(item => !isRecord(item) || !['productId', 'sku', 'brand', 'name'].every(key => text(item[key])) || !Number.isSafeInteger(item.quantity) || item.quantity <= 0 || !Number.isSafeInteger(item.unitPriceKopecks) || item.unitPriceKopecks <= 0)) continue;
      if (totalItems(order.items) !== order.totalKopecks) continue;
      const customer = validateCustomer(order.customer);
      const fulfillment = validateFulfillment(order.fulfillment);
      orders.push({
        id: order.id, number: order.number, createdAt: order.createdAt,
        customer, fulfillment,
        items: order.items.map(({ productId, sku, brand, name, quantity, unitPriceKopecks }) => ({ productId, sku, brand, name, quantity, unitPriceKopecks })),
        totalKopecks: order.totalKopecks, status: order.status, integrationStatus: 'not_connected',
      });
    } catch {
      // Повреждённая запись не должна мешать открыть остальные демозаказы.
    }
  }
  return orders;
}

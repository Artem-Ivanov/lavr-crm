export const ORDER_STATUSES = {
  new: 'Новый',
  confirmed: 'Подтверждён',
  ready: 'Готов к выдаче',
  completed: 'Завершён',
  cancelled: 'Отменён',
};

export function orderActions(order, demo = false) {
  if (demo) return ({ new: ['confirmed', 'cancelled'], confirmed: ['ready', 'cancelled'], ready: ['completed', 'cancelled'] })[order.status] || [];
  if (order.status === 'new') return order.canCancel ? ['cancelled'] : [];
  if (order.status === 'confirmed') return ['ready'];
  if (order.status === 'ready') return ['completed'];
  return [];
}

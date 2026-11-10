export const ORDER_STATUSES = {
  new: 'Новый',
  confirmed: 'Подтверждён',
  ready: 'Готов к выдаче',
  completed: 'Завершён',
  cancelled: 'Отменён',
};

export function orderActions(order, demo = false) {
  if (demo || order.integrationStatus === 'not_connected' && !order.externalDocumentId) {
    const actions = ({ new: ['confirmed', 'cancelled'], confirmed: ['ready', 'cancelled'], ready: ['completed', 'cancelled'] })[order.status] || [];
    return actions.filter(action => demo || action !== 'cancelled' || order.canCancel);
  }
  if (order.status === 'new') return order.canCancel ? ['cancelled'] : [];
  if (order.status === 'confirmed') return ['ready'];
  if (order.status === 'ready') return ['completed'];
  return [];
}

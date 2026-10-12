export function parseCartDraft(raw) {
  if (!Array.isArray(raw)) return [];
  const quantities = new Map();
  for (const item of raw) {
    if (!item || typeof item.productId !== 'string' || !item.productId || item.productId.length > 128 || !Number.isSafeInteger(item.quantity) || item.quantity <= 0) continue;
    const quantity = (quantities.get(item.productId) || 0) + item.quantity;
    if (Number.isSafeInteger(quantity)) quantities.set(item.productId, quantity);
  }
  return [...quantities].map(([productId, quantity]) => ({ productId, quantity }));
}

export function setDraftQuantity(cart, productId, quantity, stock) {
  if (!Number.isSafeInteger(quantity) || quantity < 0) throw new Error('Введите целое неотрицательное количество.');
  if (quantity > stock) throw new Error(`Доступно только ${stock} шт.`);
  const next = cart.filter(item => item.productId !== productId);
  if (!quantity) return next;
  const existing = cart.findIndex(item => item.productId === productId);
  if (existing >= 0) next.splice(existing, 0, { productId, quantity });
  else next.push({ productId, quantity });
  return next;
}

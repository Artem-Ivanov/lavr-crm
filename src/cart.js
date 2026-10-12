import { PRODUCTS } from './catalog.js';

export function sanitizeCart(raw, products = PRODUCTS) {
  if (!Array.isArray(raw)) return [];
  const quantities = new Map();
  for (const item of raw) {
    if (!item || !Number.isSafeInteger(item.quantity) || item.quantity <= 0) continue;
    const product = products.find(product => product.id === item.productId);
    if (!product || product.stock <= 0) continue;
    quantities.set(product.id, Math.min(product.stock, (quantities.get(product.id) || 0) + item.quantity));
  }
  return Array.from(quantities, ([productId, quantity]) => ({ productId, quantity }));
}

export function setCartQuantity(cart, productId, quantity, products = PRODUCTS) {
  const product = products.find(product => product.id === productId);
  if (!product) throw new Error('Товар не найден.');
  if (!Number.isSafeInteger(quantity) || quantity < 0) throw new Error('Введите целое неотрицательное количество.');
  if (quantity > product.stock) throw new Error(`Доступно только ${product.stock} шт.`);
  const result = sanitizeCart(cart, products);
  const index = result.findIndex(item => item.productId === productId);
  if (quantity === 0) return result.filter(item => item.productId !== productId);
  if (index === -1) result.push({ productId, quantity });
  else result[index] = { productId, quantity };
  return result;
}

export function addCartItem(cart, productId, products = PRODUCTS) {
  const result = sanitizeCart(cart, products);
  const quantity = result.find(item => item.productId === productId)?.quantity || 0;
  return setCartQuantity(result, productId, quantity + 1, products);
}

export function cartTotal(cart, products = PRODUCTS) {
  const total = sanitizeCart(cart, products).reduce((sum, item) =>
    sum + products.find(product => product.id === item.productId).priceKopecks * item.quantity, 0);
  if (!Number.isSafeInteger(total)) throw new Error('Не удалось рассчитать сумму корзины.');
  return total;
}

export function cartCount(cart) {
  return cart.reduce((sum, item) => sum + item.quantity, 0);
}

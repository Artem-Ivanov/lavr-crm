import { normalizeAccountInput, validateOwnOrder } from '../accounts.js';
import { filterProducts } from '../catalog.js';
import {
  initialCatalog, restoreCatalog, createCategory as addCategory,
  createProduct as addProduct, assignProductCategory as assignCategory,
} from '../catalog-admin.js';
import { createOrder as buildOrder, changeOrderStatus, sanitizeOrders } from '../orders.js';
import { createDemoContentApi } from './demo-content-api.js';
import { createDemoFeedbackApi } from './demo-feedback-api.js';

const CATALOG_KEY = 'lavr.catalog.v1';
const ORDERS_KEY = 'lavr.orders.v1';
const CATALOG_READ_ERROR = 'Сохранённый каталог повреждён или недоступен. Изменения не применены, чтобы сохранить исходные данные.';
const CATALOG_WRITE_ERROR = 'Не удалось сохранить каталог в браузере. Изменения не применены; разрешите хранилище и попробуйте снова.';
const ORDERS_READ_ERROR = 'Не удалось прочитать сохранённые данные браузера. Недоступные записи не восстановлены.';
const ORDERS_WRITE_ERROR = 'Не удалось сохранить демозаказ в браузере. Корзина сохранена; разрешите хранилище и попробуйте снова.';

function page(items, { offset = 0, limit = 24 } = {}) {
  if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1) {
    throw new Error('Некорректные параметры пагинации.');
  }
  return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
}

// shortcut: данные и повторные запросы живут в браузере, использовать HTTP API для реальных заказов.
export function createDemoApi({ localStorage = globalThis.localStorage, sessionStorage = globalThis.sessionStorage } = {}) {
  const requests = new Map();
  let currentSession = null;
  const users = ['partner', 'operator', 'admin'].map((role, index) => ({ id: `demo-${role}`, name: `Демо ${role}`, login: `${role}@example.test`, email: `${role}@example.test`, phone: `+7999000000${index + 1}`, emailVerified: true, phoneVerified: false, isActive: true, role, version: 1 }));
  function demoSession(user) {
    const { id, name, email, phone, emailVerified, phoneVerified } = user;
    return { user: { id, name, email, phone, emailVerified, phoneVerified, profileComplete: true }, capabilities: { operate: user.role !== 'partner', editSite: user.role === 'admin', manageUsers: user.role === 'admin' }, csrfToken: 'demo-no-authentication-000000000000', expiresAt: new Date(Date.now() + 86400000).toISOString(), demo: true };
  }
  function signedIn() { if (!currentSession) throw Object.assign(new Error('Войдите в демонстрационный аккаунт.'), { status: 401 }); return currentSession; }
  function loginWithEmail(input) {
    const body = normalizeAccountInput(input, 'login');
    const user = users.find(user => user.email === body.email && user.isActive);
    if (!user) throw new Error('В симуляции используйте partner@example.test, operator@example.test или admin@example.test.');
    currentSession = demoSession(user);
    return structuredClone(currentSession);
  }
  function unavailableEmail() { throw new Error('В демонстрации регистрация, коды и письма недоступны. Используйте тестовый вход.'); }
  const ownOrders = new Map();
  const ownAttempts = new Map();
  const customerOrder = order => Object.fromEntries(['id', 'number', 'createdAt', 'status', 'totalKopecks', 'items', 'fulfillment'].map(key => [key, structuredClone(order[key])]));
  function adminUser(id) { if (!signedIn().capabilities.manageUsers) throw Object.assign(new Error('Доступ недоступен.'), { status: 403 }); const user = users.find(user => user.id === id); if (!user) throw Object.assign(new Error('Пользователь не найден.'), { status: 404 }); return user; }


  function readCatalog(strict = false) {
    try {
      return { catalog: restoreCatalog(JSON.parse(localStorage.getItem(CATALOG_KEY) ?? 'null')), readOnly: false, issue: '' };
    } catch {
      if (strict) throw new Error(CATALOG_READ_ERROR);
      return { catalog: initialCatalog(), readOnly: true, issue: CATALOG_READ_ERROR };
    }
  }
  function saveCatalog(catalog) {
    try { localStorage.setItem(CATALOG_KEY, JSON.stringify(catalog)); }
    catch { throw new Error(CATALOG_WRITE_ERROR); }
  }
  function readOrders(strict = false) {
    try {
      const raw = JSON.parse(sessionStorage.getItem(ORDERS_KEY) ?? '[]');
      if (!Array.isArray(raw)) throw new Error(ORDERS_READ_ERROR);
      return { orders: sanitizeOrders(raw), readOnly: false, issue: '' };
    } catch {
      if (strict) throw new Error(ORDERS_READ_ERROR);
      return { orders: [], readOnly: true, issue: ORDERS_READ_ERROR };
    }
  }
  function saveOrders(orders) {
    try { sessionStorage.setItem(ORDERS_KEY, JSON.stringify(orders)); }
    catch { throw new Error(ORDERS_WRITE_ERROR); }
  }
  async function getCatalog(filters = {}, paging = {}) {
    const { catalog, readOnly, issue } = readCatalog();
    return {
      ...page(filterProducts(catalog.products.filter(product => product.unit !== null), filters), paging),
      stale: false, catalogUpdatedAt: null, readOnly, issue,
    };
  }

  const api = {
    mode: 'demo',
    canCreateProduct: true,
    ...createDemoContentApi({ localStorage, getCatalog: () => readCatalog(true).catalog }),
    async getCategories() {
      const { catalog } = readCatalog();
      return catalog.categories.map(category => ({
        ...category, productCount: catalog.products.filter(product => product.category === category.id).length,
      }));
    },
    getCatalog,
    async getAdminProducts(filters = {}, paging = {}) {
      const { catalog, readOnly, issue } = readCatalog();
      return { ...page(filterProducts(catalog.products, filters).map(product => ({ ...product, physicalStock: product.stock, reservedStock: 0, isVisible: true })), paging), readOnly, issue };
    },
    async getProduct(id) {
      const product = readCatalog().catalog.products.find(product => product.id === id);
      if (!product || product.unit === null) throw new Error('Товар не найден.');
      return { product, catalogUpdatedAt: null, stale: false };
    },
    async getOrders({ query = '', status = '' } = {}, paging = {}) {
      const { orders, readOnly, issue } = readOrders();
      orders.push(...[...ownOrders.values()].map(entry => entry.order));
      const search = String(query).toLocaleLowerCase('ru');
      const items = orders.filter(order => (!status || order.status === status) &&
        (!search || `${order.number} ${order.customer.name} ${order.customer.phone}`.toLocaleLowerCase('ru').includes(search)));
      return { ...page(items, paging), readOnly, issue };
    },
    async getOrder(id) {
      const order = readOrders(true).orders.find(order => order.id === id) || ownOrders.get(id)?.order;
      if (!order) throw new Error('Заказ не найден.');
      return order;
    },
    async createOrder(body, key, customerId = null) {
      const signature = JSON.stringify(body, [
        'customer', 'name', 'phone', 'email', 'fulfillment', 'method', 'address',
        'items', 'productId', 'quantity', 'expectedUnitPriceKopecks', 'expectedUnit',
      ]);
      if (key && requests.has(key)) {
        const previous = requests.get(key);
        if (previous.signature !== signature) throw new Error('Ключ повторного запроса уже использован для другого заказа.');
        return structuredClone(previous.order);
      }
      const { catalog } = readCatalog(true);
      const order = buildOrder(body?.items, body?.customer, body?.fulfillment, catalog.products);
      for (const item of body.items) {
        if (!Number.isSafeInteger(item.expectedUnitPriceKopecks) || item.expectedUnitPriceKopecks <= 0) {
          throw new Error('Проверьте ожидаемую цену товара.');
        }
        const product = catalog.products.find(product => product.id === item.productId);
        if (item.expectedUnitPriceKopecks !== product.priceKopecks) {
          throw new Error(`Цена товара «${product.name}» изменилась. Обновите корзину и попробуйте снова.`);
        }
      }
      if (customerId) ownOrders.set(order.id, { actor: customerId, order });
      else saveOrders([order, ...readOrders(true).orders]);
      if (key) requests.set(key, { signature, order: structuredClone(order) });
      return order;
    },
    async updateOrderStatus(orderOrId, status) {
      const id = typeof orderOrId === 'string' ? orderOrId : orderOrId?.id;
      const { orders } = readOrders(true);
      const owned = ownOrders.get(id);
      const order = owned?.order || orders.find(order => order.id === id);
      if (!order) throw new Error('Заказ не найден.');
      const updated = changeOrderStatus(order, status);
      if (owned) ownOrders.set(id, { ...owned, order: updated });
      else saveOrders(orders.map(order => order.id === id ? updated : order));
      return updated;
    },
    async createCategory(input) {
      const next = addCategory(readCatalog(true).catalog, input);
      saveCatalog(next);
      return next.categories.at(-1);
    },
    async createProduct(draft) {
      const next = addProduct(readCatalog(true).catalog, draft);
      saveCatalog(next);
      return next.products.at(-1);
    },
    async assignProductCategory(id, categoryId) {
      const next = assignCategory(readCatalog(true).catalog, id, categoryId);
      saveCatalog(next);
      return next.products.find(product => product.id === id);
    },
    async getSession() { return structuredClone(signedIn()); },
    async getUserSession() { return structuredClone(signedIn()); },
    async login(input = {}) { const { login, ...credentials } = input; return loginWithEmail({ ...credentials, email: credentials.email ?? login }); },
    async loginUser(input) { return loginWithEmail(input); },
    async logout() { currentSession = null; },
    signup: async () => unavailableEmail(), activate: async () => unavailableEmail(), resendCode: async () => unavailableEmail(), requestPasswordReset: async () => unavailableEmail(), confirmPasswordReset: async () => unavailableEmail(), startProfileActivation: async () => unavailableEmail(), confirmProfileActivation: async () => unavailableEmail(),
    async getOwnOrders(paging) { const actor = signedIn().user.id; return page([...ownOrders.values()].filter(entry => entry.actor === actor).map(entry => customerOrder(entry.order)), paging); },
    async getOwnOrder(id) { const actor = signedIn().user.id; const entry = ownOrders.get(id); if (!entry || entry.actor !== actor) throw Object.assign(new Error('Заказ не найден.'), { status: 404 }); return customerOrder(entry.order); },
    async createOwnOrder(input, key) {
      validateOwnOrder(input);
      if (typeof key !== 'string' || !/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(key)) throw new Error('Некорректный ключ заказа.');
      const actor = signedIn().user.id;
      const signature = JSON.stringify({ actor, input });
      if (ownAttempts.has(key)) { const previous = ownAttempts.get(key); if (previous.signature !== signature) throw Object.assign(new Error('Ключ другой попытки.'), { status: 409 }); return api.getOwnOrder(previous.id); }
      const user = signedIn().user;
      const products = readCatalog(true).catalog.products;
      for (const item of input.items) if (products.find(product => product.id === item.productId)?.priceKopecks !== item.expectedUnitPriceKopecks) throw new Error('Цена изменилась. Обновите корзину.');
      const order = buildOrder(input.items, { name: input.name || '', email: user.email, phone: user.phone }, input.fulfillment, products, { allowEmptyName: true });
      ownOrders.set(order.id, { actor, order }); ownAttempts.set(key, { signature, id: order.id });
      return customerOrder(order);
    },
    async listUsers({ query = '' } = {}, paging) { adminUser(signedIn().user.id); return page(structuredClone(users.filter(user => !query || [user.email, user.phone, user.login].includes(query))), paging); },
    async getUser(id) { return structuredClone(adminUser(id)); },
    async updateUserRole(id, role, version) { const user = adminUser(id); if (!['partner', 'operator', 'admin'].includes(role)) throw new Error('Недопустимая роль.'); return updateUser(user, { role }, version); },
    async updateUserStatus(id, isActive, version) { const user = adminUser(id); if (typeof isActive !== 'boolean') throw new Error('Недопустимый статус.'); return updateUser(user, { isActive }, version); },
  };
  function updateUser(user, patch, version) {
    if (user.version !== version) throw Object.assign(new Error('Пользователь изменился.'), { status: 412 });
    if (user.role === 'admin' && user.isActive && (patch.role && patch.role !== 'admin' || patch.isActive === false) && users.filter(user => user.role === 'admin' && user.isActive).length <= 1) throw new Error('Нельзя отключить последнего администратора.');
    Object.assign(user, patch, { version: user.version + 1 });
    if (currentSession?.user.id === user.id) currentSession = null;
    return structuredClone(user);
  }
  return { ...api, ...createDemoFeedbackApi({ getSession: api.getSession, getCustomerSession: api.getUserSession, createOrder: api.createOrder }) };
}

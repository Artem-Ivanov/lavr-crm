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
  const session = {
    user: { id: 'demo-admin', name: 'Демонстрационный администратор', role: 'admin' },
    csrfToken: 'demo-no-authentication-000000000000',
    expiresAt: new Date(Date.now() + 86400000).toISOString(),
    demo: true,
  };

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
      ...page(filterProducts(catalog.products, filters), paging),
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
    getAdminProducts: getCatalog,
    async getProduct(id) {
      const product = readCatalog().catalog.products.find(product => product.id === id);
      if (!product) throw new Error('Товар не найден.');
      return { product, catalogUpdatedAt: null, stale: false };
    },
    async getOrders({ query = '', status = '' } = {}, paging = {}) {
      const { orders, readOnly, issue } = readOrders();
      const search = String(query).toLocaleLowerCase('ru');
      const items = orders.filter(order => (!status || order.status === status) &&
        (!search || `${order.number} ${order.customer.name} ${order.customer.phone}`.toLocaleLowerCase('ru').includes(search)));
      return { ...page(items, paging), readOnly, issue };
    },
    async getOrder(id) {
      const order = readOrders(true).orders.find(order => order.id === id);
      if (!order) throw new Error('Заказ не найден.');
      return order;
    },
    async createOrder(body, key) {
      const signature = JSON.stringify(body, [
        'customer', 'name', 'phone', 'email', 'fulfillment', 'method', 'address',
        'items', 'productId', 'quantity', 'expectedUnitPriceKopecks',
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
      saveOrders([order, ...readOrders(true).orders]);
      if (key) requests.set(key, { signature, order: structuredClone(order) });
      return order;
    },
    async updateOrderStatus(orderOrId, status) {
      const id = typeof orderOrId === 'string' ? orderOrId : orderOrId?.id;
      const { orders } = readOrders(true);
      const order = orders.find(order => order.id === id);
      if (!order) throw new Error('Заказ не найден.');
      const updated = changeOrderStatus(order, status);
      saveOrders(orders.map(order => order.id === id ? updated : order));
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
    async getSession() { return structuredClone(session); },
    async login() { return structuredClone(session); },
    async logout() {},
  };
  return { ...api, ...createDemoFeedbackApi({ getSession: api.getSession, createOrder: api.createOrder }) };
}

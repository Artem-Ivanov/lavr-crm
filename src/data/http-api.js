import { isSiteConfiguration, isPublication, isAdminMedia, validateSitePatch, validatePublicationInput, validateMediaUpload } from '../content.js';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value, min = 0, max = 4000) => typeof value === 'string' && value.length >= min && value.length <= max;
const integer = (value, min = 0, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && value >= min && value <= max;
const id = value => text(value, 1, 128) && !/[\u0000-\u001f\u007f]/.test(value) && value !== '.' && value !== '..';
const timestamp = value => text(value, 1, 80) && Number.isFinite(Date.parse(value));
const list = (value, check, max = Number.MAX_SAFE_INTEGER) => Array.isArray(value) && value.length <= max && value.every(check);
const imagePath = value => typeof value === 'string' && /^(?:|\/api\/media\/[A-Za-z0-9_-]+|\/images\/(?:hero-car|motor-oil|oil-filter|brake-disc|spark-plug|air-filter)\.jpg)$/.test(value);
const media = value => object(value) && id(value.id) && /^\/api\/media\/[A-Za-z0-9_-]+$/.test(value.path)
  && text(value.alt, 0, 240) && integer(value.width, 1, 4096) && integer(value.height, 1, 4096);
const nullableText = (value, min, max) => value === null || text(value, min, max);
const productCore = value => object(value) && id(value.id) && id(value.category) && text(value.sku, 1, 80)
  && text(value.brand, 1, 80) && text(value.name, 1, 240) && integer(value.priceKopecks) && integer(value.stock, 0, 2147483647);
const product = value => productCore(value) && id(value.externalId) && imagePath(value.image) && integer(value.deliveryDays, 0, 365)
  && (value.images === undefined || list(value.images, media, 8))
  && (value.websiteTitle === undefined || nullableText(value.websiteTitle, 1, 240))
  && (value.websiteDescription === undefined || nullableText(value.websiteDescription, 0, 4000));
const adminProduct = value => productCore(value) && typeof value.isVisible === 'boolean';
const category = value => object(value) && id(value.id) && text(value.name, 1, 80) && integer(value.position, 0, 1000000) && typeof value.showInMenu === 'boolean';
const adminCategory = value => category(value) && typeof value.isVisible === 'boolean' && integer(value.version, 1);
const orderStatuses = ['new', 'confirmed', 'ready', 'completed', 'cancelled'];
const integrationStatuses = ['not_connected', 'pending_sync', 'exporting', 'exported', 'error', 'cancelled_before_sync'];
const receipt = value => object(value) && id(value.id) && text(value.number, 1, 80) && timestamp(value.createdAt)
  && integer(value.totalKopecks) && orderStatuses.includes(value.status) && integrationStatuses.includes(value.integrationStatus);
const customer = value => object(value) && text(value.name, 1, 100) && text(value.phone, 10, 30) && (value.email === undefined || text(value.email, 0, 254));
const fulfillment = value => object(value) && ['pickup', 'delivery'].includes(value.method)
  && (value.address === undefined || text(value.address, 0, 500)) && (value.method !== 'delivery' || text(value.address, 1, 500) && value.address.trim().length > 0);
const orderItem = value => object(value) && id(value.productId) && text(value.sku, 1, 80) && text(value.brand, 1, 80)
  && text(value.name, 1, 240) && integer(value.quantity, 1, 999) && integer(value.unitPriceKopecks);
const managerOrder = value => receipt(value) && customer(value.customer) && fulfillment(value.fulfillment)
  && list(value.items, orderItem) && value.items.length > 0 && integer(value.version, 1)
  && typeof value.canCancel === 'boolean' && nullableText(value.externalDocumentId, 0, 128);
const managerSession = value => object(value) && object(value.user) && id(value.user.id) && text(value.user.name, 1, 240)
  && ['manager', 'admin'].includes(value.user.role) && typeof value.csrfToken === 'string'
  && /^[\x21-\x7e]{32,256}$/.test(value.csrfToken) && timestamp(value.expiresAt);
const presentation = value => object(value) && id(value.productId) && id(value.categoryId) && integer(value.version, 1)
  && nullableText(value.websiteTitle, 1, 240) && nullableText(value.websiteDescription, 0, 4000) && list(value.imageIds, id, 8);
const target = value => object(value) && (value.type === 'catalog'
  || value.type === 'category' && id(value.categoryId) || value.type === 'product' && id(value.productId)
  || value.type === 'section' && ['offers', 'announcements', 'hot'].includes(value.section));
const publicSite = value => object(value) && object(value.hero) && text(value.hero.title, 1, 240)
  && text(value.hero.description, 0, 2000) && text(value.hero.buttonText, 1, 80)
  && (value.hero.target === null || target(value.hero.target)) && (value.hero.image === null || media(value.hero.image))
  && object(value.settings) && text(value.settings.storeName, 1, 120) && text(value.settings.phone, 10, 30)
  && text(value.settings.address, 1, 500) && text(value.settings.hours, 1, 240)
  && ['categoriesTitle', 'promotionsTitle', 'catalogTitle'].every(key => text(value.settings[key], 1, 120))
  && list(value.menu, item => object(item) && text(item.label, 1, 80) && target(item.target)
    && integer(item.position, 0, 1000000) && item.isVisible === true, 12)
  && list(value.publications, item => object(item) && id(item.id) && ['offer', 'announcement', 'hot'].includes(item.kind)
    && text(item.title, 1, 240) && text(item.description, 0, 4000) && list(item.images, media, 8)
    && target(item.target) && text(item.buttonText, 1, 80) && integer(item.position, 0, 1000000));

function apiError(message, status, code) {
  return Object.assign(new Error(message), { status, code });
}
function checked(value, check) {
  if (!check(value)) throw apiError('Некорректный ответ API. Обновите страницу или обратитесь к администратору.', undefined, 'INVALID_RESPONSE');
  return value;
}
function checkedVersion({ data, etag }, check) {
  checked(data, check);
  if (etag !== null && etag !== `"${data.version}"`) throw apiError('Некорректная версия в ответе API.', undefined, 'INVALID_RESPONSE');
  return data;
}
function versionHeader(version) {
  if (!integer(version, 1)) throw new Error('Некорректная версия. Обновите данные перед сохранением.');
  return `"${version}"`;
}
function pathId(value) {
  if (!id(value)) throw new Error('Некорректный идентификатор.');
  return encodeURIComponent(value);
}
function pagination({ offset = 0, limit = 24 } = {}) {
  if (!integer(offset) || !integer(limit, 1, 100)) throw new Error('Некорректные параметры пагинации.');
  return { offset, limit };
}
function queryString(values) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, value);
  }
  return params.size ? `?${params}` : '';
}

export function createHttpApi({ baseUrl = '/api', fetchImpl = globalThis.fetch } = {}) {
  if (typeof baseUrl !== 'string' || !/^\/(?:[A-Za-z0-9_-]+\/?)*$/.test(baseUrl)) throw new Error('Адрес API должен быть путём на том же origin, например /api.');
  if (typeof fetchImpl !== 'function') throw new Error('Fetch API недоступен.');
  const base = baseUrl.replace(/\/$/, '');
  let csrfToken = null;
  let sessionGeneration = 0;

  async function request(path, { method = 'GET', body, headers = {}, protectedMutation = false, empty = false } = {}) {
    const generation = sessionGeneration;
    const requestHeaders = { Accept: 'application/json', ...headers };
    const multipart = body instanceof FormData;
    if (body !== undefined && !multipart) requestHeaders['Content-Type'] = 'application/json';
    if (protectedMutation) {
      if (!csrfToken) throw apiError('Войдите в CRM, чтобы выполнить изменение.', 401, 'SESSION_REQUIRED');
      requestHeaders['X-CSRF-Token'] = csrfToken;
    }
    const response = await fetchImpl(`${base}${path}`, {
      method, credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: requestHeaders,
      ...(body === undefined ? {} : { body: multipart ? body : JSON.stringify(body) }),
    });
    if (response.status === 401 && generation === sessionGeneration) csrfToken = null;
    if (response.status === 204 && response.ok && empty) return { data: undefined };
    let data;
    if (/^application\/json(?:\s*;|$)/i.test(response.headers.get('Content-Type') || '')) {
      try { data = await response.json(); } catch { /* An error page must never replace application data. */ }
    }
    if (!response.ok) {
      const message = object(data) && text(data.message, 1, 2000) ? data.message : `Ошибка API (${response.status}). Попробуйте позже.`;
      const code = object(data) && text(data.code, 1, 128) ? data.code : 'HTTP_ERROR';
      throw apiError(message, response.status, code);
    }
    if (data === undefined) throw apiError('API вернул некорректный ответ JSON.', response.status, 'INVALID_RESPONSE');
    return { data, etag: response.headers.get('ETag') };
  }

  async function getPage(path, filters, requested, checkItem) {
    const { offset, limit } = pagination(requested);
    const { data } = await request(`${path}${queryString({ ...filters, offset, limit })}`);
    return checked(data, value => object(value) && integer(value.total) && value.offset === offset && value.limit === limit
      && list(value.items, checkItem, limit) && value.items.length <= Math.max(0, value.total - offset));
  }
  async function loadSession(method, body) {
    const generation = ++sessionGeneration;
    csrfToken = null;
    const { data } = await request('/manager/session', { method, body });
    checked(data, managerSession);
    if (generation === sessionGeneration) csrfToken = data.csrfToken;
    return data;
  }

  return {
    mode: 'api',
    canCreateProduct: false,
    async getSite() {
      return checked((await request('/site')).data, publicSite);
    },
    async getAdminSite() {
      return checkedVersion(await request('/admin/site'), isSiteConfiguration);
    },
    async updateSite(patch, version) {
      const headers = { 'If-Match': versionHeader(version) };
      const body = validateSitePatch(patch);
      return checkedVersion(await request('/admin/site', { method: 'PATCH', body, headers, protectedMutation: true }), isSiteConfiguration);
    },
    async getPublications({ kind = '', status = '' } = {}, requested) {
      if (kind !== '' && !['offer', 'announcement', 'hot'].includes(kind)
        || status !== '' && !['draft', 'published', 'archived'].includes(status)) throw new Error('Некорректные фильтры публикаций.');
      return getPage('/admin/publications', { kind, status }, requested, isPublication);
    },
    async getPublication(publicationId) {
      return checkedVersion(await request(`/admin/publications/${pathId(publicationId)}`), isPublication);
    },
    async createPublication(input) {
      const body = validatePublicationInput(input);
      return checkedVersion(await request('/admin/publications', { method: 'POST', body, protectedMutation: true }), isPublication);
    },
    async updatePublication(publicationId, patch, version) {
      const path = `/admin/publications/${pathId(publicationId)}`;
      const headers = { 'If-Match': versionHeader(version) };
      const body = validatePublicationInput(patch, { partial: true });
      return checkedVersion(await request(path, { method: 'PATCH', body, headers, protectedMutation: true }), isPublication);
    },
    async getMedia(requested) {
      return getPage('/admin/media', {}, requested, isAdminMedia);
    },
    async uploadMedia(file, alt = '') {
      validateMediaUpload(file, alt);
      if (!(file instanceof Blob)) throw new Error('Выберите файл изображения JPG, PNG или WebP.');
      const body = new FormData();
      body.append('file', file);
      body.append('alt', alt);
      return checked((await request('/admin/media', { method: 'POST', body, protectedMutation: true })).data, isAdminMedia);
    },
    async getCategories({ admin = false } = {}) {
      if (!admin) return checked((await request('/categories')).data, value => list(value, category));
      const items = [];
      let total;
      do {
        const page = await getPage('/admin/categories', {}, { offset: items.length, limit: 100 }, adminCategory);
        if (total !== undefined && page.total !== total || page.items.length === 0 && items.length < page.total) {
          throw apiError('Некорректный ответ пагинации категорий. Обновите список.', undefined, 'INVALID_RESPONSE');
        }
        total = page.total;
        items.push(...page.items);
      } while (items.length < total);
      return items;
    },
    async getCatalog(filters = {}, requested) {
      const page = await getPage('/catalog', {
        q: filters.query, category: filters.category, brand: filters.brand, inStock: filters.inStock, sort: filters.sort,
      }, requested, product);
      return checked(page, value => timestamp(value.catalogUpdatedAt) && typeof value.stale === 'boolean');
    },
    async getProduct(productId) {
      const { data } = await request(`/catalog/${pathId(productId)}`);
      checked(data, value => object(value) && product(value.product) && timestamp(value.catalogUpdatedAt) && typeof value.stale === 'boolean');
      return data;
    },
    async getAdminProducts({ query = '', category = '' } = {}, requested) {
      return getPage('/admin/products', { q: query, category }, requested, adminProduct);
    },
    async getOrders({ query = '', status = '' } = {}, requested) {
      return getPage('/manager/orders', { q: query, status }, requested, managerOrder);
    },
    async getOrder(orderId) {
      return checked((await request(`/manager/orders/${pathId(orderId)}`)).data, managerOrder);
    },
    async createOrder(body, idempotencyKey) {
      if (typeof idempotencyKey !== 'string' || !/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(idempotencyKey)) throw new Error('Некорректный ключ оформления заказа.');
      if (!object(body) || !customer(body.customer) || !fulfillment(body.fulfillment)
        || !list(body.items, item => object(item) && id(item.productId) && integer(item.quantity, 1, 999) && integer(item.expectedUnitPriceKopecks), 100)
        || body.items.length === 0) throw new Error('Некорректные данные заказа.');
      return checked((await request('/orders', { method: 'POST', body, headers: { 'Idempotency-Key': idempotencyKey } })).data, receipt);
    },
    async updateOrderStatus(order, status) {
      if (!object(order) || !integer(order.version, 1) || !['ready', 'completed', 'cancelled'].includes(status)) throw new Error('Некорректный статус или версия заказа.');
      return checked((await request(`/manager/orders/${pathId(order.id)}/status`, {
        method: 'PATCH', body: { status }, protectedMutation: true, headers: { 'If-Match': `"${order.version}"` },
      })).data, managerOrder);
    },
    async createCategory(input) {
      if (!object(input) || !text(input.name, 1, 80) || !integer(input.position, 0, 1000000)
        || typeof input.isVisible !== 'boolean' || typeof input.showInMenu !== 'boolean') throw new Error('Некорректные данные категории.');
      const { name, position, isVisible, showInMenu } = input;
      return checked((await request('/admin/categories', { method: 'POST', body: { name, position, isVisible, showInMenu }, protectedMutation: true })).data, adminCategory);
    },
    async createProduct() {
      throw new Error('Создание номенклатуры будет доступно после согласования адаптера 1С.');
    },
    async assignProductCategory(productId, categoryId) {
      const path = `/admin/products/${pathId(productId)}/presentation`;
      if (!id(categoryId)) throw new Error('Некорректный идентификатор категории.');
      const { data, etag } = await request(path);
      checked(data, value => object(value) && product(value.product) && presentation(value.presentation));
      const version = `"${data.presentation.version}"`;
      if (etag !== null && etag !== version) throw apiError('Некорректная версия в ответе API.', undefined, 'INVALID_RESPONSE');
      return checked((await request(path, {
        method: 'PATCH', body: { categoryId }, protectedMutation: true, headers: { 'If-Match': etag || version },
      })).data, presentation);
    },
    async getSession() {
      return loadSession('GET');
    },
    async login({ login, password }) {
      if (!text(login, 1, 120) || !text(password, 1, 256)) throw new Error('Введите логин и пароль.');
      return loadSession('POST', { login, password });
    },
    async logout() {
      const generation = ++sessionGeneration;
      await request('/manager/session', { method: 'DELETE', protectedMutation: true, empty: true });
      if (generation === sessionGeneration) csrfToken = null;
    },
  };
}

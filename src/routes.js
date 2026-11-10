const pages = ['home', 'catalog', 'cart', 'crm', 'crm-catalog', 'crm-content', 'crm-feedback', 'admin-users', 'login', 'signup', 'activate', 'password-reset', 'account', 'account-orders', 'account-feedback'];
const authPages = ['login', 'signup', 'activate', 'password-reset'];
const fields = { category: 'categoryId', product: 'productId', section: 'section', publication: 'publicationId', order: 'orderId', case: 'caseId', user: 'userId', tab: 'tab', returnTo: 'returnTo' };
const pageFields = {
  home: ['section', 'publication'], catalog: ['category', 'product'], crm: ['order'],
  'crm-feedback': ['case'], 'account-orders': ['order'], 'account-feedback': ['case'],
  'admin-users': ['user'], 'crm-content': ['tab', 'publication'],
  login: ['returnTo'], signup: ['returnTo'], activate: ['returnTo'], 'password-reset': ['returnTo'], account: ['returnTo'],
};
const controls = /[\u0000-\u001f\u007f]/;
const loneSurrogate = /[\uD800-\uDFFF]/u;
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const id = value => typeof value === 'string' && [...value].length >= 1 && [...value].length <= 128
  && !controls.test(value) && !loneSurrogate.test(value) && value !== '.' && value !== '..';
const defaults = (page = 'home', valid = true) => ({
  page, caseId: '', orderId: '', categoryId: '', productId: '', section: '', publicationId: '',
  userId: '', tab: 'site', returnTo: '', valid,
});

function queryParts(hash) {
  if (typeof hash !== 'string' || controls.test(hash) || loneSurrogate.test(hash)) throw new TypeError('Invalid route');
  const fragment = hash.startsWith('#') ? hash.slice(1) : hash;
  const separator = fragment.indexOf('?');
  const page = separator < 0 ? fragment : fragment.slice(0, separator);
  const query = separator < 0 ? '' : fragment.slice(separator + 1);
  // URLSearchParams replaces malformed escapes; reject them before its single decode.
  for (const part of query.split('&')) {
    const equals = part.indexOf('=');
    decodeURIComponent(equals < 0 ? part : part.slice(0, equals));
    decodeURIComponent(equals < 0 ? '' : part.slice(equals + 1));
  }
  return { page: page || 'home', params: new URLSearchParams(query) };
}

export function routeHash(route) {
  if (!object(route) || !pages.includes(route.page)) throw new TypeError('Invalid route');
  const normalized = { ...defaults(route.page), ...route };
  const allowed = pageFields[route.page] || [];
  const params = new URLSearchParams();
  for (const [query, field] of Object.entries(fields)) {
    const value = normalized[field];
    if (typeof value !== 'string') throw new TypeError('Invalid route field');
    if (value === '' && field !== 'tab') continue;
    if (field === 'tab' && value === 'site') continue;
    if (!allowed.includes(query)) throw new TypeError('Unsupported route field');
    if (field === 'tab') {
      if (!['site', 'publications', 'media'].includes(value)) throw new TypeError('Invalid tab');
    } else if (field === 'section') {
      if (!['offers', 'announcements', 'hot'].includes(value)) throw new TypeError('Invalid section');
    } else if (field === 'returnTo') {
      const destination = safeReturnTo(value);
      if (!destination) throw new TypeError('Invalid return destination');
      normalized.returnTo = destination;
    } else if (!id(value)) throw new TypeError('Invalid ID');
  }
  if ((normalized.section && normalized.publicationId)
    || (route.page === 'crm-content' && normalized.publicationId && normalized.tab !== 'publications')) {
    throw new TypeError('Unsupported route combination');
  }
  for (const query of allowed) {
    const value = normalized[fields[query]];
    if (value && !(query === 'tab' && value === 'site')) params.set(query, value);
  }
  const query = params.toString();
  return `#${route.page}${query ? `?${query}` : ''}`;
}

export function parseRoute(hash) {
  let page = 'home';
  try {
    // Keep a known page even when its query fails strict decoding.
    if (typeof hash === 'string') {
      const suppliedPage = hash.replace(/^#/, '').split('?')[0];
      if (pages.includes(suppliedPage)) page = suppliedPage;
    }
    const parsed = queryParts(hash);
    if (!pages.includes(parsed.page)) return defaults('home', false);
    page = parsed.page;
    const route = defaults(page);
    const allowed = pageFields[page] || [];
    for (const [query, field] of Object.entries(fields)) {
      const values = parsed.params.getAll(query);
      if (!values.length) continue;
      if (values.length !== 1 || !allowed.includes(query)) return defaults(page, false);
      if (query === 'returnTo') route.returnTo = safeReturnTo(values[0]);
      else {
        if (values[0] === '') return defaults(page, false);
        route[field] = values[0];
      }
    }
    routeHash(route);
    return route;
  } catch {
    return defaults(page, false);
  }
}

export function safeReturnTo(value) {
  if (typeof value !== 'string' || !value.startsWith('#') || value === '#' || controls.test(value)) return '';
  try {
    const { page, params } = queryParts(value);
    if (!pages.includes(page) || authPages.includes(page) || params.has('returnTo')) return '';
    const route = parseRoute(value);
    return route.valid ? routeHash(route) : '';
  } catch {
    return '';
  }
}

export function targetHash(target) {
  if (!object(target)) return null;
  const exact = keys => Object.keys(target).length === keys.length && keys.every(key => Object.hasOwn(target, key));
  try {
    if (target.type === 'catalog' && exact(['type'])) return routeHash({ page: 'catalog' });
    if (target.type === 'category' && exact(['type', 'categoryId']) && id(target.categoryId)) return routeHash({ page: 'catalog', categoryId: target.categoryId });
    if (target.type === 'product' && exact(['type', 'productId']) && id(target.productId)) return routeHash({ page: 'catalog', productId: target.productId });
    if (target.type === 'section' && exact(['type', 'section']) && target.section) return routeHash({ page: 'home', section: target.section });
  } catch {
    return null;
  }
  return null;
}

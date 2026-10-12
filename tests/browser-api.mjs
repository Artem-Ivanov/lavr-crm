import { test, after } from 'node:test';
import assert from 'node:assert/strict';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const baseUrl = process.env.BASE_URL || 'http://127.0.0.1:5173';
after(() => browser.close());

const updatedAt = '2026-10-11T08:00:00Z';
const csrfToken = 'browser-api-csrf-token-01234567890123456789';
const money = value => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(value / 100);
const error = (code, message) => ({ code, message, requestId: 'browser-api-request' });
const staff = role => ({ user: { id: `api-${role}`, name: `API ${role}`, role }, csrfToken, expiresAt: '2027-01-01T00:00:00Z' });
const tinyPng = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=', 'base64');
const product = (id, overrides = {}) => ({
  id, externalId: `external-${id}`, sku: `SKU-${id}`, brand: 'API Brand', name: `Серверная деталь ${id}`,
  category: 'api-category', priceKopecks: 123456, stock: 10, image: '', deliveryDays: 1,
  websiteTitle: null, websiteDescription: null, images: [], ...overrides,
});

function fixtures() {
  const products = Array.from({ length: 25 }, (_, index) => product(`api-part-${index + 1}`));
  products[0].websiteTitle = 'Название первой детали с сервера';
  const offPageProduct = product('api-off-page', { name: 'Восстановленная деталь вне страницы' });
  const receipt = { id: 'api-order', number: 'SERVER-ORDER-1', createdAt: updatedAt, totalKopecks: 432100, status: 'new', integrationStatus: 'pending_sync' };
  const state = {
    products, offPageProduct, receipt, stale: false, session: null, presentationVersion: 7,
    categories: [
      { id: 'api-category', name: 'Категория API', position: 0, isVisible: true, showInMenu: true, version: 1 },
      { id: 'api-secondary', name: 'Другая категория API', position: 1, isVisible: true, showInMenu: false, version: 1 },
    ],
    site: {
      hero: { title: 'Главная страница с сервера', description: 'Описание API', image: null, buttonText: 'Серверный каталог', target: { type: 'catalog' } },
      settings: { storeName: 'SERVER STORE', phone: '+79991234567', address: 'Москва, Серверная, 1', hours: 'Ежедневно 09:00–18:00', categoriesTitle: 'Серверные категории', promotionsTitle: 'Серверные публикации', catalogTitle: 'Серверный ассортимент' },
      menu: [{ label: 'Меню API', target: { type: 'category', categoryId: 'api-category' }, position: 0, isVisible: true }],
      publications: [{ id: 'api-publication', kind: 'announcement', title: 'Объявление API', description: 'Публикация пришла из backend', images: [], target: { type: 'catalog' }, buttonText: 'Открыть ассортимент', position: 0 }],
    },
    orders: [{
      ...receipt, customer: { name: 'Серверный покупатель', phone: '+79991234567', email: '' }, fulfillment: { method: 'pickup', address: '' },
      items: [{ productId: offPageProduct.id, sku: offPageProduct.sku, brand: offPageProduct.brand, name: offPageProduct.name, quantity: 2, unitPriceKopecks: receipt.totalKopecks / 2 }],
      version: 1, canCancel: true, externalDocumentId: null,
    }],
  };
  state.siteVersion = 7;
  const { image, ...hero } = state.site.hero;
  state.adminSite = { hero: { ...hero, imageId: null }, settings: structuredClone(state.site.settings), menu: structuredClone(state.site.menu), version: state.siteVersion };
  state.publications = state.site.publications.map(({ images, ...item }) => ({ ...item, imageIds: [], status: 'published', startsAt: null, endsAt: null, version: 1 }));
  state.privateMedia = [{ id: 'api-image', path: '/api/admin/media/api-image/file', alt: 'Деталь API', width: 1, height: 1, mimeType: 'image/png', sizeBytes: tinyPng.length, state: 'ready', createdAt: updatedAt }];
  return state;
}

const publicMedia = item => ({ id: item.id, path: `/api/media/${item.id}`, alt: item.alt, width: item.width, height: item.height });
function projectContent(state) {
  const { imageId, ...hero } = state.adminSite.hero;
  const image = state.privateMedia.find(item => item.id === imageId);
  const now = Date.now();
  state.site = {
    hero: { ...hero, image: image ? publicMedia(image) : null },
    settings: structuredClone(state.adminSite.settings), menu: state.adminSite.menu.filter(item => item.isVisible).map(item => structuredClone(item)),
    publications: state.publications.filter(item => item.status === 'published' && (item.startsAt === null || Date.parse(item.startsAt) <= now) && (item.endsAt === null || now < Date.parse(item.endsAt)))
      .map(({ id, kind, title, description, imageIds, target, buttonText, position }) => ({ id, kind, title, description, images: imageIds.map(imageId => state.privateMedia.find(item => item.id === imageId)).filter(Boolean).map(publicMedia), target, buttonText, position })),
  };
}

const publicCategories = state => state.categories.filter(item => item.isVisible).map(({ id, name, position, showInMenu }) => ({ id, name, position, showInMenu }));
const adminProduct = ({ id, sku, brand, name, category, priceKopecks, stock }) => ({ id, sku, brand, name, category, priceKopecks, stock, isVisible: true });
function pageData(items, url, extra = {}) {
  const offset = Number(url.searchParams.get('offset') || 0);
  const limit = Number(url.searchParams.get('limit') || 24);
  return { items: items.slice(offset, offset + limit), total: items.length, offset, limit, ...extra };
}
const catalogData = (state, url, items = state.products) => pageData(items, url, { catalogUpdatedAt: updatedAt, stale: state.stale });
const json = (route, data, status = 200, headers = {}) => route.fulfill({ status, contentType: 'application/json', headers, body: JSON.stringify(data) });

async function open(t, { state = fixtures(), hash = '', seed = {}, handle, viewport = { width: 1440, height: 1100 }, isMobile = false, hasTouch = false } = {}) {
  const context = await browser.newContext({ viewport, isMobile, hasTouch });
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  const errors = [];
  const unexpected = [];
  const requests = [];
  page.on('pageerror', issue => errors.push(issue.message));
  await context.addInitScript(({ local = {}, session = {} }) => {
    for (const [key, value] of Object.entries(local)) localStorage.setItem(key, value);
    for (const [key, value] of Object.entries(session)) sessionStorage.setItem(key, value);
    window.storageWrites = [];
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      window.storageWrites.push({ storage: this === localStorage ? 'local' : 'session', key });
      return original.call(this, key, value);
    };
  }, seed);
  await context.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.slice('/api'.length);
    const method = request.method();
    const headers = request.headers();
    const body = headers['content-type']?.startsWith('application/json') ? request.postDataJSON() : null;
    requests.push({ method, path, query: Object.fromEntries(url.searchParams), headers, body, rawBody: request.postDataBuffer() });
    if (handle && await handle({ route, request, url, path, method, state })) return;
    if (method === 'GET' && path === '/site') return json(route, state.site);
    if (method === 'GET' && path === '/categories') return json(route, publicCategories(state));
    if (method === 'GET' && path === '/catalog') return json(route, catalogData(state, url));
    if (method === 'GET' && path.startsWith('/catalog/')) {
      const id = decodeURIComponent(path.slice('/catalog/'.length));
      const found = [...state.products, state.offPageProduct].find(item => item.id === id);
      return found ? json(route, { product: found, catalogUpdatedAt: updatedAt, stale: state.stale }) : json(route, error('PRODUCT_UNAVAILABLE', 'Товар недоступен'), 404);
    }
    if (path === '/manager/session') {
      if (method === 'GET') return state.session ? json(route, state.session) : json(route, error('UNAUTHORIZED', 'Войдите в кабинет'), 401);
      if (method === 'POST') { state.session = staff(request.postDataJSON().login === 'admin' ? 'admin' : 'manager'); return json(route, state.session); }
      if (method === 'DELETE') { state.session = null; return route.fulfill({ status: 204 }); }
    }
    if (path === '/admin/site') {
      if (method === 'GET') return json(route, state.adminSite, 200, { ETag: `"${state.siteVersion}"` });
      if (method === 'PATCH') {
        if (headers['if-match'] !== `"${state.siteVersion}"`) return json(route, error('VERSION_CONFLICT', 'Настройки сайта изменились'), 412);
        state.siteVersion++;
        state.adminSite = { ...state.adminSite, ...body, version: state.siteVersion };
        projectContent(state);
        return json(route, state.adminSite, 200, { ETag: `"${state.siteVersion}"` });
      }
    }
    if (path === '/admin/publications') {
      if (method === 'GET') return json(route, pageData(state.publications.filter(item => (!url.searchParams.get('kind') || item.kind === url.searchParams.get('kind')) && (!url.searchParams.get('status') || item.status === url.searchParams.get('status'))), url));
      if (method === 'POST') {
        const created = { id: 'api-created-publication', ...body, status: body.status || 'draft', startsAt: body.startsAt ?? null, endsAt: body.endsAt ?? null, version: 1 };
        state.publications.push(created);
        projectContent(state);
        return json(route, created, 201, { ETag: '"1"' });
      }
    }
    if (path.startsWith('/admin/publications/')) {
      const item = state.publications.find(item => item.id === decodeURIComponent(path.slice('/admin/publications/'.length)));
      if (!item) return json(route, error('NOT_FOUND', 'Публикация не найдена'), 404);
      if (method === 'GET') return json(route, item, 200, { ETag: `"${item.version}"` });
      if (method === 'PATCH') {
        if (headers['if-match'] !== `"${item.version}"`) return json(route, error('VERSION_CONFLICT', 'Публикация изменилась'), 412);
        Object.assign(item, body, { version: item.version + 1 });
        projectContent(state);
        return json(route, item, 200, { ETag: `"${item.version}"` });
      }
    }
    if (path === '/admin/media') {
      if (method === 'GET') return json(route, pageData(state.privateMedia, url));
      if (method === 'POST') {
        const alt = request.postDataBuffer().toString('utf8').match(/name="alt"\r\n\r\n([\s\S]*?)\r\n--/)?.[1] || '';
        const created = { ...state.privateMedia[0], id: 'api-uploaded-image', path: '/api/admin/media/api-uploaded-image/file', alt };
        state.privateMedia.unshift(created);
        return json(route, created, 201);
      }
    }
    if (method === 'GET' && /^\/admin\/media\/[A-Za-z0-9_-]+\/file$/.test(path)) return route.fulfill({ status: 200, contentType: 'image/png', body: tinyPng });
    if (method === 'GET' && /^\/media\/[A-Za-z0-9_-]+$/.test(path)) return route.fulfill({ status: 200, contentType: 'image/png', body: tinyPng });
    if (method === 'GET' && path === '/manager/orders') return json(route, pageData(state.orders, url));
    if (method === 'GET' && path === '/admin/categories') return json(route, pageData(state.categories, url));
    if (method === 'POST' && path === '/admin/categories') {
      const created = { id: 'api-created-category', ...request.postDataJSON(), version: 1 };
      state.categories.push(created);
      return json(route, created, 201);
    }
    if (method === 'GET' && path === '/admin/products') return json(route, pageData(state.products.map(adminProduct), url));
    if (path === `/admin/products/${state.products[0].id}/presentation`) {
      const item = state.products[0];
      const presentation = () => ({ productId: item.id, categoryId: item.category, websiteTitle: item.websiteTitle, websiteDescription: item.websiteDescription, imageIds: [], version: state.presentationVersion });
      if (method === 'GET') return json(route, { product: item, presentation: presentation() }, 200, { ETag: `"${state.presentationVersion}"` });
      if (method === 'PATCH') { item.category = request.postDataJSON().categoryId; state.presentationVersion++; return json(route, presentation()); }
    }
    unexpected.push(`${method} ${path}`);
    return json(route, error('UNEXPECTED_REQUEST', 'Непредусмотренный запрос в браузерном тесте'), 500);
  });
  t.after(async () => {
    await context.close();
    assert.deepEqual(errors, [], 'No uncaught browser errors');
    assert.deepEqual(unexpected, [], 'Every API call must be covered by a fixture');
  });
  await page.goto(`${baseUrl}${hash}`, { waitUntil: 'domcontentloaded' });
  return { page, state, requests };
}

async function catalogReady(page) {
  await page.locator('.product-card').first().waitFor();
  await page.getByText('Загружаем каталог…', { exact: true }).waitFor({ state: 'hidden' });
}
async function checkoutReady(page) {
  await page.locator('#checkout-form').waitFor();
  await page.waitForFunction(() => document.querySelector('.checkout-submit')?.disabled === false);
}
async function search(page, query) {
  await page.locator('#search').fill(query);
  await page.locator('#search-form button').click();
}
async function noServerStorage(page) {
  assert.deepEqual(await page.evaluate(() => window.storageWrites.filter(item => ['lavr.catalog.v1', 'lavr.orders.v1'].includes(item.key))), []);
}

const mobileProfiles = [{ width: 320, height: 740 }, { width: 390, height: 844 }, { width: 844, height: 390 }];

async function mobileControls(page, targets, fields = []) {
  for (const [selectors, property] of [[targets, 'target'], [fields, 'field']]) {
    for (const selector of selectors) {
      await page.locator(selector).first().waitFor();
      const controls = await page.locator(selector).evaluateAll(elements => elements.filter(element => element.getClientRects().length).map(element => {
        const { width, height } = element.getBoundingClientRect();
        return { width, height, fontSize: parseFloat(getComputedStyle(element).fontSize) };
      }));
      assert.ok(controls.length, `${selector} must be visible`);
      for (const control of controls) {
        if (property === 'target') assert.ok(control.width >= 44 && control.height >= 44, `${selector} touch target: ${JSON.stringify(control)}`);
        else assert.ok(control.fontSize >= 16, `${selector} must avoid iOS focus zoom: ${JSON.stringify(control)}`);
      }
    }
  }
}

test('true mobile navigation exposes all eight long server menu entries without page overflow', async t => {
  for (const viewport of mobileProfiles) {
    const state = fixtures();
    state.site.menu = Array.from({ length: 8 }, (_, index) => ({ label: `Раздел ${index + 1}: ${'Комплектующие для обслуживания автомобиля '.repeat(2)}`.slice(0, 80), target: { type: 'category', categoryId: 'api-category' }, position: index, isVisible: true }));
    const { page, requests } = await open(t, { state, viewport, isMobile: true, hasTouch: true });
    await catalogReady(page);
    assert.equal(await page.evaluate(() => matchMedia('(pointer: coarse)').matches), true);
    const menu = page.locator('.mobile-menu');
    const summary = menu.locator('summary');
    const items = menu.locator('.mobile-menu-items button');
    assert.equal(await page.locator('.desktop-menu-item:visible').count(), 0);
    await mobileControls(page, ['.mobile-menu summary', '#search-form button', '.header-cart', '.catalog-nav', '.filter-row', '.stock-check', '#brand', '#sort', '.add-button'], ['#search', '#brand', '#sort']);
    for (let index = 0; index < state.site.menu.length; index++) {
      await summary.tap();
      assert.deepEqual(await items.allTextContents(), state.site.menu.map(item => item.label));
      await mobileControls(page, ['.mobile-menu-items button']);
      assert.equal(await items.evaluateAll(elements => elements.every(element => element.scrollWidth <= element.clientWidth + 1)), true, 'Long menu labels wrap inside their buttons');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Open menu must fit ${viewport.width}×${viewport.height}`);
      await items.nth(index).tap();
      assert.equal(await menu.evaluate(element => element.open), false);
      assert.equal(await summary.evaluate(element => document.activeElement === element), true);
      await page.waitForFunction(() => location.hash === '#catalog');
    }
    assert.ok(requests.some(item => item.path === '/catalog' && item.query.category === 'api-category'), 'A mobile server menu entry navigates to its configured category');
    await summary.tap();
    await items.last().focus();
    await page.keyboard.press('Escape');
    assert.equal(await menu.evaluate(element => element.open), false);
    assert.equal(await summary.evaluate(element => document.activeElement === element), true);
    await noServerStorage(page);
  }
});

test('mobile staff sign-in, CRM tables and category assignment stay touch and keyboard accessible', async t => {
  for (const viewport of mobileProfiles) {
    const { page, state, requests } = await open(t, {
      hash: '/#crm', viewport, isMobile: true, hasTouch: true,
      handle: async ({ route, path, method, state }) => {
        if (method !== 'GET' || path !== '/manager/orders/api-order') return false;
        await json(route, state.orders[0]); return true;
      },
    });
    await page.locator('.staff-login').waitFor();
    await mobileControls(page, ['.staff-login button'], ['[name="login"]', '[name="password"]']);
    await page.locator('[name="login"]').fill('admin');
    await page.locator('[name="password"]').fill('browser-password');
    await page.locator('.staff-login button[type="submit"]').tap();
    await page.locator('.orders-table').waitFor();
    await mobileControls(page, ['.staff-session .text-link', '#order-search-form button', '.status-filter', '.order-number', '.table-action'], ['[name="orderQuery"]']);
    const ordersRegion = page.getByRole('region', { name: 'Заказы: прокрутка таблицы', exact: true });
    assert.equal(await ordersRegion.getAttribute('tabindex'), '0');
    await page.locator('.table-scroll-hint').waitFor();
    const tableSize = await ordersRegion.evaluate(element => ({ visible: element.clientWidth, content: element.scrollWidth }));
    t.diagnostic(`Orders table ${viewport.width}×${viewport.height}: ${JSON.stringify(tableSize)}`);
    if (viewport.width <= 390) assert.ok(tableSize.content > tableSize.visible, 'Phone order overflow belongs to the table region');
    await ordersRegion.focus();
    if (tableSize.content > tableSize.visible) {
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('.table-wrap').scrollLeft > 0);
    }
    await page.locator('.table-action').scrollIntoViewIfNeeded();
    await page.locator('.table-action').tap();
    await page.locator('dialog[open] #dialog-title').waitFor();
    await mobileControls(page, ['.dialog-close', '.order-status-actions button']);
    await page.locator('.dialog-close').tap();
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `CRM table scroll remains local at ${viewport.width}px`);
    await page.locator('.crm-tabs a[href="#crm-catalog"]').tap();
    await page.locator('[data-testid="product-category-api-part-1"]').waitFor();
    await mobileControls(page, ['[data-testid="create-category"]', '.catalog-admin-table select'], ['#category-name', '#catalog-admin-search', '.catalog-admin-table select']);
    await page.locator('#category-name').fill('Мобильная категория API');
    await page.locator('[data-testid="create-category"]').tap();
    const assignment = page.locator('[data-testid="product-category-api-part-1"]');
    await assignment.locator('option[value="api-created-category"]').waitFor({ state: 'attached' });
    const catalogRegion = page.getByRole('region', { name: 'Номенклатура: прокрутка таблицы', exact: true });
    assert.equal(await catalogRegion.getAttribute('tabindex'), '0');
    await catalogRegion.focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('.catalog-table-wrap').scrollLeft > 0);
    await assignment.scrollIntoViewIfNeeded();
    await assignment.selectOption('api-created-category');
    await page.locator('.catalog-assignment-message.catalog-success').waitFor();
    assert.equal(await assignment.inputValue(), 'api-created-category');
    assert.equal(state.products[0].category, 'api-created-category');
    const patch = requests.find(item => item.method === 'PATCH' && item.path.endsWith('/presentation'));
    assert.equal(patch.headers['x-csrf-token'], csrfToken);
    assert.deepEqual(patch.body, { categoryId: 'api-created-category' });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Admin table scroll remains local at ${viewport.width}px`);
    await noServerStorage(page);
  }
});

test('HTTP failures have explicit retries; server content, pagination and latest search win', async t => {
  let failing = true;
  let releaseOld;
  const oldGate = new Promise(resolve => { releaseOld = resolve; });
  t.after(() => releaseOld());
  const state = fixtures();
  const localCatalog = JSON.stringify({ categories: [{ ...state.categories[0], name: 'LOCAL CATALOG SENTINEL' }], products: [product('local-only', { name: 'LOCAL PRODUCT SENTINEL' })] });
  const localOrders = JSON.stringify([{ ...state.orders[0], number: 'LOCAL ORDER SENTINEL' }]);
  const { page, requests } = await open(t, {
    state, seed: { local: { 'lavr.catalog.v1': localCatalog }, session: { 'lavr.orders.v1': localOrders } },
    handle: async ({ route, url, path, method }) => {
      if (failing && method === 'GET' && ['/site', '/categories', '/catalog'].includes(path)) { await json(route, error('UNAVAILABLE', `API недоступен: ${path}`), 503); return true; }
      if (method === 'GET' && path === '/catalog' && url.searchParams.has('q')) {
        const query = url.searchParams.get('q');
        if (query === 'старый запрос') await oldGate;
        const result = query === 'новый запрос' ? product('api-latest', { name: 'Последний серверный результат' }) : product('api-server-match', { name: 'Ответ серверного поиска без совпадения в тексте' });
        await json(route, catalogData(state, url, [result])); return true;
      }
      return false;
    },
  });
  for (const label of ['Повторить загрузку страницы', 'Повторить загрузку категорий', 'Повторить загрузку каталога']) await page.getByRole('button', { name: label, exact: true }).waitFor();
  assert.equal(await page.locator('.product-card').count(), 0);
  assert.equal(await page.locator('#hero-title').count(), 0);
  assert.equal(await page.locator('.demo-label, .catalog-demo').count(), 0);
  assert.ok(!(await page.locator('main').textContent()).includes('SENTINEL'));
  failing = false;
  for (const label of ['Повторить загрузку страницы', 'Повторить загрузку категорий', 'Повторить загрузку каталога']) await page.getByRole('button', { name: label, exact: true }).click();
  await catalogReady(page);
  assert.equal(await page.locator('#hero-title').textContent(), state.site.hero.title);
  assert.equal(await page.locator('#promotions h3').textContent(), state.site.publications[0].title);
  await page.getByRole('button', { name: 'Меню API', exact: true }).waitFor();
  assert.equal(await page.locator('.product-name').first().textContent(), state.products[0].websiteTitle);
  assert.equal(await page.locator('.product-card').count(), 24);
  assert.match(await page.locator('.pagination').textContent(), /1–24 из 25/);
  await page.locator('.pagination').getByRole('button', { name: 'Далее' }).click();
  await page.locator('[data-action="add"][data-id="api-part-25"]').waitFor();
  assert.equal(await page.locator('.product-card').count(), 1);
  assert.ok(requests.some(item => item.path === '/catalog' && item.query.offset === '24' && item.query.limit === '24'));
  await search(page, 'SERVER-SKU-ONLY');
  await page.locator('[data-action="add"][data-id="api-server-match"]').waitFor();
  assert.equal(await page.locator('.product-card').count(), 1, 'The client renders the server match even when local text does not match the query');
  assert.ok(requests.some(item => item.path === '/catalog' && item.query.q === 'SERVER-SKU-ONLY' && item.query.offset === '0'));
  const oldRequest = page.waitForRequest(request => new URL(request.url()).searchParams.get('q') === 'старый запрос');
  await search(page, 'старый запрос');
  await oldRequest;
  await search(page, 'новый запрос');
  await page.locator('[data-action="add"][data-id="api-latest"]').waitFor();
  const oldResponse = page.waitForResponse(response => new URL(response.url()).searchParams.get('q') === 'старый запрос');
  releaseOld();
  await (await oldResponse).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.locator('.product-name').textContent(), 'Последний серверный результат');
  assert.equal(await page.locator('#search').inputValue(), 'новый запрос');
  assert.equal(await page.evaluate(() => localStorage.getItem('lavr.catalog.v1')), localCatalog);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('lavr.orders.v1')), localOrders);
  await noServerStorage(page);
});

test('a persisted off-page cart resolves through API and retries checkout with the same key and body', async t => {
  const state = fixtures();
  const originalCart = [{ productId: state.offPageProduct.id, quantity: 2 }];
  const attempts = [];
  const { page, requests } = await open(t, {
    state, hash: '/#cart', seed: { local: { 'lavr.cart.v1': JSON.stringify(originalCart) } },
    handle: async ({ route, request, path, method }) => {
      if (path !== '/orders' || method !== 'POST') return false;
      attempts.push({ body: request.postDataJSON(), key: request.headers()['idempotency-key'] });
      if (attempts.length === 1) await route.abort('failed');
      else await json(route, state.receipt, 200);
      return true;
    },
  });
  await checkoutReady(page);
  assert.ok(requests.some(item => item.path === `/catalog/${state.offPageProduct.id}`));
  assert.ok(!state.products.some(item => item.id === state.offPageProduct.id));
  assert.equal(await page.locator('.cart-item h3').textContent(), state.offPageProduct.name);
  await page.locator('[name="name"]').fill('API Покупатель');
  await page.locator('[name="phone"]').fill('+79991234567');
  await page.locator('[name="email"]').fill('api@example.test');
  await page.locator('[name="method"][value="delivery"]').check();
  await page.locator('[name="address"]').fill('Москва, Проверочная, 2');
  await page.locator('.checkout-submit').click();
  await page.waitForFunction(() => document.querySelector('#checkout-error')?.textContent.trim());
  assert.equal(attempts.length, 1);
  assert.match(attempts[0].key, /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i);
  assert.deepEqual(attempts[0].body, {
    customer: { name: 'API Покупатель', phone: '+79991234567', email: 'api@example.test' }, fulfillment: { method: 'delivery', address: 'Москва, Проверочная, 2' },
    items: [{ productId: state.offPageProduct.id, quantity: 2, expectedUnitPriceKopecks: state.offPageProduct.priceKopecks }],
  });
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))), originalCart);
  for (const [name, value] of Object.entries(attempts[0].body.customer)) assert.equal(await page.locator(`[name="${name}"]`).inputValue(), value);
  assert.equal(await page.locator('[name="address"]').inputValue(), 'Москва, Проверочная, 2');
  assert.equal(await page.locator('[name="method"][value="delivery"]').isChecked(), true);
  assert.equal(await page.locator('.receipt').count(), 0);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('lavr.orders.v1')), null);
  const persistedAttempt = await page.evaluate(() => sessionStorage.getItem('lavr.checkout-attempt.v1'));
  assert.ok(persistedAttempt?.includes(attempts[0].key), 'The uncertain request key survives reload');
  for (const privateValue of ['API Покупатель', '+79991234567', 'api@example.test', 'Москва, Проверочная, 2', state.offPageProduct.id]) assert.ok(!persistedAttempt.includes(privateValue), 'The persisted attempt contains a digest, not customer or cart data');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await checkoutReady(page);
  for (const [name, value] of Object.entries(attempts[0].body.customer)) await page.locator(`[name="${name}"]`).fill(value);
  await page.locator('[name="method"][value="delivery"]').check();
  await page.locator('[name="address"]').fill(attempts[0].body.fulfillment.address);
  await page.locator('.checkout-submit').click();
  await page.locator('.receipt').waitFor();
  assert.deepEqual(attempts[1], attempts[0]);
  assert.equal(attempts.length, 2);
  assert.match(await page.locator('.receipt-number').textContent(), /SERVER-ORDER-1/);
  const receiptText = await page.locator('.receipt').textContent();
  assert.ok(receiptText.includes(money(state.receipt.totalKopecks)), 'Receipt renders the server total');
  assert.ok(!receiptText.includes(money(state.offPageProduct.priceKopecks * 2)), 'Receipt does not reuse the client estimate');
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))), []);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('lavr.checkout-attempt.v1')), null);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('lavr.orders.v1')), null);
  await noServerStorage(page);
});

test('a delayed checkout receipt preserves a new cart received from another tab', async t => {
  const state = fixtures();
  let releaseOrder;
  const pendingOrder = new Promise(resolve => { releaseOrder = resolve; });
  t.after(() => releaseOrder());
  const { page } = await open(t, {
    state, hash: '/#cart', seed: { local: { 'lavr.cart.v1': JSON.stringify([{ productId: state.products[0].id, quantity: 1 }]) } },
    handle: async ({ route, path, method }) => {
      if (path !== '/orders' || method !== 'POST') return false;
      await pendingOrder;
      await json(route, state.receipt, 201);
      return true;
    },
  });
  await checkoutReady(page);
  await page.locator('[name="name"]').fill('Покупатель во время запроса');
  await page.locator('[name="phone"]').fill('+79991234567');
  const post = page.waitForRequest(request => new URL(request.url()).pathname === '/api/orders' && request.method() === 'POST');
  await page.locator('.checkout-submit').click();
  const submitted = (await post).postDataJSON();
  const newCart = [{ productId: state.products[1].id, quantity: 3 }];
  await page.evaluate(cart => {
    const newValue = JSON.stringify(cart);
    localStorage.setItem('lavr.cart.v1', newValue);
    dispatchEvent(new StorageEvent('storage', { key: 'lavr.cart.v1', newValue, storageArea: localStorage }));
  }, newCart);
  await page.locator('.cart-item .remove-button[data-id="api-part-2"]').waitFor();
  releaseOrder();
  await page.locator('.receipt').waitFor();
  assert.deepEqual(submitted.items, [{ productId: state.products[0].id, quantity: 1, expectedUnitPriceKopecks: state.products[0].priceKopecks }]);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))), newCart, 'A receipt for the submitted cart must not clear a newer cart');
  await page.locator('[data-action="continue"]').click();
  await page.locator('.header-cart').click();
  await checkoutReady(page);
  assert.equal(await page.locator('.cart-item .remove-button[data-id="api-part-2"]').count(), 1);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))), newCart);
  await noServerStorage(page);
});

test('staff auth gates API access; managers cannot open admin catalog and admins create categories with CSRF', async t => {
  const { page, state, requests } = await open(t, { hash: '/#crm' });
  await page.locator('.staff-login').waitFor();
  assert.ok(requests.some(item => item.path === '/manager/session' && item.method === 'GET'));
  assert.equal(requests.filter(item => item.path === '/manager/orders' || item.path.startsWith('/admin/')).length, 0);
  await page.locator('[name="login"]').fill('manager');
  await page.locator('[name="password"]').fill('browser-password');
  await page.locator('.staff-login button[type="submit"]').click();
  await page.locator('.orders-table').waitFor();
  assert.match(await page.locator('.orders-table').textContent(), /Серверный покупатель/);
  assert.equal(await page.locator('.crm-tabs a[href="#crm-catalog"]').count(), 0);
  await page.evaluate(() => { location.hash = 'crm-catalog'; });
  await page.getByText('Управление каталогом доступно администратору.', { exact: true }).waitFor();
  assert.equal(await page.locator('#category-form').count(), 0);
  assert.equal(requests.filter(item => item.path.startsWith('/admin/')).length, 0);
  await page.locator('.staff-session').getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.locator('.staff-login').waitFor();
  assert.equal(requests.find(item => item.path === '/manager/session' && item.method === 'DELETE').headers['x-csrf-token'], csrfToken);
  await page.locator('[name="login"]').fill('admin');
  await page.locator('[name="password"]').fill('browser-password');
  await page.locator('.staff-login button[type="submit"]').click();
  await page.locator('[data-testid="product-category-api-part-1"]').waitFor();
  assert.equal(await page.locator('#product-form').count(), 0, 'API mode cannot create accounting products');
  await page.locator('#category-name').fill('Категория создана сервером');
  await page.locator('[data-testid="create-category"]').click();
  await page.locator('[data-testid="product-category-api-part-1"] option[value="api-created-category"]').waitFor({ state: 'attached' });
  const create = requests.find(item => item.path === '/admin/categories' && item.method === 'POST');
  assert.equal(create.headers['x-csrf-token'], csrfToken);
  assert.deepEqual(create.body, { name: 'Категория создана сервером', position: 2, isVisible: true, showInMenu: true });
  assert.equal(state.categories.at(-1).name, 'Категория создана сервером');
  assert.equal(await page.evaluate(() => localStorage.getItem('lavr.catalog.v1')), null);
  await noServerStorage(page);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator('[data-testid="product-category-api-part-1"] option[value="api-created-category"]').waitFor({ state: 'attached' });
  assert.equal(await page.locator('.crm-tabs a[href="#crm-catalog"]').count(), 1);
  assert.equal(await page.evaluate(() => localStorage.getItem('lavr.catalog.v1')), null);
  await noServerStorage(page);
});

test('category assignment reads the presentation and sends only categoryId with its current version', async t => {
  const state = fixtures();
  state.session = staff('admin');
  let conflict = true;
  const { page, requests } = await open(t, {
    state, hash: '/#crm-catalog',
    handle: async ({ route, path, method }) => {
      if (method !== 'PATCH' || !path.endsWith('/presentation') || !conflict) return false;
      conflict = false; state.presentationVersion++;
      await json(route, error('VERSION_CONFLICT', 'Категория изменена другим сотрудником. Повторите действие.'), 412);
      return true;
    },
  });
  const select = page.locator('[data-testid="product-category-api-part-1"]');
  await select.waitFor();
  await select.selectOption('api-secondary');
  await page.locator('.catalog-assignment-message[role="alert"]').waitFor();
  assert.equal(await select.inputValue(), 'api-category', 'A rejected mutation restores the previous selection');
  assert.equal(state.products[0].category, 'api-category');
  await select.selectOption('api-secondary');
  await page.locator('.catalog-assignment-message.catalog-success').waitFor();
  await select.waitFor();
  assert.equal(await select.inputValue(), 'api-secondary');
  const presentationRequests = requests.filter(item => item.path.endsWith('/presentation'));
  assert.deepEqual(presentationRequests.map(item => item.method), ['GET', 'PATCH', 'GET', 'PATCH']);
  const patches = presentationRequests.filter(item => item.method === 'PATCH');
  assert.deepEqual(patches.map(item => item.headers['if-match']), ['"7"', '"8"']);
  for (const patch of patches) { assert.equal(patch.headers['x-csrf-token'], csrfToken); assert.deepEqual(patch.body, { categoryId: 'api-secondary' }); }
  assert.equal(state.products[0].category, 'api-secondary');
  assert.equal(state.products[0].websiteTitle, 'Название первой детали с сервера');
  assert.equal(state.presentationVersion, 9);
  assert.equal(await page.evaluate(() => localStorage.getItem('lavr.catalog.v1')), null);
  await noServerStorage(page);
});

test('unavailable products and a stale catalog preserve the cart draft until explicit removal and recovery', async t => {
  const state = fixtures();
  state.stale = true;
  const originalCart = [{ productId: state.products[0].id, quantity: 2 }, { productId: 'api-gone', quantity: 1 }];
  const { page, requests } = await open(t, { state, hash: '/#cart', seed: { local: { 'lavr.cart.v1': JSON.stringify(originalCart) } } });
  await page.getByRole('button', { name: 'Повторить проверку', exact: true }).waitFor();
  await page.getByText('Проверяем товары корзины…', { exact: true }).waitFor({ state: 'hidden' });
  assert.ok(requests.some(item => item.path === '/catalog/api-gone'));
  assert.equal(await page.locator('.cart-item').count(), 2);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))), originalCart);
  assert.equal(await page.locator('.checkout-submit').isDisabled(), true);
  await page.locator('[name="name"]').fill('Сохранённый покупатель');
  await page.locator('[name="phone"]').fill('+79991234567');
  await page.locator('.remove-button[data-id="api-gone"]').click();
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))), [originalCart[0]]);
  await page.getByRole('button', { name: 'Повторить проверку', exact: true }).click();
  await page.getByText('Остаток изменился или каталог ожидает обновления. Уменьшите количество либо повторите проверку.', { exact: true }).waitFor();
  assert.equal(await page.locator('.checkout-submit').isDisabled(), true, 'Stale catalog still blocks checkout after removing the unavailable row');
  assert.equal(await page.locator('[name="name"]').inputValue(), 'Сохранённый покупатель');
  state.stale = false;
  await page.getByRole('button', { name: 'Повторить проверку', exact: true }).click();
  await checkoutReady(page);
  assert.equal(await page.locator('[name="name"]').inputValue(), 'Сохранённый покупатель');
  assert.equal(await page.locator('[name="phone"]').inputValue(), '+79991234567');
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))), [originalCart[0]]);
  assert.equal(requests.filter(item => item.path === '/orders').length, 0);
  await noServerStorage(page);
});

test('expired catalog session returns to login instead of keeping an unusable admin screen', async t => {
  const state = fixtures();
  state.session = staff('admin');
  let expire = false;
  const { page } = await open(t, {
    state, hash: '/#crm-catalog',
    handle: async ({ route, path, method }) => {
      if (!expire || method !== 'GET' || path !== '/admin/products') return false;
      state.session = null;
      await json(route, error('SESSION_EXPIRED', 'Войдите снова'), 401);
      return true;
    },
  });
  await page.locator('[data-testid="product-category-api-part-1"]').waitFor();
  expire = true;
  await page.locator('#catalog-admin-search').fill('SKU');
  await page.locator('.staff-login').waitFor();
  assert.equal(await page.locator('#category-form').count(), 0);
  assert.match(await page.locator('.staff-login').textContent(), /Сессия завершена/);
  await noServerStorage(page);
});

test('a late status 401 from the previous session cannot invalidate a newer login', async t => {
  const state = fixtures();
  state.session = staff('manager');
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  const { page } = await open(t, {
    state, hash: '/#crm',
    handle: async ({ route, path, method }) => {
      if (method === 'GET' && path === '/manager/orders/api-order') { await json(route, state.orders[0]); return true; }
      if (method !== 'PATCH' || path !== '/manager/orders/api-order/status') return false;
      await pending;
      await json(route, error('SESSION_EXPIRED', 'Истекла старая сессия'), 401);
      return true;
    },
  });
  await page.locator('.order-number').click();
  await page.locator('[data-action="status"][data-status="cancelled"]').waitFor();
  const sent = page.waitForRequest(request => request.method() === 'PATCH' && request.url().endsWith('/status'));
  await page.locator('[data-action="status"][data-status="cancelled"]').click();
  await sent;
  await page.locator('.dialog-close').click();
  await page.locator('.staff-session').getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.locator('.staff-login').waitFor();
  await page.locator('[name="login"]').fill('admin');
  await page.locator('[name="password"]').fill('browser-password');
  await page.locator('.staff-login button[type="submit"]').click();
  await page.locator('.crm-tabs a[href="#crm-catalog"]').waitFor();
  const finished = page.waitForResponse(response => response.request().method() === 'PATCH' && response.url().endsWith('/status'));
  release();
  await finished;
  await page.waitForFunction(() => !document.querySelector('.staff-login') && document.querySelector('.staff-session')?.textContent.includes('API admin'));
  assert.equal(await page.locator('.staff-login').count(), 0);
  await noServerStorage(page);
});

test('server-managed long headings, descriptions and buttons fit desktop and mobile', async t => {
  const state = fixtures();
  Object.assign(state.site.settings, { storeName: 'М'.repeat(120), address: 'А'.repeat(500), hours: 'Ч'.repeat(240), categoriesTitle: 'К'.repeat(120), promotionsTitle: 'П'.repeat(120), catalogTitle: 'Т'.repeat(120) });
  Object.assign(state.site.hero, { title: 'З'.repeat(240), description: 'О'.repeat(2000), buttonText: 'Б'.repeat(80) });
  Object.assign(state.site.publications[0], { title: 'Н'.repeat(240), description: 'Д'.repeat(4000), buttonText: 'Щ'.repeat(80) });
  const { page } = await open(t, { state });
  await catalogReady(page);
  await page.locator('#hero-title').waitFor();
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1100 });
    const overflow = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
    assert.ok(overflow.scroll <= overflow.width + 1, `API content overflow at ${width}px: ${JSON.stringify(overflow)}`);
  }
});

test('a delayed conflict refresh cannot replace a different order dialog', async t => {
  const state = fixtures();
  state.session = staff('manager');
  state.orders.push({ ...state.orders[0], id: 'api-order-2', number: 'SERVER-ORDER-2' });
  let firstReads = 0;
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  const { page } = await open(t, {
    state, hash: '/#crm',
    handle: async ({ route, path, method }) => {
      if (method === 'GET' && path === '/manager/orders/api-order') {
        if (++firstReads > 1) await pending;
        await json(route, state.orders[0]); return true;
      }
      if (method === 'GET' && path === '/manager/orders/api-order-2') { await json(route, state.orders[1]); return true; }
      if (method !== 'PATCH' || path !== '/manager/orders/api-order/status') return false;
      await json(route, error('VERSION_CONFLICT', 'Заказ изменён'), 412); return true;
    },
  });
  await page.locator('.order-number[data-id="api-order"]').click();
  await page.locator('[data-action="status"][data-status="cancelled"]').waitFor();
  const refresh = page.waitForRequest(request => request.method() === 'GET' && request.url().endsWith('/manager/orders/api-order'));
  await page.locator('[data-action="status"][data-status="cancelled"]').click();
  await refresh;
  await page.locator('.dialog-close').click();
  await page.locator('.order-number[data-id="api-order-2"]').click();
  await page.waitForFunction(() => document.querySelector('dialog #dialog-title')?.textContent.includes('SERVER-ORDER-2'));
  const response = page.waitForResponse(item => item.request().method() === 'GET' && item.url().endsWith('/manager/orders/api-order'));
  release(); await (await response).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.match(await page.locator('dialog #dialog-title').textContent(), /SERVER-ORDER-2/);
});

test('mobile admins save long hero text, typed targets and reordered menu items through versioned HTTP', async t => {
  for (const viewport of [{ width: 390, height: 844 }, { width: 320, height: 740 }]) {
    const state = fixtures();
    state.session = staff('admin');
    const { page, requests } = await open(t, { state, hash: '/#crm-content', viewport, isMobile: true, hasTouch: true });
    await page.getByTestId('hero-title').waitFor();
    await page.getByTestId('hero-title').fill('З'.repeat(240));
    await page.getByTestId('hero-description').fill('Описание '.repeat(220).slice(0, 2000));
    await page.getByTestId('hero-button-text').fill('Б'.repeat(80));
    await page.getByTestId('site-storeName').fill('М'.repeat(120));
    await page.getByTestId('site-address').fill('А'.repeat(500));
    await page.getByTestId('hero-target-type').selectOption('category');
    await page.getByTestId('hero-target-category').selectOption('api-secondary');
    await page.getByTestId('menu-label-0').fill('Первый '.repeat(12).slice(0, 80));
    await page.getByTestId('add-menu-item').tap();
    await page.getByTestId('menu-label-1').fill('Горячие предложения из API');
    await page.getByTestId('menu-1-target-type').selectOption('section');
    await page.getByTestId('menu-1-target-section').selectOption('hot');
    await page.getByTestId('move-menu-up-1').tap();
    assert.equal(await page.getByTestId('menu-label-0').inputValue(), 'Горячие предложения из API');
    await page.getByTestId('remove-menu-item-1').tap();
    await mobileControls(page, ['.content-tabs button', '[data-testid="save-site"]', '[data-testid="add-menu-item"]', '.menu-editor-actions button', '.content-check'], ['.content-manager input:not([type="checkbox"])', '.content-manager textarea', '.content-manager select']);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Content editor fits ${viewport.width}px with long fields`);
    await page.getByTestId('save-site').tap();
    await page.getByTestId('site-success').waitFor();
    const patch = requests.find(item => item.path === '/admin/site' && item.method === 'PATCH');
    assert.equal(patch.headers['if-match'], '"7"');
    assert.equal(patch.headers['x-csrf-token'], csrfToken);
    assert.equal(patch.body.hero.title, 'З'.repeat(240));
    assert.equal(patch.body.hero.imageId, null);
    assert.deepEqual(patch.body.hero.target, { type: 'category', categoryId: 'api-secondary' });
    assert.deepEqual(patch.body.menu, [{ label: 'Горячие предложения из API', target: { type: 'section', section: 'hot' }, position: 0, isVisible: true }]);
    assert.equal(patch.body.settings.storeName, 'М'.repeat(120));
    assert.deepEqual(Object.keys(patch.body).sort(), ['hero', 'menu', 'settings']);
    await page.evaluate(() => { location.hash = 'home'; });
    await page.locator('.hero #hero-title').waitFor();
    assert.equal(await page.locator('.hero #hero-title').textContent(), 'З'.repeat(240));
    assert.equal(state.siteVersion, 8);
    await noServerStorage(page);
  }
});

test('HTTP publication drafts stay private until publish and disappear after archive', async t => {
  const state = fixtures();
  state.session = staff('admin');
  const { page, requests } = await open(t, { state, hash: '/#crm-content', viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await page.getByTestId('content-tab-publications').tap();
  await page.getByTestId('new-publication').tap();
  const title = `<b>${'П'.repeat(233)}</b>`;
  await page.getByTestId('publication-title').fill(title);
  await page.getByTestId('publication-description').fill('Д'.repeat(4000));
  await page.getByTestId('publication-button-text').fill('Открыть предложение');
  await page.getByTestId('publication-target-type').selectOption('product');
  await page.getByTestId('publication-target-product').fill('api-part-1');
  await page.getByTestId('publication-media-api-image').tap();
  await page.getByTestId('publication-starts-at').fill('2026-10-12T10:00');
  await page.getByTestId('publication-ends-at').fill('2026-10-11T10:00');
  await page.getByTestId('save-publication').tap();
  await page.getByText('Окончание публикации должно быть позже начала.', { exact: true }).waitFor();
  assert.equal(requests.filter(item => item.path === '/admin/publications' && item.method === 'POST').length, 0);
  await page.getByTestId('publication-starts-at').fill('');
  await page.getByTestId('publication-ends-at').fill('');
  await mobileControls(page, ['[data-testid="save-publication"]', '.publication-list-item', '[data-testid="publication-media-api-image"]'], ['[data-testid="publication-title"]', '[data-testid="publication-description"]', '[data-testid="publication-status"]']);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.getByTestId('save-publication').tap();
  await page.getByTestId('publication-success').waitFor();
  const create = requests.find(item => item.path === '/admin/publications' && item.method === 'POST');
  assert.equal(create.headers['x-csrf-token'], csrfToken);
  assert.equal(create.body.status, 'draft');
  assert.deepEqual(create.body.imageIds, ['api-image']);
  assert.deepEqual(create.body.target, { type: 'product', productId: 'api-part-1' });
  assert.equal(create.body.startsAt, null);
  assert.equal(create.body.endsAt, null);
  await page.evaluate(() => { location.hash = 'home'; });
  await page.locator('.hero').waitFor();
  assert.equal(await page.locator('.promotion-card h3').filter({ hasText: title }).count(), 0);
  await page.evaluate(() => { location.hash = 'crm-content'; });
  await page.getByTestId('content-tab-publications').tap();
  await page.getByTestId('edit-publication-api-created-publication').tap();
  await page.waitForFunction(title => document.querySelector('[data-testid="publication-title"]')?.value === title, title);
  await page.getByTestId('publication-status').selectOption('published');
  await page.getByTestId('save-publication').tap();
  await page.getByTestId('publication-success').waitFor();
  await page.evaluate(() => { location.hash = 'home'; });
  await page.locator('.promotion-card h3').filter({ hasText: title }).waitFor();
  assert.equal(await page.locator('.promotion-copy h3 b').count(), 0, 'Markup is displayed as plain text');
  assert.equal(await page.locator('.promotion-card').filter({ hasText: title }).locator('img').getAttribute('src'), '/api/media/api-image');
  await page.evaluate(() => { location.hash = 'crm-content'; });
  await page.getByTestId('content-tab-publications').tap();
  await page.getByTestId('edit-publication-api-created-publication').tap();
  await page.waitForFunction(() => document.querySelector('[data-testid="publication-status"]')?.value === 'published');
  await page.getByTestId('publication-status').selectOption('archived');
  await page.getByTestId('save-publication').tap();
  await page.getByTestId('publication-success').waitFor();
  await page.evaluate(() => { location.hash = 'home'; });
  await page.locator('.hero').waitFor();
  await page.waitForFunction(title => ![...document.querySelectorAll('.promotion-card h3')].some(element => element.textContent === title), title);
  const patches = requests.filter(item => item.path === '/admin/publications/api-created-publication' && item.method === 'PATCH');
  assert.deepEqual(patches.map(item => item.headers['if-match']), ['"1"', '"2"']);
  assert.deepEqual(patches.map(item => item.body.status), ['published', 'archived']);
  assert.equal(state.publications.find(item => item.id === 'api-created-publication').version, 3);
  await noServerStorage(page);
});

test('content access and failed saves preserve drafts while session generations reject late expiry', async t => {
  const managerState = fixtures();
  managerState.session = staff('manager');
  const manager = await open(t, { state: managerState, hash: '/#crm-content' });
  await manager.page.getByText('Редактор сайта доступен администратору.', { exact: true }).waitFor();
  assert.equal(await manager.page.locator('.content-manager').count(), 0);
  assert.equal(await manager.page.locator('.crm-tabs a[href="#crm-content"]').count(), 0);
  assert.equal(manager.requests.filter(item => item.path.startsWith('/admin/')).length, 0);

  const state = fixtures();
  state.session = staff('admin');
  let failure = 'conflict';
  let release;
  const pending = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  const { page, requests } = await open(t, {
    state, hash: '/#crm-content',
    handle: async ({ route, path, method }) => {
      if (path !== '/admin/site' || method !== 'PATCH' || !failure) return false;
      if (failure === 'conflict') {
        state.siteVersion++;
        state.adminSite.version = state.siteVersion;
        state.adminSite.hero.title = 'Новая версия другого администратора';
        await json(route, error('VERSION_CONFLICT', 'Настройки изменились'), 412);
      } else if (failure === 'network') await route.abort('failed');
      else {
        if (failure === 'pending-expiry') await pending;
        await json(route, error('SESSION_EXPIRED', 'Войдите снова'), 401);
      }
      return true;
    },
  });
  page.on('dialog', dialog => dialog.accept());
  await page.getByTestId('hero-title').fill('Мой сохранённый черновик');
  await page.getByTestId('save-site').click();
  await page.getByTestId('reload-site').waitFor();
  assert.equal(await page.getByTestId('hero-title').inputValue(), 'Мой сохранённый черновик');
  assert.equal(await page.getByTestId('save-site').isDisabled(), true);
  assert.equal(requests.filter(item => item.path === '/admin/site' && item.method === 'GET').length, 1, 'Conflict must not silently replace the draft');
  assert.equal(requests.filter(item => item.path === '/admin/site' && item.method === 'PATCH').length, 1, 'Conflict must not automatically retry');
  await page.getByTestId('reload-site').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="hero-title"]')?.value === 'Новая версия другого администратора');
  failure = 'network';
  await page.getByTestId('hero-title').fill('Черновик после сетевого сбоя');
  await page.getByTestId('save-site').click();
  await page.locator('.content-manager [role="alert"]').filter({ hasText: /fetch|network|загруз/i }).waitFor();
  assert.equal(await page.getByTestId('hero-title').inputValue(), 'Черновик после сетевого сбоя');
  assert.equal(requests.filter(item => item.path === '/admin/site' && item.method === 'GET').length, 2);
  assert.equal(requests.filter(item => item.path === '/admin/site' && item.method === 'PATCH').length, 2);
  failure = '';
  await page.getByTestId('save-site').click();
  await page.getByTestId('site-success').waitFor();
  assert.equal(requests.filter(item => item.path === '/admin/site' && item.method === 'PATCH').at(-1).headers['if-match'], '"8"');
  failure = 'pending-expiry';
  await page.getByTestId('hero-title').fill('Запрос из прежней сессии');
  const sent = page.waitForRequest(request => request.method() === 'PATCH' && request.url().endsWith('/admin/site'));
  await page.getByTestId('save-site').click();
  await sent;
  await page.locator('.staff-session').getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.locator('.staff-login').waitFor();
  await page.locator('[name="login"]').fill('admin');
  await page.locator('[name="password"]').fill('browser-password');
  await page.locator('.staff-login button[type="submit"]').click();
  await page.getByTestId('hero-title').waitFor();
  const finished = page.waitForResponse(response => response.request().method() === 'PATCH' && response.url().endsWith('/admin/site'));
  release();
  await (await finished).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.locator('.staff-login').count(), 0, 'An unmounted editor cannot expire the newer login');
  failure = 'expiry';
  await page.getByTestId('hero-title').fill('Проверка текущей сессии');
  await page.getByTestId('save-site').click();
  await page.locator('.staff-login').waitFor();
  assert.equal(await page.locator('.content-manager').count(), 0);
  assert.match(await page.locator('.staff-login').textContent(), /Сессия завершена/);
  await noServerStorage(page);
});

test('multipart upload errors keep file and alt, then selecting the uploaded ID publishes the hero image', async t => {
  const state = fixtures();
  state.session = staff('admin');
  let rejectUpload = true;
  const { page, requests } = await open(t, {
    state, hash: '/#crm-content',
    handle: async ({ route, path, method }) => {
      if (!rejectUpload || path !== '/admin/media' || method !== 'POST') return false;
      rejectUpload = false;
      await json(route, error('UNSUPPORTED_MEDIA_TYPE', 'Сервер отклонил формат изображения'), 415);
      return true;
    },
  });
  await page.getByTestId('content-tab-media').click();
  await page.getByTestId('media-file').setInputFiles({ name: 'content-photo.png', mimeType: 'image/png', buffer: tinyPng });
  const alt = 'Изображение из браузерной загрузки';
  await page.getByTestId('media-alt').fill(alt);
  await page.getByTestId('upload-media').click();
  await page.getByText('Сервер отклонил формат изображения', { exact: true }).waitFor();
  assert.equal(await page.getByTestId('media-file').evaluate(element => element.files[0]?.name), 'content-photo.png');
  assert.equal(await page.getByTestId('media-alt').inputValue(), alt);
  assert.equal(requests.filter(item => item.path === '/admin/media' && item.method === 'POST').length, 1);
  assert.equal(requests.filter(item => item.path === '/admin/site' && item.method === 'PATCH').length, 0);
  await page.getByTestId('upload-media').click();
  await page.getByTestId('upload-success').waitFor();
  await page.getByTestId('media-api-uploaded-image').waitFor();
  assert.equal(await page.getByTestId('media-file').evaluate(element => element.files.length), 0);
  assert.equal(await page.getByTestId('media-alt').inputValue(), '');
  const uploads = requests.filter(item => item.path === '/admin/media' && item.method === 'POST');
  assert.equal(uploads.length, 2);
  for (const upload of uploads) {
    assert.equal(upload.headers['x-csrf-token'], csrfToken);
    assert.match(upload.headers['content-type'], /^multipart\/form-data; boundary=/);
    assert.ok(upload.rawBody.includes(tinyPng), 'Multipart retains the binary image bytes');
    assert.ok(upload.rawBody.toString('utf8').includes('name="file"; filename="content-photo.png"'));
    assert.ok(upload.rawBody.toString('utf8').includes(`name="alt"\r\n\r\n${alt}`));
  }
  await page.getByTestId('content-tab-site').click();
  await page.getByTestId('hero-media-api-uploaded-image').click();
  assert.equal(await page.getByTestId('hero-media-api-uploaded-image').getAttribute('aria-pressed'), 'true');
  await page.getByTestId('save-site').click();
  await page.getByTestId('site-success').waitFor();
  const patch = requests.find(item => item.path === '/admin/site' && item.method === 'PATCH');
  assert.equal(patch.body.hero.imageId, 'api-uploaded-image');
  assert.equal(Object.hasOwn(patch.body.hero, 'image'), false);
  assert.equal(patch.headers['if-match'], '"7"');
  await page.evaluate(() => { location.hash = 'home'; });
  await page.locator('.hero').waitFor();
  await page.waitForFunction(() => document.querySelector('.hero')?.style.getPropertyValue('--hero-image').includes('/api/media/api-uploaded-image'));
  assert.equal(state.site.hero.image.alt, alt);
  await noServerStorage(page);
});

test('a delayed public site response from the first content save cannot replace a newer save', async t => {
  const state = fixtures();
  state.session = staff('admin');
  let release;
  const pendingFirstRefresh = new Promise(resolve => { release = resolve; });
  t.after(() => release());
  const { page, requests } = await open(t, {
    state, hash: '/#crm-content',
    handle: async ({ route, path, method }) => {
      if (path !== '/site' || method !== 'GET' || state.siteVersion < 8) return false;
      const version = state.siteVersion;
      const snapshot = structuredClone(state.site);
      if (version === 8) await pendingFirstRefresh;
      await json(route, snapshot, 200, { 'X-Test-Site-Version': String(version) });
      return true;
    },
  });
  await page.getByTestId('hero-title').fill('Первое сохранение с задержанным ответом');
  const firstRefresh = page.waitForRequest(request => request.method() === 'GET' && new URL(request.url()).pathname === '/api/site');
  await page.getByTestId('save-site').click();
  await firstRefresh;
  await page.getByTestId('site-success').waitFor();
  assert.equal(await page.getByTestId('hero-title').inputValue(), 'Первое сохранение с задержанным ответом');
  await page.getByTestId('hero-title').fill('Второе актуальное сохранение');
  const latestRefresh = page.waitForResponse(response => new URL(response.url()).pathname === '/api/site' && response.headers()['x-test-site-version'] === '9');
  await page.getByTestId('save-site').click();
  await page.getByTestId('site-success').waitFor();
  await (await latestRefresh).finished();
  const firstResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/site' && response.headers()['x-test-site-version'] === '8');
  release();
  await (await firstResponse).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await page.getByTestId('hero-title').inputValue(), 'Второе актуальное сохранение', 'Both saves finish in the same editor');
  await page.evaluate(() => { location.hash = 'home'; });
  await page.locator('.hero #hero-title').waitFor();
  assert.equal(await page.locator('.hero #hero-title').textContent(), 'Второе актуальное сохранение', 'An older public response must not replace the latest save');
  const patches = requests.filter(item => item.path === '/admin/site' && item.method === 'PATCH');
  assert.deepEqual(patches.map(item => item.headers['if-match']), ['"7"', '"8"']);
  assert.deepEqual(patches.map(item => item.body.hero.title), ['Первое сохранение с задержанным ответом', 'Второе актуальное сохранение']);
  assert.equal(state.siteVersion, 9);
  await noServerStorage(page);
});

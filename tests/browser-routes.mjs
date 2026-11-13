import { test, after } from 'node:test';
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
after(() => browser.close());
const baseUrl = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5196';
const now = '2026-10-28T10:00:00Z';
const session = (role = 'partner', id = role, complete = true) => ({ user: { id, name: '', email: complete ? `${id}@example.test` : null, phone: complete ? '+79991234567' : null, emailVerified: complete, phoneVerified: false, profileComplete: complete }, capabilities: { operate: role !== 'partner', editSite: role === 'admin', manageUsers: role === 'admin' }, csrfToken: `${id}-`.padEnd(40, 'a'), expiresAt: '2027-01-01T00:00:00Z' });
const product = { id: 'part', externalId: 'external', sku: 'P-1', brand: 'A', name: 'Тестовая деталь', category: 'parts', priceKopecks: 10000, stock: 10, image: '', deliveryDays: 1 };
const order = id => ({ id: `order-${id}`, number: `PRIVATE-${id}`, createdAt: now, status: 'new', totalKopecks: 10000, fulfillment: { method: 'pickup', address: '' }, items: [{ productId: 'part', sku: 'P-1', brand: 'A', name: 'Тестовая деталь', quantity: 1, unitPriceKopecks: 10000 }] });
const flow = { flowId: 'opaque-flow', resendAfterSeconds: 0 };
const adminUser = { id: 'other', name: '', login: 'other@example.test', email: 'other@example.test', phone: '+79991234567', emailVerified: true, phoneVerified: false, role: 'partner', isActive: true, version: 1 };
async function open(t, { initial = null, hash = '', width = 1440, waitUntil = 'networkidle', handle, publicTarget } = {}) {
 const context = await browser.newContext({ viewport: { width, height: 1000 } }); const page = await context.newPage();
 page.setDefaultTimeout(5000); const errors = []; const state = { session: initial, calls: [], user: { ...adminUser } };
 page.on('pageerror', issue => errors.push(issue.message));
 t.after(async () => { await context.close(); assert.deepEqual(errors, []); });
 await context.route('**/api/**', async route => {
  const request = route.request(); const url = new URL(request.url()); const path = url.pathname.slice(4); const method = request.method(); const body = request.postData() ? request.postDataJSON() : undefined;
  state.calls.push({ path, method, body, headers: request.headers() });
  const json = (value, status = 200, headers = {}) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value), headers });
  if (handle && await handle({ route, path, method, body, state, json })) return;
  if (path === '/site') return json({ hero: { title: 'MIRZAP67', description: 'Магазин', image: null, target: publicTarget || { type: 'catalog' }, buttonText: 'В каталог' }, settings: { storeName: 'MIRZAP67', phone: '+79991234567', address: 'Тестовый адрес', hours: '9–18', categoriesTitle: 'Категории', promotionsTitle: 'Предложения', catalogTitle: 'Каталог' }, menu: [{ label: 'Детали меню', position: 0, isVisible: true, target: publicTarget || { type: 'category', categoryId: 'parts' } }], publications: [{ id: 'news one', kind: 'offer', position: 0, title: 'Тестовая публикация', description: 'Описание', images: [], target: publicTarget || { type: 'catalog' }, buttonText: 'Каталог' }] });
  if (path === '/categories') return json([{ id: 'parts', name: 'Запчасти', position: 0, showInMenu: true }]);
  if (path === '/catalog') return json({ items: [product], total: 1, offset: 0, limit: 24, catalogUpdatedAt: now, stale: false });
  if (path === '/catalog/part') return json({ product, catalogUpdatedAt: now, stale: false });
  if (['/customer/session', '/manager/session'].includes(path)) {
   if (method === 'DELETE') { state.session = null; return route.fulfill({ status: 204 }); }
   if (method === 'POST') { const role = body.email.startsWith('admin') ? 'admin' : body.email.startsWith('operator') ? 'operator' : 'partner'; state.session = session(role, body.email?.split('@')[0] || role); return json(state.session); }
   return state.session ? json(state.session) : json({ code: 'SESSION_REQUIRED', message: 'Войдите снова' }, 401);
  }
  if (['/customer/auth/signup', '/customer/auth/resend', '/customer/auth/password-reset', '/customer/profile/activation'].includes(path)) return json(flow, 202);
  if (['/customer/auth/activate', '/customer/profile/activation/confirm'].includes(path)) { state.session = session(state.session?.capabilities.operate ? 'operator' : 'partner', state.session?.user.id || 'new-user'); return json(state.session); }
  if (path === '/customer/auth/password-reset/confirm') return route.fulfill({ status: 204 });
  if (path === '/customer/orders') {
   if (method === 'POST') return json(order(state.session.user.id), 201);
   return json({ items: [order(state.session.user.id)], total: 1, offset: 0, limit: 24 });
  }
  if (path.startsWith('/customer/orders/')) return json(order(state.session.user.id));
  if (path === '/customer/feedback') {
   const feedback = { id: 'own-feedback', number: 1, createdAt: now, type: 'request', message: body?.message || 'Мой вопрос', status: 'new', order: null };
   return method === 'POST' ? json(feedback, 201) : json({ items: [feedback], total: 1, offset: 0, limit: 24 });
  }
  if (path === '/admin/users') return json({ items: [state.user], total: 1, offset: 0, limit: 24 });
  if (path.startsWith('/admin/users/')) {
   if (method === 'PATCH') { Object.assign(state.user, body); state.user.version++; }
   return json(state.user, 200, { ETag: `"${state.user.version}"` });
  }
  if (path === '/manager/orders/staff-one') return json({ ...order('staff'), id: 'staff-one', customer: { name: 'Тест', phone: '+79991234567', email: '' }, integrationStatus: 'not_connected', version: 1, canCancel: true, externalDocumentId: null });
  if (path === '/manager/orders') return json({ items: [{ ...order('staff'), id: 'staff-one', customer: { name: 'Тест', phone: '+79991234567', email: '' }, integrationStatus: 'not_connected', version: 1, canCancel: true, externalDocumentId: null }], total: 1, offset: 0, limit: 24 });
  if (path === '/manager/feedback') return json({ items: [], total: 0, offset: 0, limit: 24 });
  return json({ code: 'NOT_FOUND', message: `Недоступен ${path}` }, 404);
 });
 await page.goto(`${baseUrl}/#${hash}`, { waitUntil });
 return { page, state };
}

const hashOf = page => page.evaluate(() => location.hash);
async function emailLogin(page) {
 await page.locator('.account-form input[name="email"]').fill('partner@example.test');
 await page.locator('.account-form input[name="password"]').fill('long password');
 await page.locator('.account-form button[type="submit"]').click();
}
test('category anchors survive fresh tab/reload and product modal follows URL back/forward/close/reopen', async t => {
 const { page } = await open(t);
 const category = page.locator('.category-card[data-id="parts"]');
 assert.equal(await category.evaluate(node => node.tagName), 'A');
 assert.equal(await category.getAttribute('href'), '#catalog?category=parts');
 await category.click(); await page.waitForURL('**/#catalog?category=parts');
 await page.reload(); assert.equal(await page.locator('input[name="category"][value="parts"]').isChecked(), true);
 const productLink = page.locator('.product-name'); assert.equal(await productLink.getAttribute('href'), '#catalog?category=parts&product=part');
 assert.match(await page.locator('.product-brand').textContent(), /Арт\. P-1/);
 await productLink.click(); await page.locator('dialog[open] #dialog-title').waitFor(); assert.match(await hashOf(page), /product=part/);
 const article = page.locator('.product-specs > div').filter({ has: page.getByText('Артикул', { exact: true }) });
 assert.equal(await article.locator('dd').textContent(), 'P-1');
 await page.goBack(); await page.waitForFunction(() => !document.querySelector('dialog[open]'));
 await page.goForward(); await page.locator('dialog[open] #dialog-title').waitFor();
 await page.locator('dialog [data-action="close"]').click(); await page.waitForURL('**/#catalog?category=parts');
 await productLink.click(); await page.locator('dialog[open]').waitFor();
 const fresh = await page.context().newPage(); await fresh.goto(`${baseUrl}/#catalog?product=part`); await fresh.locator('dialog[open] #dialog-title').waitFor(); await fresh.locator('dialog [data-action="close"]').click(); await fresh.waitForURL('**/#catalog');
});
test('direct product waits for its endpoint independently of catalog and rejects ambiguous details', async t => {
 const { page, state } = await open(t, { hash: 'catalog?product=part' });
 await page.locator('dialog[open] #dialog-title').waitFor(); assert.ok(state.calls.some(call => call.path === '/catalog/part'));
 await page.goto(`${baseUrl}/#catalog?product=part&product=other`); await page.waitForTimeout(100); assert.equal(await page.locator('dialog[open]').count(), 0);
 assert.equal(state.calls.filter(call => call.path === '/catalog/other').length, 0);
});
test('staff order list anchors and direct route open before list selection and close onsite', async t => {
 const { page } = await open(t, { initial: session('operator'), hash: 'crm?order=staff-one' });
 await page.locator('dialog[open]').waitFor(); await page.getByRole('heading', { name: 'Заказ № PRIVATE-staff' }).waitFor();
 await page.locator('dialog [data-action="close"]').click(); await page.waitForURL('**/#crm');
 const link=page.locator('.order-number'); assert.equal(await link.getAttribute('href'), '#crm?order=staff-one'); await link.click(); await page.locator('dialog[open]').waitFor();
});
test('anonymous private destination requires login and authentication opens the account landing', async t => {
 const { page, state } = await open(t, { hash: 'account-orders?order=owned' });
 await page.locator('.account-form input[name="email"]').waitFor();
 assert.equal(new URL(page.url()).hash, '#login?returnTo=%23account-orders%3Forder%3Downed');
 assert.equal(state.calls.filter(call => call.path.startsWith('/customer/orders')).length,0);
 await emailLogin(page); await page.waitForURL('**/#account-orders'); await page.getByText('№ PRIVATE-partner', { exact: true }).waitFor();
 assert.equal(state.calls.some(call => call.path === '/customer/orders/owned'), false);
});
test('signup activation and external return both use the common account landing', async t => {
 const destination=encodeURIComponent('#account-orders?order=owned');
 const { page } = await open(t, { hash: `signup?returnTo=${destination}` });
 await page.locator('.account-form input[name="email"]').fill('new@example.test'); await page.locator('.account-form input[name="phone"]').fill('+79991234567'); await page.locator('.account-form input[name="password"]').fill('long password'); await page.getByRole('button', { name: 'Получить код', exact: true }).click();
 await page.waitForURL(`**/#activate?returnTo=${destination}`); await page.getByLabel('Код из письма').fill('123456'); await page.getByRole('button', { name: 'Подтвердить код', exact: true }).click(); await page.waitForURL('**/#account-orders');
 await page.goto(`${baseUrl}/#activate?returnTo=${destination}`); assert.equal(await page.getByRole('link', { name:'Начать регистрацию' }).getAttribute('href'), `#signup?returnTo=${destination}`);
 await page.goto(`${baseUrl}/#login?returnTo=https%3A%2F%2Fevil.test`); await emailLogin(page); await page.waitForURL('**/#account-orders');
});
test('incomplete own profile reads the requested record without redirection and incomplete staff stays staff', async t => {
 const { page, state } = await open(t, { initial: session('partner','legacy',false), hash: 'account-orders?order=owned' });
 await page.getByTestId('own-detail').waitFor();
 assert.equal(await hashOf(page), '#account-orders?order=owned');
 assert.equal(state.calls.filter(call => call.path === '/customer/orders/owned').length, 1);
 assert.equal(state.calls.some(call => call.path.startsWith('/customer/profile/')), false);
 assert.equal(await page.locator('.crm-page .account-form').count(), 0);
 await page.getByTestId('account-profile').waitFor();
 const staff = await open(t, { initial: session('operator','staff',false), hash:'crm?order=staff-one' }); await staff.page.locator('dialog[open]').waitFor(); assert.equal(await hashOf(staff.page),'#crm?order=staff-one');
});
test('public async publication and section anchors focus actual card or report missing without private reads', async t => {
 const { page, state } = await open(t, { hash:'home?publication=news+one', handle: async ({path,json}) => { if(path === '/site') { await new Promise(resolve => setTimeout(resolve,150)); return false; } } });
 await page.waitForFunction(() => document.activeElement?.dataset.publicationId === 'news one');
 await page.goto(`${baseUrl}/#home?section=offers`); await page.waitForFunction(() => document.activeElement?.dataset.kind === 'offer');
 await page.goto(`${baseUrl}/#home?publication=hidden`); await page.getByText('Публикация недоступна.', {exact:true}).waitFor();
 assert.equal(state.calls.filter(call => call.path.startsWith('/admin/')).length,0);
});

for (const width of [1440, 390]) test(`repeated current section links scroll and focus from menu and landing at ${width}px without intercepting modified clicks`, async t => {
 const hash = '#home?section=offers';
 const { page } = await open(t, { width, publicTarget: { type: 'section', section: 'offers' } });
 const menuSelector = width === 390 ? '.mobile-menu-items a' : '.desktop-menu-item';
 const menu = page.locator(menuSelector);
 assert.equal(await menu.getAttribute('href'), hash);
 if (width === 390) await page.locator('.mobile-menu summary').click();
 await menu.click(); await page.waitForURL(`**/${hash}`);
 await page.waitForFunction(() => document.activeElement?.dataset.publicationId === 'news one');
 for (const selector of [menuSelector, '.hero-button', '.promotion-link']) {
  await page.evaluate(() => { document.activeElement.blur(); window.scrollTo({ top: 0, behavior: 'instant' }); });
  if (width === 390 && selector === menuSelector) await page.locator('.mobile-menu summary').click();
  await page.locator(selector).click();
  await page.waitForFunction(() => document.activeElement?.dataset.publicationId === 'news one' && window.scrollY > 0);
  assert.equal(await hashOf(page), hash);
  if (width === 390) assert.equal(await page.locator('.mobile-menu').evaluate(node => node.open), false);
 }
 await page.evaluate(() => { document.activeElement.blur(); window.scrollTo({ top: 0, behavior: 'instant' }); });
 if (width === 390) await page.locator('.mobile-menu summary').click();
 const popup = page.context().waitForEvent('page'); await menu.click({ modifiers: ['Meta'] }); const fresh = await popup;
 await fresh.waitForLoadState('networkidle'); assert.equal(new URL(fresh.url()).hash, hash);
 assert.equal(await page.evaluate(() => window.scrollY), 0);
 assert.equal(await page.evaluate(() => document.activeElement?.dataset.publicationId), undefined);
 await fresh.close();
});

test('late public detail cannot replace new selection; direct detail does not wait for catalog', async t => {
 let releaseCatalog; const catalogWait = new Promise(resolve => { releaseCatalog = resolve; }); t.after(() => releaseCatalog());
 const direct = await open(t, { hash:'catalog?product=part', waitUntil:'domcontentloaded', handle: async ({path}) => { if(path === '/catalog') await catalogWait; return false; } });
 await direct.page.locator('dialog[open] #dialog-title').waitFor(); assert.ok(await direct.page.getByText('Загружаем каталог', {exact:false}).count()); releaseCatalog();
 let release; const held = new Promise(resolve => { release=resolve; }); t.after(() => release());
 const { page } = await open(t, { hash:'catalog', handle:async ({path,json}) => {
  if(path === '/catalog/part') { await held; await json({product, catalogUpdatedAt:now, stale:false}); return true; }
  if(path === '/catalog/second') { await json({product:{...product,id:'second',name:'Вторая деталь'},catalogUpdatedAt:now,stale:false}); return true; } return false;
 } });
 await page.locator('.product-name').click(); await page.locator('dialog[open]').waitFor(); await page.evaluate(() => {location.hash='catalog?product=second';});
 await page.getByRole('heading', {name:'Вторая деталь',exact:true}).waitFor(); release(); await page.waitForTimeout(100); assert.equal(await page.locator('#dialog-title').textContent(),'Вторая деталь');
});
test('wrong role never fetches staff order and re-login resets an expired own detail to the account', async t => {
 const denied=await open(t,{initial:session('partner'),hash:'crm?order=staff-one'}); await denied.page.getByText('Этот раздел недоступен.',{exact:false}).waitFor(); assert.equal(denied.state.calls.filter(call => call.path.startsWith('/manager/orders')).length,0);
 let expired=true;
 const {page,state}=await open(t,{initial:session('partner'),hash:'account-orders?order=owned',handle:async({path,json})=>{if(path === '/customer/orders/owned' && expired){expired=false;await json({code:'SESSION_REQUIRED',message:'Сессия завершена'},401);return true;}return false;}});
 await page.locator('.crm-page').getByRole('link', { name: 'Войти снова', exact: true }).click();
 await page.locator('.account-form input[name="email"]').waitFor(); assert.equal(new URL(page.url()).hash, '#login?returnTo=%23account-orders%3Forder%3Downed'); await emailLogin(page); await page.waitForURL('**/#account-orders'); await page.getByText('№ PRIVATE-partner', { exact: true }).waitFor();
 assert.equal(state.calls.filter(call=>call.path==='/customer/orders/owned').length,1);
 assert.equal(await page.getByTestId('own-detail').count(), 0);
});

test('native modified clicks open public menu, product and staff order in new tabs without navigating original', async t => {
 const {page}=await open(t,{hash:'catalog'});
 for (const [selector,hash] of [['.desktop-menu-item','#catalog?category=parts'],['.product-name','#catalog?product=part']]) {
  const popup=page.context().waitForEvent('page'); await page.locator(selector).first().click({modifiers:['Meta']}); const fresh=await popup; await fresh.waitForLoadState('networkidle'); assert.equal(new URL(fresh.url()).hash,hash); assert.equal(new URL(page.url()).hash,'#catalog'); if(selector==='.product-name') await fresh.locator('dialog[open]').waitFor(); await fresh.close();
 }
 const staff=await open(t,{hash:'crm',initial:session('operator')}); const popup=staff.page.context().waitForEvent('page'); await staff.page.locator('.order-number').click({modifiers:['Meta']}); const fresh=await popup; await fresh.locator('dialog[open]').waitFor(); assert.equal(new URL(fresh.url()).hash,'#crm?order=staff-one'); assert.equal(new URL(staff.page.url()).hash,'#crm');
});

for (const width of [1440, 390]) test(`product detail preserves catalog scroll on open, close and history at ${width}px`, async t => {
 const { page } = await open(t, { hash: 'catalog?category=parts', width, handle: async ({ path, json }) => {
  if (path === '/catalog/part-12') { await json({ product: { ...product, id: 'part-12' }, catalogUpdatedAt: now, stale: false }); return true; }
  if (path !== '/catalog') return false;
  await json({ items: Array.from({ length: 24 }, (_, i) => ({ ...product, id: i ? `part-${i}` : 'part' })), total: 24, offset: 0, limit: 24, catalogUpdatedAt: now, stale: false }); return true;
 } });
 const link = page.locator('.product-name').nth(12);
 await link.scrollIntoViewIfNeeded();
 const scroll = await page.evaluate(() => scrollY);
 assert.ok(scroll > 100, 'start with a scrolled catalog');
 await link.click(); await page.locator('dialog[open] #dialog-title').waitFor();
 assert.ok(Math.abs(await page.evaluate(() => scrollY) - scroll) <= 1, 'opening a detail must keep the catalog in place');
 await page.locator('dialog [data-action="close"]').click(); await page.waitForURL('**/#catalog?category=parts');
 assert.ok(Math.abs(await page.evaluate(() => scrollY) - scroll) <= 1, 'closing must restore the same catalog position');
 assert.equal(await link.evaluate(node => document.activeElement === node), true);
 await link.click(); await page.locator('dialog[open]').waitFor();
 await page.goBack(); await page.waitForFunction(() => !document.querySelector('dialog[open]'));
 assert.ok(Math.abs(await page.evaluate(() => scrollY) - scroll) <= 1, 'browser Back must keep the catalog position');
 await page.locator('.header-cart').click(); await page.waitForURL('**/#cart');
 assert.equal(await page.evaluate(() => scrollY), 0, 'a different page still starts at the top');
});

for (const width of [1440, 390]) test(`current category is identified consistently in navigation at ${width}px`, async t => {
 const { page } = await open(t, { hash: 'catalog?category=parts', width });
 if (width < 600) await page.locator('.mobile-menu summary').click();
 const menu = page.locator(width < 600 ? '.mobile-menu-items a' : '.desktop-menu-item').first();
 assert.equal(await menu.getAttribute('aria-current'), 'page');
 assert.ok((await menu.getAttribute('class')).includes('selected'));
 assert.equal(await page.locator('.catalog-nav').getAttribute('aria-current'), 'page');
});

test('standalone catalog has its own page heading', async t => {
 const { page } = await open(t, { hash: 'catalog' });
 assert.equal(await page.locator('main h1#catalog-title').count(), 1);
});

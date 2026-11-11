import { test, after } from 'node:test';
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
after(() => browser.close());
const baseUrl = process.env.BASE_URL || 'http://127.0.0.1:5188';
const now = '2026-10-28T10:00:00Z';
const session = (role = 'partner', id = role, complete = true) => ({ user: { id, name: '', email: complete ? `${id}@example.test` : null, phone: complete ? '+79991234567' : null, emailVerified: complete, phoneVerified: false, profileComplete: complete }, capabilities: { operate: role !== 'partner', editSite: role === 'admin', manageUsers: role === 'admin' }, csrfToken: `${id}-`.padEnd(40, 'a'), expiresAt: '2027-01-01T00:00:00Z' });
const product = { id: 'part', externalId: 'external', sku: 'P-1', brand: 'A', name: 'Тестовая деталь', category: 'parts', priceKopecks: 10000, stock: 10, image: '', deliveryDays: 1 };
const order = id => ({ id: `order-${id}`, number: `PRIVATE-${id}`, createdAt: now, status: 'new', totalKopecks: 10000, fulfillment: { method: 'pickup', address: '' }, items: [{ productId: 'part', sku: 'P-1', brand: 'A', name: 'Тестовая деталь', quantity: 1, unitPriceKopecks: 10000 }] });
const flow = { flowId: 'opaque-flow', resendAfterSeconds: 0 };
const adminUser = { id: 'other', name: '', login: 'other@example.test', email: 'other@example.test', phone: '+79991234567', emailVerified: true, phoneVerified: false, role: 'partner', isActive: true, version: 1 };
async function open(t, { initial = null, hash = '', width = 1440, waitUntil = 'networkidle', handle } = {}) {
 const context = await browser.newContext({ viewport: { width, height: 1000 } }); const page = await context.newPage();
 page.setDefaultTimeout(5000); const errors = []; const state = { session: initial, calls: [], user: { ...adminUser } };
 page.on('pageerror', issue => errors.push(issue.message));
 t.after(async () => { await context.close(); assert.deepEqual(errors, []); });
 await context.route('**/api/**', async route => {
  const request = route.request(); const url = new URL(request.url()); const path = url.pathname.slice(4); const method = request.method(); const body = request.postData() ? request.postDataJSON() : undefined;
  state.calls.push({ path, method, body, headers: request.headers() });
  const json = (value, status = 200, headers = {}) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value), headers });
  if (handle && await handle({ route, path, method, body, state, json })) return;
  if (path === '/site') return json({ hero: { title: 'MIRZAP67', description: 'Магазин', image: null, target: { type: 'catalog' }, buttonText: 'В каталог' }, settings: { storeName: 'MIRZAP67', phone: '+79991234567', address: 'Тестовый адрес', hours: '9–18', categoriesTitle: 'Категории', promotionsTitle: 'Предложения', catalogTitle: 'Каталог' }, menu: [], publications: [] });
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
  if (path === '/customer/feedback/own-feedback') return json({ id: 'own-feedback', number: 1, createdAt: now, type: 'request', message: 'Мой вопрос', status: 'new', order: null });
  if (path === '/admin/users') return json({ items: [state.user], total: 1, offset: 0, limit: 24 });
  if (path.startsWith('/admin/users/')) {
   if (method === 'PATCH') { Object.assign(state.user, body); state.user.version++; }
   return json(state.user, 200, { ETag: `"${state.user.version}"` });
  }
  if (path === '/manager/orders' || path === '/manager/feedback') return json({ items: [], total: 0, offset: 0, limit: 24 });
  return json({ code: 'NOT_FOUND', message: `Недоступен ${path}` }, 404);
 });
 await page.goto(`${baseUrl}/#${hash}`, { waitUntil });
 return { page, state };
}
async function signIn(page, email = 'partner@example.test') {
 await page.locator('.account-form input[name="email"]').fill(email);
 await page.locator('.account-form input[name="password"]').fill('long password');
 await page.locator('.account-form button[type="submit"]').click();
 await page.getByRole('heading', { name: 'Контакты профиля' }).waitFor();
}
test('guest home has one sign-in entry and registration is reached from the login form', async t => {
 const { page } = await open(t);
 const login = page.getByRole('link', { name: 'Войти', exact: true });
 for (const width of [1440, 390, 320]) {
  await page.setViewportSize({ width, height: 1000 });
  assert.equal(await login.count(), 1);
  assert.equal(await page.getByRole('link', { name: 'Зарегистрироваться', exact: true }).count(), 0);
  assert.equal(await page.locator('.header-account').getByRole('link', { name: 'Войти', exact: true }).count(), 1);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
 }
 await login.click();
 await page.getByRole('heading', { name: 'Войти в личный кабинет', exact: true }).waitFor();
 const registration = page.locator('.account-card').getByRole('link', { name: 'Зарегистрироваться', exact: true });
 assert.equal(await registration.count(), 1);
 await registration.click();
 await page.getByRole('heading', { name: 'Регистрация', exact: true }).waitFor();
 await page.locator('.account-actions').getByRole('link', { name: 'Войти', exact: true }).click();
 await signIn(page);
 await page.getByRole('button', { name: 'Выйти', exact: true }).click();
 await page.getByRole('heading', { name: 'Войти в личный кабинет', exact: true }).waitFor();
 assert.equal(await page.locator('.header-account').getByRole('link', { name: 'Войти', exact: true }).count(), 1);
});
test('public auth navigation, signup/code/resend and password reset use accessible real forms', async t => {
 const { page, state } = await open(t, { hash: 'signup' });
 await page.locator('.account-form input[name="email"]').fill('new@example.test'); await page.locator('.account-form input[name="phone"]').fill('+79991234567'); await page.locator('.account-form input[name="password"]').fill('long password');
 await page.getByRole('button', { name: 'Получить код', exact: true }).click();
 await page.getByLabel('Код из письма').fill('123456'); await page.getByRole('button', { name: 'Отправить код повторно' }).click();
 await page.getByRole('button', { name: 'Подтвердить код', exact: true }).click(); await page.getByRole('heading', { name: 'Контакты профиля' }).waitFor();
 assert.equal(state.calls.find(call => call.path === '/customer/auth/signup').body.role, undefined);
 await page.goto(`${baseUrl}/#password-reset`); await page.locator('.account-form input[name="email"]').fill('new@example.test'); await page.getByRole('button', { name: 'Получить код', exact: true }).click();
 await page.getByLabel('Код из письма').fill('123456'); await page.getByLabel('Новый пароль', { exact: false }).fill('another password'); await page.getByRole('button', { name: 'Сохранить новый пароль' }).click();
 await page.waitForFunction(() => !document.querySelector('input[name="newPassword"]'));
 const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })); assert.ok(!stored.includes('long password')); assert.ok(!stored.includes('123456'));
});
test('partner sees only private account data and own checkout uses server contacts without mandatory name', async t => {
 const { page, state } = await open(t, { hash: 'login' }); await signIn(page);
 assert.equal(await page.locator('a[href="#admin-users"]').count(), 0); assert.equal(await page.locator('a[href="#crm-content"]').count(), 0);
 await page.locator('.crm-tabs a[href="#account-orders"]').click(); await page.getByText('№ PRIVATE-partner', { exact: true }).click(); await page.locator('[data-testid="own-detail"]').waitFor();
 await page.goto(`${baseUrl}/#catalog`); await page.locator('.product-card button[data-action="add"]').first().click(); await page.goto(`${baseUrl}/#cart`);
 assert.equal(await page.locator('#checkout-form input[name="phone"]').count(), 0); assert.equal(await page.locator('#checkout-form input[name="name"]').getAttribute('required'), null);
 await page.locator('.checkout-submit').click(); await page.getByRole('heading', { name: 'Заказ на самовывоз принят', exact: true }).waitFor();
 const create = state.calls.find(call => call.path === '/customer/orders' && call.method === 'POST'); assert.equal(create.body.customer, undefined); assert.equal(create.body.name, undefined); assert.ok(create.headers['x-csrf-token']);
});
test('operator keeps operational tabs while direct CMS and users routes stay unavailable', async t => {
 const { page, state } = await open(t, { initial: session('operator'), hash: 'crm' });
 await page.locator('.crm-tabs a[href="#crm-feedback"]').waitFor(); assert.equal(await page.locator('.crm-tabs a[href="#crm-content"]').count(), 0);
 await page.goto(`${baseUrl}/#crm-content`); await page.getByText('Редактор сайта доступен администратору.').waitFor();
 await page.goto(`${baseUrl}/#admin-users`); await page.getByText('Управление пользователями недоступно.').waitFor(); assert.equal(state.calls.filter(call => call.path === '/admin/users').length, 0);
 assert.equal(await page.getByRole('button', { name: 'Создать пользователя', exact: true }).count(), 0);
});
test('admin creates another administrator without storing credentials or changing the current identity', async t => {
 let release; const held = new Promise(resolve => { release = resolve; }); t.after(release);
 const { page, state } = await open(t, { initial: session('admin'), hash: 'admin-users', handle: async ({ path, method, body, state, json }) => {
  if (path !== '/admin/users' || method !== 'POST') return false;
  await held;
  state.user = { ...adminUser, id: 'created-admin', name: body.name, login: body.email, email: body.email, phone: body.phone, role: body.role };
  await json(state.user, 201, { ETag: '"1"' }); return true;
 } });
 await page.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
 const form = page.getByTestId('user-create-form');
 assert.deepEqual(await form.locator('select[name="role"] option').evaluateAll(options => options.map(option => option.value)), ['partner', 'operator', 'admin']);
 assert.equal(await form.locator('[name="role"]').inputValue(), 'partner');
 assert.equal(await form.locator('[name="password"]').getAttribute('type'), 'password');
 assert.equal(await form.locator('[name="password"]').getAttribute('autocomplete'), 'new-password');
 const password = ' Strong admin password 123 ';
 await form.locator('[name="name"]').fill('  Новый администратор  ');
 await form.locator('[name="email"]').fill('New.Admin@Example.test');
 await form.locator('[name="phone"]').fill('8 (999) 123-45-67');
 await form.locator('[name="password"]').fill(password);
 await form.locator('[name="role"]').selectOption('admin');
 await form.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
 await page.waitForFunction(() => document.querySelector('[data-testid="user-create-form"] button[type="submit"]')?.disabled === true);
 assert.equal(state.calls.filter(call => call.path === '/admin/users' && call.method === 'POST').length, 1);
 const create = state.calls.find(call => call.path === '/admin/users' && call.method === 'POST');
 assert.deepEqual(create.body, { name: 'Новый администратор', email: 'new.admin@example.test', phone: '+79991234567', password, role: 'admin' });
 assert.equal(create.headers['x-csrf-token'], session('admin').csrfToken);
 release(); await page.getByTestId('user-detail').getByRole('heading', { name: 'Новый администратор', exact: true }).waitFor();
 assert.equal(new URL(page.url()).hash, '#admin-users?user=created-admin');
 assert.equal(state.session.user.id, 'admin');
 assert.equal(await page.getByTestId('user-detail').locator('select').inputValue(), 'admin');
 assert.equal(await page.getByTestId('user-create-form').count(), 0);
 const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
 for (const secret of [password, 'new.admin@example.test']) { assert.ok(!stored.includes(secret)); assert.ok(!page.url().includes(secret)); }
 await page.getByRole('link', { name: 'К списку пользователей', exact: true }).click();
 await page.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
 assert.equal(await form.locator('[name="password"]').inputValue(), '');
});
test('admin can correct a duplicate-email error and cancelling clears the creation password', async t => {
 const { page, state } = await open(t, { initial: session('admin'), hash: 'admin-users', width: 390, handle: async ({ path, method, body, state, json }) => {
  if (path !== '/admin/users' || method !== 'POST') return false;
  if (body.email === 'other@example.test') { await json({ code: 'EMAIL_ALREADY_EXISTS', message: 'Email уже существует.' }, 409); return true; }
  state.user = { ...adminUser, id: 'created-operator', name: body.name, login: body.email, email: body.email, phone: body.phone || null, role: body.role };
  await json(state.user, 201, { ETag: '"1"' }); return true;
 } });
 await page.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
 const form = page.getByTestId('user-create-form'); const password = 'new operator password';
 await form.locator('[name="email"]').fill('other@example.test'); await form.locator('[name="password"]').fill(password); await form.locator('[name="role"]').selectOption('operator');
 await form.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
 await page.getByRole('alert').filter({ hasText: 'Email уже существует.' }).waitFor();
 assert.equal(await form.locator('[name="email"]').inputValue(), 'other@example.test'); assert.equal(await form.locator('[name="password"]').inputValue(), password);
 assert.equal(await form.locator('[name="role"]').inputValue(), 'operator');
 await form.locator('[name="email"]').fill('operator.new@example.test'); await form.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
 await page.getByTestId('user-detail').waitFor(); assert.equal(await page.getByTestId('user-detail').locator('select').inputValue(), 'operator');
 assert.equal(state.calls.filter(call => call.path === '/admin/users' && call.method === 'POST').length, 2);
 await page.getByRole('link', { name: 'К списку пользователей', exact: true }).click(); await page.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
 assert.equal(await form.locator('[name="password"]').inputValue(), '');
 await form.locator('[name="password"]').fill('discard this password'); await form.getByRole('button', { name: 'Отмена', exact: true }).click();
 await page.getByRole('button', { name: 'Создать пользователя', exact: true }).click(); assert.equal(await form.locator('[name="password"]').inputValue(), '');
 assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
 assert.equal(state.calls.filter(call => call.path === '/admin/users' && call.method === 'POST').length, 2);
});
test('a late admin creation response cannot restore a previous identity or its password draft', async t => {
 let release; const held = new Promise(resolve => { release = resolve; }); t.after(release);
 const { page, state } = await open(t, { initial: session('admin'), hash: 'admin-users', handle: async ({ path, method, body, state, json }) => {
  if (path !== '/admin/users' || method !== 'POST') return false;
  await held;
  state.user = { ...adminUser, id: 'created-late', name: body.name, login: body.email, email: body.email, phone: null, role: body.role };
  await json(state.user, 201, { ETag: '"1"' }); return true;
 } });
 await page.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
 const form = page.getByTestId('user-create-form'); const password = 'late admin creation password';
 await form.locator('[name="name"]').fill('Поздний пользователь'); await form.locator('[name="email"]').fill('late.created@example.test'); await form.locator('[name="password"]').fill(password); await form.locator('[name="role"]').selectOption('admin');
 await form.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
 await page.waitForFunction(() => document.querySelector('[data-testid="user-create-form"] button[type="submit"]')?.disabled === true);
 await page.getByRole('button', { name: 'Выйти', exact: true }).click();
 await page.getByRole('heading', { name: 'Войти в личный кабинет', exact: true }).waitFor();
 await page.goto(`${baseUrl}/#login?returnTo=%23account`);
 await signIn(page, 'admin-next@example.test');
 const finished = page.waitForResponse(response => new URL(response.url()).pathname === '/api/admin/users' && response.request().method() === 'POST');
 release(); await finished;
 assert.equal(state.session.user.id, 'admin-next'); assert.equal(new URL(page.url()).hash, '#account');
 assert.equal(await page.getByTestId('user-detail').count(), 0);
 await page.locator('.crm-tabs a[href="#admin-users"]').click(); await page.getByRole('button', { name: 'Создать пользователя', exact: true }).click();
 for (const field of ['name', 'email', 'phone', 'password']) assert.equal(await form.locator(`[name="${field}"]`).inputValue(), '');
 assert.equal(await form.locator('[name="role"]').inputValue(), 'partner');
 const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
 assert.ok(!stored.includes(password)); assert.ok(!stored.includes('late.created@example.test')); assert.ok(!page.url().includes('late.created'));
 assert.equal(state.calls.filter(call => call.path === '/admin/users' && call.method === 'POST').length, 1);
});
test('admin manages versioned roles and status, with stale versions requiring refresh', async t => {
 let stale = true;
 const { page, state } = await open(t, { initial: session('admin'), hash: 'admin-users', handle: async ({ path, method, json }) => { if (path.endsWith('/permissions') && method === 'PATCH' && stale) { stale = false; await json({ code: 'VERSION_CONFLICT', message: 'Версия изменилась' }, 412); return true; } } });
 await page.locator('.account-user-row').click(); await page.locator('[data-testid="user-detail"] select').selectOption('operator'); await page.getByRole('button', { name: 'Сохранить роль' }).click();
 await page.getByText(/Загрузите актуальную карточку перед изменением/).waitFor(); assert.equal(await page.getByRole('button', { name: 'Сохранить роль' }).isDisabled(), true);
 await page.getByRole('button', { name: 'Загрузить актуальную карточку', exact: true }).click(); await page.locator('[data-testid="user-detail"] select').selectOption('operator'); await page.getByRole('button', { name: 'Сохранить роль' }).click();
 await page.waitForFunction(() => document.querySelector('[data-testid="user-detail"]')?.textContent.includes('Версия 2'));
 await page.locator('[data-testid="user-detail"] select').selectOption('admin'); await page.getByRole('button', { name: 'Сохранить роль' }).click();
 await page.waitForFunction(() => document.querySelector('[data-testid="user-detail"]')?.textContent.includes('Версия 3'));
 assert.deepEqual(state.calls.findLast(call => call.path.endsWith('/permissions')).body, { role: 'admin' });
 assert.equal(state.calls.findLast(call => call.path.endsWith('/permissions')).headers['if-match'], '"2"');
 await page.getByRole('button', { name: 'Заблокировать', exact: true }).click(); await page.getByRole('button', { name: 'Разблокировать', exact: true }).waitFor();
 assert.equal(state.calls.findLast(call => call.path.endsWith('/status')).headers['if-match'], '"3"');
 assert.ok(await page.locator('.crm-tabs a[href="#crm"]').count());
});
test('partner without a phone sees owned orders and feedback below known profile values without activation', async t => {
 const initial = session('partner', 'legacy', false);
 Object.assign(initial.user, { name: 'Известный партнёр', email: 'legacy@example.test', emailVerified: true });
 const { page, state } = await open(t, { initial, hash: 'account', width: 390 });
 const cabinet = page.locator('.crm-page'), profile = page.getByTestId('account-profile');
 await profile.getByRole('heading', { name: 'Контакты профиля', exact: true }).waitFor();
 for (const value of ['Известный партнёр', 'legacy@example.test', 'Партнёр', 'Не указан']) assert.ok((await profile.innerText()).includes(value));
 assert.equal(await profile.locator('input, select, textarea, form').count(), 0);
 assert.equal(await cabinet.getByRole('heading', { name: 'Завершите профиль', exact: true }).count(), 0);
 assert.equal(await cabinet.getByRole('link', { name: 'Профиль', exact: true }).count(), 0);
 const ownOrder = cabinet.getByRole('link').filter({ hasText: '№ PRIVATE-legacy' }); await ownOrder.waitFor();
 assert.equal(await ownOrder.getAttribute('href'), '#account-orders?order=order-legacy');
 assert.equal(new URL(page.url()).hash, '#account');
 assert.ok(await profile.evaluate(element => Boolean(element.compareDocumentPosition(document.querySelector('.account-panel nav')) & Node.DOCUMENT_POSITION_FOLLOWING)));
 for (const width of [320, 390, 1440]) {
  await page.setViewportSize({ width, height: 1000 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
 }
 await ownOrder.click(); await page.getByTestId('own-detail').waitFor();
 assert.equal(new URL(page.url()).hash, '#account-orders?order=order-legacy');
 await cabinet.getByRole('link', { name: '← К списку', exact: true }).click();
 await page.reload({ waitUntil: 'networkidle' }); await cabinet.getByText('№ PRIVATE-legacy', { exact: true }).waitFor();
 await cabinet.getByRole('link', { name: 'Мои обращения', exact: true }).click();
 await cabinet.getByRole('link').filter({ hasText: 'Мой вопрос' }).click();
 await page.getByTestId('own-detail').getByText('Мой вопрос', { exact: true }).waitFor();
 assert.equal(new URL(page.url()).hash, '#account-feedback?case=own-feedback');
 assert.equal(state.session.user.id, 'legacy');
 assert.equal(state.calls.some(call => call.path.startsWith('/customer/profile/')), false);
 assert.equal(state.calls.some(call => call.path.startsWith('/admin/') || call.path.startsWith('/manager/') && call.path !== '/manager/session'), false);
 await page.getByRole('button', { name: 'Выйти', exact: true }).click();
 await page.getByRole('heading', { name: 'Войти в личный кабинет', exact: true }).waitFor();
 assert.equal(await page.getByTestId('account-profile').count(), 0);
 assert.equal(await page.getByText('legacy@example.test', { exact: true }).count(), 0);
});
test('an incomplete partner with no owned records sees honest empty lists without a completion form', async t => {
 const { page, state } = await open(t, { initial: session('partner', 'empty', false), hash: 'account', handle: async ({ path, method, json }) => {
  if (method !== 'GET' || !['/customer/orders', '/customer/feedback'].includes(path)) return false;
  await json({ items: [], total: 0, offset: 0, limit: 24 }); return true;
 } });
 await page.getByText('У вас пока нет заказов.', { exact: true }).waitFor();
 assert.equal(await page.locator('.crm-page .account-form').count(), 0);
 await page.locator('.crm-page').getByRole('link', { name: 'Мои обращения', exact: true }).click();
 await page.getByText('У вас пока нет обращений.', { exact: true }).waitFor();
 assert.equal(state.calls.some(call => call.path.startsWith('/customer/profile/')), false);
 assert.ok((await page.getByTestId('account-profile').innerText()).includes('Не указано'));
});
test('a slow first-user response is discarded after logout and second-user login', async t => {
 let release; let held;
 t.after(() => release?.());
 const { page } = await open(t, { initial: session('partner', 'first'), hash: 'account', waitUntil: 'domcontentloaded', handle: async ({ path, route }) => { if (path === '/customer/orders' && !held) { held = true; await new Promise(resolve => { release = resolve; }); await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items: [order('first')], total: 1, offset: 0, limit: 24 }) }); return true; } } });
 await page.getByText('Загружаем ваши данные…').waitFor();
 await page.getByRole('button', { name: 'Выйти', exact: true }).click();
 await page.waitForURL(url => url.hash === '#login');
 await page.locator('.account-section').getByRole('heading', { name: 'Войти в личный кабинет' }).waitFor();
 await signIn(page, 'second@example.test'); release();
 await page.getByText('№ PRIVATE-second', { exact: true }).waitFor(); assert.equal(await page.getByText('PRIVATE-first', { exact: false }).count(), 0);
 const profile = await page.getByTestId('account-profile').innerText(); assert.ok(profile.includes('second@example.test')); assert.ok(!profile.includes('first@example.test'));
});
test('expired signed-in checkout requires re-login and never falls back to guest', async t => {
 const { page, state } = await open(t, { initial: session(), hash: 'catalog', handle: async ({ path, method, json }) => { if (path === '/customer/orders' && method === 'POST') { await json({ code: 'SESSION_EXPIRED', message: 'Войдите снова' }, 401); return true; } } });
 await page.locator('.product-card button[data-action="add"]').first().click(); await page.goto(`${baseUrl}/#cart`); await page.locator('.checkout-submit').click();
 await page.getByRole('link', { name: 'Войти снова', exact: true }).waitFor(); assert.equal(await page.locator('.checkout-submit').isDisabled(), true); assert.equal(state.calls.some(call => call.path === '/orders'), false);
 assert.ok(await page.evaluate(() => localStorage.getItem('lavr.cart.v1').includes('part')));
});
for (const width of [320, 390]) test(`keyboard login and account layout fit ${width}px`, async t => {
 const { page } = await open(t, { hash: 'login', width });
 await page.locator('.account-form input[name="email"]').focus(); await page.keyboard.type('partner@example.test'); await page.keyboard.press('Tab'); await page.keyboard.type('long password'); await page.keyboard.press('Enter');
 await page.getByRole('heading', { name: 'Контакты профиля' }).waitFor();
 assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
 await page.screenshot({ path: `/tmp/lavr-accounts-mobile-${width}.png`, fullPage: true });
});
test('own feedback uses profile contacts, and expiry keeps the draft until the same account logs in', async t => {
 let fail = true;
 const { page, state } = await open(t, { initial: session('partner'), hash: 'account-feedback', handle: async ({ path, method, json }) => { if (path === '/customer/feedback' && method === 'POST' && fail) { fail = false; await json({ code: 'SESSION_EXPIRED', message: 'Войдите снова' }, 401); return true; } } });
 await page.getByRole('button', { name: 'Новое обращение' }).click();
 const dialog = page.locator('.feedback-dialog');
 assert.equal(await dialog.locator('input[name="feedbackEmail"]').count(), 0);
 await dialog.locator('textarea').fill('Сохранённый вопрос'); await dialog.getByRole('button', { name: 'Отправить обращение' }).click();
 await dialog.getByRole('link', { name: 'Войти снова', exact: true }).click(); await signIn(page);
 await page.goto(`${baseUrl}/#account-feedback`); await page.getByRole('button', { name: 'Новое обращение' }).click();
 assert.equal(await dialog.locator('textarea').inputValue(), 'Сохранённый вопрос');
 await dialog.getByRole('button', { name: 'Отправить обращение' }).click(); await dialog.locator('[data-testid="feedback-receipt"]').waitFor();
 const call = state.calls.findLast(call => call.path === '/customer/feedback' && call.method === 'POST'); assert.deepEqual(call.body, { type: 'request', message: 'Сохранённый вопрос' });
});
test('demo clearly labels simulation, refuses real signup and simulates account feedback', async t => {
 const context = await browser.newContext({ viewport: { width: 390, height: 844 } }); const page = await context.newPage(); t.after(() => context.close());
 await page.goto(`${process.env.DEMO_BASE_URL || 'http://127.0.0.1:5189'}/#signup`);
 await page.locator('.account-form input[name="email"]').fill('fake@example.test'); await page.locator('.account-form input[name="phone"]').fill('+79991234567'); await page.locator('.account-form input[name="password"]').fill('long password'); await page.getByRole('button', { name: 'Получить код', exact: true }).click();
 await page.getByRole('alert').filter({ hasText: /В демонстрации регистрация, коды и письма недоступны/ }).waitFor(); assert.equal(await page.locator('input[name="code"]').count(), 0);
 await page.locator('.account-actions a[href="#login"]').click(); await signIn(page);
 await page.locator('.crm-tabs a[href="#account-feedback"]').click(); await page.getByRole('button', { name: 'Новое обращение' }).click(); await page.locator('.feedback-dialog textarea').fill('Демонстрационный вопрос'); await page.locator('.feedback-dialog button[type="submit"]').click();
 await page.getByRole('heading', { name: 'Демообращение сохранено' }).waitFor(); assert.ok((await page.locator('.feedback-dialog').textContent()).includes('Магазину они не отправлены'));
});
test('checkout recovery restores the same account name and clears it on explicit logout', async t => {
 const { page } = await open(t, { initial: session('partner', 'first'), hash: 'catalog', handle: async ({ path, method, json }) => { if (path === '/customer/orders' && method === 'POST') { await json({ code: 'SESSION_EXPIRED', message: 'Войдите снова' }, 401); return true; } } });
 await page.locator('.product-card button[data-action="add"]').first().click(); await page.evaluate(() => { location.hash = 'cart'; });
 await page.locator('#checkout-form input[name="name"]').fill('Имя для этого заказа');
 assert.equal(await page.locator('#checkout-form [name="method"], #checkout-form [name="address"]').count(), 0);
 await page.locator('.checkout-submit').click(); await page.getByRole('link', { name: 'Войти снова', exact: true }).click(); await signIn(page, 'first@example.test'); await page.evaluate(() => { location.hash = 'cart'; });
 assert.equal(await page.locator('#checkout-form input[name="name"]').inputValue(), 'Имя для этого заказа');
 await page.getByRole('button', { name: 'Выйти', exact: true }).click(); await signIn(page, 'second@example.test'); await page.evaluate(() => { location.hash = 'cart'; });
 assert.equal(await page.locator('#checkout-form input[name="name"]').inputValue(), '');
 assert.equal(await page.locator('#checkout-form [name="method"], #checkout-form [name="address"]').count(), 0);
});

for (const [role, label] of [['admin', 'Администратор'], ['operator', 'Оператор'], ['partner', 'Партнёр']]) test(`${role} account controls appear once beside the cart and disappear on logout`, async t => {
 const { page } = await open(t, { initial: session(role), hash: 'account' });
 const account = page.locator('.header-account');
 const signOut = page.getByRole('button', { name: 'Выйти', exact: true });
 await account.locator('.account-role').waitFor();
 assert.equal(await account.locator('.account-role').textContent(), label);
 assert.equal(await account.locator('.crm-nav').getAttribute('title'), `${role}@example.test`);
 assert.equal(await account.locator('.crm-nav').getAttribute('href'), '#account');
 assert.equal(await signOut.count(), 1);
 assert.equal(await account.getByRole('button', { name: 'Выйти', exact: true }).count(), 1);
 assert.ok(await account.evaluate(element => element.parentElement.matches('.header-actions') && element.previousElementSibling?.matches('.header-cart')));
 for (const width of [320, 390, 1440]) {
  await page.setViewportSize({ width, height: 1000 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${role} header fits ${width}px`);
  for (const control of [account.locator('.crm-nav'), signOut]) {
   const size = await control.boundingBox();
   assert.ok(size && size.width >= 44 && size.height >= 44, `${role} account touch target at ${width}px: ${JSON.stringify(size)}`);
  }
 }
 await signOut.click();
 await page.getByRole('heading', { name: 'Войти в личный кабинет', exact: true }).waitFor();
 assert.equal(await page.locator('.account-role').count(), 0);
 assert.equal(await signOut.count(), 0);
 const login = account.getByRole('link', { name: 'Войти', exact: true });
 assert.equal(await login.getAttribute('title'), null);
 assert.equal(await login.getAttribute('href'), '#login');
});

for (const role of ['admin', 'operator', 'partner']) test(`${role} account stays inside the shared cabinet menu`, async t => {
 const { page, state } = await open(t, { initial: session(role), hash: 'account' });
 const cabinet = page.locator('.crm-page');
 const profile = page.getByTestId('account-profile'); await profile.getByRole('heading', { name: 'Контакты профиля' }).waitFor();
 const menu = cabinet.getByRole('navigation', { name: 'Разделы CRM', exact: true });
 assert.equal(await menu.count(), role === 'partner' ? 0 : 1);
 if (role !== 'partner') assert.equal(await menu.getByRole('link', { name: 'Личный кабинет', exact: true }).getAttribute('aria-current'), 'page');
 assert.equal(await menu.locator('a[href="#crm"]').count(), role === 'partner' ? 0 : 1);
 assert.equal(await menu.locator('a[href="#crm-content"]').count(), role === 'admin' ? 1 : 0);
 assert.equal(await menu.locator('a[href="#admin-users"]').count(), role === 'admin' ? 1 : 0);
 assert.equal(await profile.locator('input, form').count(), 0);
 assert.ok(await profile.evaluate(element => Boolean(element.compareDocumentPosition(document.querySelector('.crm-page nav')) & Node.DOCUMENT_POSITION_FOLLOWING)));
 assert.equal(await page.locator('main h1').count(), 1);
 await cabinet.getByText(`№ PRIVATE-${role}`, { exact: true }).waitFor();
 await cabinet.getByRole('link', { name: 'Мои заказы', exact: true }).click();
 await cabinet.getByText(`№ PRIVATE-${role}`, { exact: true }).waitFor();
 if (role !== 'partner') assert.equal(await menu.getByRole('link', { name: 'Личный кабинет', exact: true }).getAttribute('aria-current'), 'page');
 await page.reload({ waitUntil: 'networkidle' });
 await cabinet.getByText(`№ PRIVATE-${role}`, { exact: true }).waitFor();
 await cabinet.getByRole('link', { name: 'Мои обращения', exact: true }).click();
 await cabinet.getByText('Мой вопрос', { exact: true }).waitFor();
 if (role === 'partner') assert.equal(state.calls.some(call => call.path.startsWith('/admin/') || call.path.startsWith('/manager/') && call.path !== '/manager/session'), false);
});

test('incomplete admin profile is embedded and leaves staff sections available at 320px', async t => {
 const { page } = await open(t, { initial: session('admin', 'admin', false), hash: 'account', width: 320 });
 const cabinet = page.locator('.crm-page');
 const profile = page.getByTestId('account-profile'); await profile.getByRole('heading', { name: 'Контакты профиля' }).waitFor();
 assert.equal(await cabinet.locator('.account-form').count(), 0);
 assert.ok((await profile.innerText()).includes('Не указано'));
 await cabinet.getByText('№ PRIVATE-admin', { exact: true }).waitFor();
 assert.equal(await page.locator('main h1').count(), 1);
 assert.equal(await cabinet.getByRole('navigation', { name: 'Вход и регистрация' }).count(), 0);
 assert.equal(await cabinet.locator('.container').count(), 0);
 assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
 await cabinet.getByRole('link', { name: 'Обращения', exact: true }).click();
 await cabinet.getByRole('heading', { name: 'Обращения покупателей.' }).waitFor();
 await profile.waitFor();
 assert.ok(await profile.evaluate(element => Boolean(element.compareDocumentPosition(document.querySelector('.crm-page > nav')) & Node.DOCUMENT_POSITION_FOLLOWING)));
 await cabinet.getByRole('link', { name: 'Личный кабинет', exact: true }).click();
 await profile.getByRole('heading', { name: 'Контакты профиля' }).waitFor();
 assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
});

test('guest direct account link keeps login inside the cabinet without duplicate headings', async t => {
 const { page, state } = await open(t, { hash: 'account-orders?order=private', width: 320 });
 const cabinet = page.locator('.crm-page');
 await cabinet.getByRole('heading', { name: 'Войти в личный кабинет', exact: true }).waitFor();
 assert.equal(await page.getByTestId('account-profile').count(), 0);
 assert.equal(await page.locator('main h1').count(), 1);
 assert.equal(await cabinet.locator('.container').count(), 0);
 assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
 assert.equal(state.calls.some(call => call.path.startsWith('/customer/orders')), false);
 await page.locator('.account-form input[name="email"]').fill('partner@example.test');
 await page.locator('.account-form input[name="password"]').fill('long password');
 await page.locator('.account-form button[type="submit"]').click();
 await cabinet.getByTestId('own-detail').waitFor();
 assert.equal(new URL(page.url()).hash, '#account-orders?order=private');
});

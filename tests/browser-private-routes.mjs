import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { routeHash } from '../src/routes.js';
import { isFeedbackCase } from '../src/feedback.js';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || '/tmp/lavr-server-browser-qa/node_modules/playwright/index.mjs');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
after(() => browser.close());
const baseUrl = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5196';
const session = (role = 'admin') => ({ user: { id: role, name: '', email: `${role}@example.test`, phone: '+79991234567', emailVerified: true, phoneVerified: false, profileComplete: true }, capabilities: { operate: role !== 'partner', editSite: role === 'admin', manageUsers: role === 'admin' }, csrfToken: 'a'.repeat(40), expiresAt: '2027-01-01T00:00:00Z' });
const settings = { storeName: 'Private route test', phone: '+79991234567', address: 'Тестовый адрес', hours: '9–18', categoriesTitle: 'Категории', promotionsTitle: 'Предложения', catalogTitle: 'Каталог' };
const hero = { title: 'Тестовая главная', description: 'Описание', buttonText: 'Каталог', target: { type: 'catalog' } };
const publication = id => ({ id, kind: 'announcement', title: `Публикация ${id}`, description: 'Описание публикации', imageIds: [], target: { type: 'catalog' }, buttonText: 'Каталог', position: 0, status: 'draft', startsAt: null, endsAt: null, version: 3 });
const user = id => ({ id, name: `Пользователь ${id}`, login: 'other@example.test', email: 'other@example.test', phone: '+79991234567', emailVerified: true, phoneVerified: false, isActive: true, role: 'partner', version: 4 });
const pageData = items => ({ items, total: items.length, offset: 0, limit: 24 });

async function open(t, { hash, initial = session(), handle } = {}) {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const state = { session: initial, calls: [] };
  const errors = []; const unexpected = [];
  page.on('pageerror', issue => errors.push(issue.message));
  t.after(async () => { await context.close(); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); });
  await context.route('**/api/**', async route => {
    const request = route.request(); const path = new URL(request.url()).pathname.slice(4); const method = request.method();
    state.calls.push({ path, method, headers: request.headers(), body: request.postData() ? request.postDataJSON() : undefined });
    const json = (value, status = 200, headers = {}) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value), headers });
    if (handle && await handle({ path, method, route, state, json })) return;
    if (path === '/site') return json({ hero: { ...hero, image: null }, settings, menu: [], publications: [] });
    if (path === '/categories') return json([]);
    if (path === '/catalog') return json({ ...pageData([]), catalogUpdatedAt: '2026-10-29T10:00:00Z', stale: false });
    if (['/customer/session', '/manager/session'].includes(path)) {
      if (method === 'POST') { state.session = session(); return json(state.session); }
      return state.session ? json(state.session) : json({ code: 'SESSION_REQUIRED', message: 'Войдите снова' }, 401);
    }
    if (path === '/admin/site') return json({ hero: { ...hero, imageId: null }, settings, menu: [], version: 2 }, 200, { ETag: '"2"' });
    if (path === '/admin/categories') return json(pageData([]));
    if (path === '/admin/publications') return json(pageData([publication('listed')]));
    if (path.startsWith('/admin/publications/')) return json(publication(decodeURIComponent(path.slice('/admin/publications/'.length))), 200, { ETag: '"3"' });
    if (path === '/admin/media') return json(pageData([]));
    if (path === '/admin/users') return json(pageData([user('listed-user')]));
    if (path.startsWith('/admin/users/')) return json(user(decodeURIComponent(path.slice('/admin/users/'.length))), 200, { ETag: '"4"' });
    if (['/manager/orders', '/manager/feedback'].includes(path)) return json(pageData([]));
    unexpected.push(`${method} ${path}`);
    return json({ code: 'UNEXPECTED_REQUEST', message: path }, 500);
  });
  await page.goto(`${baseUrl}/${hash || '#home'}`, { waitUntil: 'domcontentloaded' });
  return { page, state, context };
}

for (const tab of ['media', 'publications']) test(`direct CMS ${tab} route selects its accepted tab on reload`, async t => {
  const { page } = await open(t, { hash: routeHash({ page: 'crm-content', tab }) });
  assert.equal(await page.getByTestId(`content-tab-${tab}`).getAttribute('aria-current'), 'page');
  assert.equal(await page.getByTestId(`content-tab-${tab}`).getAttribute('href'), routeHash({ page: 'crm-content', tab }));
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.getByTestId(`content-tab-${tab}`).getAttribute('aria-current'), 'page');
});

test('direct publication reads its opaque ID before an unrelated list page returns', async t => {
  const id = 'вне страницы ?&#%';
  let release;
  const held = new Promise(resolve => { release = resolve; });
  const { page, state } = await open(t, { hash: routeHash({ page: 'crm-content', tab: 'publications', publicationId: id }), handle: async ({ path, json }) => {
    if (path !== '/admin/publications') return false;
    await held; await json(pageData([publication('listed')])); return true;
  } });
  t.after(release);
  await page.getByTestId('publication-title').waitFor({ state: 'visible' });
  await page.waitForFunction(expected => document.querySelector('[data-testid="publication-title"]')?.value === expected, `Публикация ${id}`);
  assert.equal(state.calls.filter(call => call.path === `/admin/publications/${encodeURIComponent(id)}`).length, 1);
  release();
});

test('CMS links open in a new tab and browser history restores accepted selections', async t => {
  const { page, context } = await open(t, { hash: '#crm-content?tab=publications' });
  const link = page.getByTestId('edit-publication-listed');
  assert.equal(await link.getAttribute('href'), '#crm-content?tab=publications&publication=listed');
  const [popup] = await Promise.all([context.waitForEvent('page'), link.click({ modifiers: ['ControlOrMeta'] })]);
  await popup.waitForLoadState('networkidle');
  assert.equal(await popup.getByTestId('publication-title').inputValue(), 'Публикация listed');
  await link.click();
  await page.waitForURL('**/\u0023crm-content?tab=publications&publication=listed');
  await page.getByTestId('content-tab-media').click();
  await page.waitForURL('**/\u0023crm-content?tab=media');
  await page.goBack();
  await page.waitForFunction(() => document.querySelector('[data-testid="publication-title"]')?.value === 'Публикация listed');
  await page.goForward();
  assert.equal(await page.getByTestId('content-tab-media').getAttribute('aria-current'), 'page');
});

test('declining dirty CMS navigation keeps URL, editor, and detail fetch unchanged', async t => {
  const hash = '#crm-content?tab=publications&publication=original';
  const { page, state } = await open(t, { hash });
  await page.waitForFunction(() => document.querySelector('[data-testid="publication-title"]')?.value === 'Публикация original');
  await page.getByTestId('publication-title').fill('Несохранённый заголовок');
  let prompts = 0;
  const reject = dialog => { prompts++; dialog.dismiss(); };
  page.on('dialog', reject);
  await page.getByTestId('edit-publication-listed').click();
  await page.waitForFunction(expected => location.hash === expected, hash);
  assert.equal(await page.getByTestId('publication-title').inputValue(), 'Несохранённый заголовок');
  assert.equal(state.calls.some(call => call.path === '/admin/publications/listed'), false);
  await page.getByTestId('content-tab-media').click();
  await page.waitForFunction(expected => location.hash === expected, hash);
  assert.equal(await page.getByTestId('publication-title').inputValue(), 'Несохранённый заголовок');
  assert.equal(prompts, 2);
  page.off('dialog', reject);
  page.on('dialog', dialog => { prompts++; dialog.accept(); });
  await page.getByTestId('edit-publication-listed').click();
  await page.waitForFunction(() => document.querySelector('[data-testid="publication-title"]')?.value === 'Публикация listed');
  assert.equal(prompts, 3);
});

test('saving a new publication makes its ID shareable and reload restores that record', async t => {
  const created = { ...publication('created'), title: 'Созданная публикация' };
  const { page } = await open(t, { hash: '#crm-content?tab=publications', handle: async ({ path, method, json }) => {
    if (path === '/admin/publications' && method === 'POST') { await json(created, 201, { ETag: '"3"' }); return true; }
    if (path === '/admin/publications/created') { await json(created, 200, { ETag: '"3"' }); return true; }
    return false;
  } });
  const prompts = [];
  page.on('dialog', dialog => { prompts.push(dialog.message()); dialog.dismiss(); });
  await page.getByTestId('publication-title').fill(created.title);
  await page.getByTestId('save-publication').click();
  await page.waitForURL('**/\u0023crm-content?tab=publications&publication=created');
  await page.waitForFunction(title => document.querySelector('[data-testid="publication-title"]')?.value === title, created.title);
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.getByTestId('publication-title').inputValue(), created.title);
  assert.deepEqual(prompts, []);
});

test('direct user ID survives staff login and reload, and list/back updates selection', async t => {
  const id = 'пользователь ?&#%';
  const hash = routeHash({ page: 'admin-users', userId: id });
  const { page, state } = await open(t, { hash, initial: null });
  await page.locator('.account-form input[name="email"]').fill('admin@example.test');
  await page.locator('.account-form input[name="password"]').fill('long password');
  await page.locator('.account-form button[type="submit"]').click();
  await page.getByTestId('user-detail').waitFor();
  assert.ok((await page.getByTestId('user-detail').textContent()).includes(id));
  assert.equal(new URL(page.url()).hash, hash);
  await page.reload({ waitUntil: 'networkidle' });
  await page.getByTestId('user-detail').waitFor();
  assert.ok((await page.getByTestId('user-detail').textContent()).includes(id));
  await page.getByRole('link', { name: 'К списку пользователей', exact: true }).click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="user-detail"]'));
  assert.equal(new URL(page.url()).hash, '#admin-users');
  const listed = page.locator('.account-user-row');
  assert.equal(await listed.getAttribute('href'), '#admin-users?user=listed-user');
  await listed.click(); await page.getByTestId('user-detail').waitFor();
  assert.equal(new URL(page.url()).hash, '#admin-users?user=listed-user');
  await page.goBack(); await page.waitForFunction(() => !document.querySelector('[data-testid="user-detail"]'));
  assert.equal(state.calls.filter(call => call.path === `/admin/users/${encodeURIComponent(id)}`).length, 2);
});

test('user detail replaces a full list while loading and returns to the list', async t => {
  const users = Array.from({ length: 24 }, (_, index) => user(`user-${index}`));
  let release;
  const held = new Promise(resolve => { release = resolve; });
  const { page } = await open(t, { hash: '#admin-users', handle: async ({ path, json }) => {
    if (path === '/admin/users') { await json({ items: users, total: 48, offset: 0, limit: 24 }); return true; }
    if (path !== '/admin/users/user-3') return false;
    await held; await json(users[3], 200, { ETag: '"4"' }); return true;
  } });
  t.after(release);
  await page.locator('.account-user-row').nth(3).click();
  const back = page.getByRole('link', { name: 'К списку пользователей', exact: true });
  await back.waitFor();
  assert.equal(await page.locator('.account-user-row').count(), 0, 'The detail must replace the long list');
  assert.equal(await page.getByRole('searchbox', { name: 'Поиск пользователей' }).count(), 0);
  assert.equal(await page.getByRole('navigation', { name: 'Страницы пользователей' }).count(), 0);
  assert.equal(await page.getByRole('status').filter({ hasText: 'Загружаем карточку пользователя' }).count(), 1);
  release();
  const detail = page.getByTestId('user-detail');
  await detail.waitFor();
  assert.equal(await detail.evaluate(element => element.contains(document.activeElement)), true, 'Opening the detail must move focus into it');
  assert.ok(await detail.locator('h3').evaluate(element => {
    const { top, bottom } = element.getBoundingClientRect();
    return top >= 0 && bottom <= innerHeight;
  }), 'The selected user heading must be visible without scrolling through users');
  await back.click();
  await page.locator('.account-user-row').first().waitFor();
  assert.equal(await page.locator('.account-user-row').count(), 24);
  assert.equal(await page.locator('.account-user-row').nth(3).evaluate(element => element === document.activeElement), true, 'Returning to the list must focus the selected user');
  assert.equal(await detail.count(), 0);
});

for (const role of ['partner', 'operator']) test(`${role} cannot fetch private CMS or user detail through a direct URL`, async t => {
  const { page, state } = await open(t, { initial: session(role), hash: '#crm-content?tab=publications&publication=secret' });
  await page.getByText(role === 'partner' ? 'Этот раздел недоступен.' : 'Редактор сайта доступен администратору.', { exact: false }).waitFor();
  await page.goto(`${baseUrl}/#admin-users?user=secret`, { waitUntil: 'networkidle' });
  await page.getByText(role === 'partner' ? 'Этот раздел недоступен.' : 'Управление пользователями недоступно.', { exact: false }).waitFor();
  assert.equal(state.calls.some(call => call.path.startsWith('/admin/')), false);
});

for (const kind of ['publication', 'user']) test(`a late ${kind} response cannot replace the new accepted route`, async t => {
  const hash = id => kind === 'publication' ? routeHash({ page: 'crm-content', tab: 'publications', publicationId: id }) : routeHash({ page: 'admin-users', userId: id });
  const path = kind === 'publication' ? '/admin/publications/old' : '/admin/users/old';
  let release;
  const held = new Promise(resolve => { release = resolve; });
  const { page, state } = await open(t, { hash: hash('old'), handle: async ({ path: requested, json }) => {
    if (requested !== path) return false;
    await held; await json(kind === 'publication' ? publication('old') : user('old'), 200, { ETag: kind === 'publication' ? '"3"' : '"4"' }); return true;
  } });
  t.after(release);
  await page.waitForFunction(() => document.querySelector('[data-testid="publication-title"]') || document.querySelector('.account-admin'));
  assert.ok(state.calls.some(call => call.path === path));
  await page.evaluate(destination => { location.hash = destination; }, hash('new'));
  const selected = () => kind === 'publication' ? page.getByTestId('publication-title').inputValue() : page.getByTestId('user-detail').textContent();
  if (kind === 'publication') await page.waitForFunction(() => document.querySelector('[data-testid="publication-title"]')?.value === 'Публикация new');
  else await page.waitForFunction(() => document.querySelector('[data-testid="user-detail"]')?.textContent.includes('ID: new'));
  const oldResponse = page.waitForResponse(response => new URL(response.url()).pathname === `/api${path}`);
  release(); await oldResponse;
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.ok((await selected()).includes('new'));
  assert.ok(!(await selected()).includes('old'));
});

const feedbackCase = (id, number = 12) => ({ id, number, createdAt: '2026-11-02T10:45:00Z', updatedAt: '2026-11-02T10:45:00Z', type: 'request', name: 'Покупатель', phone: '', email: 'buyer@example.test', messenger: { service: '', address: '' }, message: 'Вопрос о детали', status: 'new', assignedTo: null, version: 1, resolution: '', order: null, history: [], canManage: true, notification: { state: 'not_configured', code: 'TELEGRAM_NOT_CONFIGURED' } });
const ownOrder = id => ({ id, number: 'ORDER-12', createdAt: '2026-11-02T10:45:00Z', status: 'new', totalKopecks: 10000, fulfillment: { method: 'pickup', address: '' }, items: [{ productId: 'part', sku: 'P-1', brand: 'A', name: 'Деталь', quantity: 1, unitPriceKopecks: 10000 }] });

test('returning from an own order or feedback detail keeps the current list page', async t => {
  for (const kind of ['orders', 'feedback']) {
    const records = Array.from({ length: 25 }, (_, index) => kind === 'orders' ? ownOrder(`record-${index}`) : ({ id: `record-${index}`, number: index + 1, createdAt: '2026-11-02T10:45:00Z', type: 'request', message: `Обращение ${index + 1}`, status: 'new', order: null }));
    const { page } = await open(t, { hash: `#account-${kind}`, initial: session('partner'), handle: async ({ path, route, json }) => {
      const resource = `/customer/${kind}`;
      if (path === `/customer/${kind === 'orders' ? 'feedback' : 'orders'}`) { await json(pageData([])); return true; }
      if (path === resource) {
        const offset = Number(new URL(route.request().url()).searchParams.get('offset') || 0);
        await json({ items: records.slice(offset, offset + 24), total: 25, offset, limit: 24 }); return true;
      }
      if (!path.startsWith(`${resource}/`)) return false;
      await json(records.find(item => item.id === path.slice(resource.length + 1))); return true;
    } });
    const paging = page.getByRole('navigation', { name: 'Страницы личных записей' });
    await paging.getByRole('button', { name: 'Далее', exact: true }).click();
    const last = page.locator(`.account-panel .feedback-list a[href="#account-${kind}?${kind === 'orders' ? 'order' : 'case'}=record-24"]`);
    await last.click();
    await page.getByTestId('own-detail').waitFor();
    await page.getByRole('link', { name: '← К списку', exact: true }).click();
    await paging.waitFor();
    assert.equal(await paging.locator('span').textContent(), '25–25 из 25', `${kind}: returning to the list must retain page 2`);
    assert.equal(await last.count(), 1);
    await last.click();
    await page.getByTestId('own-detail').waitFor();
    await page.goBack();
    await last.waitFor();
    assert.equal(await paging.locator('span').textContent(), '25–25 из 25', `${kind}: browser Back must retain page 2`);
    const other = kind === 'orders' ? 'feedback' : 'orders';
    await page.getByRole('navigation', { name: 'Личный кабинет', exact: true }).getByRole('link', { name: kind === 'orders' ? 'Мои обращения' : 'Мои заказы', exact: true }).click();
    await page.waitForURL(`**/#account-${other}`);
    await page.getByRole('navigation', { name: 'Личный кабинет', exact: true }).getByRole('link', { name: kind === 'orders' ? 'Мои заказы' : 'Мои обращения', exact: true }).click();
    await paging.waitFor();
    assert.equal(await paging.locator('span').textContent(), '1–24 из 25', `${kind}: changing sections must reset paging`);
  }
});

for (const kind of ['case', 'order']) test(`anonymous private ${kind} link uses email auth through recovery and registration and returns to the same record`, async t => {
  const id = 'не на странице ?&#%';
  const target = routeHash({ page: kind === 'case' ? 'crm-feedback' : 'crm', [kind === 'case' ? 'caseId' : 'orderId']: id });
  const privatePath = kind === 'case' ? `/manager/feedback/${encodeURIComponent(id)}` : `/manager/orders/${encodeURIComponent(id)}`;
  const { page, state } = await open(t, { hash: target, initial: null, handle: async ({ path, json }) => {
    if (path !== privatePath) return false;
    await json(kind === 'case' ? feedbackCase(id) : { ...ownOrder(id), customer: { name: 'Покупатель', phone: '+79991234567', email: '' }, integrationStatus: 'pending_sync', externalDocumentId: null, version: 1, canCancel: true });
    return true;
  } });
  await page.locator('.account-form input[name="email"]').waitFor();
  assert.equal(state.calls.some(call => call.path === privatePath), false);
  assert.equal(await page.locator('input[name="login"]').count(), 0);
  assert.equal(await page.getByRole('button', { name: 'Проверить сессию', exact: true }).count(), 0);
  await page.getByRole('link', { name: 'Забыли пароль?', exact: true }).click();
  await page.waitForFunction(() => location.hash.startsWith('#password-reset?'));
  assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('returnTo'), target);
  await page.locator('.account-actions').getByRole('link', { name: 'Зарегистрироваться', exact: true }).click();
  await page.waitForFunction(() => location.hash.startsWith('#signup?'));
  assert.equal(new URLSearchParams(new URL(page.url()).hash.split('?')[1]).get('returnTo'), target);
  await page.locator('.account-actions').getByRole('link', { name: 'Войти', exact: true }).click();
  await page.locator('.account-form input[name="email"]').fill('ADMIN@Example.Test');
  await page.locator('.account-form input[name="password"]').fill('long password');
  await page.locator('.account-form button[type="submit"]').click();
  await page.waitForFunction(expected => location.hash === expected, target);
  await (kind === 'case' ? page.getByTestId('feedback-detail') : page.locator('.detail-dialog[open]')).waitFor();
  const login = state.calls.find(call => call.method === 'POST');
  assert.equal(login.path, '/customer/session');
  assert.deepEqual(login.body, { email: 'admin@example.test', password: 'long password' });
  assert.equal(state.calls.filter(call => call.path === privatePath).length, 1);
  if (kind === 'case') {
    assert.equal(await page.getByRole('heading', { name: '№12 от 02-11-2026 13:45', exact: true }).count(), 1);
    assert.match(await page.getByTestId('feedback-detail').textContent(), /Отправка в Telegram не настроена/);
  }
});

test('a partner logging in through a private case link retains the destination and cannot read staff records', async t => {
  const hash = '#crm-feedback?case=staff-only';
  const { page, state } = await open(t, { hash, initial: null, handle: async ({ path, method, state, json }) => {
    if (path !== '/customer/session' || method !== 'POST') return false;
    state.session = session('partner'); await json(state.session); return true;
  } });
  await page.locator('.account-form input[name="email"]').fill('partner@example.test');
  await page.locator('.account-form input[name="password"]').fill('long password');
  await page.locator('.account-form button[type="submit"]').click();
  await page.getByText('Этот раздел недоступен.', { exact: false }).waitFor();
  assert.equal(new URL(page.url()).hash, hash);
  assert.equal(state.calls.some(call => call.path.startsWith('/manager/feedback')), false);
});

for (const width of [1440, 390, 320]) test(`manager request columns and long contacts remain usable at ${width}px`, async t => {
  const cases = [feedbackCase('short'), { ...feedbackCase('long', 13), name: 'ОченьДлинноеИмя'.repeat(6), email: `${'a'.repeat(80)}@example.test`, message: 'Очень длинное описание '.repeat(40).trim(), status: 'in_progress', assignedTo: { id: 'admin', name: 'Администратор' } }];
  assert.ok(cases.every(isFeedbackCase), 'Layout fixtures must satisfy the feedback DTO contract');
  const { page } = await open(t, { hash: '#crm-feedback', handle: async ({ path, json }) => {
    if (path !== '/manager/feedback') return false;
    await json(pageData(cases)); return true;
  } });
  await page.setViewportSize({ width, height: 1000 });
  await page.locator('.feedback-manager-list > a').first().waitFor();
  const layout = await page.locator('.feedback-manager-list > a').evaluateAll(rows => rows.map(row => ({ columns: getComputedStyle(row).gridTemplateColumns, widths: [...row.children].map(cell => Math.round(cell.getBoundingClientRect().width)) })));
  assert.deepEqual(layout[0].widths, layout[1].widths);
  assert.equal(layout[0].widths.length, 3);
  if (width > 760) { assert.equal(layout[0].widths[0], 240); assert.equal(layout[0].widths[2], 220); }
  assert.equal(await page.locator('.feedback-manager-list > a').first().getAttribute('href'), '#crm-feedback?case=short');
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  assert.equal(await page.getByText('Подготовлено к интеграции с 1С', { exact: true }).count(), 0);
});

for (const width of [1440, 390]) test(`header sign-in and sign-out share computed button styling at ${width}px`, async t => {
  const { page } = await open(t, { hash: '#login', initial: null });
  await page.setViewportSize({ width, height: 1000 });
  const buttonStyle = locator => locator.evaluate(element => {
    const style = getComputedStyle(element);
    return Object.fromEntries(['height', 'backgroundColor', 'color', 'fontSize', 'fontWeight', 'lineHeight', 'padding', 'borderRadius', 'borderWidth', 'borderStyle', 'borderColor'].map(property => [property, style[property]]));
  });
  const signIn = await buttonStyle(page.locator('.header-account a.btn'));
  await page.locator('.account-form input[name="email"]').fill('admin@example.test');
  await page.locator('.account-form input[name="password"]').fill('long password');
  await page.locator('.account-form button[type="submit"]').click();
  const signOut = page.getByRole('button', { name: 'Выйти', exact: true });
  await signOut.waitFor();
  assert.deepEqual(await buttonStyle(signOut), signIn);
});

test('account is an embedded menu section and leaving a dirty editor still asks first', async t => {
  const hash = '#crm-content?tab=publications&publication=original';
  const { page } = await open(t, { hash });
  await page.getByTestId('publication-title').fill('Несохранённый заголовок');
  let prompts = 0;
  const reject = dialog => { prompts++; dialog.dismiss(); };
  page.on('dialog', reject);
  await page.locator('.crm-page > nav').getByRole('link', { name: 'Личный кабинет', exact: true }).click();
  await page.waitForFunction(expected => location.hash === expected, hash);
  assert.equal(await page.getByTestId('publication-title').inputValue(), 'Несохранённый заголовок');
  assert.equal(prompts, 1);
  page.off('dialog', reject);
  page.once('dialog', dialog => dialog.accept());
  await page.locator('.crm-page > nav').getByRole('link', { name: 'Личный кабинет', exact: true }).click();
  await page.locator('.crm-page').getByRole('heading', { name: 'Контакты профиля' }).waitFor();
  assert.equal(await page.locator('.crm-page > nav a[href="#account"]').getAttribute('aria-current'), 'page');
  await page.locator('.crm-page > nav').getByRole('link', { name: 'Контент сайта', exact: true }).click();
  await page.getByTestId('content-tab-site').waitFor();
  await page.goBack();
  await page.locator('.crm-page').getByRole('heading', { name: 'Контакты профиля' }).waitFor();
});

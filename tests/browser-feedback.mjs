import { test, after } from 'node:test';
import assert from 'node:assert/strict';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
after(() => browser.close());
const baseUrl = process.env.BASE_URL || 'http://127.0.0.1:5173';
const now = '2026-10-20T09:00:00Z';
const ids = ['10000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000002'];
const staff = { user: { id: 'manager-1', name: 'Менеджер Тест', role: 'manager' }, csrfToken: 'feedback-browser-csrf-token-1234567890', expiresAt: '2027-01-01T00:00:00Z' };
const product = { id: 'part-1', externalId: 'external-1', sku: 'TEST-1', brand: 'LAVR', name: 'Тестовый фильтр', category: 'filters', priceKopecks: 123400, stock: 10, image: '', deliveryDays: 1 };
const feedback = (id, index = 1) => ({ id, number: `F-00${index}`, createdAt: now, updatedAt: now, type: 'request', name: '', phone: '', email: `buyer${index}@example.com`, messenger: { service: 'Telegram', address: `@buyer${index}` }, message: `Нужен фильтр ${index}`, status: 'new', assignedTo: null, version: 1, resolution: '', order: null, history: [], notification: { state: 'not_configured', code: null } });
const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
const failure = (route, message, status, code = 'TEST_ERROR') => json(route, { code, message, requestId: 'feedback-test' }, status);
async function open(t, { hash = '', viewport = { width: 1440, height: 1100 }, session = null, handle } = {}) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  const errors = [];
  const state = { session, cases: ids.map((id, index) => feedback(id, index + 1)), submissions: [], conversions: [], notes: [] };
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { await context.close(); assert.deepEqual(errors, []); });
  await context.route('**/api/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.slice(4);
    const method = request.method();
    const body = request.postData() ? request.postDataJSON() : null;
    if (handle && await handle({ route, path, method, body, state })) return;
    if (path === '/site') return json(route, { hero: { title: 'Автозапчасти', description: 'Тестовый магазин', image: null, target: { type: 'catalog' }, buttonText: 'В каталог' }, settings: { storeName: 'LAVR', phone: '+79991234567', address: 'Москва, Тестовая, 1', hours: '09:00–18:00', categoriesTitle: 'Категории', promotionsTitle: 'Предложения', catalogTitle: 'Каталог' }, menu: [], publications: [] });
    if (path === '/categories') return json(route, [{ id: 'filters', name: 'Фильтры', position: 0, showInMenu: true }]);
    if (path === '/catalog') return json(route, { items: [product], total: 1, offset: Number(url.searchParams.get('offset') || 0), limit: Number(url.searchParams.get('limit') || 24), catalogUpdatedAt: now, stale: false });
    if (path === '/catalog/part-1') return json(route, { product, catalogUpdatedAt: now, stale: false });
    if (path === '/feedback' && method === 'POST') { state.submissions.push({ body, key: request.headers()['idempotency-key'] }); return json(route, { id: ids[0], number: 'F-001', createdAt: now, state: 'accepted' }, 202); }
    if (path === '/manager/session') {
      if (method === 'POST') { state.session = staff; return json(route, staff); }
      if (method === 'DELETE') { state.session = null; return route.fulfill({ status: 204 }); }
      return state.session ? json(route, state.session) : failure(route, 'Войдите в кабинет', 401);
    }
    if (!state.session) return failure(route, 'Войдите в кабинет', 401);
    if (path === '/manager/feedback') return json(route, { items: state.cases, total: state.cases.length, offset: Number(url.searchParams.get('offset') || 0), limit: Number(url.searchParams.get('limit') || 24) });
    const found = state.cases.find(item => path.startsWith(`/manager/feedback/${item.id}`));
    if (found) {
      if (method === 'GET') return json(route, found);
      assert.equal(request.headers()['x-csrf-token'], staff.csrfToken);
      assert.equal(request.headers()['if-match'], `"${found.version}"`);
      if (path.endsWith('/status')) { Object.assign(found, { status: body.status, resolution: body.resolution || '', assignedTo: { id: staff.user.id, name: staff.user.name }, version: found.version + 1 }); return json(route, found); }
      if (path.endsWith('/notes')) { state.notes.push(body); found.history.push({ id: `note-${state.notes.length}`, createdAt: now, staff: { id: staff.user.id, name: staff.user.name }, action: 'feedback.note_added', ...body }); found.version++; return json(route, found); }
      if (path.endsWith('/order')) { state.conversions.push({ body, key: request.headers()['idempotency-key'] }); return failure(route, 'Адаптер 1С не настроен', 503, 'ERP_NOT_CONFIGURED'); }
    }
    if (path === '/manager/orders') return json(route, { items: [], total: 0, offset: 0, limit: 24 });
    return failure(route, `Unexpected ${method} ${path}`, 404);
  });
  await page.goto(`${baseUrl}/#${hash}`, { waitUntil: 'networkidle' });
  return { page, state };
}
async function login(page) {
  await page.getByLabel('Логин', { exact: true }).fill('manager');
  await page.getByLabel('Пароль', { exact: true }).fill('test-password');
  await page.getByRole('button', { name: 'Войти', exact: true }).click();
}

test('public form validates every contact, preserves draft and accepts an email-only request', async t => {
  const { page, state } = await open(t);
  await page.getByTestId('home-feedback-contact').click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Email', { exact: true }).fill('buyer@example.com');
  await dialog.getByLabel('Телефон', { exact: true }).fill('invalid');
  await dialog.getByTestId('feedback-submit').click();
  await dialog.getByRole('alert').waitFor();
  assert.equal(state.submissions.length, 0);
  await dialog.getByLabel('Телефон', { exact: true }).fill('');
  await dialog.getByRole('button', { name: 'Оставить запрос', exact: true }).click();
  assert.equal(await dialog.getByLabel('Email', { exact: true }).inputValue(), 'buyer@example.com');
  await dialog.getByLabel('Ваш запрос', { exact: true }).fill('Нужен фильтр');
  await dialog.getByTestId('feedback-submit').dblclick();
  await dialog.getByTestId('feedback-receipt').waitFor();
  assert.ok(!(await dialog.getByTestId('feedback-receipt').textContent()).includes('Номер обращения'));
  assert.ok(!(await dialog.getByTestId('feedback-receipt').textContent()).includes('F-001'));
  assert.equal(state.submissions.length, 1);
  assert.equal(state.submissions[0].body.type, 'request');
  assert.ok(state.submissions[0].key);
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  assert.ok(!stored.includes('buyer@example.com') && !stored.includes('Нужен фильтр'));
  await page.keyboard.press('Escape');
  assert.equal(await page.getByTestId('home-feedback-contact').evaluate(element => element === document.activeElement), true);
});

test('a successful submission allows a fresh request immediately and after reopening', async t => {
  const { page, state } = await open(t, { viewport: { width: 320, height: 740 } });
  const dialog = page.getByRole('dialog');
  await page.getByTestId('home-feedback-contact').click();
  await dialog.getByLabel('Email', { exact: true }).fill('repeat@example.com');
  await dialog.getByTestId('feedback-submit').click();
  await dialog.getByTestId('feedback-receipt').waitFor();
  assert.ok(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth + 1));
  await dialog.getByRole('button', { name: 'Готово', exact: true }).click();

  await page.getByTestId('home-feedback-contact').click();
  await dialog.getByTestId('feedback-submit').waitFor();
  assert.equal(await dialog.getByTestId('feedback-receipt').count(), 0);
  assert.equal(await dialog.getByLabel('Email', { exact: true }).inputValue(), '');
  await dialog.getByLabel('Email', { exact: true }).fill('repeat@example.com');
  await dialog.getByTestId('feedback-submit').click();
  await dialog.getByTestId('feedback-receipt').waitFor();
  assert.equal(state.submissions.length, 2);
  assert.deepEqual(state.submissions[1].body, state.submissions[0].body);
  assert.notEqual(state.submissions[1].key, state.submissions[0].key);

  await dialog.getByRole('button', { name: 'Отправить ещё обращение', exact: true }).click();
  assert.equal(await dialog.getByLabel('Email', { exact: true }).inputValue(), '');
  await dialog.getByLabel('Email', { exact: true }).fill('repeat@example.com');
  await dialog.getByTestId('feedback-submit').click();
  await dialog.getByTestId('feedback-receipt').waitFor();
  assert.equal(state.submissions.length, 3);
  assert.notEqual(state.submissions[2].key, state.submissions[1].key);

  await page.keyboard.press('Escape');
  await page.getByTestId('footer-feedback-request').click();
  await dialog.getByRole('heading', { name: 'Оставить запрос', exact: true }).waitFor();
  assert.equal(await dialog.getByLabel('Ваш запрос', { exact: true }).inputValue(), '');
  assert.equal(await dialog.getByLabel('Email', { exact: true }).inputValue(), '');
});

test('case deep link survives login, notes and closure remain in the same case', async t => {
  const { page, state } = await open(t, { hash: `crm-feedback?case=${ids[1]}` });
  await login(page);
  await page.getByTestId('feedback-detail').waitFor();
  assert.match(await page.getByTestId('feedback-detail').textContent(), /F-002/);
  await page.getByRole('button', { name: 'Взять в работу', exact: true }).click();
  await page.getByLabel('Заметка', { exact: true }).fill('Позвонили, вопрос решён');
  await page.getByLabel('Способ связи', { exact: true }).selectOption('email');
  await page.getByRole('button', { name: 'Добавить заметку', exact: true }).click();
  await page.getByText('Позвонили, вопрос решён', { exact: true }).waitFor();
  assert.equal(state.notes[0].contactMethod, 'email');
  await page.getByLabel('Результат закрытия', { exact: true }).fill('Вопрос решён');
  await page.getByRole('button', { name: 'Закрыть без заказа', exact: true }).click();
  await page.getByText('Результат: Вопрос решён', { exact: true }).waitFor();
  assert.equal(state.cases[1].resolution, 'Вопрос решён');
  assert.equal(await page.getByRole('button', { name: 'Подготовить заказ', exact: true }).count(), 0);
});

test('case order preparation keeps cart and retry key independent and retains inputs after 503', async t => {
  const { page, state } = await open(t, { hash: `crm-feedback?case=${ids[0]}`, session: staff });
  await page.evaluate(() => { localStorage.setItem('lavr.cart.v1', JSON.stringify([{ productId: 'part-1', quantity: 3 }])); sessionStorage.setItem('lavr.checkout-attempt.v1', 'buyer-checkout-sentinel'); });
  await page.getByRole('button', { name: 'Взять в работу', exact: true }).click();
  await page.getByRole('button', { name: 'Подготовить заказ', exact: true }).click();
  await page.getByTestId('feedback-order-name').fill('Покупатель Тест');
  await page.getByTestId('feedback-order-phone').fill('+7 (999) 123-45-67');
  await page.getByRole('button', { name: 'Добавить Тестовый фильтр', exact: true }).click();
  await page.getByTestId('feedback-order-submit').click();
  await page.getByText('Адаптер 1С не настроен', { exact: false }).waitFor();
  assert.equal(await page.getByTestId('feedback-order-name').inputValue(), 'Покупатель Тест');
  assert.equal(await page.getByTestId('feedback-order-phone').inputValue(), '+7 (999) 123-45-67');
  await page.getByTestId('feedback-order-submit').click();
  await page.waitForFunction(() => !document.querySelector('[data-testid="feedback-order-submit"]').disabled);
  assert.equal(state.conversions.length, 2);
  assert.equal(state.conversions[0].key, state.conversions[1].key);
  assert.equal(state.conversions[0].body.items[0].expectedUnitPriceKopecks, product.priceKopecks);
  assert.equal(await page.evaluate(() => localStorage.getItem('lavr.cart.v1')), JSON.stringify([{ productId: 'part-1', quantity: 3 }]));
  assert.equal(await page.evaluate(() => sessionStorage.getItem('lavr.checkout-attempt.v1')), 'buyer-checkout-sentinel');
});

test('late case response cannot replace a newer route or reappear after logout', async t => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  const { page } = await open(t, { hash: 'crm-feedback', session: staff, handle: async ({ route, path, method }) => {
    if (path === `/manager/feedback/${ids[0]}` && method === 'GET') { await waiting; await json(route, feedback(ids[0])); return true; }
    return false;
  } });
  await page.evaluate(id => { location.hash = `crm-feedback?case=${id}`; }, ids[0]);
  await page.getByText('Загружаем обращение…', { exact: true }).waitFor();
  await page.evaluate(id => { location.hash = `crm-feedback?case=${id}`; }, ids[1]);
  await page.getByTestId('feedback-detail').waitFor();
  release();
  await page.waitForTimeout(80);
  assert.match(await page.getByTestId('feedback-detail').textContent(), /F-002/);
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.getByRole('heading', { name: 'Вход для сотрудников' }).waitFor();
  assert.equal(await page.getByTestId('feedback-detail').count(), 0);
});

for (const width of [320, 390]) test(`public and CRM form controls fit ${width}px`, async t => {
  const { page } = await open(t, { viewport: { width, height: 844 }, hash: `crm-feedback?case=${ids[0]}`, session: staff });
  await page.getByTestId('feedback-detail').waitFor();
  await page.getByRole('button', { name: 'Взять в работу', exact: true }).click();
  await page.getByRole('button', { name: 'Подготовить заказ', exact: true }).click();
  await page.getByRole('button', { name: 'Добавить Тестовый фильтр', exact: true }).waitFor();
  const managerControls = await page.locator('.feedback-manager').locator('button,input,textarea,select').evaluateAll(elements => elements.filter(element => element.getClientRects().length).map(element => ({ tag: element.tagName, height: element.getBoundingClientRect().height, width: element.getBoundingClientRect().width, font: parseFloat(getComputedStyle(element).fontSize) })));
  for (const item of managerControls) { assert.ok(item.height >= 44 && item.width >= 44, JSON.stringify(item)); if (item.tag !== 'BUTTON') assert.ok(item.font >= 16, JSON.stringify(item)); }
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.getByTestId('footer-feedback-request').click();
  await page.getByRole('dialog').waitFor();
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  const controls = await page.getByRole('dialog').locator('button,input,textarea').evaluateAll(elements => elements.filter(element => element.getClientRects().length).map(element => ({ tag: element.tagName, height: element.getBoundingClientRect().height, width: element.getBoundingClientRect().width, font: parseFloat(getComputedStyle(element).fontSize) })));
  for (const item of controls) { assert.ok(item.height >= 44 && item.width >= 44, JSON.stringify(item)); if (item.tag !== 'BUTTON') assert.ok(item.font >= 16, JSON.stringify(item)); }
});

test('failed public delivery keeps phone draft and key; messenger-only contact is accepted', async t => {
  const attempts = [];
  const { page, state } = await open(t, { handle: async ({ route, path, method, body }) => {
    if (path === '/feedback' && method === 'POST') {
      attempts.push({ body, key: route.request().headers()['idempotency-key'] });
      if (attempts.length === 1) { await failure(route, 'Временно недоступно. Повторите отправку.', 503); return true; }
    }
    return false;
  } });
  await page.getByTestId('home-feedback-contact').click();
  const dialog = page.getByRole('dialog');
  await dialog.getByLabel('Телефон', { exact: true }).fill('8 (999) 123-45-67');
  await dialog.getByTestId('feedback-submit').click();
  await dialog.getByRole('alert').waitFor();
  await page.keyboard.press('Escape');
  await page.getByTestId('footer-feedback-contact').click();
  assert.equal(await dialog.getByLabel('Телефон', { exact: true }).inputValue(), '8 (999) 123-45-67');
  await dialog.getByTestId('feedback-submit').click();
  await dialog.getByTestId('feedback-receipt').waitFor();
  assert.equal(attempts[0].key, attempts[1].key);
  assert.equal(attempts[1].body.phone, '+79991234567');
  await dialog.getByRole('button', { name: 'Отправить ещё обращение', exact: true }).click();
  await dialog.getByLabel('Мессенджер', { exact: true }).fill('MAX');
  await dialog.getByTestId('feedback-submit').click();
  await dialog.getByRole('alert').waitFor();
  assert.equal(state.submissions.length, 1);
  await dialog.getByLabel('Контакт в мессенджере', { exact: true }).fill('test-user');
  await dialog.getByTestId('feedback-submit').click();
  await dialog.getByTestId('feedback-receipt').waitFor();
  assert.deepEqual(state.submissions[1].body.messenger, { service: 'MAX', address: 'test-user' });
  assert.equal(state.submissions[1].body.message, '');
});

test('a version conflict and rejected navigation preserve a manager note', async t => {
  let conflict = true;
  const { page, state } = await open(t, { hash: `crm-feedback?case=${ids[0]}`, session: staff, handle: async ({ route, path, method, state }) => {
    if (path.endsWith('/notes') && method === 'POST' && conflict) {
      conflict = false; state.cases[0].version++;
      await failure(route, 'Обращение изменилось', 412, 'VERSION_CONFLICT'); return true;
    }
    return false;
  } });
  await page.getByRole('button', { name: 'Взять в работу', exact: true }).click();
  await page.getByLabel('Заметка', { exact: true }).fill('Несохранённый результат');
  page.once('dialog', dialog => dialog.dismiss());
  await page.getByRole('button', { name: '← Все обращения', exact: true }).click();
  await page.waitForFunction(id => location.hash === `#crm-feedback?case=${id}`, ids[0]);
  assert.equal(await page.getByLabel('Заметка', { exact: true }).inputValue(), 'Несохранённый результат');
  await page.getByRole('button', { name: 'Добавить заметку', exact: true }).click();
  await page.getByRole('alert').filter({ hasText: 'Ваши данные остались в форме' }).waitFor();
  assert.equal(await page.getByLabel('Заметка', { exact: true }).inputValue(), 'Несохранённый результат');
  await page.getByRole('button', { name: 'Загрузить актуальную карточку', exact: true }).click();
  await page.getByLabel('Заметка', { exact: true }).waitFor();
  assert.equal(await page.getByLabel('Заметка', { exact: true }).inputValue(), 'Несохранённый результат');
  await page.getByRole('button', { name: 'Добавить заметку', exact: true }).click();
  await page.getByText('Несохранённый результат', { exact: true }).waitFor();
  assert.equal(state.notes.length, 1);
});

test('order deep link opens after login and reload independently of the list page', async t => {
  const order = { id: 'linked-order-1', number: 'ORDER-100', createdAt: now, totalKopecks: product.priceKopecks, status: 'new', integrationStatus: 'pending_sync', customer: { name: 'Покупатель заказа', phone: '+79991234567', email: '' }, fulfillment: { method: 'pickup', address: '' }, items: [{ productId: product.id, sku: product.sku, brand: product.brand, name: product.name, quantity: 1, unitPriceKopecks: product.priceKopecks }], version: 1, canCancel: true, externalDocumentId: null };
  const { page } = await open(t, { hash: `crm?order=${order.id}`, handle: async ({ route, path, state }) => {
    if (path === `/manager/orders/${order.id}`) { await (state.session ? json(route, order) : failure(route, 'Войдите в кабинет', 401)); return true; }
    return false;
  } });
  await login(page);
  await page.getByRole('dialog').getByRole('heading', { name: 'Заказ № ORDER-100', exact: true }).waitFor();
  await page.reload();
  await page.getByRole('dialog').getByRole('heading', { name: 'Заказ № ORDER-100', exact: true }).waitFor();
  assert.equal(await page.locator('.orders-table tbody tr').count(), 0);
});

test('a case response arriving after logout stays private', async t => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  const { page } = await open(t, { hash: 'crm-feedback', session: staff, handle: async ({ route, path, method }) => {
    if (path === `/manager/feedback/${ids[0]}` && method === 'GET') { await waiting; await json(route, feedback(ids[0])); return true; }
    return false;
  } });
  await page.evaluate(id => { location.hash = `crm-feedback?case=${id}`; }, ids[0]);
  await page.getByText('Загружаем обращение…', { exact: true }).waitFor();
  await page.getByRole('button', { name: 'Выйти', exact: true }).click();
  await page.getByRole('heading', { name: 'Вход для сотрудников' }).waitFor();
  release();
  await page.waitForTimeout(80);
  assert.equal(await page.getByTestId('feedback-detail').count(), 0);
  assert.ok(!(await page.locator('main').textContent()).includes('buyer1@example.com'));
});

test('catalog refresh freezes selected product edits and applies results by product identity', async t => {
  let release;
  let started;
  const waiting = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { started = resolve; });
  const extra = { ...product, id: 'part-2', externalId: 'external-2', sku: 'TEST-2', name: 'Тестовая свеча', priceKopecks: 50000 };
  const { page } = await open(t, { hash: `crm-feedback?case=${ids[0]}`, session: staff, handle: async ({ route, path }) => {
    if (path === '/catalog') {
      const url = new URL(route.request().url());
      await json(route, { items: [product, extra], total: 2, offset: 0, limit: Number(url.searchParams.get('limit') || 24), catalogUpdatedAt: now, stale: false }); return true;
    }
    if (path === '/catalog/part-1') { started(); await waiting; await json(route, { product: { ...product, priceKopecks: 150000 }, catalogUpdatedAt: now, stale: false }); return true; }
    if (path === '/catalog/part-2') { await json(route, { product: extra, catalogUpdatedAt: now, stale: false }); return true; }
    return false;
  } });
  t.after(() => release());
  await page.getByRole('button', { name: 'Взять в работу', exact: true }).click();
  await page.getByRole('button', { name: 'Подготовить заказ', exact: true }).click();
  await page.getByRole('button', { name: 'Добавить Тестовый фильтр', exact: true }).click();
  await page.getByRole('button', { name: 'Добавить Тестовая свеча', exact: true }).click();
  await page.getByRole('button', { name: 'Проверить цены и остатки', exact: true }).click();
  await requested;
  assert.equal(await page.getByRole('button', { name: 'Удалить Тестовый фильтр', exact: true }).isDisabled(), true);
  assert.equal(await page.getByRole('spinbutton', { name: 'Количество Тестовая свеча', exact: true }).isDisabled(), true);
  release();
  await page.getByRole('button', { name: 'Добавить Тестовый фильтр', exact: true }).waitFor();
  assert.deepEqual(await page.locator('.feedback-selected-product strong').allTextContents(), ['Тестовый фильтр', 'Тестовая свеча']);
  await page.getByRole('button', { name: 'Удалить Тестовый фильтр', exact: true }).click();
  assert.deepEqual(await page.locator('.feedback-selected-product strong').allTextContents(), ['Тестовая свеча']);
});

test('an order deep link within CRM does not strand a pending orders list', async t => {
  let release;
  let started;
  const waiting = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { started = resolve; });
  const { page } = await open(t, { session: staff, handle: async ({ route, path }) => {
    if (path === '/manager/orders') { started(); await waiting; await json(route, { items: [], total: 0, offset: 0, limit: 24 }); return true; }
    return false;
  } });
  t.after(() => release());
  await page.locator('.crm-nav').click();
  await requested;
  await page.getByText('Загружаем заказы…', { exact: true }).waitFor();
  await page.evaluate(() => { location.hash = 'crm?order=off-page-order'; });
  await page.getByRole('dialog').waitFor();
  release();
  await page.getByRole('dialog').getByRole('button', { name: 'Закрыть', exact: true }).click();
  await page.getByRole('heading', { name: 'Первый заказ — начало пути', exact: true }).waitFor();
  assert.equal(await page.getByText('Загружаем заказы…', { exact: true }).count(), 0);
});

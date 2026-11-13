import { test, after } from 'node:test';
import assert from 'node:assert/strict';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
after(() => browser.close());
const base = process.env.BASE_URL || 'http://127.0.0.1:5188';
const stamp = '2026-11-12T10:00:00Z';
const products = ['first', 'second'].map((id, index) => ({ id, externalId: id, sku: id, brand: 'TEST', name: `Деталь ${id}`, category: 'parts', priceKopecks: (index + 1) * 10000, stock: 10, unit: 'шт', image: '', deliveryDays: 0 }));
const account = { user: { id: 'buyer', name: 'Имя из профиля', email: 'buyer@example.test', phone: null, emailVerified: false, phoneVerified: false, profileComplete: false }, capabilities: { operate: false, editSite: false, manageUsers: false }, csrfToken: 'a'.repeat(40), expiresAt: '2027-01-01T00:00:00Z' };
async function open(t, { signedIn = false, missing = false, width = 1440, hash = 'cart', failOnce = false, deferOrder = false, pendingProduct = false, waitUntil = 'networkidle' } = {}) {
  const context = await browser.newContext({ viewport: { width, height: 1000 } });
  const page = await context.newPage(); page.setDefaultTimeout(5000);
  const errors = [], calls = []; let release;
  const delayed = new Promise(resolve => { release = resolve; });
  let releaseProduct; const heldProduct = new Promise(resolve => { releaseProduct = resolve; });
  let firstUnavailable = false;
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { release(); releaseProduct(); await context.close(); assert.deepEqual(errors, []); });
  await page.addInitScript(() => {
    if (!sessionStorage.getItem('checkout-fixture')) {
      localStorage.setItem('lavr.cart.v1', JSON.stringify([{ productId: 'first', quantity: 1 }, { productId: 'second', quantity: 2 }]));
      sessionStorage.setItem('checkout-fixture', 'yes');
    }
  });
  await context.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname.slice(4);
    const method = route.request().method();
    const json = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/site') return json({ hero: { title: 'MIRZAP67', description: 'Магазин', image: null, target: { type: 'catalog' }, buttonText: 'Каталог' }, settings: { storeName: 'MIRZAP67', phone: '+79991234567', address: 'Тестовый адрес', hours: '9–18', categoriesTitle: 'Категории', promotionsTitle: 'Предложения', catalogTitle: 'Каталог' }, menu: [], publications: [] });
    if (path === '/categories') return json([{ id: 'parts', name: 'Запчасти', position: 0, showInMenu: true }]);
    if (path === '/catalog') return json({ items: products, total: products.length, offset: 0, limit: 24, catalogUpdatedAt: stamp, stale: false });
    if (path.startsWith('/catalog/')) { const product = products.find(item => path.endsWith(`/${item.id}`)); if (pendingProduct && product?.id === 'second') await heldProduct; return !product || missing && product.id === 'second' || firstUnavailable && product.id === 'first' ? json({ code: 'NOT_FOUND', message: 'Товар недоступен.' }, 404) : json({ product, catalogUpdatedAt: stamp, stale: false }); }
    if (path === '/customer/session') return signedIn ? json(account) : json({ code: 'SESSION_REQUIRED', message: 'Войдите снова.' }, 401);
    if (['/orders', '/customer/orders'].includes(path) && method === 'POST') {
      const body = route.request().postDataJSON(); calls.push({ path, body, key: route.request().headers()['idempotency-key'] });
      if (failOnce && calls.length === 1) return json({ code: 'TEMPORARY', message: 'Повторите отправку.' }, 503);
      if (deferOrder) await delayed;
      const result = { id: 'created', number: 'TEST-ORDER', createdAt: stamp, status: 'new', totalKopecks: body.items.reduce((sum, item) => sum + item.quantity * item.expectedUnitPriceKopecks, 0) };
      return json(path === '/orders' ? { ...result, integrationStatus: 'not_connected' } : { ...result, fulfillment: body.fulfillment, items: body.items.map(item => { const product = products.find(product => product.id === item.productId); return { productId: item.productId, sku: product.sku, brand: product.brand, name: product.name, quantity: item.quantity, unit: product.unit, unitPriceKopecks: item.expectedUnitPriceKopecks }; }) }, 201);
    }
    return json({ code: 'NOT_FOUND', message: 'Недоступно.' }, 404);
  });
  await page.goto(`${base}/#${hash}`, { waitUntil });
  return { page, calls, release, rejectFirst: () => { firstUnavailable = true; } };
}
async function continueCheckout(page) {
  await page.locator('.checkout-continue').click();
  await page.waitForURL('**/#checkout');
  await page.locator('#checkout-form').waitFor();
}
async function contacts(page, name = 'Тестовый покупатель') {
  await page.locator('#checkout-form [name="name"]').fill(name);
  await page.locator('#checkout-form [name="phone"]').fill('+79991234567');
}
for (const width of [1440, 390, 320]) test(`cart selects positions and checkout submits only those positions at ${width}px`, async t => {
  const { page, calls } = await open(t, { width });
  assert.equal(await page.locator('#checkout-form').count(), 0);
  await page.getByRole('checkbox', { name: 'Выбрать Деталь second', exact: true }).uncheck();
  await continueCheckout(page);
  assert.equal(await page.locator('.checkout-items li').count(), 1);
  assert.match(await page.locator('.summary-total strong').innerText(), /100/);
  await contacts(page);
  await page.locator('.checkout-submit').click();
  await page.getByRole('heading', { name: 'Заказ на самовывоз принят', exact: true }).waitFor();
  assert.deepEqual(calls[0].body.items.map(item => item.productId), ['first']);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))), [{ productId: 'second', quantity: 2 }]);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
});
test('unverified account checks out with editable contacts without changing its profile', async t => {
  const { page, calls } = await open(t, { signedIn: true });
  assert.equal(await page.getByText(/Для оформления нужны подтверждённый/).count(), 0);
  await continueCheckout(page);
  assert.equal(await page.locator('#checkout-form [name="name"]').inputValue(), account.user.name);
  assert.equal(await page.locator('#checkout-form [name="email"]').inputValue(), account.user.email);
  await contacts(page, 'Получатель заказа');
  await page.locator('#checkout-form [name="email"]').fill('order@example.test');
  await page.locator('.checkout-submit').click();
  await page.getByRole('heading', { name: 'Заказ на самовывоз принят', exact: true }).waitFor();
  assert.equal(calls[0].path, '/customer/orders');
  assert.equal(calls[0].body.phone, '+79991234567');
  assert.equal(calls[0].body.email, 'order@example.test');
  assert.equal(calls[0].body.name, 'Получатель заказа');
  assert.equal((await page.evaluate(() => fetch('/api/customer/session').then(response => response.json()))).user.phone, null);
});
test('unavailable unselected items do not block checkout, and empty selection cannot proceed', async t => {
  const { page } = await open(t, { missing: true });
  await page.getByRole('checkbox', { name: 'Выбрать все', exact: true }).uncheck();
  assert.equal(await page.locator('.checkout-continue').isDisabled(), true);
  await page.getByRole('checkbox', { name: 'Выбрать Деталь first', exact: true }).check();
  await continueCheckout(page);
  assert.equal(await page.locator('.checkout-items li').count(), 1);
  assert.equal(await page.locator('.checkout-submit').isEnabled(), true);
});
test('an unchecked product still being verified does not block a verified selection', async t => {
  const { page } = await open(t, { pendingProduct: true, waitUntil: 'domcontentloaded' });
  await page.getByRole('checkbox', { name: 'Выбрать Деталь second', exact: true }).uncheck();
  await page.waitForFunction(() => document.querySelector('.checkout-continue')?.disabled === false, null, { timeout: 3000 });
  await continueCheckout(page);
  await page.waitForFunction(() => document.querySelector('.checkout-submit')?.disabled === false, null, { timeout: 3000 });
  assert.equal(await page.locator('.checkout-items li').count(), 1);
});
test('a selected product error stays visible while an unchecked product is pending', async t => {
  const { page, rejectFirst } = await open(t, { pendingProduct: true, waitUntil: 'domcontentloaded' });
  await page.getByRole('checkbox', { name: 'Выбрать Деталь second', exact: true }).uncheck();
  await page.waitForFunction(() => document.querySelector('.checkout-continue')?.disabled === false);
  rejectFirst();
  await continueCheckout(page);
  await page.getByRole('alert').filter({ hasText: /Проверьте выбранные товары/ }).waitFor();
  assert.equal(await page.locator('.checkout-submit').isDisabled(), true);
});
test('direct and reloaded checkout require reviewed cart selection', async t => {
  const { page, calls } = await open(t, { hash: 'checkout' });
  assert.equal(await page.locator('#checkout-form').count(), 0);
  await page.getByRole('link', { name: 'Выбрать товары в корзине', exact: true }).click();
  await continueCheckout(page);
  await page.reload({ waitUntil: 'networkidle' });
  assert.equal(await page.locator('#checkout-form').count(), 0);
  await page.getByRole('link', { name: 'Выбрать товары в корзине', exact: true }).waitFor();
  assert.equal(calls.length, 0);
});
test('retry preserves selected body and key; a late receipt keeps changed and unselected cart rows', async t => {
  const { page, calls, release } = await open(t, { failOnce: true, deferOrder: true });
  await page.getByRole('checkbox', { name: 'Выбрать Деталь second', exact: true }).uncheck();
  await continueCheckout(page); await contacts(page);
  await page.locator('.checkout-submit').click();
  await page.getByRole('alert').filter({ hasText: 'Повторите отправку.' }).waitFor();
  await page.locator('.checkout-submit').click();
  await page.waitForFunction(() => document.querySelector('.checkout-submit')?.disabled);
  await page.evaluate(() => {
    localStorage.setItem('lavr.cart.v1', JSON.stringify([{ productId: 'first', quantity: 3 }, { productId: 'second', quantity: 2 }]));
    dispatchEvent(new StorageEvent('storage', { key: 'lavr.cart.v1' }));
  });
  release();
  await page.getByRole('heading', { name: 'Заказ на самовывоз принят', exact: true }).waitFor();
  assert.equal(calls[0].key, calls[1].key); assert.deepEqual(calls[0].body, calls[1].body);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))), [{ productId: 'first', quantity: 3 }, { productId: 'second', quantity: 2 }]);
});

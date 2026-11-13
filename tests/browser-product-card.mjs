import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
after(() => browser.close());
const baseUrl = process.env.BASE_URL || 'http://127.0.0.1:5173';
const previewDir = process.env.PREVIEW_DIR || '/private/tmp/lavr-product-card-previews';
const catalogHash = '#catalog?category=parts';
const productHash = '#catalog?category=parts&product=part';
const product = { id: 'part', externalId: 'external-part', name: 'Тестовый фильтр', sku: 'FILTER-1', brand: 'Test Brand', category: 'parts', priceKopecks: 123400, stock: 2, image: '', deliveryDays: 1 };
const unavailable = { ...product, id: 'unavailable', stock: 0 };
async function open(t, width = 1440) {
  const context = await browser.newContext({ viewport: { width, height: 1000 } });
  const page = await context.newPage(); page.setDefaultTimeout(5000);
  const errors = []; const unexpected = []; page.on('pageerror', issue => errors.push(issue.message));
  t.after(async () => { await context.close(); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); });
  await context.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname.slice(4);
    const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (route.request().method() !== 'GET') { unexpected.push(path); return json({}, 405); }
    if (path === '/site') return json({ hero: { title: 'Магазин', description: 'Запчасти', image: null, target: { type: 'catalog' }, buttonText: 'В каталог' }, settings: { storeName: 'Магазин', phone: '+79991234567', address: 'Москва', hours: '9–18', categoriesTitle: 'Категории', promotionsTitle: 'Предложения', catalogTitle: 'Каталог' }, menu: [], publications: [] });
    if (path === '/categories') return json([{ id: 'parts', name: 'Запчасти', position: 0, showInMenu: true }]);
    if (path === '/catalog') return json({ items: [product, unavailable], total: 2, offset: 0, limit: 24, catalogUpdatedAt: '2026-11-12T09:00:00Z', stale: false });
    if (path === '/catalog/part') return json({ product, catalogUpdatedAt: '2026-11-12T09:00:00Z', stale: false });
    if (path === '/customer/session') return json({ code: 'SESSION_REQUIRED', message: 'Войдите' }, 401);
    unexpected.push(path); return json({}, 404);
  });
  await page.goto(`${baseUrl}/${catalogHash}`, { waitUntil: 'networkidle' });
  const card = page.locator('.product-card').first(); await card.waitFor();
  return { page, context, card };
}
async function position(card, selector) {
  const outer = await card.boundingBox();
  if (!selector) return { x: outer.width / 2, y: outer.height - 5 };
  const inner = await card.locator(selector).boundingBox();
  return { x: inner.x - outer.x + (selector === '.product-brand' ? 6 : inner.width / 2), y: inner.y - outer.y + inner.height / 2 };
}
const hash = page => page.evaluate(() => location.hash);
const dialog = page => page.locator('dialog[open]').filter({ has: page.locator('#dialog-title') });

for (const width of [1440, 390, 320]) {
  test(`the whole tile opens through brand, SKU, price, image and blank space at ${width}px`, async t => {
    const { page, card } = await open(t, width); const results = [];
    for (const [area, selector] of [['brand', '.product-brand'], ['sku', '.product-brand span'], ['price', '.product-bottom > strong'], ['image', '.product-image'], ['space', null]]) {
      await card.click({ position: await position(card, selector) });
      const opened = await hash(page) === productHash; results.push({ area, opened });
      if (opened) { await dialog(page).waitFor(); await dialog(page).getByRole('button', { name: 'Закрыть', exact: true }).click(); await page.waitForURL(url => url.hash === catalogHash); }
    }
    t.diagnostic(JSON.stringify({ width, results }));
    assert.deepEqual(results.filter(result => !result.opened), []);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  });

  test(`add buttons stay isolated from the product link, including disabled buttons at ${width}px`, async t => {
    const { page, card } = await open(t, width); const add = card.locator('.add-button');
    await add.click(); assert.equal(await page.locator('.cart-count').innerText(), '1');
    await add.click(); assert.equal(await page.locator('.cart-count').innerText(), '1');
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))[0].quantity), 2);
    assert.equal(await add.isDisabled(), true);
    for (const button of [add, page.locator('.product-card').nth(1).locator('.add-button')]) {
      assert.equal(await button.isDisabled(), true); const bounds = await button.boundingBox();
      await page.mouse.click(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
    }
    assert.equal(await page.locator('.cart-count').innerText(), '1');
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))[0].quantity), 2);
    assert.equal(await hash(page), catalogHash); assert.equal(await dialog(page).count(), 0);
  });

  test(`one product tab stop keeps Enter, Back, Forward and focus restoration at ${width}px`, async t => {
    const { page, card } = await open(t, width); const link = card.locator('.product-name');
    assert.equal(await card.locator('a[href]').count(), 1); assert.equal(await link.getAttribute('href'), productHash);
    await link.focus(); await page.keyboard.press('Tab');
    assert.equal(await card.locator('.add-button').evaluate(element => element === document.activeElement), true);
    await page.keyboard.press('Shift+Tab');
    assert.equal(await link.evaluate(element => element === document.activeElement), true);
    assert.equal(await link.evaluate(element => getComputedStyle(element, '::after').outlineStyle), 'solid');
    await mkdir(previewDir, { recursive: true }); await card.screenshot({ path: `${previewDir}/product-card-${width}-keyboard.png` });
    await page.keyboard.press('Enter'); await dialog(page).waitFor(); assert.equal(await hash(page), productHash);
    await page.goBack(); await dialog(page).waitFor({ state: 'hidden' }); assert.equal(await hash(page), catalogHash);
    await page.goForward(); await dialog(page).waitFor(); assert.equal(await hash(page), productHash);
    await dialog(page).getByRole('button', { name: 'Закрыть', exact: true }).click();
    assert.equal(await link.evaluate(element => element === document.activeElement), true);
  });
}

test('modified and middle clicks on tile metadata open native new tabs without navigating the current page', async t => {
  const { page, context, card } = await open(t);
  for (const [selector, options] of [['.product-brand', { modifiers: ['ControlOrMeta'] }], ['.product-bottom > strong', { button: 'middle' }]]) {
    const [popup] = await Promise.all([context.waitForEvent('page'), card.click({ position: await position(card, selector), ...options })]);
    await popup.waitForLoadState('networkidle'); await dialog(popup).waitFor();
    assert.equal(await hash(popup), productHash); assert.equal(await hash(page), catalogHash); await popup.close();
  }
});

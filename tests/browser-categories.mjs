import { test, after } from 'node:test';
import assert from 'node:assert/strict';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
after(() => browser.close());
const baseUrl = process.env.BROWSER_BASE_URL || 'http://127.0.0.1:5199';
const category = index => ({ id: `category-${index}`, name: index === 11 ? 'Длинное название категории автозапчастей и оборудования' : `Категория ${index + 1}`, position: index, showInMenu: true, productCount: 12 });

async function open(t, width, count = 12) {
  const context = await browser.newContext({ viewport: { width, height: 1100 } });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const errors = [], unexpected = [], catalogQueries = [];
  page.on('pageerror', issue => errors.push(issue.message));
  t.after(async () => { await context.close(); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); });
  await context.route('**/api/**', route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname.slice(4);
    const json = (value, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
    if (request.method() === 'GET') {
      if (path === '/site') return json({ hero: { title: 'MIRZAP67', description: 'Магазин', image: null, target: { type: 'catalog' }, buttonText: 'В каталог' }, settings: { storeName: 'MIRZAP67', phone: '+79991234567', address: 'Тестовый адрес', hours: '9–18', categoriesTitle: 'Категории', promotionsTitle: 'Предложения', catalogTitle: 'Каталог' }, menu: [], publications: [] });
      if (path === '/categories') return json(Array.from({ length: count }, (_, index) => category(index)));
      if (path === '/catalog') { catalogQueries.push(url.searchParams.get('category')); return json({ items: [], total: 0, offset: 0, limit: 24, catalogUpdatedAt: '2026-11-11T10:00:00Z', stale: false }); }
      if (path === '/customer/session') return json({ code: 'SESSION_REQUIRED', message: 'Войдите снова' }, 401);
    }
    unexpected.push(`${request.method()} ${path}`);
    return json({ code: 'NOT_FOUND', message: 'Недоступно' }, 404);
  });
  await page.goto(baseUrl, { waitUntil: 'networkidle' });
  await page.locator('.landing-categories').waitFor();
  return { page, catalogQueries };
}

const cards = page => page.locator('.landing-categories .category-card');
const visibleCards = page => page.locator('.landing-categories .category-card:visible');
const expand = page => page.getByRole('button', { name: 'Показать все категории', exact: true });
async function assertOneRow(page, count) {
  assert.equal(await visibleCards(page).count(), count);
  const tops = await visibleCards(page).evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().top));
  assert.ok(tops.every(top => Math.abs(top - tops[0]) < 1), 'Collapsed categories share one row');
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'No horizontal page overflow');
}

for (const [width, count] of [[1440, 6], [900, 3], [390, 2], [320, 2]]) {
  test(`categories show one row, expand and collapse at ${width}px`, async t => {
    const { page } = await open(t, width);
    await assertOneRow(page, count);
    assert.equal(await cards(page).count(), 12);
    const button = expand(page);
    assert.equal(await button.getAttribute('aria-expanded'), 'false');
    assert.equal(await button.getAttribute('aria-controls'), 'category-grid');
    assert.equal(await page.locator('#category-grid').count(), 1);
    assert.equal(await cards(page).nth(count).evaluate(node => getComputedStyle(node).display), 'none');
    await visibleCards(page).last().focus();
    await page.keyboard.press('Tab');
    assert.equal(await button.evaluate(node => document.activeElement === node), true, 'Hidden links are skipped by keyboard');
    await page.keyboard.press('Enter');
    const collapse = page.getByRole('button', { name: 'Свернуть категории', exact: true });
    assert.equal(await collapse.getAttribute('aria-expanded'), 'true');
    assert.equal(await visibleCards(page).count(), 12);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await collapse.click();
    await assertOneRow(page, count);
    assert.equal(await expand(page).evaluate(node => document.activeElement === node), true, 'Collapse keeps keyboard focus on the control');
  });
}

test('collapsed categories follow resizing and expanded category links retain filtering', async t => {
  const { page, catalogQueries } = await open(t, 900);
  await assertOneRow(page, 3);
  for (const [width, count] of [[390, 2], [1440, 6]]) {
    await page.setViewportSize({ width, height: 1100 });
    await assertOneRow(page, count);
  }
  await expand(page).click();
  const last = cards(page).last();
  assert.equal(await last.getAttribute('href'), '#catalog?category=category-11');
  await last.click();
  await page.waitForURL('**/#catalog?category=category-11');
  await page.locator('input[name="category"][value="category-11"]').waitFor();
  assert.equal(await page.locator('input[name="category"][value="category-11"]').isChecked(), true);
  assert.ok(catalogQueries.includes('category-11'));
});

for (const [width, count] of [[1440, 6], [900, 3], [390, 2]]) {
  test(`categories need no expansion when ${count} cards fit at ${width}px`, async t => {
    const { page } = await open(t, width, count);
    await assertOneRow(page, count);
    assert.equal(await expand(page).isVisible(), false);
  });
}

test('empty categories retain the empty message without an expansion control', async t => {
  const { page } = await open(t, 390, 0);
  assert.equal(await cards(page).count(), 0);
  await page.getByText('Категории пока не добавлены. Найдите запчасть по артикулу в поиске.', { exact: true }).waitFor();
  assert.equal(await expand(page).isVisible(), false);
});

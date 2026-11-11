import { test, after } from 'node:test';
import assert from 'node:assert/strict';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
const baseUrl = process.env.BASE_URL || 'http://127.0.0.1:5173';
after(() => browser.close());
const updatedAt = '2026-11-11T09:00:00Z';
const category = { id: 'parts', name: 'Запчасти', position: 0, isVisible: true, showInMenu: true, version: 1 };
const products = Array.from({ length: 67 }, (_, index) => ({
  id: `part-${index + 1}`, externalId: `external-${index + 1}`, sku: `SKU-${index + 1}`, brand: 'Бренд',
  name: `${index < 23 ? 'Фильтр' : 'Деталь'} ${index + 1}`, category: 'parts', unit: 'шт', priceKopecks: 10000,
  stock: 10, physicalStock: 12, reservedStock: 2, isVisible: true, image: '', deliveryDays: 1,
  websiteTitle: null, websiteDescription: null, images: [],
}));
const session = {
  user: { id: 'pagination-admin', name: 'Администратор', email: null, phone: null, emailVerified: false, phoneVerified: false, profileComplete: false },
  capabilities: { operate: true, editSite: true, manageUsers: true },
  csrfToken: 'pagination-browser-csrf-token-0123456789', expiresAt: '2027-01-01T00:00:00Z',
};
const site = {
  hero: { title: 'Запчасти', description: '', image: null, buttonText: 'Каталог', target: { type: 'catalog' } },
  settings: { storeName: 'Проверка', phone: '+79991234567', address: 'Смоленск', hours: '09:00–18:00', categoriesTitle: 'Категории', promotionsTitle: 'Новости', catalogTitle: 'Каталог' },
  menu: [], publications: [],
};
const json = (route, data) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(data) });
function pageData(items, url) {
  const offset = Number(url.searchParams.get('offset') || 0);
  const limit = Number(url.searchParams.get('limit') || 24);
  return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
}
async function open(t, { viewport = { width: 1440, height: 1100 }, handle } = {}) {
  const context = await browser.newContext({ viewport });
  const page = await context.newPage();
  page.setDefaultTimeout(5000);
  const errors = [], requests = [], unexpected = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname.slice(4);
    if (path === '/admin/products') {
      requests.push(Object.fromEntries(url.searchParams));
      if (handle && await handle(route, url)) return;
      const query = (url.searchParams.get('q') || '').toLowerCase();
      return json(route, pageData(products.filter(item => `${item.name} ${item.sku}`.toLowerCase().includes(query)), url));
    }
    if (path === '/site') return json(route, site);
    if (path === '/customer/session') return json(route, session);
    if (path === '/categories') return json(route, [category]);
    if (path === '/admin/categories') return json(route, pageData([category], url));
    if (path === '/catalog') return json(route, { ...pageData(products, url), catalogUpdatedAt: updatedAt, stale: false });
    unexpected.push(`${route.request().method()} ${path}`);
    return route.fulfill({ status: 500, body: 'Unexpected API request' });
  });
  t.after(async () => { await context.close(); assert.deepEqual(errors, []); assert.deepEqual(unexpected, []); });
  await page.goto(`${baseUrl}/#crm-catalog`, { waitUntil: 'domcontentloaded' });
  await page.locator('.catalog-admin-table tbody tr').first().waitFor();
  return { page, requests };
}
async function expectPage(page, count, counter) {
  await page.waitForFunction(({ count, counter }) => document.querySelectorAll('.catalog-admin-table tbody tr').length === count
    && (!counter || document.querySelector('.catalog-pagination span')?.textContent === counter), { count, counter });
  assert.equal(await page.locator('.catalog-admin-table tbody tr').count(), count);
}

test('nomenclature starts with ten records and selectable page sizes', async t => {
  const { page, requests } = await open(t);
  assert.equal(requests[0].limit, '10', 'The first server page must contain ten records');
  await expectPage(page, 10, '1–10 из 67');
  const size = page.getByRole('combobox', { name: 'Записей на странице', exact: true });
  assert.equal(await size.inputValue(), '10');
  assert.deepEqual(await size.locator('option').allTextContents(), ['10', '20', '50']);
  const navigation = page.getByRole('navigation', { name: 'Страницы номенклатуры' });
  assert.equal(await navigation.getByRole('button', { name: 'Назад', exact: true }).isDisabled(), true);
  await navigation.getByRole('button', { name: 'Далее', exact: true }).click();
  await expectPage(page, 10, '11–20 из 67');
  assert.equal(await page.locator('.catalog-admin-table tbody tr').first().getAttribute('data-testid'), 'catalog-product-part-11');
  await navigation.getByRole('button', { name: 'Назад', exact: true }).click();
  await expectPage(page, 10, '1–10 из 67');
});

test('changing page size resets the page, preserves search and handles the last partial page', async t => {
  const { page, requests } = await open(t);
  const size = page.getByRole('combobox', { name: 'Записей на странице', exact: true });
  const next = page.locator('.catalog-pagination').getByRole('button', { name: 'Далее', exact: true });
  await next.click();
  await expectPage(page, 10, '11–20 из 67');
  await size.selectOption('20');
  await expectPage(page, 20, '1–20 из 67');
  assert.deepEqual(requests.at(-1), { offset: '0', limit: '20' });
  await next.click();
  await expectPage(page, 20, '21–40 из 67');
  await size.selectOption('50');
  await expectPage(page, 50, '1–50 из 67');
  await next.click();
  await expectPage(page, 17, '51–67 из 67');
  assert.equal(await next.isDisabled(), true);
  await size.selectOption('20');
  await expectPage(page, 20, '1–20 из 67');
  await next.click();
  await expectPage(page, 20, '21–40 из 67');
  await page.getByRole('searchbox', { name: 'Поиск номенклатуры' }).fill('Фильтр');
  await expectPage(page, 20, '1–20 из 23');
  assert.deepEqual(requests.at(-1), { q: 'Фильтр', offset: '0', limit: '20' });
  await next.click();
  await expectPage(page, 3, '21–23 из 23');
  assert.equal(await next.isDisabled(), true);
  await size.selectOption('50');
  await expectPage(page, 23);
  assert.equal(await page.locator('.catalog-pagination').count(), 0);
  assert.equal(await size.inputValue(), '50');
  assert.deepEqual(requests.at(-1), { q: 'Фильтр', offset: '0', limit: '50' });
  await page.getByRole('searchbox', { name: 'Поиск номенклатуры' }).fill('Нет такой детали');
  await page.getByText('Номенклатура не найдена', { exact: true }).waitFor();
  assert.equal(await size.isVisible(), true, 'Page size remains available for an empty search');
  await size.selectOption('10');
  await page.getByRole('searchbox', { name: 'Поиск номенклатуры' }).fill('');
  await expectPage(page, 10, '1–10 из 67');
});

test('late page size and search responses cannot replace the latest chosen page', async t => {
  let releaseSize, releaseSearch;
  const sizeGate = new Promise(resolve => { releaseSize = resolve; });
  const searchGate = new Promise(resolve => { releaseSearch = resolve; });
  t.after(() => { releaseSize(); releaseSearch(); });
  const { page } = await open(t, { handle: async (route, url) => {
    if (url.searchParams.get('limit') === '20' && !url.searchParams.has('q')) {
      await sizeGate; await json(route, pageData(products, url)); return true;
    }
    if (url.searchParams.get('q') === 'Фильтр') {
      await searchGate; await json(route, pageData(products.slice(0, 23), url)); return true;
    }
    return false;
  } });
  const size = page.getByRole('combobox', { name: 'Записей на странице', exact: true });
  const oldSizeSent = page.waitForRequest(request => new URL(request.url()).searchParams.get('limit') === '20');
  await size.selectOption('20'); await oldSizeSent;
  await size.selectOption('50'); await expectPage(page, 50, '1–50 из 67');
  const oldSizeResponse = page.waitForResponse(response => new URL(response.url()).searchParams.get('limit') === '20');
  releaseSize(); await (await oldSizeResponse).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  assert.equal(await size.inputValue(), '50'); await expectPage(page, 50, '1–50 из 67');
  const search = page.getByRole('searchbox', { name: 'Поиск номенклатуры' });
  const oldSearchSent = page.waitForRequest(request => new URL(request.url()).searchParams.get('q') === 'Фильтр');
  await search.fill('Фильтр'); await oldSearchSent;
  await search.fill('Деталь'); await expectPage(page, 44);
  const oldSearchResponse = page.waitForResponse(response => new URL(response.url()).searchParams.get('q') === 'Фильтр');
  releaseSearch(); await (await oldSearchResponse).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expectPage(page, 44);
  assert.equal(await page.locator('.catalog-admin-table tbody tr').first().getAttribute('data-testid'), 'catalog-product-part-24');
  assert.equal(await size.inputValue(), '50');
});

test('page size and navigation fit narrow mobile screens with usable controls', async t => {
  for (const width of [390, 320]) {
    const { page } = await open(t, { viewport: { width, height: 844 } });
    const size = page.getByRole('combobox', { name: 'Записей на странице', exact: true });
    await size.selectOption('20'); await expectPage(page, 20, '1–20 из 67');
    const control = await size.evaluate(element => ({ height: element.getBoundingClientRect().height, fontSize: parseFloat(getComputedStyle(element).fontSize) }));
    assert.ok(control.height >= 44 && control.fontSize >= 16, JSON.stringify(control));
    const next = page.locator('.catalog-pagination').getByRole('button', { name: 'Далее', exact: true });
    await next.click(); await expectPage(page, 20, '21–40 из 67');
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `Page must fit ${width}px`);
  }
});

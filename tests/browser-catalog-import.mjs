import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, mkdir } from 'node:fs/promises';

const base = process.env.BASE_URL || 'http://127.0.0.1:5191';
const environment = process.env.CATALOG_IMPORT_TEST_ENV;
const pdf = process.env.CATALOG_IMPORT_PDF;
const output = process.env.CATALOG_IMPORT_OUTPUT || '/private/tmp/catalog-import-browser';

test('local real API: PDF upload, preview, units/category, atomic apply, repeated import, mobile and role boundaries', { skip: !environment || !pdf, timeout: 240000 }, async () => {
  assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'This test writes only to a loopback test site');
  const env = JSON.parse(await readFile(environment, 'utf8'));
  assert.match(env.DATABASE_URL, /_test(?:\?|$)/);
  const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  await mkdir(output, { recursive: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const failures = [];
  const page = await context.newPage();
  page.on('pageerror', error => failures.push(error.message));
  let packageId;
  page.on('response', async response => {
    if (response.url().includes('/api/admin/catalog/imports/') && response.request().method() === 'GET' && response.ok()) {
      const data = await response.json().catch(() => null);
      if (data?.rows) packageId = data.id;
    }
  });
  async function login(ctx, email) {
    const response = await ctx.request.post(`${base}/api/customer/session`, { data: { email, password: env.ADMIN_PASSWORD }, headers: { Origin: base } });
    assert.equal(response.status(), 200);
    return response.json();
  }
  async function upload() {
    await page.getByTestId('catalog-import-file').setInputFiles(pdf);
    await page.getByTestId('catalog-import-price-confirmed').check();
    await page.getByTestId('catalog-import-upload').click();
    await page.getByTestId('catalog-import-table').waitFor({ timeout: 90000 });
    assert.equal(await page.locator('.import-error').count(), 0);
  }
  async function saveDecision(button) {
    const [request, response] = await Promise.all([
      page.waitForRequest(item => item.method() === 'PATCH' && item.url().endsWith('/decisions')),
      page.waitForResponse(item => item.request().method() === 'PATCH' && item.url().endsWith('/decisions')),
      button.click(),
    ]);
    assert.equal(response.status(), 200);
    await page.waitForFunction(() => {
      const fieldset = document.querySelector('.import-bulk');
      return fieldset && !fieldset.disabled;
    });
    assert.equal(await page.locator('.import-error').count(), 0);
    return request.postDataJSON();
  }
  try {
    await login(context, env.ADMIN_LOGIN);
    await page.goto(`${base}/#crm-catalog`);
    await page.getByTestId('catalog-import-open').click();
    await page.getByTestId('catalog-import-file').waitFor();
    assert.match(await page.locator('.import-sources').innerText(), /Адаптер.*не подключён/);
    await upload();
    assert.ok(packageId);
    let details = await (await context.request.get(`${base}/api/admin/catalog/imports/${packageId}?limit=50`)).json();
    assert.ok(details.rowCount > 50);
    assert.equal(details.rows.items.length, 50);
    const selectedCount = details.summary.selected;
    assert.ok(selectedCount > 0);
    await page.screenshot({ path: `${output}/preview-desktop.png` });
    const scope = page.locator('.import-bulk select').first();
    assert.match(await scope.innerText(), /Отмеченные на текущей странице — 0 строк/);
    const marks = page.locator('input[aria-label^="Отметить для назначения"]');
    await marks.nth(0).check(); await marks.nth(2).check();
    assert.match(await scope.innerText(), /Отмеченные на текущей странице — 2 строк/);
    await page.getByTestId('catalog-import-bulk-unit').selectOption('шт');
    const markedAssignment = await saveDecision(page.getByTestId('catalog-import-assign-unit'));
    assert.deepEqual(markedAssignment, { rowIds: [details.rows.items[0].id, details.rows.items[2].id], unit: 'шт' });
    await scope.selectOption('filter');
    await page.locator('.import-bulk input[type="checkbox"]').check();
    await page.getByTestId('catalog-import-bulk-unit').selectOption('шт');
    await saveDecision(page.getByTestId('catalog-import-assign-unit'));
    await page.locator('.import-bulk input[type="checkbox"]').check();
    await page.locator('.import-bulk select').last().selectOption('filters');
    const categoryDecision = await saveDecision(page.getByRole('button', { name: 'Назначить категорию', exact: true }));
    assert.equal(categoryDecision.categoryId, 'filters');
    details = await (await context.request.get(`${base}/api/admin/catalog/imports/${packageId}?limit=50`)).json();
    assert.ok(details.rows.items.every(row => row.unit === 'шт'));
    assert.ok(details.rows.items.every(row => row.categoryId === 'filters'));
    if (details.summary.skipped) await page.getByTestId('catalog-import-ack-skips').check();
    await page.getByTestId('catalog-import-apply').click();
    await page.getByTestId('catalog-import-report').waitFor({ timeout: 60000 });
    assert.equal(await page.locator('.import-error').count(), 0);
    details = await (await context.request.get(`${base}/api/admin/catalog/imports/${packageId}?limit=50`)).json();
    assert.equal(details.status, 'applied');
    assert.equal(details.report.createdCount, selectedCount);
    const reportText = await page.getByTestId('catalog-import-report').innerText();
    assert.ok(reportText.includes(details.report.authorId));
    assert.ok(reportText.includes(details.report.fileHash));
    assert.ok(reportText.includes(details.report.sourceNamespace));
    const products = await (await context.request.get(`${base}/api/admin/products?limit=100&offset=0`)).json();
    const ids = products.items.map(item => item.id).sort();
    await page.getByRole('button', { name: 'Другой файл', exact: true }).click();
    await upload();
    details = await (await context.request.get(`${base}/api/admin/catalog/imports/${packageId}?limit=50`)).json();
    assert.equal(details.summary.new, 0);
    assert.equal(details.summary.changed, 0);
    assert.equal(details.summary.unchanged, selectedCount);
    if (details.summary.skipped) await page.getByTestId('catalog-import-ack-skips').check();
    await page.getByTestId('catalog-import-apply').click();
    await page.getByTestId('catalog-import-report').waitFor({ timeout: 60000 });
    const after = await (await context.request.get(`${base}/api/admin/products?limit=100&offset=0`)).json();
    assert.deepEqual(after.items.map(item => item.id).sort(), ids);
    assert.equal(after.total, products.total);
    await page.setViewportSize({ width: 320, height: 760 });
    await page.screenshot({ path: `${output}/report-mobile.png` });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    assert.ok(await page.locator('.import-table-wrap').evaluate(el => el.scrollWidth > el.clientWidth));
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('dialog[open]').count(), 0);
    assert.equal(await page.getByTestId('catalog-import-open').evaluate(el => document.activeElement === el), true);
    await page.goto(`${base}/#catalog?product=${encodeURIComponent(ids[0])}`);
    await page.getByText('Код каталога', { exact: true }).waitFor();
    assert.equal(await page.getByText('ID из 1С', { exact: true }).count(), 0);
    assert.match(await page.locator('dialog[open]').innerText(), /pdf:/);
    for (const role of ['operator', 'partner']) {
      const other = await browser.newContext();
      await login(other, `${role}@example.test`);
      const response = await other.request.get(`${base}/api/admin/catalog/imports/capabilities`);
      assert.equal(response.status(), 403);
      await other.close();
    }
    assert.deepEqual(failures, []);
    console.log(JSON.stringify({ flow: 'real-local-pdf-import', rows: details.rowCount, created: selectedCount, repeatedImportPreservedIds: true, viewport: [1440, 320], operatorPartnerForbidden: true }));
  } finally { await context.close(); await browser.close(); }
});

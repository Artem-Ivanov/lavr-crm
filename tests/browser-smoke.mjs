import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { initialCatalog, createCategory, createProduct } from '../src/catalog-admin.js';

// Optional QA tool kept separate from the Vue application dependencies.
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
after(() => browser.close());

async function open(t, width = 1440) {
  const context = await browser.newContext(typeof width === 'number' ? { viewport: { width, height: 1100 } } : width);
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => { await context.close(); assert.deepEqual(errors, []); });
  await page.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' });
  return page;
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

async function pageFits(page, view) {
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${view} must not scroll horizontally`);
}

test('admin content editor saves the hero, headings and menu without changing the landing template', async t => {
  const page = await open(t);
  await page.locator('.crm-nav').click();
  await page.getByRole('link', { name: 'Контент сайта', exact: true }).click();
  await page.getByTestId('hero-title').fill('Запчасти для вашей машины');
  await page.getByTestId('hero-description').fill('<img src=x onerror=alert(1)> Описание как текст');
  await page.getByTestId('site-storeName').fill('LAVR ТЕСТ');
  await page.getByTestId('site-categoriesTitle').fill('Категории из редактора');
  await page.getByTestId('menu-label-0').fill('Наши фильтры');
  await page.getByTestId('save-site').click();
  await page.getByTestId('site-success').waitFor();
  await page.locator('.header-main .logo').click();
  await page.locator('.hero h1').waitFor();
  assert.equal(await page.locator('.hero h1').textContent(), 'Запчасти для вашей машины');
  assert.match(await page.locator('.hero p').textContent(), /<img src=x onerror=alert\(1\)>/);
  assert.equal(await page.locator('.hero p img').count(), 0);
  assert.equal(await page.locator('#categories-title').textContent(), 'Категории из редактора');
  assert.equal(await page.locator('.navigation .desktop-menu-item').first().textContent(), 'Наши фильтры');
  await page.reload();
  await page.locator('.hero h1').waitFor();
  assert.equal(await page.locator('.hero h1').textContent(), 'Запчасти для вашей машины');
  await page.goto('http://127.0.0.1:5173/#crm-content');
  await page.getByTestId('hero-title').waitFor();
  assert.equal(await page.getByTestId('hero-title').inputValue(), 'Запчасти для вашей машины');
});

test('content menu section targets work from the catalog and announcement images are displayed', async t => {
  const page = await open(t);
  await page.goto('http://127.0.0.1:5173/#crm-content');
  await page.getByTestId('menu-label-0').fill('Объявления магазина');
  await page.getByTestId('menu-0-target-type').selectOption('section');
  await page.getByTestId('menu-0-target-section').selectOption('announcements');
  await page.getByTestId('save-site').click();
  await page.getByTestId('site-success').waitFor();
  await page.getByTestId('content-tab-publications').click();
  await page.getByTestId('new-publication').click();
  await page.getByTestId('publication-kind').selectOption('announcement');
  await page.getByTestId('publication-status').selectOption('published');
  await page.getByTestId('publication-title').fill('Объявление с фотографиями');
  for (const id of ['demo-brake-disc', 'demo-air-filter', 'demo-spark-plug']) await page.getByTestId(`publication-media-${id}`).click();
  await page.getByTestId('save-publication').click();
  await page.getByTestId('publication-success').waitFor();
  await page.locator('.header-main .logo').click();
  const card = page.locator('.promotion-card').filter({ hasText: 'Объявление с фотографиями' });
  await card.waitFor();
  assert.equal(await card.locator('img').count(), 3);
  await page.getByRole('link', { name: 'Весь каталог', exact: true }).click();
  await page.locator('#catalog-title').waitFor();
  await page.getByRole('button', { name: 'Объявления магазина', exact: true }).click();
  await page.waitForURL('**/#home');
  await page.waitForFunction(() => {
    const top = document.querySelector('.promotion-card[data-kind="announcement"]')?.getBoundingClientRect().top;
    return top !== undefined && top >= -1 && top < innerHeight;
  });
});

test('content forms fit touch screens and maximum valid texts remain readable on the landing', async t => {
  const page = await open(t, { viewport: { width: 1440, height: 1100 }, hasTouch: true });
  await page.goto('http://127.0.0.1:5173/#crm-content');
  await page.getByTestId('hero-title').waitFor();
  await mkdir('docs/previews', { recursive: true });
  await page.screenshot({ path: 'docs/previews/content-admin-desktop.png' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator('.content-site-fields').scrollIntoViewIfNeeded();
  await page.screenshot({ path: 'docs/previews/content-admin-mobile.png' });
  await page.getByTestId('hero-title').fill('З'.repeat(240));
  await page.getByTestId('hero-description').fill('О'.repeat(2000));
  await page.getByTestId('hero-button-text').fill('К'.repeat(80));
  await page.getByTestId('site-storeName').fill('М'.repeat(120));
  await page.getByTestId('menu-label-0').fill('П'.repeat(80));
  await page.getByTestId('save-site').click();
  await page.getByTestId('site-success').waitFor();
  await page.getByTestId('content-tab-publications').click();
  await page.getByTestId('new-publication').click();
  await page.getByTestId('publication-title').fill('А'.repeat(240));
  await page.getByTestId('publication-description').fill('Д'.repeat(4000));
  await page.getByTestId('publication-button-text').fill('Б'.repeat(80));
  await page.getByTestId('publication-status').selectOption('published');
  for (const id of ['demo-hero', 'demo-motor-oil', 'demo-oil-filter', 'demo-air-filter', 'demo-brake-disc', 'demo-spark-plug']) await page.getByTestId(`publication-media-${id}`).click();
  await page.getByTestId('save-publication').click();
  await page.getByTestId('publication-success').waitFor();
  for (const viewport of mobileProfiles) {
    await page.setViewportSize(viewport);
    for (const tab of ['site', 'publications', 'media']) {
      await page.getByTestId(`content-tab-${tab}`).click();
      await pageFits(page, `${tab} content form ${viewport.width}`);
      await mobileControls(page, ['.content-manager button:visible'], ['.content-manager input:not([type="checkbox"]):visible', ...(tab === 'media' ? [] : ['.content-manager select:visible', '.content-manager textarea:visible'])]);
    }
  }
  await page.locator('.header-main .logo').click();
  await page.locator('.hero').waitFor();
  assert.equal(await page.locator('.promotion-card').filter({ hasText: 'А'.repeat(240) }).locator('img').count(), 6);
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 844 });
    await pageFits(page, `edited landing ${width}`);
    const overflow = await page.locator('.hero h1, .hero p, .hero-button, .promotion-copy h3, .promotion-copy p, .promotion-link, .promotion-art img').evaluateAll(elements => elements.filter(element => element.scrollWidth > element.clientWidth + 1 || element.getBoundingClientRect().right > element.parentElement.getBoundingClientRect().right + 1).map(element => element.className || element.tagName));
    assert.deepEqual(overflow, [], `edited landing text ${width}`);
  }
});

test('content publication lifecycle, dates and uploaded image bytes survive a mobile reload', async t => {
  const page = await open(t, { viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  await page.goto('http://127.0.0.1:5173/#crm-content');
  await page.getByTestId('hero-title').waitFor();
  await page.getByTestId('content-tab-media').tap();
  await page.getByTestId('media-file').setInputFiles({ name: 'fake.png', mimeType: 'image/png', buffer: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"></svg>') });
  await page.getByTestId('media-alt').fill('Новая фотография');
  await page.getByTestId('upload-media').tap();
  await page.getByRole('alert').filter({ hasText: /Содержимое файла/ }).waitFor();
  assert.equal(await page.getByTestId('media-alt').inputValue(), 'Новая фотография');
  await page.getByTestId('media-file').setInputFiles('public/images/brake-disc.jpg');
  await page.getByTestId('upload-media').tap();
  await page.getByTestId('upload-success').waitFor();
  const uploaded = page.locator('.media-library-item').filter({ hasText: 'Новая фотография' });
  await uploaded.waitFor();
  const mediaId = (await uploaded.getAttribute('data-testid')).slice('media-'.length);
  const imagePath = await uploaded.locator('img').getAttribute('src');
  assert.match(imagePath, /^blob:/);
  await page.getByTestId('content-tab-publications').tap();
  await page.getByTestId('new-publication').tap();
  await page.getByTestId('publication-kind').selectOption('hot');
  await page.getByTestId('publication-title').fill('Горячее предложение из редактора');
  await page.getByTestId('publication-description').fill('Проверенная картинка и текст');
  await page.getByTestId(`publication-media-${mediaId}`).tap();
  await page.getByTestId('save-publication').tap();
  await page.getByTestId('publication-success').waitFor();
  const entry = page.locator('.publication-list-item').filter({ hasText: 'Горячее предложение из редактора' });
  await entry.waitFor();
  const publicationId = (await entry.getAttribute('data-testid')).slice('edit-publication-'.length);
  await page.locator('.header-main .logo').tap();
  assert.equal(await page.locator('.promotion-copy h3').filter({ hasText: 'Горячее предложение из редактора' }).count(), 0, 'A draft stays private');
  await page.locator('.crm-nav').tap();
  await page.getByRole('link', { name: 'Контент сайта', exact: true }).tap();
  await page.getByTestId('content-tab-publications').tap();
  await page.getByTestId(`edit-publication-${publicationId}`).tap();
  await page.getByTestId('publication-status').selectOption('published');
  await page.getByTestId('publication-starts-at').fill('2100-01-01T00:00');
  await page.getByTestId('publication-ends-at').fill('2000-01-01T00:00');
  await page.getByTestId('save-publication').tap();
  await page.getByRole('alert').filter({ hasText: /позже начала/ }).waitFor();
  await page.getByTestId('publication-starts-at').fill('');
  await page.getByTestId('publication-ends-at').fill('');
  await page.getByTestId('save-publication').tap();
  await page.getByTestId('publication-success').waitFor();
  await page.locator('.header-main .logo').tap();
  const card = page.locator('.promotion-card').filter({ hasText: 'Горячее предложение из редактора' });
  await card.waitFor();
  assert.equal(await card.locator('img').evaluate(image => image.complete && image.naturalWidth === 870), true);
  await page.reload();
  await card.waitFor();
  await card.scrollIntoViewIfNeeded();
  await page.waitForFunction(() => [...document.querySelectorAll('.promotion-card img')].some(image => image.naturalWidth === 870));
  await pageFits(page, 'Published upload at 390');
  await page.locator('.crm-nav').tap();
  await page.getByRole('link', { name: 'Контент сайта', exact: true }).tap();
  await page.getByTestId('content-tab-publications').tap();
  await page.getByTestId(`edit-publication-${publicationId}`).tap();
  await page.getByTestId('publication-status').selectOption('archived');
  await page.getByTestId('save-publication').tap();
  await page.getByTestId('publication-success').waitFor();
  await page.locator('.header-main .logo').tap();
  await page.locator('.hero').waitFor();
  assert.equal(await page.locator('.promotion-copy h3').filter({ hasText: 'Горячее предложение из редактора' }).count(), 0);
});

test('content drafts survive failed writes, sibling conflicts and rejected navigation', async t => {
  const page = await open(t, { viewport: { width: 320, height: 740 }, isMobile: true, hasTouch: true });
  await page.goto('http://127.0.0.1:5173/#crm-content');
  await page.getByTestId('hero-title').fill('Мой несохранённый заголовок');
  const rejectLeave = dialog => dialog.dismiss();
  page.on('dialog', rejectLeave);
  await page.locator('.header-main .logo').tap();
  await page.waitForFunction(() => location.hash === '#crm-content');
  assert.equal(await page.getByTestId('hero-title').inputValue(), 'Мой несохранённый заголовок');
  page.off('dialog', rejectLeave);
  await page.evaluate(() => {
    window.originalSiteSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key === 'lavr.site.v1') throw new DOMException('Quota exceeded', 'QuotaExceededError');
      return window.originalSiteSetItem.call(this, key, value);
    };
  });
  await page.getByTestId('save-site').tap();
  await page.getByRole('alert').filter({ hasText: /Не удалось сохранить содержимое/ }).waitFor();
  assert.equal(await page.getByTestId('hero-title').inputValue(), 'Мой несохранённый заголовок');
  assert.equal(await page.evaluate(() => localStorage.getItem('lavr.site.v1')), null);
  await page.evaluate(() => { Storage.prototype.setItem = window.originalSiteSetItem; });
  const sibling = await page.context().newPage();
  await sibling.goto('http://127.0.0.1:5173/#crm-content');
  await sibling.getByTestId('hero-title').fill('Заголовок из другой вкладки');
  await sibling.getByTestId('save-site').click();
  await sibling.getByTestId('site-success').waitFor();
  await page.getByTestId('save-site').tap();
  await page.getByRole('alert').filter({ hasText: /уже изменён/ }).waitFor();
  assert.equal(await page.getByTestId('hero-title').inputValue(), 'Мой несохранённый заголовок');
  page.once('dialog', dialog => dialog.accept());
  await page.getByTestId('reload-site').tap();
  await page.waitForFunction(() => document.querySelector('[data-testid="hero-title"]').value === 'Заголовок из другой вкладки');
  while (await page.locator('[data-testid^="remove-menu-item-"]').count()) await page.locator('[data-testid^="remove-menu-item-"]').first().tap();
  await page.getByTestId('save-site').tap();
  await page.getByTestId('site-success').waitFor();
  await page.locator('.header-main .logo').tap();
  await page.locator('.hero').waitFor();
  assert.equal(await page.locator('.mobile-menu').count(), 0, 'An intentionally empty menu must stay empty');
  await pageFits(page, 'Content form at 320');
  const damaged = '{"site":';
  await sibling.close();
  await page.evaluate(value => localStorage.setItem('lavr.site.v1', value), damaged);
  await page.goto('http://127.0.0.1:5173/#crm-content');
  await page.getByTestId('content-read-only').waitFor();
  assert.equal(await page.getByTestId('save-site').isDisabled(), true);
  await page.getByTestId('content-tab-publications').tap();
  assert.equal(await page.getByTestId('new-publication').isDisabled(), true);
  await page.getByTestId('content-tab-media').tap();
  assert.equal(await page.getByTestId('media-file').isDisabled(), true);
  assert.equal(await page.evaluate(() => localStorage.getItem('lavr.site.v1')), damaged);
});

test('mobile menu remains reachable and returns focus; dialog close stays visible after scrolling', async t => {
  for (const viewport of mobileProfiles) {
    const page = await open(t, { viewport, isMobile: true, hasTouch: true });
    const menu = page.locator('.mobile-menu');
    const summary = menu.locator('summary');
    await summary.waitFor();
    assert.equal(await summary.textContent(), 'Меню');
    assert.equal(await page.locator('.desktop-menu-item:visible').count(), 0);
    await mobileControls(page, ['.mobile-menu summary']);
    await summary.tap();
    const items = menu.locator('.mobile-menu-items button');
    assert.equal(await items.count(), 3);
    await mobileControls(page, ['.mobile-menu-items button']);
    await items.last().tap();
    assert.equal(await menu.evaluate(element => element.open), false);
    assert.equal(await summary.evaluate(element => document.activeElement === element), true);
    await summary.tap();
    await items.first().focus();
    await page.keyboard.press('Escape');
    assert.equal(await menu.evaluate(element => element.open), false);
    assert.equal(await summary.evaluate(element => document.activeElement === element), true);
    await page.locator('.product-name').first().tap();
    const dialog = page.locator('dialog[open]');
    await dialog.waitFor();
    assert.ok(await dialog.evaluate(element => element.scrollHeight > element.clientHeight), 'Mobile product dialog must exercise its scroll area');
    await dialog.evaluate(element => { element.scrollTop = element.scrollHeight; });
    const close = page.locator('dialog[open] .dialog-close');
    await mobileControls(page, ['dialog[open] .dialog-close']);
    assert.equal(await close.evaluate(element => {
      const rect = element.getBoundingClientRect();
      return rect.top >= 0 && rect.bottom <= innerHeight && document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2)?.closest('.dialog-close') === element;
    }), true, `Dialog close must stay reachable at ${viewport.width}×${viewport.height}`);
    await close.tap();
    assert.equal(await page.locator('dialog[open]').count(), 0);
    await pageFits(page, `menu/dialog ${viewport.width}`);
  }
});

test('true mobile SKU search, basket quantities, checkout and CRM use readable touch controls', async t => {
  for (const viewport of mobileProfiles) {
    const page = await open(t, { viewport, isMobile: true, hasTouch: true });
    assert.equal(await page.evaluate(() => matchMedia('(pointer: coarse)').matches), true);
    await mobileControls(page, ['#search-form button', '.header-cart', '.catalog-nav', '.crm-nav', '.filter-row', '.stock-check', '#brand', '#sort', '.add-button'], ['#search', '#brand', '#sort']);
    await page.locator('#search').fill('w 914 / 2');
    await page.locator('#search-form button').tap();
    assert.equal(await page.locator('.product-card').count(), 1);
    await page.locator('[data-action="add"][data-id="filter-mann-oil"]').tap();
    await page.locator('.header-cart').tap();
    await mobileControls(page, ['.quantity-button', '.remove-button', '.delivery-option', '.checkout-submit'], ['[name="name"]', '[name="phone"]', '[name="email"]']);
    await pageFits(page, `basket ${viewport.width}`);
    assert.equal(await page.locator('.cart-item').evaluate(element => element.scrollWidth <= element.clientWidth + 1), true, 'The complete basket row must fit even at 320px');
    await page.locator('.quantity-button[data-quantity="2"]').tap();
    assert.equal(await page.locator('.quantity-control span').textContent(), '2');
    await page.locator('[name="name"]').fill('Мобильный демопокупатель');
    await page.locator('[name="phone"]').fill('+79991234567');
    await page.locator('.delivery-option').last().tap();
    await page.locator('[name="address"]').fill('Тестовая улица, 1');
    await mobileControls(page, [], ['[name="address"]']);
    await page.locator('.checkout-submit').tap();
    await page.locator('.receipt').waitFor();
    assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('lavr.orders.v1'))[0].totalKopecks), 178000);
    await pageFits(page, `receipt ${viewport.width}`);
    await page.locator('.receipt-actions a').tap();
    await mobileControls(page, ['#order-search-form button', '.status-filter', '.order-number', '.table-action'], ['[name="orderQuery"]']);
    await page.locator('[name="orderQuery"]').fill('Мобильный');
    await page.locator('#order-search-form button').tap();
    await page.locator('.status-filter[data-status="new"]').tap();
    await page.locator('.order-number').tap();
    await page.locator('[data-action="status"][data-status="confirmed"]').tap();
    await page.locator('[data-action="status"][data-status="ready"]').waitFor();
    assert.match(await page.locator('.order-detail-meta').textContent(), /Подтверждён/);
    await page.locator('dialog[open] .dialog-close').tap();
    await pageFits(page, `CRM ${viewport.width}`);
  }
});

test('mobile catalog administration creates and assigns categories through a keyboard-scrollable table', async t => {
  for (const viewport of mobileProfiles) {
    const page = await open(t, { viewport, isMobile: true, hasTouch: true });
    await page.locator('.crm-nav').tap();
    await page.locator('.crm-tabs a[href="#crm-catalog"]').tap();
    await page.locator('#category-form').waitFor();
    await mobileControls(page, ['#category-form button[type="submit"]', '#product-form button[type="submit"]', '.catalog-admin-table select'], ['.catalog-form input', '.catalog-form select', '#catalog-admin-search', '.catalog-admin-table select']);
    await page.locator('#category-name').fill('Мобильная категория');
    await page.locator('[data-testid="create-category"]').tap();
    const category = await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.catalog.v1')).categories.find(item => item.name === 'Мобильная категория'));
    const region = page.getByRole('region', { name: 'Номенклатура: прокрутка таблицы', exact: true });
    assert.equal(await region.getAttribute('tabindex'), '0');
    await page.locator('.table-scroll-hint').waitFor();
    assert.ok(await region.evaluate(element => element.scrollWidth > element.clientWidth), 'Overflow belongs to the table region');
    await region.focus();
    await page.keyboard.press('ArrowRight');
    await page.waitForFunction(() => document.querySelector('.catalog-table-wrap').scrollLeft > 0);
    const assignment = page.locator('[data-testid="product-category-filter-mann-oil"]');
    await assignment.scrollIntoViewIfNeeded();
    await assignment.selectOption(category.id);
    assert.equal(await assignment.inputValue(), category.id);
    assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.catalog.v1')).products.find(item => item.id === 'filter-mann-oil').category), category.id);
    await pageFits(page, `admin ${viewport.width}`);
    await page.locator('.catalog-nav').tap();
    await page.locator(`[name="category"][value="${category.id}"]`).check();
    assert.equal(await page.locator('.product-card').count(), 1);
    assert.match(await page.locator('.product-name').textContent(), /Масляный фильтр/);
  }
});

async function checkout(page) {
  await page.locator('[data-action="add"][data-id="filter-mann-oil"]').click();
  await page.locator('.header-cart').click();
  await page.locator('[name="name"]').fill('Демо Покупатель');
  await page.locator('[name="phone"]').fill('+7 (999) 123-45-67');
  await page.locator('.checkout-submit').click();
  await page.locator('.receipt').waitFor();
}

test('catalog search, combined filters, price sorting and empty result', async t => {
  const page = await open(t);
  assert.equal(await page.locator('.product-card').count(), 14);
  await page.locator('#search').fill('w 914 / 2');
  await page.locator('#search-form button').click();
  assert.equal(await page.locator('.product-card').count(), 1);
  assert.match(await page.locator('.product-name').textContent(), /Масляный фильтр/);
  await page.locator('#search').fill('нет-такой-детали');
  await page.locator('#search-form button').click();
  await page.locator('.empty-state').waitFor();
  await page.locator('[data-action="reset"]').last().click();
  await page.locator('[name="category"][value="filters"]').check();
  await page.locator('#brand').selectOption('MANN-FILTER');
  await page.locator('[name="inStock"]').check();
  assert.equal(await page.locator('.product-card').count(), 1);
  await page.locator('[data-action="reset"]').click();
  await page.locator('#sort').selectOption('price_asc');
  assert.match(await page.locator('.product-name').first().textContent(), /Свеча/);
});

test('checkout validates contacts, delivery and saves the order in CRM', async t => {
  const page = await open(t);
  await page.locator('[data-action="add"][data-id="filter-mann-oil"]').click();
  await page.locator('.header-cart').click();
  await page.locator('[data-action="quantity"][data-quantity="2"]').click();
  await page.locator('[name="name"]').fill('Демо Покупатель');
  await page.locator('[name="phone"]').fill('123');
  await page.locator('.checkout-submit').click();
  assert.match(await page.locator('#checkout-error').textContent(), /телефон/);
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))[0].quantity), 2);
  await page.locator('[name="phone"]').fill('+7 (999) 123-45-67');
  await page.locator('[name="method"][value="delivery"]').check();
  assert.equal(await page.locator('[name="name"]').inputValue(), 'Демо Покупатель');
  await page.locator('[name="address"]').fill('Демо-город, Тестовая улица, 1');
  await page.locator('.checkout-submit').click();
  await page.locator('.receipt').waitFor();
  const order = await page.evaluate(() => JSON.parse(sessionStorage.getItem('lavr.orders.v1'))[0]);
  assert.equal(order.totalKopecks, 178000);
  assert.equal(order.fulfillment.method, 'delivery');
  assert.equal(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1')).length), 0);
  await page.locator('.receipt-actions a').click();
  await page.locator('.order-number').click();
  await page.locator('[data-action="status"][data-status="confirmed"]').focus();
  await page.keyboard.press('Enter');
  await page.locator('[data-action="status"][data-status="ready"]').waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.status), 'ready');
  await page.keyboard.press('Enter');
  await page.locator('[data-action="status"][data-status="completed"]').waitFor();
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.status), 'completed');
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('[data-action="status"]').count(), 0);
  assert.match(await page.locator('.order-detail-meta').textContent(), /Завершён/);
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.action), 'close');
});

test('a second basket can be checked out after leaving confirmation through CRM', async t => {
  const page = await open(t);
  await checkout(page);
  await page.locator('.receipt-actions a').click();
  await page.locator('.catalog-nav').click();
  await page.locator('[data-action="add"][data-id="spark-ngk"]').click();
  await page.locator('.header-cart').click();
  await page.locator('#checkout-form').waitFor();
  assert.equal(await page.locator('.receipt').count(), 0);
  await page.locator('[name="name"]').fill('Второй демопокупатель');
  await page.locator('[name="phone"]').fill('89991234567');
  await page.locator('.checkout-submit').click();
  await page.locator('.receipt').waitFor();
  assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('lavr.orders.v1')).length), 2);
  const sibling = await page.context().newPage();
  await sibling.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' });
  await sibling.locator('[data-action="add"][data-id="spark-ngk"]').click();
  await page.locator('#checkout-form').waitFor();
  assert.equal(await page.locator('.receipt').count(), 0);
  await sibling.close();
});

test('product dialog returns keyboard focus to its opener on Escape and close', async t => {
  const page = await open(t);
  await page.locator('.product-name').first().focus();
  await page.keyboard.press('Enter');
  await page.locator('dialog[open]').waitFor();
  await page.keyboard.press('Escape');
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.action), 'product');
  await page.keyboard.press('Enter');
  await page.locator('dialog[open]').waitFor();
  await page.locator('dialog[open] .dialog-close').click();
  assert.equal(await page.evaluate(() => document.activeElement?.dataset.action), 'product');
});

test('skip link keeps the current route and fulfillment choices keep keyboard focus', async t => {
  const page = await open(t);
  await page.locator('.crm-nav').click();
  await page.locator('.skip-link').focus();
  await page.keyboard.press('Enter');
  assert.equal(new URL(page.url()).hash, '#crm');
  assert.equal(await page.evaluate(() => document.activeElement.id), 'main');
  await page.locator('.catalog-nav').click();
  await page.locator('[data-action="add"][data-id="filter-mann-oil"]').click();
  await page.locator('.header-cart').click();
  await page.locator('[name="method"][value="pickup"]').focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.evaluate(() => document.activeElement?.value), 'delivery');
});

test('stored customer text is escaped and a storage failure preserves the cart', async t => {
  const page = await open(t);
  await page.locator('[data-action="add"][data-id="filter-mann-oil"]').click();
  await page.locator('.header-cart').click();
  await page.locator('[name="name"]').fill('<img src=x onerror=alert(1)>');
  await page.locator('[name="phone"]').fill('+79991234567');
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key === 'lavr.orders.v1') throw new DOMException('Storage blocked', 'QuotaExceededError');
      return original.call(this, key, value);
    };
    window.restoreStorage = () => { Storage.prototype.setItem = original; };
  });
  await page.locator('.checkout-submit').click();
  assert.match(await page.locator('#checkout-error').textContent(), /Корзина сохранена/);
  assert.equal(await page.locator('.cart-item').count(), 1);
  await page.evaluate(() => window.restoreStorage());
  await page.locator('.checkout-submit').click();
  await page.locator('.receipt').waitFor();
  await page.locator('.receipt-actions a').click();
  assert.equal(await page.locator('.orders-table img').count(), 0);
  assert.match(await page.locator('.orders-table').textContent(), /<img src=x onerror=alert\(1\)>/);
});

test('responsive storefront, basket and CRM fit mobile widths; capture design previews', async t => {
  const page = await open(t);
  await mkdir('docs/previews', { recursive: true });
  await page.screenshot({ path: 'docs/previews/storefront-desktop.png', fullPage: true });
  for (const width of [320, 390, 768, 1024]) {
    await page.setViewportSize({ width, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `home width ${width}`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'docs/previews/storefront-mobile.png', fullPage: true });
  await checkout(page);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'receipt width 390');
  await page.locator('.receipt-actions a').click();
  await page.locator('.crm-page').waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'CRM width 390');
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.locator('#toast.visible').waitFor({ state: 'hidden' });
  await page.screenshot({ path: 'docs/previews/crm-desktop.png', fullPage: true });
  await page.getByRole('link', { name: 'Каталог', exact: true }).click();
  await page.locator('#category-form').waitFor();
  await page.screenshot({ path: 'docs/previews/catalog-admin-desktop.png', fullPage: true });
  for (const width of [320, 390, 768, 1024]) {
    await page.setViewportSize({ width, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `catalog manager width ${width}`);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: 'docs/previews/catalog-admin-mobile.png', fullPage: true });
});

test('manager creates a category and a SKU that survives reload and can be ordered', async t => {
  const page = await open(t);
  await page.locator('.crm-nav').click();
  await page.getByRole('link', { name: 'Каталог', exact: true }).click();
  await page.locator('#category-form [name="categoryName"]').fill('Датчики');
  await page.locator('#category-form button[type="submit"]').click();
  await page.locator('#product-form [name="productName"]').fill('Тестовый датчик');
  await page.locator('#product-form [name="sku"]').fill('DEMO-CUST-001');
  await page.locator('#product-form [name="brand"]').fill('DemoBrand');
  await page.locator('#product-form [name="price"]').fill('1234,56');
  await page.locator('#product-form [name="stock"]').fill('3');
  await page.locator('#product-form button[type="submit"]').click();
  await page.getByRole('cell', { name: 'Тестовый датчик', exact: false }).waitFor();
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.catalog.v1')));
  const product = stored.products.find(item => item.sku === 'DEMO-CUST-001');
  assert.equal(product.priceKopecks, 123456);
  assert.equal(stored.categories.find(item => item.id === product.category).name, 'Датчики');
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.logo').first().click();
  await page.locator(`.category-card[data-id="${product.category}"]`).waitFor();
  await page.locator('#promotions').waitFor();
  await page.locator('#search').fill('demo cust 001');
  await page.locator('#search-form button').click();
  assert.equal(await page.locator('.product-card').count(), 1);
  await page.locator(`[data-action="add"][data-id="${product.id}"]`).click();
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('.header-cart').click();
  await page.locator('[data-action="quantity"][data-quantity="2"]').click();
  await page.locator('[name="name"]').fill('Тестовый Покупатель');
  await page.locator('[name="phone"]').fill('+79991234567');
  await page.locator('.checkout-submit').click();
  await page.locator('.receipt').waitFor();
  const order = await page.evaluate(() => JSON.parse(sessionStorage.getItem('lavr.orders.v1'))[0]);
  assert.equal(order.totalKopecks, 246912);
  assert.equal(order.items[0].productId, product.id);
  assert.equal(order.items[0].name, 'Тестовый датчик');
});

test('catalog save errors preserve forms and another tab receives catalog changes', async t => {
  const page = await open(t);
  await page.goto('http://127.0.0.1:5173/#crm-catalog', { waitUntil: 'networkidle' });
  await page.locator('#category-form').waitFor();
  await page.evaluate(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key === 'lavr.catalog.v1') throw new DOMException('Storage blocked', 'QuotaExceededError');
      return original.call(this, key, value);
    };
    window.restoreStorage = () => { Storage.prototype.setItem = original; };
  });
  await page.locator('#category-form [name="categoryName"]').fill('Электрика');
  await page.locator('#category-form button[type="submit"]').click();
  assert.match(await page.locator('#category-form [role="alert"]').textContent(), /сохранить/);
  assert.equal(await page.locator('#category-form [name="categoryName"]').inputValue(), 'Электрика');
  assert.equal(await page.evaluate(() => localStorage.getItem('lavr.catalog.v1')), null);
  await page.evaluate(() => window.restoreStorage());
  const sibling = await page.context().newPage();
  await sibling.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' });
  await page.locator('#category-form button[type="submit"]').click();
  await sibling.getByRole('button', { name: /Электрика/ }).waitFor();
  const category = await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.catalog.v1')).categories.find(item => item.name === 'Электрика'));
  await page.locator('[data-testid="product-category-filter-mann-oil"]').selectOption(category.id);
  await sibling.locator(`.category-card[data-id="${category.id}"]`).click();
  assert.equal(await sibling.locator('.product-card').count(), 1);
  assert.match(await sibling.locator('.product-name').textContent(), /Масляный фильтр/);
  await sibling.close();
});

test('a corrupt saved catalog is preserved when a manager tries to add a category', async t => {
  const page = await open(t);
  const damaged = '{"categories":';
  await page.evaluate(value => localStorage.setItem('lavr.catalog.v1', value), damaged);
  await page.goto('http://127.0.0.1:5173/#crm-catalog', { waitUntil: 'networkidle' });
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('#category-form [name="categoryName"]').fill('Новая категория');
  await page.locator('#category-form button[type="submit"]').click();
  assert.ok(await page.evaluate(value => localStorage.getItem('lavr.catalog.v1') === value, damaged), 'Damaged catalog must remain untouched');
  assert.equal(await page.locator('#category-form [name="categoryName"]').inputValue(), 'Новая категория');
  assert.match(await page.locator('#category-form [role="alert"]').textContent(), /каталог/);
});

test('a catalog read error preserves custom cart rows and blocks checkout until recovery', async t => {
  const page = await open(t);
  const catalog = createProduct(initialCatalog(), { name: 'Датчик', sku: 'SAFE-001', brand: 'Demo', category: 'filters', price: '12,34', stock: 4 });
  const originalCart = [{ productId: catalog.products.at(-1).id, quantity: 2 }, { productId: 'filter-mann-oil', quantity: 1 }];
  await page.evaluate(({ catalog, cart }) => {
    localStorage.setItem('lavr.catalog.v1', JSON.stringify(catalog));
    localStorage.setItem('lavr.cart.v1', JSON.stringify(cart));
  }, { catalog, cart: originalCart });
  await page.reload({ waitUntil: 'networkidle' });
  await page.evaluate(() => localStorage.setItem('lavr.catalog.v1', '{"categories":'));
  await page.reload({ waitUntil: 'networkidle' });
  await page.locator('[data-action="add"][data-id="filter-mann-oil"]').click();
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('lavr.cart.v1'))), originalCart);
  await page.locator('.header-cart').click();
  await page.locator('[name="name"]').fill('Демо Покупатель');
  await page.locator('[name="phone"]').fill('+79991234567');
  await page.locator('.checkout-submit').click();
  assert.match(await page.locator('#checkout-error').textContent(), /каталог/);
  assert.equal(await page.evaluate(() => sessionStorage.getItem('lavr.orders.v1')), null);
  const sibling = await page.context().newPage();
  await sibling.goto('http://127.0.0.1:5173');
  await sibling.evaluate(value => localStorage.setItem('lavr.catalog.v1', JSON.stringify(value)), catalog);
  await page.waitForFunction(() => document.querySelectorAll('.cart-item').length === 2);
  await page.locator('.checkout-submit').click();
  await page.locator('.receipt').waitFor();
  assert.equal(await page.evaluate(() => JSON.parse(sessionStorage.getItem('lavr.orders.v1'))[0].totalKopecks), 91468);
  await sibling.close();
});

test('long valid catalog names and identifiers remain readable at desktop and mobile widths', async t => {
  const page = await open(t);
  const withCategory = createCategory(initialCatalog(), { name: 'К'.repeat(80) });
  const catalog = createProduct(withCategory, { name: 'Д'.repeat(160), sku: 'S'.repeat(80), brand: 'B'.repeat(80), category: withCategory.categories.at(-1).id, price: '10000000', stock: 2 });
  await page.evaluate(value => localStorage.setItem('lavr.catalog.v1', JSON.stringify(value)), catalog);
  await page.reload({ waitUntil: 'networkidle' });
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `catalog names width ${width}`);
    assert.equal(await page.evaluate(() => [...document.querySelectorAll('.product-name')].some(element => element.scrollWidth > element.clientWidth)), false, `product name width ${width}`);
    assert.equal(await page.evaluate(() => [...document.querySelectorAll('.product-card')].some(element => element.scrollWidth > element.clientWidth)), false, `card content width ${width}`);
  }
  async function assertViewFits(selectors, view) {
    for (const width of [1440, 390, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `${view} page width ${width}`);
      const overflow = await page.evaluate(selectors => selectors.filter(selector => [...document.querySelectorAll(selector)].some(element => element.scrollWidth > element.clientWidth + 1)), selectors);
      assert.deepEqual(overflow, [], `${view} content width ${width}`);
    }
  }
  const product = catalog.products.at(-1);
  await page.locator(`.category-card[data-id="${product.category}"]`).click();
  await assertViewFits(['#catalog-title'], 'selected category');
  await page.locator('.product-name').click();
  await page.locator('dialog[open]').waitFor();
  await assertViewFits(['dialog', '.detail-grid', '#dialog-title', '.detail-grid .eyebrow'], 'product dialog');
  await page.locator(`dialog [data-action="add"][data-id="${product.id}"]`).click();
  await page.locator('dialog[open] .dialog-close').click();
  await page.locator('.header-cart').click();
  await assertViewFits(['.cart-items', '.cart-item-info', '.cart-item h3', '.order-summary'], 'cart');
  await page.locator('[name="name"]').fill('Демо Покупатель');
  await page.locator('[name="phone"]').fill('+79991234567');
  await page.locator('.checkout-submit').click();
  await page.locator('.receipt').waitFor();
  await page.locator('.receipt-actions a').click();
  await assertViewFits(['.metric-grid article', '.metric-grid article > strong'], 'CRM totals');
  await page.locator('.order-number').click();
  await page.locator('dialog[open]').waitFor();
  await assertViewFits(['dialog', '.order-detail-items', '.order-detail-items > div > span'], 'order dialog');
});

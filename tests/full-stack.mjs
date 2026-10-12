import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { randomUUID } from 'node:crypto';

// Optional QA dependency; run against the actual Compose stack, without request intercepts.
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const envFile = process.env.DOCKER_ENV_FILE || new URL('../.env.docker', import.meta.url);
const environment = Object.fromEntries((await readFile(envFile, 'utf8')).split(/\r?\n/).flatMap(line => {
  const match = line.match(/^([A-Z][A-Z0-9_]*)=(.*)$/);
  return match ? [[match[1], match[2].trim().replace(/^(['"])(.*)\1$/, '$2')]] : [];
}));
const login = process.env.ADMIN_LOGIN || environment.ADMIN_LOGIN;
const password = process.env.ADMIN_PASSWORD || environment.ADMIN_PASSWORD;
assert.ok(login && password, 'Set administrator credentials in the ignored Docker environment file.');
const baseUrl = (process.env.BASE_URL || `http://127.0.0.1:${environment.APP_PORT || 8080}`).replace(/\/$/, '');
const origin = new URL(baseUrl).origin;
const keepFixtures = process.env.FULL_STACK_KEEP_FIXTURES === '1';
const runId = `${Date.now()}-${randomUUID().slice(0, 8)}`;

async function request(path, { method = 'GET', body, headers = {} } = {}, expected = 200) {
  const response = await fetch(baseUrl + path, {
    method, headers: { ...(body && !(body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...headers },
    body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
    redirect: 'error',
  });
  assert.equal(response.status, expected, `${method} ${path} returned an unexpected status`);
  return response;
}

test('live Docker stack stores catalog/content/images in PostgreSQL and serves Vue on desktop and mobile', async t => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const contexts = [];
  const errors = [];
  let sessionHeaders;
  let category;
  let publication;
  let media;
  let presentationBefore;
  let presentationVersion;
  let siteBefore;
  let siteVersion;
  let product;
  let completed = false;
  const publicTitle = `QA Docker ${runId}`;

  const api = (path, options = {}, expected = 200) => request(path, { ...options, headers: { ...sessionHeaders, ...options.headers } }, expected);
  t.after(async () => {
    for (const context of contexts) await context.close();
    await browser.close();
    const canKeep = keepFixtures && completed && errors.length === 0;
    const cleanups = [];
    if (siteVersion) cleanups.push(() => api('/api/admin/site', { method: 'PATCH', body: { hero: siteBefore.hero }, headers: { 'If-Match': `"${siteVersion}"` } }));
    if (presentationVersion) {
      const { version, productId, ...presentation } = presentationBefore;
      cleanups.push(() => api(`/api/admin/products/${product.id}/presentation`, { method: 'PATCH', body: presentation, headers: { 'If-Match': `"${presentationVersion}"` } }));
    }
    if (!canKeep && publication) cleanups.push(() => api(`/api/admin/publications/${publication.id}`, { method: 'PATCH', body: { status: 'archived' }, headers: { 'If-Match': `"${publication.version}"` } }));
    if (!canKeep && category) cleanups.push(() => api(`/api/admin/categories/${category.id}`, { method: 'PATCH', body: { isVisible: false }, headers: { 'If-Match': `"${category.version}"` } }));
    if (sessionHeaders) cleanups.push(() => api('/api/manager/session', { method: 'DELETE' }, 204));
    const cleanupFailures = [];
    for (const cleanup of cleanups) {
      try { await cleanup(); } catch (error) { cleanupFailures.push(error); }
    }
    assert.equal(cleanupFailures.length, 0, 'QA cleanup failed; a concurrent edit may have changed a record version.');
    assert.equal(errors.length, 0, 'A browser page raised an unexpected JavaScript exception.');
    console.log(canKeep ? 'Cleanup: original hero/product restored; QA publication retained for persistence verification.' : 'Cleanup: original hero/product restored; QA category hidden and publication archived.');
  });

  const site = await (await request('/api/site')).json();
  assert.ok(site.hero.title && site.settings.storeName);
  const categories = await (await request('/api/categories')).json();
  assert.ok(categories.length > 0, 'This check expects the explicitly seeded local catalog.');
  const catalog = await (await request('/api/catalog?q=W914%2F2')).json();
  product = catalog.items.find(item => item.sku.replace(/\s/g, '') === 'W914/2');
  assert.ok(product && product.stock > 0, 'Seed SKU W914/2 must be available.');
  const detail = await (await request(`/api/catalog/${product.id}`)).json();
  assert.equal(detail.product.id, product.id);
  await request('/api/admin/site', {}, 401);
  await request('/api/manager/orders', {}, 401);
  const authentication = await request('/api/manager/session', { method: 'POST', body: { login, password }, headers: { Origin: origin } });
  const session = await authentication.json();
  assert.equal(session.user.role, 'admin');
  const cookie = authentication.headers.getSetCookie().find(value => value.startsWith('lavr_session='))?.split(';')[0];
  assert.ok(cookie && session.csrfToken.length >= 32);
  sessionHeaders = { Cookie: cookie, Origin: origin, 'X-CSRF-Token': session.csrfToken };
  siteBefore = await (await api('/api/admin/site')).json();
  const health = await (await api('/api/manager/integration/health')).json();
  assert.equal(health.state, 'not_connected');
  const denied = await request('/api/admin/categories', { method: 'POST', body: { name: 'No CSRF', position: 0, isVisible: true, showInMenu: false }, headers: { Cookie: cookie, Origin: origin } }, 403);
  assert.equal((await denied.json()).code, 'INVALID_CSRF');

  category = await (await api('/api/admin/categories', { method: 'POST', body: { name: `QA ${runId}`, position: 1000000, isVisible: true, showInMenu: false } }, 201)).json();
  assert.ok((await (await request('/api/categories')).json()).some(item => item.id === category.id));
  const presentation = await api(`/api/admin/products/${product.id}/presentation`);
  presentationBefore = (await presentation.json()).presentation;
  assert.equal(presentation.headers.get('etag'), `"${presentationBefore.version}"`);
  const title = `QA ${runId}: ${product.name.slice(0, 180)}`;
  const changed = await (await api(`/api/admin/products/${product.id}/presentation`, { method: 'PATCH', body: { websiteTitle: title }, headers: { 'If-Match': `"${presentationBefore.version}"` } })).json();
  presentationVersion = changed.version;
  const conflict = await api(`/api/admin/products/${product.id}/presentation`, { method: 'PATCH', body: { websiteTitle: 'Stale edit' }, headers: { 'If-Match': `"${presentationBefore.version}"` } }, 412);
  assert.equal((await conflict.json()).code, 'VERSION_CONFLICT');
  assert.equal((await (await request(`/api/catalog/${product.id}`)).json()).product.websiteTitle, title);
  const receipt = await request('/api/orders', { method: 'POST', body: { customer: { name: 'QA purchase', phone: '+79991234567' }, fulfillment: { method: 'pickup' }, items: [{ productId: product.id, quantity: 1, expectedUnitPriceKopecks: product.priceKopecks }] }, headers: { 'Idempotency-Key': randomUUID() } }, 503);
  assert.equal((await receipt.json()).code, 'ERP_NOT_CONFIGURED');
  console.log('API: public catalog, SKU search, sessions, CSRF, versions and ERP gate passed.');

  const bytes = await readFile(new URL('../public/images/brake-disc.jpg', import.meta.url));
  const upload = new FormData();
  upload.append('file', new Blob([bytes], { type: 'image/jpeg' }), 'docker-qa.jpg');
  upload.append('alt', `QA image ${runId}`);
  media = await (await api('/api/admin/media', { method: 'POST', body: upload }, 201)).json();
  assert.equal(media.width, 870);
  assert.deepEqual(Buffer.from(await (await api(media.path)).arrayBuffer()), bytes);
  await request(media.path, {}, 401);
  await request(`/api/media/${media.id}`, {}, 404);
  publication = await (await api('/api/admin/publications', { method: 'POST', body: { kind: 'announcement', title: publicTitle, description: 'Проверка серверной публикации и изображения.', imageIds: [media.id], target: { type: 'catalog' }, buttonText: 'Открыть каталог', position: 0, status: 'draft', startsAt: null, endsAt: null } }, 201)).json();
  assert.ok(!(await (await request('/api/site')).json()).publications.some(item => item.id === publication.id));
  publication = await (await api(`/api/admin/publications/${publication.id}`, { method: 'PATCH', body: { status: 'published' }, headers: { 'If-Match': `"${publication.version}"` } })).json();
  assert.ok((await (await request('/api/site')).json()).publications.some(item => item.id === publication.id && item.images[0]?.id === media.id));
  assert.deepEqual(Buffer.from(await (await request(`/api/media/${media.id}`)).arrayBuffer()), bytes);
  console.log('API: private upload, draft publication, publish and public image bytes passed.');

  async function open(width) {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1100 }, hasTouch: width === 390, isMobile: width === 390 });
    contexts.push(context);
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error));
    await page.goto(baseUrl, { waitUntil: 'networkidle' });
    await page.locator('.hero h1').waitFor();
    return page;
  }

  async function fits(page, view) {
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), `${view} must fit the viewport.`);
  }

  async function publishedImage(page) {
    const image = page.locator('.promotion-card').filter({ hasText: publicTitle }).locator('img').first();
    await image.scrollIntoViewIfNeeded();
    await image.evaluate(element => element.decode());
    assert.equal(await image.evaluate(element => element.naturalWidth), media.width);
    assert.equal(new URL(await image.getAttribute('src'), baseUrl).pathname, `/api/media/${media.id}`);
  }

  async function signIn(page) {
    await page.goto(baseUrl + '/#crm-content');
    await page.locator('.staff-login input[name="login"]').fill(login);
    await page.locator('.staff-login input[name="password"]').fill(password);
    await page.locator('.staff-login button[type="submit"]').click();
    await page.getByTestId('hero-title').waitFor();
    assert.equal(await page.locator('.staff-login').count(), 0);
  }

  const desktop = await open(1440);
  await publishedImage(desktop);
  await fits(desktop, 'Desktop storefront');
  await signIn(desktop);
  assert.equal(await desktop.getByTestId('hero-title').inputValue(), siteBefore.hero.title);
  const editedHero = `Полная Docker сборка ${runId}`;
  await desktop.getByTestId('hero-title').fill(editedHero);
  const saveResponse = desktop.waitForResponse(response => new URL(response.url()).pathname === '/api/admin/site' && response.request().method() === 'PATCH');
  await desktop.getByTestId('save-site').click();
  const saved = await saveResponse;
  assert.equal(saved.status(), 200);
  siteVersion = (await saved.json()).version;
  await desktop.getByTestId('site-success').waitFor();
  await fits(desktop, 'Desktop content editor');
  await desktop.locator('.header-main .logo').click();
  await desktop.locator('.hero h1').waitFor();
  assert.equal(await desktop.locator('.hero h1').textContent(), editedHero);
  console.log('Chrome 1440: actual administrator login, editor save and storefront refresh passed.');

  const mobile = await open(390);
  assert.equal(await mobile.locator('.hero h1').textContent(), editedHero);
  await publishedImage(mobile);
  await fits(mobile, 'Mobile storefront');
  await mobile.getByRole('searchbox', { name: 'Поиск запчастей' }).fill('W914/2');
  const searchResponse = mobile.waitForResponse(response => new URL(response.url()).pathname === '/api/catalog' && new URL(response.url()).searchParams.get('q') === 'W914/2');
  await mobile.locator('#search-form button[type="submit"]').click();
  assert.equal((await searchResponse).status(), 200);
  await mobile.locator(`.product-card [data-action="product"][data-id="${product.id}"]`).first().waitFor();
  await mobile.waitForFunction(() => document.querySelectorAll('.product-card').length === 1);
  assert.equal(await mobile.locator('.product-card .product-name').first().textContent(), title);
  const stockResponse = mobile.waitForResponse(response => new URL(response.url()).pathname === '/api/catalog' && new URL(response.url()).searchParams.get('inStock') === 'true');
  await mobile.locator('input[name="inStock"]').check();
  assert.equal((await stockResponse).status(), 200);
  assert.equal(await mobile.locator('input[name="inStock"]').isChecked(), true);
  await fits(mobile, 'Mobile search');
  await mobile.locator(`.product-card [data-action="add"][data-id="${product.id}"]`).click();
  await mobile.locator('.header-cart').click();
  await mobile.locator('.cart-item').waitFor();
  await mobile.locator('.checkout-layout[aria-busy="false"]').waitFor();
  const cartBefore = await mobile.evaluate(() => localStorage.getItem('lavr.cart.v1'));
  assert.equal(JSON.parse(cartBefore)[0].quantity, 1);
  await fits(mobile, 'Mobile cart');
  if (await mobile.locator('.checkout-submit').isEnabled()) {
    await mobile.locator('#checkout-form input[name="name"]').fill('QA purchase');
    await mobile.locator('#checkout-form input[name="phone"]').fill('+79991234567');
    const orderResponse = mobile.waitForResponse(response => new URL(response.url()).pathname === '/api/orders' && response.request().method() === 'POST');
    await mobile.locator('.checkout-submit').click();
    const rejected = await orderResponse;
    assert.equal(rejected.status(), 503);
    assert.equal((await rejected.json()).code, 'ERP_NOT_CONFIGURED');
    await mobile.locator('#checkout-error').filter({ hasText: '1С' }).waitFor();
    assert.equal(await mobile.locator('#checkout-form input[name="name"]').inputValue(), 'QA purchase');
  } else {
    assert.ok(await mobile.locator('.api-feedback').filter({ hasText: /каталог|Остаток/ }).count(), 'A stale catalog must explain why checkout is disabled.');
  }
  assert.equal(await mobile.evaluate(() => localStorage.getItem('lavr.cart.v1')), cartBefore);
  assert.equal(await mobile.locator('.receipt').count(), 0);
  await signIn(mobile);
  assert.equal(await mobile.getByTestId('hero-title').inputValue(), editedHero);
  await fits(mobile, 'Mobile content editor');
  await mobile.getByTestId('content-tab-media').click();
  await mobile.getByTestId(`media-${media.id}`).waitFor();
  await fits(mobile, 'Mobile image library');
  console.log('Chrome 390: public server changes, image decoding, SKU search, retained cart and administrator editor passed.');
  assert.equal(errors.length, 0, 'A browser page raised an unexpected JavaScript exception.');
  completed = true;
  console.log('QA_RECORDS ' + JSON.stringify({ categoryId: category.id, mediaId: media.id, publicationId: publication.id, publicationTitle: publicTitle, publicImagePath: `/api/media/${media.id}` }));
});

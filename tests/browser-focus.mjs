import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const browser = await chromium.launch({ channel: 'chrome', headless: true });
after(() => browser.close());
const baseUrl = process.env.BASE_URL || 'http://127.0.0.1:5173';
const previewDir = process.env.PREVIEW_DIR || '/tmp/lavr-focus-previews';
const accent = 'rgb(225, 38, 47)';
const focusStyle = locator => locator.evaluate(element => {
  const style = getComputedStyle(element);
  return { borderColor: style.borderColor, borderWidth: style.borderWidth, shadow: style.boxShadow, outlineColor: style.outlineColor, outlineWidth: style.outlineWidth, outlineStyle: style.outlineStyle, outlineOffset: style.outlineOffset, keyboard: element.matches(':focus-visible') };
});

for (const width of [1440, 390, 320]) test(`search and login use one consistent accessible focus indicator at ${width}px`, async t => {
  const context = await browser.newContext({ viewport: { width, height: 1000 } });
  const page = await context.newPage(); page.setDefaultTimeout(7000);
  const errors = []; page.on('pageerror', issue => errors.push(issue.message));
  t.after(async () => { await context.close(); assert.deepEqual(errors, []); });
  await mkdir(previewDir, { recursive: true });
  await page.goto(`${baseUrl}/#catalog`, { waitUntil: 'networkidle' });
  const search = page.locator('#search'); const searchForm = page.locator('#search-form');
  await search.click();
  const searchPointer = { field: await focusStyle(search), wrapper: await focusStyle(searchForm) };
  await page.locator('.header-main').screenshot({ path: `${previewDir}/focus-${width}-search-pointer.png` });
  await page.keyboard.press('Tab');
  const searchButton = await focusStyle(page.locator('#search-form button'));
  const searchWithButton = await focusStyle(searchForm);
  await page.keyboard.press('Shift+Tab');
  const searchKeyboard = { field: await focusStyle(search), wrapper: await focusStyle(searchForm) };
  await page.locator('.header-main').screenshot({ path: `${previewDir}/focus-${width}-search-keyboard.png` });

  await page.locator('.header-account').getByRole('link', { name: 'Войти', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: 'Авторизация', exact: true }); await dialog.waitFor();
  await dialog.screenshot({ path: `${previewDir}/focus-${width}-login-open.png` });
  const email = dialog.locator('[name="email"]'); const password = dialog.locator('[name="password"]');
  await email.click(); const emailPointer = await focusStyle(email);
  await dialog.screenshot({ path: `${previewDir}/focus-${width}-email-pointer.png` });
  await page.keyboard.press('Tab'); const passwordKeyboard = await focusStyle(password);
  assert.equal(await password.evaluate(element => element === document.activeElement), true);
  await dialog.screenshot({ path: `${previewDir}/focus-${width}-password-keyboard.png` });
  await page.keyboard.press('Shift+Tab'); const emailKeyboard = await focusStyle(email);
  await password.click(); const passwordPointer = await focusStyle(password);
  await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Shift+Tab');
  const close = dialog.getByRole('button', { name: 'Закрыть авторизацию', exact: true });
  assert.equal(await close.evaluate(element => element === document.activeElement), true);
  const closeKeyboard = await focusStyle(close);
  await dialog.screenshot({ path: `${previewDir}/focus-${width}-close-keyboard.png` });
  await email.click();
  const bounds = await close.boundingBox();
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2); await page.mouse.down();
  const closePointer = await focusStyle(close); await page.mouse.up();
  await dialog.waitFor({ state: 'hidden' });
  await page.keyboard.press('Tab');
  const link = page.locator('a:focus'); assert.equal(await link.count(), 1);
  const linkKeyboard = await focusStyle(link);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);

  t.diagnostic(JSON.stringify({ width, searchPointer, searchKeyboard, searchButton, searchWithButton, emailPointer, emailKeyboard, passwordPointer, passwordKeyboard, closeKeyboard, closePointer, linkKeyboard }));
  for (const searchState of [searchPointer, searchKeyboard]) {
    assert.equal(searchState.wrapper.borderColor, accent);
    assert.equal(searchState.wrapper.borderWidth, '1px');
    assert.notEqual(searchState.wrapper.shadow, 'none');
    assert.equal(searchState.field.outlineStyle, 'none'); assert.equal(searchState.field.shadow, 'none');
  }
  assert.equal(searchKeyboard.field.keyboard, true);
  assert.equal(searchWithButton.shadow, 'none');
  for (const field of [emailPointer, emailKeyboard, passwordPointer, passwordKeyboard]) {
    assert.equal(field.borderColor, accent); assert.equal(field.borderWidth, '1px');
    assert.equal(field.shadow, searchPointer.wrapper.shadow); assert.equal(field.outlineStyle, 'none');
  }
  for (const control of [searchButton, closeKeyboard, linkKeyboard]) {
    assert.equal(control.keyboard, true); assert.equal(control.outlineColor, accent); assert.equal(control.outlineWidth, '2px');
  }
  assert.equal(closeKeyboard.outlineOffset, '-3px');
  assert.equal(closePointer.outlineStyle, 'none');
});

test('forced colors retain a visible field indicator without doubling the search outline', async t => {
  const context = await browser.newContext({ viewport: { width: 390, height: 1000 }, forcedColors: 'active' });
  t.after(() => context.close()); const page = await context.newPage();
  await page.goto(`${baseUrl}/#catalog`, { waitUntil: 'networkidle' });
  await page.locator('#search').click();
  const wrapper = await focusStyle(page.locator('#search-form')); const field = await focusStyle(page.locator('#search'));
  await page.locator('.header-account').getByRole('link', { name: 'Войти', exact: true }).click();
  await page.locator('.auth-dialog [name="email"]').click();
  const email = await focusStyle(page.locator('.auth-dialog [name="email"]'));
  assert.equal(wrapper.outlineStyle, 'solid'); assert.equal(wrapper.outlineWidth, '2px');
  assert.equal(field.outlineStyle, 'none');
  assert.equal(email.outlineStyle, 'solid'); assert.equal(email.outlineWidth, '2px');
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { createHttpApi } from '../src/data/http-api.js';

const session = { user: { id: 'admin', name: 'Ольга', role: 'admin' }, csrfToken: 'a'.repeat(32), expiresAt: '2026-10-12T10:00:00Z' };
const settings = { storeName: 'LAVR', phone: '+79991234567', address: 'Москва', hours: '9–18', categoriesTitle: 'Категории', promotionsTitle: 'Акции', catalogTitle: 'Каталог' };
const hero = { title: 'Автозапчасти', description: 'Для вашего авто', imageId: 'hero', buttonText: 'В каталог', target: { type: 'catalog' } };
const menu = [{ label: 'Масла', target: { type: 'category', categoryId: 'oils' }, position: 0, isVisible: true }];
const site = { hero, settings, menu, version: 7 };
const input = { kind: 'offer', title: 'Акция', description: 'Описание', imageIds: ['image-1'], target: { type: 'product', productId: 'oil' }, buttonText: 'Открыть', position: 0 };
const publication = { ...input, id: 'offer-1', status: 'draft', startsAt: null, endsAt: null, version: 1 };
const media = { id: 'image-1', path: '/api/admin/media/image-1/file', alt: 'Масло', width: 1200, height: 800, mimeType: 'image/png', sizeBytes: 3, state: 'ready', createdAt: '2026-10-11T10:00:00Z' };
const page = (items, extra = {}) => ({ items, total: items.length, offset: 0, limit: 24, ...extra });
const json = (data, status = 200, headers = {}) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', ...headers } });
const image = (type = 'image/png', size = 3) => new File([new Uint8Array(size)], 'photo.png', { type });

function setup(responses, options = {}) {
  const calls = [];
  const api = createHttpApi({ ...options, fetchImpl: async (url, init) => {
    calls.push({ url, ...init });
    assert.ok(responses.length, `Unexpected request: ${url}`);
    const response = responses.shift();
    if (response instanceof Error) throw response;
    return response;
  } });
  return { api, calls };
}

test('admin site reads configuration and PATCH sends only supplied complete sections with its version', async () => {
  const saved = { ...site, hero: { ...hero, title: '<strong>Текст</strong>', imageId: null }, version: 8 };
  const patch = { hero: { ...saved.hero, html: '<script>bad()</script>' }, version: 999, publications: ['bad'] };
  const { api, calls } = setup([json(site, 200, { ETag: '"7"' }), json(session), json(saved, 200, { ETag: '"8"' })]);
  assert.deepEqual(await api.getAdminSite(), site);
  await api.getSession();
  assert.deepEqual(await api.updateSite(patch, 7), saved);
  assert.equal(calls[0].url, '/api/admin/site');
  assert.equal(calls[2].url, '/api/admin/site');
  assert.equal(calls[2].method, 'PATCH');
  assert.equal(calls[2].headers['If-Match'], '"7"');
  assert.equal(calls[2].headers['X-CSRF-Token'], session.csrfToken);
  assert.equal(calls[2].headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(calls[2].body), { hero: saved.hero });
  assert.equal(patch.hero.html, '<script>bad()</script>');
  assert.equal(calls.length, 3);
});

test('site patches can clear the menu without replacing omitted sections', async () => {
  const { api, calls } = setup([json(session), json({ ...site, menu: [], version: 8 })]);
  await api.getSession();
  await api.updateSite({ menu: [] }, 7);
  assert.deepEqual(JSON.parse(calls[1].body), { menu: [] });
});

test('publication creation sends a whitelisted draft and updates archive or dates without a preliminary GET', async () => {
  const created = { ...publication, id: 'new-offer' };
  const saved = { ...created, status: 'archived', startsAt: null, version: 2 };
  const { api, calls } = setup([json(session), json(created, 201, { ETag: '"1"' }), json(saved, 200, { ETag: '"2"' })]);
  await api.getSession();
  assert.deepEqual(await api.createPublication({ ...input, id: 'ignored', version: 99, image: 'blob:ignored' }), created);
  assert.deepEqual(await api.updatePublication('new-offer', { status: 'archived', startsAt: null, version: 99, html: 'ignored' }, 1), saved);
  assert.equal(calls[1].url, '/api/admin/publications');
  assert.equal(calls[1].method, 'POST');
  assert.deepEqual(JSON.parse(calls[1].body), { ...input, status: 'draft', startsAt: null, endsAt: null });
  assert.equal(calls[2].url, '/api/admin/publications/new-offer');
  assert.equal(calls[2].method, 'PATCH');
  assert.equal(calls[2].headers['If-Match'], '"1"');
  assert.deepEqual(JSON.parse(calls[2].body), { status: 'archived', startsAt: null });
  assert.equal(calls[1].headers['X-CSRF-Token'], session.csrfToken);
  assert.equal(calls[2].headers['X-CSRF-Token'], session.csrfToken);
  assert.equal(calls.length, 3);
});

test('publication and media lists preserve server pages and filter publication kind and status', async () => {
  const publications = page([publication], { total: 25, offset: 24 });
  const images = page([media], { total: 4, offset: 3, limit: 1 });
  const { api, calls } = setup([json(publications), json(publication), json(images)], { baseUrl: '/backend/api/' });
  assert.deepEqual(await api.getPublications({ kind: 'offer', status: 'draft' }, { offset: 24, limit: 24 }), publications);
  assert.deepEqual(await api.getPublication('offer-1'), publication);
  assert.deepEqual(await api.getMedia({ offset: 3, limit: 1 }), images);
  assert.deepEqual(calls.map(call => call.url), ['/backend/api/admin/publications?kind=offer&status=draft&offset=24&limit=24', '/backend/api/admin/publications/offer-1', '/backend/api/admin/media?offset=3&limit=1']);
  for (const call of calls) {
    assert.equal(call.credentials, 'same-origin');
    assert.equal(call.cache, 'no-store');
    assert.equal(call.redirect, 'error');
    assert.equal(call.headers.Accept, 'application/json');
    assert.equal(call.headers['X-CSRF-Token'], undefined);
    assert.equal(call.headers['Content-Type'], undefined);
  }
});

test('versioned content responses reject mismatching or weak ETags and accept a missing ETag', async () => {
  for (const [method, record] of [['getAdminSite', site], ['getPublication', publication]]) {
    for (const etag of ['"99"', `W/"${record.version}"`, String(record.version)]) {
      const { api } = setup([json(record, 200, { ETag: etag })]);
      await assert.rejects(api[method]('offer-1'), error => error.code === 'INVALID_RESPONSE');
    }
    const { api } = setup([json(record)]);
    assert.deepEqual(await api[method]('offer-1'), record);
  }
  for (const [mutate, record] of [[api => api.updateSite({ menu: [] }, 7), site], [api => api.createPublication(input), publication], [api => api.updatePublication('offer-1', { status: 'published' }, 1), publication]]) {
    const { api } = setup([json(session), json(record, 200, { ETag: '"99"' })]);
    await api.getSession();
    await assert.rejects(mutate(api), error => error.code === 'INVALID_RESPONSE');
  }
});

test('invalid content IDs, versions, filters and pagination do not send requests', async () => {
  const { api, calls } = setup([]);
  for (const version of [undefined, null, '1', 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1]) {
    await assert.rejects(api.updateSite({ menu: [] }, version));
    await assert.rejects(api.updatePublication('offer-1', { status: 'draft' }, version));
  }
  await assert.rejects(api.getPublication('..'));
  await assert.rejects(api.updatePublication('', { status: 'draft' }, 1));
  await assert.rejects(api.getPublications({ kind: 'unknown' }));
  await assert.rejects(api.getPublications({ status: 'deleted' }));
  await assert.rejects(api.getPublications({}, { offset: -1 }));
  await assert.rejects(api.getMedia({ limit: 101 }));
  assert.equal(calls.length, 0);
});

test('invalid content patches reject before the mutation reaches the network', async () => {
  const { api, calls } = setup([json(session)]);
  await api.getSession();
  for (const patch of [{}, { version: 7 }, { hero: { title: 'Incomplete' } }, { settings: { storeName: 'Incomplete' } }, { menu: [{ ...menu[0], target: { type: 'catalog', url: 'https://example.com' } }] }, { hero: { ...hero, imageId: '' } }]) {
    await assert.rejects(api.updateSite(patch, 7));
  }
  for (const invalid of [
    {}, { ...input, kind: 'unknown' }, { ...input, title: '' }, { ...input, position: -1 },
    { ...input, imageIds: ['one', 'one'] }, { ...input, imageIds: Array.from({ length: 9 }, (_, index) => `image-${index}`) },
    { ...input, target: { type: 'url', url: 'https://example.com' } }, { ...input, status: 'deleted' },
    { ...input, startsAt: 'bad-date' }, { ...input, startsAt: '2026-10-12T10:00:00Z', endsAt: '2026-10-11T10:00:00Z' },
  ]) await assert.rejects(api.createPublication(invalid));
  for (const patch of [{}, { version: 1 }, { status: 'deleted' }, { imageIds: [''] }, { startsAt: '2026-10-12T10:00:00Z', endsAt: '2026-10-12T10:00:00Z' }]) {
    await assert.rejects(api.updatePublication('offer-1', patch, 1));
  }
  assert.equal(calls.length, 1);
});

test('uploads send the file and alt as multipart with CSRF and the browser Content-Type boundary', async () => {
  const file = image();
  const { api, calls } = setup([json(session), json(media, 201)]);
  await api.getSession();
  assert.deepEqual(await api.uploadMedia(file, 'Масло'), media);
  const call = calls[1];
  assert.equal(call.url, '/api/admin/media');
  assert.equal(call.method, 'POST');
  assert.ok(call.body instanceof FormData);
  assert.equal(call.body.get('file'), file);
  assert.equal(call.body.get('alt'), 'Масло');
  assert.deepEqual([...call.body.keys()], ['file', 'alt']);
  assert.equal(call.headers['Content-Type'], undefined);
  assert.equal(call.headers['X-CSRF-Token'], session.csrfToken);
  assert.equal(call.headers.Accept, 'application/json');
  assert.equal(call.credentials, 'same-origin');
  assert.equal(call.cache, 'no-store');
  assert.equal(call.redirect, 'error');
});

test('upload validation allows JPG PNG and WebP through 5 MiB and rejects unsupported or empty files and long alt', async () => {
  const { api, calls } = setup([json(session), json(media, 201), json(media, 201), json(media, 201), json(media, 201)]);
  await api.getSession();
  for (const type of ['image/jpeg', 'image/png', 'image/webp']) await api.uploadMedia(image(type), '');
  await api.uploadMedia(image('image/png', 5242880), 'a'.repeat(240));
  for (const [file, alt] of [[image('image/svg+xml'), ''], [image('text/plain'), ''], [image('image/png', 0), ''], [image('image/png', 5242881), ''], [image(), 'a'.repeat(241)], [image(), null], [{ type: 'image/png', size: 3 }, '']]) {
    await assert.rejects(api.uploadMedia(file, alt));
  }
  assert.equal(calls.length, 5);
});

test('all content mutations require an established session token', async () => {
  const { api, calls } = setup([]);
  for (const mutate of [() => api.updateSite({ menu: [] }, 7), () => api.createPublication(input), () => api.updatePublication('offer-1', { status: 'published' }, 1), () => api.uploadMedia(image(), '')]) {
    await assert.rejects(mutate(), error => error.status === 401 && error.code === 'SESSION_REQUIRED');
  }
  assert.equal(calls.length, 0);
});

test('admin site GET and PATCH reject malformed configuration instead of returning it', async () => {
  for (const invalid of [{ ...site, version: '7' }, { ...site, hero: { ...hero, imageId: '..' } }, { ...site, settings: { ...settings, phone: '123' } }, { ...site, menu: [{ ...menu[0], isVisible: 'false' }] }, { ...site, hero: { ...hero, target: { type: 'section', section: 'unknown' } } }]) {
    const { api } = setup([json(invalid), json(session), json(invalid)]);
    await assert.rejects(api.getAdminSite(), error => error.code === 'INVALID_RESPONSE');
    await api.getSession();
    await assert.rejects(api.updateSite({ menu: [] }, 7), error => error.code === 'INVALID_RESPONSE');
  }
});

test('all publication reads and mutations reject malformed fields and publication windows', async () => {
  for (const change of [{ id: '' }, { kind: 'unknown' }, { status: 'unknown' }, { imageIds: ['a', 'a'] }, { imageIds: ['..'] }, { target: { type: 'catalog', url: 'https://example.com' } }, { startsAt: 'broken' }, { startsAt: '2026-10-12T10:00:00Z', endsAt: '2026-10-11T10:00:00Z' }, { version: 0 }]) {
    const invalid = { ...publication, ...change };
    const { api } = setup([json(invalid), json(page([invalid])), json(session), json(invalid, 201), json(invalid)]);
    await assert.rejects(api.getPublication('offer-1'), error => error.code === 'INVALID_RESPONSE');
    await assert.rejects(api.getPublications(), error => error.code === 'INVALID_RESPONSE');
    await api.getSession();
    await assert.rejects(api.createPublication(input), error => error.code === 'INVALID_RESPONSE');
    await assert.rejects(api.updatePublication('offer-1', { status: 'published' }, 1), error => error.code === 'INVALID_RESPONSE');
  }
});

test('media reads and uploads reject untrusted preview paths or malformed ready metadata', async () => {
  for (const change of [
    { path: 'blob:local' }, { path: 'data:image/png;base64,x' }, { path: 'https://example.com/pixel' }, { path: '//example.com/pixel' },
    { path: '/api/admin/media/../private/file' }, { path: '/api/media/image-1' }, { state: 'pending' }, { mimeType: 'image/svg+xml' },
    { width: 4097 }, { height: 0 }, { sizeBytes: 0 }, { sizeBytes: 5242881 }, { alt: 'a'.repeat(241) }, { createdAt: 'broken' },
  ]) {
    const invalid = { ...media, ...change };
    const { api } = setup([json(page([invalid])), json(session), json(invalid, 201)]);
    await assert.rejects(api.getMedia(), error => error.code === 'INVALID_RESPONSE');
    await api.getSession();
    await assert.rejects(api.uploadMedia(image(), ''), error => error.code === 'INVALID_RESPONSE');
  }
});

test('content and media pages reject inconsistent totals offsets limits and list shapes', async () => {
  for (const [method, record] of [['getPublications', publication], ['getMedia', media]]) {
    for (const change of [{ items: null }, { total: 0 }, { total: -1 }, { total: 1.5 }, { offset: 1 }, { limit: 25 }]) {
      const { api } = setup([json(page([record], change))]);
      await assert.rejects(api[method](), error => error.code === 'INVALID_RESPONSE');
    }
  }
});

test('content 401 clears CSRF and a version conflict preserves its details without automatic GET or retry', async () => {
  const { api, calls } = setup([json(session), json({ code: 'VERSION_CONFLICT', message: 'Материал изменился' }, 412), json({ code: 'SESSION_EXPIRED', message: 'Войдите снова' }, 401)]);
  await api.getSession();
  await assert.rejects(api.updatePublication('offer-1', { title: 'Изменено' }, 1), error => error.status === 412 && error.code === 'VERSION_CONFLICT' && error.message === 'Материал изменился');
  assert.equal(calls.length, 2);
  await assert.rejects(api.getAdminSite(), error => error.status === 401);
  await assert.rejects(api.uploadMedia(image(), ''), error => error.code === 'SESSION_REQUIRED');
  assert.equal(calls.length, 3);
});

test('failed upload sends one request and retains safe JSON or generic HTML errors', async () => {
  for (const response of [json({ code: 'UNSUPPORTED_MEDIA_TYPE', message: 'Формат не поддерживается' }, 415), new Response('<html>private disk path</html>', { status: 500, headers: { 'Content-Type': 'text/html' } }), new TypeError('Network failed')]) {
    const { api, calls } = setup([json(session), response]);
    await api.getSession();
    await assert.rejects(api.uploadMedia(image(), ''), error => !error.message.includes('private disk path'));
    assert.equal(calls.length, 2);
  }
});

test('content responses still require valid JSON MIME and never fall back to local data', async () => {
  for (const response of [new Response('<html>fallback</html>', { headers: { 'Content-Type': 'text/html' } }), new Response('{}', { headers: { 'Content-Type': 'image/png' } }), new Response('{broken', { headers: { 'Content-Type': 'application/json' } }), new Response(null, { status: 204 })]) {
    const { api } = setup([response]);
    await assert.rejects(api.getAdminSite(), error => error.code === 'INVALID_RESPONSE');
  }
});

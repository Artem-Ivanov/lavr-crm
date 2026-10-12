import test from 'node:test';
import assert from 'node:assert/strict';
import { createDemoContentApi } from '../src/data/demo-content-api.js';
import { createDemoApi } from '../src/data/demo-api.js';
import { initialCatalog } from '../src/catalog-admin.js';
import { isSiteConfiguration, isPublication } from '../src/content.js';

const KEY = 'lavr.site.v1';
class Storage {
  values = new Map();
  failRead = false;
  failWrite = false;
  getItem(key) { if (this.failRead) throw new Error('unavailable'); return this.values.get(key) ?? null; }
  setItem(key, value) { if (this.failWrite) throw new Error('quota'); this.values.set(key, String(value)); }
}
const publication = () => ({ kind: 'hot', title: 'Новинка', description: 'Описание', imageIds: [], target: { type: 'catalog' }, buttonText: 'Открыть', position: 4 });
function setup() {
  const localStorage = new Storage();
  const catalog = initialCatalog();
  const records = [];
  const mediaStore = {
    async list() { return structuredClone(records); },
    async save(file, alt) {
      const media = { id: `uploaded-${records.length}`, path: `blob:demo-${records.length}`, alt, width: 120, height: 80, mimeType: file.type, sizeBytes: file.size, state: 'ready', createdAt: '2026-10-11T12:00:00Z' };
      records.push(media);
      return structuredClone(media);
    },
  };
  const options = { localStorage, getCatalog: () => catalog, mediaStore, now: () => new Date('2026-10-11T12:00:00Z') };
  return { api: createDemoContentApi(options), options, localStorage, catalog, mediaStore, records };
}

test('demo editor starts with valid config and published fixture records without writing storage', async () => {
  const { api, localStorage } = setup();
  const site = await api.getAdminSite();
  assert.equal(isSiteConfiguration(site), true);
  assert.equal(site.version, 1);
  assert.equal(site.hero.imageId, 'demo-hero');
  assert.deepEqual(site.menu.map(item => item.target.categoryId), ['oils', 'filters', 'brakes']);
  assert.equal((await api.getSite()).settings.phone, '');
  const publications = await api.getPublications();
  assert.equal(publications.total, 3);
  assert.equal(publications.items.every(isPublication), true);
  assert.equal(publications.items.every(item => item.status === 'published' && item.startsAt === null), true);
  assert.equal(localStorage.getItem(KEY), null);
});

test('site edits persist before returning and an explicitly empty menu stays empty', async () => {
  const { api, localStorage } = setup();
  const before = await api.getAdminSite();
  const hero = { ...before.hero, title: 'Новый заголовок', imageId: null };
  const next = await api.updateSite({ hero, menu: [] }, before.version);
  assert.equal(next.version, 2);
  assert.equal(JSON.parse(localStorage.getItem(KEY)).site.hero.title, 'Новый заголовок');
  const site = await api.getSite();
  assert.equal(site.hero.title, 'Новый заголовок');
  assert.equal(site.hero.image, null);
  assert.equal(site.hero.kicker, 'ДЕТАЛИ, КОТОРЫЕ ВАЖНЫ');
  assert.equal(site.menuConfigured, true);
  assert.deepEqual(site.menu, []);
});

test('failed site persistence leaves the previous snapshot and visible content intact', async () => {
  const { api, localStorage } = setup();
  const site = await api.getAdminSite();
  await api.updateSite({ hero: { ...site.hero, title: 'Сохранено' } }, site.version);
  const raw = localStorage.getItem(KEY);
  localStorage.failWrite = true;
  await assert.rejects(api.updateSite({ hero: { ...site.hero, title: 'Не сохранено' } }, 2), /сохранить/i);
  assert.equal(localStorage.getItem(KEY), raw);
  assert.equal((await api.getSite()).hero.title, 'Сохранено');
});

test('corrupt and unavailable content storage is preserved and all mutations refuse to overwrite it', async () => {
  const { api, localStorage } = setup();
  for (const damaged of ['', 'null', '{}', '{"site":', '{"site":{},"publications":[]}']) {
    localStorage.setItem(KEY, damaged);
    assert.equal((await api.getAdminSite()).readOnly, true);
    assert.equal((await api.getPublications()).readOnly, true);
    assert.equal((await api.getSite()).hero.title, 'Всё для вашего\nавтомобиля.\nВ одном месте.');
    await assert.rejects(api.updateSite({ menu: [] }, 1), /поврежд|недоступ/i);
    await assert.rejects(api.createPublication(publication()), /поврежд|недоступ/i);
    await assert.rejects(api.updatePublication('demo-filters', { title: 'Новое' }, 1), /поврежд|недоступ/i);
    assert.equal(localStorage.getItem(KEY), damaged);
  }
  localStorage.failRead = true;
  assert.equal((await api.getAdminSite()).readOnly, true);
  await assert.rejects(api.createPublication(publication()), /поврежд|недоступ/i);
});

test('sibling adapters enforce record versions and preserve changes to other records', async () => {
  const { api, options, localStorage } = setup();
  const sibling = createDemoContentApi(options);
  const oldSite = await api.getAdminSite();
  await sibling.updateSite({ hero: { ...oldSite.hero, title: 'Из другой вкладки' } }, oldSite.version);
  await assert.rejects(api.updateSite({ menu: [] }, oldSite.version), error => error.status === 412 && error.code === 'VERSION_CONFLICT');
  const created = await api.createPublication(publication());
  assert.equal((await sibling.getSite()).hero.title, 'Из другой вкладки');
  const updated = await sibling.updatePublication(created.id, { status: 'published' }, created.version);
  await assert.rejects(api.updatePublication(created.id, { title: 'Старые данные' }, created.version), error => error.status === 412);
  assert.equal(JSON.parse(localStorage.getItem(KEY)).publications.find(item => item.id === created.id).version, updated.version);
  await assert.rejects(api.updateSite({ menu: [] }, 0), /верси/i);
});

test('concurrent changes to one record allow one save and reject the stale save', async () => {
  const { api, options } = setup();
  const sibling = createDemoContentApi(options);
  const created = await api.createPublication(publication());
  const results = await Promise.allSettled([
    api.updatePublication(created.id, { title: 'Первая правка' }, 1),
    sibling.updatePublication(created.id, { title: 'Вторая правка' }, 1),
  ]);
  assert.equal(results.filter(result => result.status === 'fulfilled').length, 1);
  assert.equal(results.find(result => result.status === 'rejected').reason.status, 412);
  assert.equal((await api.getPublication(created.id)).version, 2);
});

test('publication write failures never add a draft or change an existing saved record', async () => {
  const { api, localStorage } = setup();
  localStorage.failWrite = true;
  await assert.rejects(api.createPublication(publication()), /сохранить/i);
  assert.equal(localStorage.getItem(KEY), null);
  assert.equal((await api.getPublications()).total, 3);
  localStorage.failWrite = false;
  const created = await api.createPublication(publication());
  const raw = localStorage.getItem(KEY);
  localStorage.failWrite = true;
  await assert.rejects(api.updatePublication(created.id, { status: 'published' }, created.version), /сохранить/i);
  assert.equal(localStorage.getItem(KEY), raw);
  assert.equal((await api.getPublication(created.id)).status, 'draft');
});

test('publication drafts, published records and archived records project to the storefront correctly', async () => {
  const { api } = setup();
  const created = await api.createPublication(publication());
  assert.equal(created.status, 'draft');
  assert.equal((await api.getSite()).publications.some(item => item.id === created.id), false);
  const published = await api.updatePublication(created.id, { status: 'published' }, 1);
  assert.equal((await api.getSite()).publications.find(item => item.id === created.id).title, 'Новинка');
  assert.equal((await api.getPublications({ kind: 'hot', status: 'published' })).total, 1);
  const archived = await api.updatePublication(created.id, { status: 'archived' }, published.version);
  assert.equal(archived.version, 3);
  assert.equal((await api.getSite()).publications.some(item => item.id === created.id), false);
  assert.equal((await api.getPublication(created.id)).status, 'archived');
  await assert.rejects(api.getPublication('missing'), /не найдена/i);
});

test('schedule starts are inclusive and ends are exclusive, including merged partial updates', async () => {
  const { api } = setup();
  const future = await api.createPublication({ ...publication(), status: 'published', startsAt: '2026-10-11T12:00:01Z' });
  const active = await api.createPublication({ ...publication(), status: 'published', startsAt: '2026-10-11T12:00:00Z', endsAt: '2026-10-11T12:00:01Z' });
  const ended = await api.createPublication({ ...publication(), status: 'published', endsAt: '2026-10-11T12:00:00Z' });
  const ids = (await api.getSite()).publications.map(item => item.id);
  assert.equal(ids.includes(future.id), false);
  assert.equal(ids.includes(active.id), true);
  assert.equal(ids.includes(ended.id), false);
  await assert.rejects(api.updatePublication(active.id, { endsAt: '2026-10-11T11:00:00Z' }, active.version), /публикац/i);
  assert.equal((await api.getPublication(active.id)).endsAt, '2026-10-11T12:00:01Z');
});

test('public projection rechecks current target visibility and keeps only visible sorted menu items', async () => {
  const { api, catalog } = setup();
  const config = await api.getAdminSite();
  await api.updateSite({
    hero: { ...config.hero, target: { type: 'product', productId: 'filter-mann-oil' } },
    menu: [
      { label: 'Скрытый', target: { type: 'catalog' }, position: 0, isVisible: false },
      { label: 'Второй', target: { type: 'catalog' }, position: 2, isVisible: true },
      { label: 'Первый', target: { type: 'category', categoryId: 'filters' }, position: 1, isVisible: true },
    ],
  }, config.version);
  assert.deepEqual((await api.getSite()).menu.map(item => item.label), ['Первый', 'Второй']);
  catalog.categories.find(item => item.id === 'filters').isVisible = false;
  const site = await api.getSite();
  assert.equal(site.hero.target, null);
  assert.deepEqual(site.menu.map(item => item.label), ['Второй']);
  assert.equal(site.publications.some(item => item.id === 'demo-filters'), false);
});

test('invalid media and unavailable targets cannot be saved, and uploaded media resolves in public content', async () => {
  const { api, localStorage } = setup();
  await assert.rejects(api.createPublication({ ...publication(), imageIds: ['missing'] }), /изображен/i);
  await assert.rejects(api.createPublication({ ...publication(), target: { type: 'category', categoryId: 'missing' } }), /цел|категор/i);
  assert.equal(localStorage.getItem(KEY), null);
  const uploaded = await api.uploadMedia(new Blob(['photo'], { type: 'image/png' }), 'Подпись');
  const mediaPage = await api.getMedia({ offset: 0, limit: 1 });
  assert.equal(mediaPage.total, 7);
  assert.equal(mediaPage.items[0].id, uploaded.id);
  const created = await api.createPublication({ ...publication(), imageIds: [uploaded.id], status: 'published' });
  const item = (await api.getSite()).publications.find(item => item.id === created.id);
  assert.deepEqual(item.images, [{ id: uploaded.id, path: 'blob:demo-0', alt: 'Подпись', width: 120, height: 80 }]);
  const config = await api.getAdminSite();
  await api.updateSite({ hero: { ...config.hero, imageId: uploaded.id } }, config.version);
  assert.equal((await api.getSite()).hero.image.path, 'blob:demo-0');
});

test('editing a seed publication kind drops incompatible fixture style', async () => {
  const { api } = setup();
  const original = await api.getPublication('demo-filters');
  await api.updatePublication(original.id, { kind: 'announcement' }, original.version);
  const projected = (await api.getSite()).publications.find(item => item.id === original.id);
  assert.equal(projected.kind, 'announcement');
  assert.equal(projected.style, undefined);
});

test('demo adapter exposes durable content edits through its existing factory', async () => {
  const localStorage = new Storage();
  const api = createDemoApi({ localStorage, sessionStorage: new Storage() });
  const config = await api.getAdminSite();
  await api.updateSite({ menu: [] }, config.version);
  assert.equal((await createDemoApi({ localStorage, sessionStorage: new Storage() }).getSite()).menuConfigured, true);
});

test('demo content pagination and filters reject unsupported values', async () => {
  const { api } = setup();
  for (const paging of [{ offset: -1 }, { limit: 0 }, { limit: 101 }, { limit: 1.5 }]) {
    await assert.rejects(api.getPublications({}, paging), /пагинац/i);
    await assert.rejects(api.getMedia(paging), /пагинац/i);
  }
  await assert.rejects(api.getPublications({ status: 'deleted' }), /фильтр/i);
  await assert.rejects(api.getPublications({ kind: 'banner' }), /фильтр/i);
  const result = await api.getPublications({}, { offset: 1, limit: 1 });
  assert.equal(result.total, 3);
  assert.equal(result.items[0].id, 'demo-oils');
});

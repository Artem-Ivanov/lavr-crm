import { initialCatalog } from '../catalog-admin.js';
import { isSiteConfiguration, isPublication, validateSitePatch, validatePublicationInput, validateMediaUpload } from '../content.js';
import { demoSite } from './demo-site.js';
import { DEMO_MEDIA, createDemoMediaStore } from './demo-media.js';

const KEY = 'lavr.site.v1';
const READ_ERROR = 'Сохранённое содержимое сайта повреждено или недоступно. Изменения не применены, чтобы сохранить исходные данные.';
const WRITE_ERROR = 'Не удалось сохранить содержимое сайта в браузере. Изменения не применены; разрешите хранилище и попробуйте снова.';
const settingsKeys = ['storeName', 'phone', 'address', 'hours', 'categoriesTitle', 'promotionsTitle', 'catalogTitle'];
const byPosition = (a, b) => a.position - b.position;
const page = (items, { offset = 0, limit = 24 } = {}) => {
  if (!Number.isSafeInteger(offset) || offset < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 100) {
    throw new Error('Некорректные параметры пагинации.');
  }
  return { items: structuredClone(items.slice(offset, offset + limit)), total: items.length, offset, limit };
};

function seed(catalog) {
  const { hero, settings, publications } = demoSite;
  return {
    site: {
      hero: { title: hero.title, description: hero.description, imageId: hero.image.id, buttonText: hero.buttonText, target: structuredClone(hero.target) },
      settings: {
        ...Object.fromEntries(settingsKeys.map(key => [key, settings[key]])),
        phone: '+79990000000', address: 'Адрес уточняется у менеджера', hours: 'Время работы уточняется у менеджера',
      },
      menu: catalog.categories.filter(category => category.isVisible !== false).slice(0, 3).map((category, position) => ({
        label: category.name, target: { type: 'category', categoryId: category.id }, position, isVisible: true,
      })),
      version: 1,
    },
    publications: publications.map(({ id, kind, title, description, images, target, buttonText, position }) => ({
      id, kind, title, description, imageIds: images.map(image => image.id), target: structuredClone(target), buttonText, position,
      status: 'published', startsAt: null, endsAt: null, version: 1,
    })),
  };
}

function targetAvailable(target, catalog) {
  if (target.type === 'catalog' || target.type === 'section') return true;
  const categoryVisible = categoryId => catalog.categories.some(category => category.id === categoryId && category.isVisible !== false);
  if (target.type === 'category') return categoryVisible(target.categoryId);
  const product = catalog.products.find(product => product.id === target.productId);
  return Boolean(product && product.isActive !== false && product.isVisible !== false && categoryVisible(product.category));
}

function checkVersion(current, requested) {
  if (!Number.isSafeInteger(requested) || requested < 1) throw new Error('Некорректная версия содержимого сайта.');
  if (current !== requested) {
    const error = new Error('Содержимое изменилось в другой вкладке. Обновите данные и повторите правку.');
    error.status = 412;
    error.code = 'VERSION_CONFLICT';
    throw error;
  }
}

// shortcut: редактор хранит содержимое в этом браузере, использовать HTTP API для общей рабочей витрины.
export function createDemoContentApi({
  localStorage = globalThis.localStorage, getCatalog = initialCatalog, mediaStore = createDemoMediaStore(), now = () => new Date(),
} = {}) {
  async function publicCatalog() {
    try { return await getCatalog(); }
    catch { return initialCatalog(); }
  }
  function read(catalog, strict = false) {
    try {
      const raw = localStorage.getItem(KEY);
      if (raw === null) return { ...seed(catalog), saved: false, readOnly: false, issue: '' };
      const snapshot = JSON.parse(raw);
      if (!snapshot || Object.keys(snapshot).length !== 2 || !isSiteConfiguration(snapshot.site)
        || !Array.isArray(snapshot.publications) || !snapshot.publications.every(isPublication)
        || new Set(snapshot.publications.map(publication => publication.id)).size !== snapshot.publications.length) throw new Error(READ_ERROR);
      return { ...snapshot, saved: true, readOnly: false, issue: '' };
    } catch {
      if (strict) throw new Error(READ_ERROR);
      return { ...seed(catalog), saved: false, readOnly: true, issue: READ_ERROR };
    }
  }
  function save(snapshot) {
    try { localStorage.setItem(KEY, JSON.stringify({ site: snapshot.site, publications: snapshot.publications })); }
    catch { throw new Error(WRITE_ERROR); }
  }
  async function references() {
    const [catalog, uploaded] = await Promise.all([getCatalog(), mediaStore.list()]);
    return { catalog, media: [...DEMO_MEDIA, ...uploaded] };
  }
  function checkReferences(value, { catalog, media }) {
    if (!targetAvailable(value.target, catalog)) throw new Error('Выберите доступную внутреннюю цель: существующую категорию или товар.');
    const ids = Object.hasOwn(value, 'imageId') ? value.imageId === null ? [] : [value.imageId] : value.imageIds;
    if (ids?.some(id => !media.some(image => image.id === id && image.state === 'ready'))) {
      throw new Error('Выберите готовое изображение из библиотеки.');
    }
  }

  return {
    async getSite() {
      const catalog = await publicCatalog();
      const snapshot = read(catalog);
      if (!snapshot.saved) {
        const site = structuredClone(demoSite);
        if (!targetAvailable(site.hero.target, catalog)) site.hero.target = null;
        site.publications = site.publications.filter(publication => targetAvailable(publication.target, catalog));
        return site;
      }
      const media = [...DEMO_MEDIA, ...await mediaStore.list()];
      const image = id => {
        const item = media.find(item => item.id === id && item.state === 'ready');
        return item ? { id: item.id, path: item.path, alt: item.alt, width: item.width, height: item.height } : null;
      };
      const { hero, settings, menu } = snapshot.site;
      const time = now().getTime();
      return {
        hero: { ...structuredClone(demoSite.hero), title: hero.title, description: hero.description, buttonText: hero.buttonText,
          image: image(hero.imageId), target: targetAvailable(hero.target, catalog) ? structuredClone(hero.target) : null },
        settings: { ...demoSite.settings, ...settings },
        menu: structuredClone(menu.filter(item => item.isVisible && targetAvailable(item.target, catalog)).sort(byPosition)),
        menuConfigured: true,
        publications: snapshot.publications.filter(publication => publication.status === 'published'
          && (publication.startsAt === null || Date.parse(publication.startsAt) <= time)
          && (publication.endsAt === null || time < Date.parse(publication.endsAt))
          && targetAvailable(publication.target, catalog)).sort(byPosition).map(publication => {
          const fixture = demoSite.publications.find(item => item.id === publication.id && item.kind === publication.kind);
          const { id, kind, title, description, target, buttonText, position, imageIds } = publication;
          return { ...(fixture ? structuredClone(fixture) : {}), id, kind, title, description, target: structuredClone(target), buttonText, position,
            images: imageIds.map(image).filter(Boolean) };
        }),
      };
    },
    async getAdminSite() {
      const snapshot = read(await publicCatalog());
      return structuredClone(snapshot.readOnly ? { ...snapshot.site, readOnly: true, issue: snapshot.issue } : snapshot.site);
    },
    async updateSite(input, requestedVersion) {
      const patch = validateSitePatch(input);
      const refs = await references();
      if (patch.hero) checkReferences(patch.hero, refs);
      for (const item of patch.menu || []) checkReferences(item, refs);
      const snapshot = read(refs.catalog, true);
      checkVersion(snapshot.site.version, requestedVersion);
      const site = { ...snapshot.site, ...patch, version: snapshot.site.version + 1 };
      save({ ...snapshot, site });
      return structuredClone(site);
    },
    async getPublications({ kind = '', status = '' } = {}, paging) {
      if (kind && !['offer', 'announcement', 'hot'].includes(kind) || status && !['draft', 'published', 'archived'].includes(status)) {
        throw new Error('Некорректный фильтр публикаций.');
      }
      const snapshot = read(await publicCatalog());
      return { ...page(snapshot.publications.filter(item => (!kind || item.kind === kind) && (!status || item.status === status)).sort(byPosition), paging),
        readOnly: snapshot.readOnly, issue: snapshot.issue };
    },
    async getPublication(id) {
      const publication = read(await publicCatalog(), true).publications.find(item => item.id === id);
      if (!publication) throw new Error('Публикация не найдена.');
      return structuredClone(publication);
    },
    async createPublication(input) {
      const fields = validatePublicationInput(input);
      const refs = await references();
      checkReferences(fields, refs);
      const snapshot = read(refs.catalog, true);
      const publication = { id: crypto.randomUUID(), ...fields, version: 1 };
      save({ ...snapshot, publications: [...snapshot.publications, publication] });
      return structuredClone(publication);
    },
    async updatePublication(id, input, requestedVersion) {
      const patch = validatePublicationInput(input, { partial: true });
      const refs = await references();
      const snapshot = read(refs.catalog, true);
      const current = snapshot.publications.find(item => item.id === id);
      if (!current) throw new Error('Публикация не найдена.');
      checkVersion(current.version, requestedVersion);
      const publication = { id, ...validatePublicationInput({ ...current, ...patch }), version: current.version + 1 };
      checkReferences(publication, refs);
      save({ ...snapshot, publications: snapshot.publications.map(item => item.id === id ? publication : item) });
      return structuredClone(publication);
    },
    async getMedia(paging) {
      return page([...DEMO_MEDIA, ...await mediaStore.list()].sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt)), paging);
    },
    async uploadMedia(file, alt = '') {
      validateMediaUpload(file, alt);
      read(await publicCatalog(), true);
      return mediaStore.save(file, alt);
    },
  };
}

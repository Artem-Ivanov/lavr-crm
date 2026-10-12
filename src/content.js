const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = (value, min, max) => typeof value === 'string' && value.length >= min && value.length <= max && (min === 0 || Boolean(value.trim()));
const integer = (value, min, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(value) && value >= min && value <= max;
const id = value => text(value, 1, 128) && !/[\u0000-\u001f\u007f]/.test(value) && value !== '.' && value !== '..';
const exact = (value, keys) => object(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value, key));
const pick = (value, keys) => Object.fromEntries(keys.filter(key => Object.hasOwn(value, key)).map(key => [key, value[key]]));
const list = (value, check, max) => Array.isArray(value) && value.length <= max && value.every(check);
const position = value => integer(value, 0, 1000000);
const version = value => integer(value, 1);
const kinds = ['offer', 'announcement', 'hot'];
const statuses = ['draft', 'published', 'archived'];
const imageIds = value => list(value, id, 8) && new Set(value).size === value.length;

function timestamp(value) {
  if (typeof value !== 'string') return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$/i.exec(value);
  if (!match || !Number.isFinite(Date.parse(value))) return false;
  const [, year, month, day, hour, minute, second, , offsetHour = '0', offsetMinute = '0'] = match;
  const leap = Number(year) % 4 === 0 && (Number(year) % 100 !== 0 || Number(year) % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return Number(month) >= 1 && Number(month) <= 12 && Number(day) >= 1 && Number(day) <= days[Number(month) - 1]
    && Number(hour) < 24 && Number(minute) < 60 && Number(second) < 60 && Number(offsetHour) < 24 && Number(offsetMinute) < 60;
}

function target(value) {
  if (!object(value)) return false;
  if (value.type === 'catalog') return exact(value, ['type']);
  if (value.type === 'category') return exact(value, ['type', 'categoryId']) && id(value.categoryId);
  if (value.type === 'product') return exact(value, ['type', 'productId']) && id(value.productId);
  return value.type === 'section' && exact(value, ['type', 'section']) && ['offers', 'announcements', 'hot'].includes(value.section);
}

const heroKeys = ['title', 'description', 'imageId', 'buttonText', 'target'];
const settingsKeys = ['storeName', 'phone', 'address', 'hours', 'categoriesTitle', 'promotionsTitle', 'catalogTitle'];
const menuKeys = ['label', 'target', 'position', 'isVisible'];
const hero = value => exact(value, heroKeys) && text(value.title, 1, 240) && text(value.description, 0, 2000)
  && (value.imageId === null || id(value.imageId)) && text(value.buttonText, 1, 80) && target(value.target);
const settings = value => exact(value, settingsKeys) && text(value.storeName, 1, 120) && text(value.phone, 10, 30)
  && text(value.address, 1, 500) && text(value.hours, 1, 240)
  && ['categoriesTitle', 'promotionsTitle', 'catalogTitle'].every(key => text(value[key], 1, 120));
const menuItem = value => exact(value, menuKeys) && text(value.label, 1, 80) && target(value.target)
  && position(value.position) && typeof value.isVisible === 'boolean';

export const isSiteConfiguration = value => exact(value, ['hero', 'settings', 'menu', 'version'])
  && hero(value.hero) && settings(value.settings) && list(value.menu, menuItem, 12) && version(value.version);

const publicationChecks = {
  kind: value => kinds.includes(value), title: value => text(value, 1, 240), description: value => text(value, 0, 4000),
  imageIds, target, buttonText: value => text(value, 1, 80), position, status: value => statuses.includes(value),
  startsAt: value => value === null || timestamp(value), endsAt: value => value === null || timestamp(value),
};
const publicationKeys = Object.keys(publicationChecks);
const orderedDates = value => value.startsAt == null || value.endsAt == null || Date.parse(value.startsAt) < Date.parse(value.endsAt);

export const isPublication = value => exact(value, ['id', ...publicationKeys, 'version']) && id(value.id) && version(value.version)
  && publicationKeys.every(key => publicationChecks[key](value[key])) && orderedDates(value);

export const isAdminMedia = value => exact(value, ['id', 'path', 'alt', 'width', 'height', 'mimeType', 'sizeBytes', 'state', 'createdAt'])
  && id(value.id) && typeof value.path === 'string' && /^\/api\/admin\/media\/[A-Za-z0-9_-]+\/file$/.test(value.path)
  && text(value.alt, 0, 240) && integer(value.width, 1, 4096) && integer(value.height, 1, 4096)
  && ['image/jpeg', 'image/png', 'image/webp'].includes(value.mimeType) && integer(value.sizeBytes, 1, 5242880)
  && value.state === 'ready' && timestamp(value.createdAt);

export function validateSitePatch(input) {
  const error = () => { throw new Error('Проверьте данные сайта: заполните обязательные поля, внутренние цели и меню.'); };
  if (!object(input)) error();
  const patch = pick(input, ['hero', 'settings', 'menu']);
  if (!Object.keys(patch).length) error();
  for (const [key, keys, check] of [['hero', heroKeys, hero], ['settings', settingsKeys, settings]]) {
    if (!Object.hasOwn(patch, key)) continue;
    if (!object(patch[key])) error();
    patch[key] = pick(patch[key], keys);
    if (!check(patch[key])) error();
  }
  if (Object.hasOwn(patch, 'menu')) {
    if (!Array.isArray(patch.menu) || patch.menu.some(item => !object(item))) error();
    patch.menu = patch.menu.map(item => pick(item, menuKeys));
    if (!list(patch.menu, menuItem, 12)) error();
  }
  return structuredClone(patch);
}

export function validatePublicationInput(input, { partial = false } = {}) {
  const error = () => { throw new Error('Проверьте данные публикации: обязательные поля, изображения, внутреннюю цель и период показа.'); };
  if (!object(input)) error();
  const patch = pick(input, publicationKeys);
  if (!Object.keys(patch).length) error();
  if (!partial) {
    for (const key of ['kind', 'title', 'description', 'imageIds', 'target', 'buttonText', 'position']) {
      if (!Object.hasOwn(patch, key)) error();
    }
    for (const [key, defaultValue] of [['status', 'draft'], ['startsAt', null], ['endsAt', null]]) {
      if (!Object.hasOwn(patch, key)) patch[key] = defaultValue;
    }
  }
  if (Object.entries(patch).some(([key, value]) => !publicationChecks[key](value)) || !orderedDates(patch)) error();
  return structuredClone(patch);
}

export function validateMediaUpload(file, alt = '') {
  if (!object(file) || !integer(file.size, 1, 5242880) || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
    throw new Error('Выберите изображение JPG, PNG или WebP размером до 5 МиБ.');
  }
  if (!text(alt, 0, 240)) throw new Error('Альтернативный текст изображения: максимум 240 символов.');
}

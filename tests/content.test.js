import test from 'node:test';
import assert from 'node:assert/strict';
import {
  isSiteConfiguration, isPublication, isAdminMedia,
  validateSitePatch, validatePublicationInput, validateMediaUpload,
} from '../src/content.js';

const hero = () => ({ title: 'Заголовок', description: '', imageId: null, buttonText: 'Каталог', target: { type: 'catalog' } });
const settings = () => ({ storeName: 'LAVR', phone: '+79991234567', address: 'Москва', hours: '9–18', categoriesTitle: 'Категории', promotionsTitle: 'Акции', catalogTitle: 'Каталог' });
const publication = () => ({ kind: 'offer', title: 'Акция', description: '', imageIds: [], target: { type: 'section', section: 'offers' }, buttonText: 'Открыть', position: 0 });
const media = () => ({ id: 'photo', path: '/api/admin/media/photo/file', alt: '', width: 100, height: 80, mimeType: 'image/jpeg', sizeBytes: 42, state: 'ready', createdAt: '2026-10-11T12:00:00Z' });

test('site patches whitelist metadata, preserve plain text, and require complete sections', () => {
  const input = { hero: { ...hero(), title: '<script>alert(1)</script>', ignored: true }, settings: settings(), menu: [], version: 8 };
  assert.deepEqual(validateSitePatch(input), { hero: { ...hero(), title: '<script>alert(1)</script>' }, settings: settings(), menu: [] });
  for (const invalid of [{}, { version: 8 }, { hero: () => {} }, { hero: { title: 'Только заголовок' } }, { settings: { ...settings(), hours: ' ' } }, { menu: [{ label: ' ', target: { type: 'catalog' }, position: 0, isVisible: true }] }]) {
    assert.throws(() => validateSitePatch(invalid), /данные|поле|меню|сайта/i);
  }
  assert.throws(() => validateSitePatch({ hero: { ...hero(), target: { type: 'catalog', url: 'javascript:alert(1)' } } }), /сайта|цел/i);
  assert.throws(() => validateSitePatch({ menu: Array.from({ length: 13 }, () => ({ label: 'Каталог', target: { type: 'catalog' }, position: 0, isVisible: true })) }), /меню|сайта/i);
  const original = { hero: hero() };
  const result = validateSitePatch(original);
  result.hero.target.type = 'product';
  assert.equal(original.hero.target.type, 'catalog');
});

test('publication inputs enforce limits, unique image ids and create defaults', () => {
  assert.deepEqual(validatePublicationInput({ ...publication(), id: 'injected', version: 100 }), { ...publication(), status: 'draft', startsAt: null, endsAt: null });
  assert.deepEqual(validatePublicationInput({ title: 'Новое' }, { partial: true }), { title: 'Новое' });
  assert.doesNotThrow(() => validatePublicationInput({ ...publication(), position: 1000000, imageIds: Array.from({ length: 8 }, (_, i) => `image-${i}`) }));
  for (const invalid of [{ ...publication(), title: ' ' }, { ...publication(), title: 'x'.repeat(241) }, { ...publication(), position: 1000001 }, { ...publication(), position: 0.5 }, { ...publication(), imageIds: ['same', 'same'] }, { ...publication(), imageIds: [''] }, { ...publication(), imageIds: Array(9).fill('x') }, { ...publication(), kind: 'banner' }, { ...publication(), status: 'deleted' }, { ...publication(), target: { type: 'url', url: 'https://example.com' } }]) {
    assert.throws(() => validatePublicationInput(invalid), /публикац/i);
  }
  assert.throws(() => validatePublicationInput({ title: 'Неполная' }), /публикац/i);
  assert.throws(() => validatePublicationInput({ id: 'x' }, { partial: true }), /публикац/i);
});

test('publication date bounds require real timestamps and an increasing interval', () => {
  for (const bounds of [
    { startsAt: '2026-10-11' }, { startsAt: '2026-02-30T00:00:00Z' }, { startsAt: 'bad' },
    { startsAt: '2026-10-11T12:00:00Z', endsAt: '2026-10-11T12:00:00Z' },
    { startsAt: '2026-10-11T12:00:00Z', endsAt: '2026-10-11T11:00:00Z' },
  ]) assert.throws(() => validatePublicationInput(bounds, { partial: true }), /публикац/i);
  assert.doesNotThrow(() => validatePublicationInput({ startsAt: '2026-10-11T12:00:00+03:00', endsAt: '2026-10-11T12:00:01+03:00' }, { partial: true }));
  assert.deepEqual(validatePublicationInput({ startsAt: null }, { partial: true }), { startsAt: null });
});

test('admin responses validate full schemas and reject unsafe media or targets', () => {
  const config = { hero: hero(), settings: settings(), menu: [], version: 1 };
  const record = { ...publication(), id: 'offer', status: 'draft', startsAt: null, endsAt: null, version: 1 };
  assert.equal(isSiteConfiguration(config), true);
  assert.equal(isPublication(record), true);
  assert.equal(isAdminMedia(media()), true);
  assert.equal(isSiteConfiguration({ ...config, version: 0 }), false);
  assert.equal(isSiteConfiguration({ ...config, extra: true }), false);
  assert.equal(isPublication({ ...record, imageIds: ['photo', 'photo'] }), false);
  assert.equal(isPublication({ ...record, startsAt: 'bad' }), false);
  assert.equal(isPublication({ ...record, target: { type: 'section', section: 'elsewhere' } }), false);
  for (const patch of [{ path: 'https://example.com/file' }, { path: '/api/admin/media/../file' }, { path: '/api/media/photo' }, { state: 'pending' }, { width: 4097 }, { createdAt: 'yesterday' }, { extra: true }]) {
    assert.equal(isAdminMedia({ ...media(), ...patch }), false);
  }
});

test('image upload validation rejects invalid types and limits before transport', () => {
  const file = new Blob(['image'], { type: 'image/png' });
  assert.doesNotThrow(() => validateMediaUpload(file, '<b>обычный текст</b>'));
  assert.doesNotThrow(() => validateMediaUpload({ size: 5242880, type: 'image/webp' }, 'x'.repeat(240)));
  for (const [input, alt] of [[null, ''], [{ size: 0, type: 'image/jpeg' }, ''], [{ size: 5242881, type: 'image/jpeg' }, ''], [{ size: 1, type: 'image/svg+xml' }, ''], [{ size: 1.5, type: 'image/png' }, ''], [file, 'x'.repeat(241)], [file, null]]) {
    assert.throws(() => validateMediaUpload(input, alt), /изображен|файл|текст/i);
  }
});

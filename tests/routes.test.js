import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { parseRoute, routeHash, safeReturnTo, targetHash } from '../src/routes.js';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/navigation-v1.json', import.meta.url), 'utf8'));
const empty = (page = 'home', valid = true) => ({
  page, caseId: '', orderId: '', categoryId: '', productId: '', section: '', publicationId: '',
  userId: '', tab: 'site', returnTo: '', valid,
});
const fields = { case: 'caseId', order: 'orderId', category: 'categoryId', product: 'productId', publication: 'publicationId', user: 'userId' };

for (const { page, query, hash } of fixture.valid) {
  test(`shared canonical destination ${hash}`, () => {
    const expected = { ...empty(page), ...Object.fromEntries(Object.entries(query).map(([key, value]) => [fields[key] || key, value])) };
    assert.deepEqual(parseRoute(hash), expected);
    assert.equal(routeHash(expected), hash);
  });
}

test('blank home and every existing page retain complete normalized defaults', () => {
  for (const hash of ['', '#', '#home', 'home']) assert.deepEqual(parseRoute(hash), empty());
  for (const page of ['catalog', 'cart', 'crm', 'crm-catalog', 'crm-content', 'crm-feedback', 'admin-users', 'login', 'signup', 'activate', 'password-reset', 'account', 'account-orders', 'account-feedback']) {
    assert.deepEqual(parseRoute(`#${page}`), empty(page));
    assert.equal(routeHash(empty(page)), `#${page}`);
  }
  assert.deepEqual(parseRoute('#crm-content?tab=site'), empty('crm-content'));
  assert.equal(routeHash(parseRoute('#crm-content?tab=site')), '#crm-content');
});

test('legacy details and opaque IDs decode exactly once with canonical query order', () => {
  assert.equal(parseRoute('#crm?order=legacy-order').orderId, 'legacy-order');
  assert.equal(parseRoute('#crm-feedback?case=legacy-case').caseId, 'legacy-case');
  assert.equal(parseRoute('#catalog?product=%252E%252E').productId, '%2E%2E');
  assert.equal(routeHash({ page: 'catalog', productId: '%2E%2E' }), '#catalog?product=%252E%252E');
  assert.equal(routeHash(parseRoute('#catalog?product=sku%201&category=oils')), '#catalog?category=oils&product=sku+1');
  assert.equal(routeHash({ page: 'catalog', productId: ' ~*?&#% деталь' }), '#catalog?product=+%7E*%3F%26%23%25+%D0%B4%D0%B5%D1%82%D0%B0%D0%BB%D1%8C');
  for (const section of ['offers', 'announcements', 'hot']) assert.equal(routeHash({ page: 'home', section }), `#home?section=${section}`);
  assert.equal(routeHash({ page: 'crm-content', tab: 'publications', publicationId: 'p 1' }), '#crm-content?tab=publications&publication=p+1');
});

test('ID boundaries count Unicode code points and preserve whitespace IDs', () => {
  for (const id of [' ', '😀'.repeat(128), 'a'.repeat(128)]) {
    const hash = routeHash({ page: 'catalog', productId: id });
    assert.equal(parseRoute(hash).productId, id);
    assert.equal(parseRoute(hash).valid, true);
  }
  for (const id of ['😀'.repeat(129), 'a'.repeat(129), '.', '..', '\0', 'a\nb', '\x7f']) {
    assert.throws(() => routeHash({ page: 'catalog', productId: id }), TypeError);
    assert.deepEqual(parseRoute(`#catalog?category=valid&product=${encodeURIComponent(id)}`), empty('catalog', false));
  }
});


test('ill-formed Unicode never silently changes a resource ID', () => {
  for (const id of ['\ud800', '\udfff', 'part\ud800tail']) {
    assert.throws(() => routeHash({ page: 'catalog', productId: id }), TypeError);
    assert.deepEqual(parseRoute(`#catalog?product=${id}`), empty('catalog', false));
  }
  assert.equal(parseRoute('#catalog?product=%EF%BF%BD').productId, '\ufffd');
});

test('invalid details clear every detail and retain only the known page', () => {
  for (const hash of [...fixture.invalidHashes,
    '#catalog?category=ok&product=', '#catalog?product=a&product=a', '#catalog?%70roduct=a&product=b',
    '#crm?order=a&case=b', '#cart?product=x', '#home?section=', '#crm-content?tab=', '#crm-content?tab=other',
    '#crm-content?publication=x', '#crm-content?tab=publications&tab=media&publication=x',
    '#catalog?product=%FF', '#catalog?product=%', '#catalog?product=x&ignored=%ZZ',
    '#catalog?product=a\nb', '#catalog?product=%01', '#catalog?product=%7f',
  ]) {
    const page = hash.slice(1).split('?')[0];
    assert.deepEqual(parseRoute(hash), empty(page, false), hash);
  }
  for (const hash of ['#unknown?product=x', '#https://evil.test', '/catalog', '##catalog', '#catalog#home', null, undefined, {}]) {
    assert.deepEqual(parseRoute(hash), empty('home', false));
  }
});

test('unknown query fields and builder properties never serialize sensitive data', () => {
  assert.deepEqual(parseRoute('#catalog?product=part&password=secret&search=contact&unknown=a&unknown=b'), { ...empty('catalog'), productId: 'part' });
  assert.equal(routeHash({ page: 'catalog', productId: 'part', password: 'secret', search: 'contact', unknown: 'x' }), '#catalog?product=part');
  assert.equal(routeHash(parseRoute('#crm?order=a&ignored=x')), '#crm?order=a');
});

test('builder rejects invalid internal route data and unsupported combinations', () => {
  for (const route of [null, undefined, [], 'home', {}, { page: 'unknown' },
    { page: 'crm', orderId: null }, { page: 'crm', orderId: 1 }, { page: 'crm', orderId: {} },
    { page: 'home', section: 'elsewhere' }, { page: 'home', section: 'offers', publicationId: 'x' },
    { page: 'crm-content', tab: 'media', publicationId: 'x' }, { page: 'crm-content', tab: '' },
    { page: 'cart', productId: 'x' }, { page: 'crm', tab: 'media' },
    { page: 'catalog', returnTo: '#crm' }, { page: 'login', returnTo: 'https://evil.test' },
  ]) assert.throws(() => routeHash(route), TypeError, JSON.stringify(route));
});

test('safe return destinations canonicalize once and exclude auth and recursion', () => {
  for (const hash of fixture.validReturnTargets) assert.equal(safeReturnTo(hash), hash);
  assert.equal(safeReturnTo('#catalog?product=part%20one&category=oils&ignored=x'), '#catalog?category=oils&product=part+one');
  assert.equal(safeReturnTo('#account'), '#account');
  assert.equal(safeReturnTo('#crm?order=%2523login'), '#crm?order=%2523login');
  for (const hash of [...fixture.invalidReturnTargets, '', '#', 'crm', '#activate', '#password-reset',
    '#home?returnTo=', '#crm?order=x&returnTo=%23home', '#home?%72eturnTo=%23crm',
    '#catalog?product=a&product=b', '#home?section=offers&publication=x', '#catalog?ignored=%FF',
    '#catalog\n', '#home#crm', '%23crm', 'https://evil.test/#crm', null, {},
  ]) assert.equal(safeReturnTo(hash), '', String(hash));
});

test('auth and profile continuation retain only safe canonical return destinations', () => {
  for (const page of ['login', 'signup', 'activate', 'password-reset', 'account']) {
    const hash = `#${page}?returnTo=%23crm%3Forder%3Da%2Bb`;
    assert.deepEqual(parseRoute(hash), { ...empty(page), returnTo: '#crm?order=a+b' });
    assert.equal(routeHash({ page, returnTo: '#crm?order=a%20b' }), hash);
    for (const value of ['', 'https://evil.test', '#login', '#account?returnTo=%23home', '#crm?order=%00x']) {
      assert.deepEqual(parseRoute(`#${page}?returnTo=${encodeURIComponent(value)}`), empty(page));
    }
    assert.deepEqual(parseRoute(`#${page}?returnTo=%23home&returnTo=%23crm`), empty(page, false));
  }
});

test('CMS targets map only the four supported types and return null for invalid data', () => {
  for (const [target, hash] of [
    [{ type: 'catalog' }, '#catalog'], [{ type: 'category', categoryId: 'oils' }, '#catalog?category=oils'],
    [{ type: 'product', productId: 'part 1' }, '#catalog?product=part+1'],
    [{ type: 'section', section: 'hot' }, '#home?section=hot'],
  ]) assert.equal(targetHash(target), hash);
  for (const target of [null, undefined, [], 'catalog', {}, { type: 'url', url: '#crm' },
    { type: 'category' }, { type: 'product', productId: '' }, { type: 'product', productId: '..' },
    { type: 'section', section: 'unknown' }, { type: 'catalog', url: 'https://evil.test' },
    { type: 'category', categoryId: 'oils', productId: 'part' },
  ]) assert.equal(targetHash(target), null);
});

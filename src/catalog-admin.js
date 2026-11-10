import { isQuantity, parseQuantity, productUnit, MAX_STOCK } from './quantity.js';
import { CATEGORIES, PRODUCTS } from './catalog.js';

const imagePaths = new Set(PRODUCTS.map(product => product.image));

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function requiredText(value, limit, label) {
  if (typeof value !== 'string' || !value.trim() || value.length > limit) {
    throw new Error(`${label}: заполните поле, максимум ${limit} символов.`);
  }
  return value.trim().replace(/\s+/g, ' ');
}

function identifier(value) {
  if (typeof value !== 'string' || !value.trim()) throw new Error('Некорректный идентификатор каталога.');
  return value;
}

function normalizedCode(value) {
  return value.toLocaleLowerCase('ru').replace(/[^\p{L}\p{N}]/gu, '');
}

function productKey(product) {
  return `${normalizedCode(product.brand)}\0${normalizedCode(product.sku)}`;
}

function readProduct(raw, categories) {
  if (!isRecord(raw)) throw new Error('Некорректная номенклатура каталога.');
  const id = identifier(raw.id);
  const name = requiredText(raw.name, 160, 'Наименование');
  const sku = raw.sku === '' ? '' : requiredText(raw.sku, 80, 'Артикул');
  const brand = raw.brand === '' ? '' : requiredText(raw.brand, 80, 'Бренд');
  if (sku && !normalizedCode(sku) || brand && !normalizedCode(brand)) throw new Error('Артикул и бренд должны содержать буквы или цифры.');
  if (!categories.some(category => category.id === raw.category)) throw new Error('Выберите существующую категорию.');
  if (!Number.isSafeInteger(raw.priceKopecks) || raw.priceKopecks <= 0 || raw.priceKopecks > 1000000000) {
    throw new Error('Цена должна быть больше нуля и не превышать 10 000 000 ₽.');
  }
  const unit = productUnit(raw);
  if (!isQuantity(raw.stock, unit === null ? 'кг' : unit, { max: MAX_STOCK, zero: true })) throw new Error('Проверьте остаток и единицу измерения.');
  if (typeof raw.externalId !== 'string' || typeof raw.description !== 'string' ||
      !Number.isSafeInteger(raw.deliveryDays) || raw.deliveryDays < 0 ||
      (raw.image !== '' && !imagePaths.has(raw.image)) || !isRecord(raw.specs) ||
      Object.entries(raw.specs).some(([key, value]) => !key.trim() || typeof value !== 'string')) {
    throw new Error('Некорректные дополнительные данные товара.');
  }
  return {
    id, externalId: raw.externalId, sku, brand, name, category: raw.category,
    priceKopecks: raw.priceKopecks, stock: raw.stock, unit, image: raw.image, deliveryDays: raw.deliveryDays,
    description: raw.description, specs: Object.fromEntries(Object.entries(raw.specs)),
  };
}

function parsePrice(value) {
  const match = typeof value === 'string' && /^(\d+)(?:[.,](\d{1,2}))?$/.exec(value.trim());
  if (!match) throw new Error('Введите цену в рублях с максимум двумя знаками после запятой.');
  return Number(match[1]) * 100 + Number((match[2] || '').padEnd(2, '0'));
}


export function initialCatalog() {
  return structuredClone({ categories: CATEGORIES, products: PRODUCTS });
}

export function restoreCatalog(raw) {
  if (raw === null) return initialCatalog();
  if (!isRecord(raw) || !Array.isArray(raw.categories) || !Array.isArray(raw.products)) {
    throw new Error('Не удалось прочитать сохранённый каталог.');
  }
  const categories = raw.categories.map(category => {
    if (!isRecord(category)) throw new Error('Некорректная категория каталога.');
    return { id: identifier(category.id), name: requiredText(category.name, 80, 'Название категории') };
  });
  if (new Set(categories.map(category => category.id)).size !== categories.length ||
      new Set(categories.map(category => category.name.toLocaleLowerCase('ru'))).size !== categories.length) {
    throw new Error('В сохранённом каталоге повторяются категории.');
  }
  const products = raw.products.map(product => readProduct(product, categories));
  if (new Set(products.map(product => product.id)).size !== products.length ||
      new Set(products.filter(product => product.sku && product.brand).map(productKey)).size !== products.filter(product => product.sku && product.brand).length) {
    throw new Error('В сохранённом каталоге повторяются товары или артикулы одного бренда.');
  }
  return { categories, products };
}

export function createCategory(catalog, input) {
  const name = requiredText(input?.name, 80, 'Название категории');
  if (catalog.categories.some(category => category.name.toLocaleLowerCase('ru') === name.toLocaleLowerCase('ru'))) {
    throw new Error('Категория с таким названием уже существует.');
  }
  return { ...catalog, categories: [...catalog.categories, { id: crypto.randomUUID(), name }] };
}

export function createProduct(catalog, input) {
  const product = readProduct({
    id: crypto.randomUUID(), externalId: '', name: input?.name, sku: input?.sku, brand: input?.brand,
    category: input?.category, priceKopecks: parsePrice(input?.price), stock: parseQuantity(input?.stock, input?.unit === null ? 'кг' : input?.unit ?? 'шт', { max: MAX_STOCK }), unit: input?.unit,
    image: '', deliveryDays: 0, description: '', specs: {},
  }, catalog.categories);
  if (product.sku && product.brand && catalog.products.some(existing => productKey(existing) === productKey(product))) {
    throw new Error('Товар с таким артикулом и брендом уже существует.');
  }
  return { ...catalog, products: [...catalog.products, product] };
}

export function assignProductCategory(catalog, productId, categoryId) {
  if (!catalog.products.some(product => product.id === productId)) throw new Error('Товар не найден.');
  if (!catalog.categories.some(category => category.id === categoryId)) throw new Error('Категория не найдена.');
  return {
    ...catalog,
    products: catalog.products.map(product => product.id === productId ? { ...product, category: categoryId } : product),
  };
}

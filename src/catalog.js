export const CATEGORIES = [
  { id: 'oils', name: 'Масла и жидкости' },
  { id: 'filters', name: 'Фильтры' },
  { id: 'brakes', name: 'Тормозная система' },
  { id: 'ignition', name: 'Свечи зажигания' },
  { id: 'suspension', name: 'Подвеска' },
  { id: 'belts', name: 'Ремни и ролики' },
];

// Цены, остатки и характеристики иллюстративные; применяемость требует отдельной проверки.
export const PRODUCTS = [
  {
    id: 'oil-shell-5w30', externalId: 'demo-1c-001', sku: '550046363', brand: 'Shell',
    name: 'Моторное масло Helix Ultra 5W-30, 4 л', category: 'oils', priceKopecks: 389000, stock: 12,
    image: '/images/motor-oil.jpg', deliveryDays: 1,
    description: 'Синтетическое моторное масло для планового обслуживания. Изображение и данные демонстрационные.',
    specs: { 'Вязкость': '5W-30', 'Объём': '4 л', 'Тип': 'Синтетическое' },
  },
  {
    id: 'filter-mann-oil', externalId: 'demo-1c-004', sku: 'W 914/2', brand: 'MANN-FILTER',
    name: 'Масляный фильтр W 914/2', category: 'filters', priceKopecks: 89000, stock: 24,
    image: '/images/oil-filter.jpg', deliveryDays: 1,
    description: 'Сменный масляный фильтр. Перед заказом проверьте артикул и применяемость. Данные демонстрационные.',
    specs: { 'Тип': 'Масляный', 'Исполнение': 'Навинчиваемый', 'Единица': '1 шт.' },
  },
  {
    id: 'brake-brembo-disc', externalId: 'demo-1c-007', sku: '09.9772.11', brand: 'Brembo',
    name: 'Тормозной диск передний', category: 'brakes', priceKopecks: 459000, stock: 6,
    image: '/images/brake-disc.jpg', deliveryDays: 1,
    description: 'Передний тормозной диск. Цена за одну деталь; применяемость проверяется по артикулу. Данные демонстрационные.',
    specs: { 'Ось': 'Передняя', 'Исполнение': 'Вентилируемый', 'Единица': '1 шт.' },
  },
  {
    id: 'spark-ngk', externalId: 'demo-1c-010', sku: 'BKR6E-11', brand: 'NGK',
    name: 'Свеча зажигания BKR6E-11', category: 'ignition', priceKopecks: 69000, stock: 40,
    image: '/images/spark-plug.jpg', deliveryDays: 1,
    description: 'Свеча зажигания для бензинового двигателя. Изображение и данные демонстрационные.',
    specs: { 'Тип': 'Свеча зажигания', 'Единица': '1 шт.', 'Подбор': 'По артикулу' },
  },
  {
    id: 'oil-mobil-5w40', externalId: 'demo-1c-002', sku: '153265', brand: 'Mobil',
    name: 'Моторное масло Super 3000 5W-40, 4 л', category: 'oils', priceKopecks: 429000, stock: 8,
    image: '/images/motor-oil.jpg', deliveryDays: 1,
    description: 'Масло для регулярной замены по регламенту автомобиля. Изображение и данные демонстрационные.',
    specs: { 'Вязкость': '5W-40', 'Объём': '4 л', 'Тип': 'Синтетическое' },
  },
  {
    id: 'oil-lukoil-5w30', externalId: 'demo-1c-003', sku: '1539435', brand: 'ЛУКОЙЛ',
    name: 'Моторное масло Genesis 5W-30, 4 л', category: 'oils', priceKopecks: 299000, stock: 15,
    image: '/images/motor-oil.jpg', deliveryDays: 1,
    description: 'Синтетическое масло для технического обслуживания. Изображение и данные демонстрационные.',
    specs: { 'Вязкость': '5W-30', 'Объём': '4 л', 'Тип': 'Синтетическое' },
  },
  {
    id: 'filter-bosch-air', externalId: 'demo-1c-005', sku: '1 457 433 700', brand: 'Bosch',
    name: 'Воздушный фильтр двигателя', category: 'filters', priceKopecks: 149000, stock: 10,
    image: '/images/air-filter.jpg', deliveryDays: 2,
    description: 'Фильтрующий элемент системы впуска. Изображение и данные демонстрационные.',
    specs: { 'Тип': 'Воздушный', 'Исполнение': 'Панельный', 'Единица': '1 шт.' },
  },
  {
    id: 'filter-mann-cabin', externalId: 'demo-1c-006', sku: 'CUK 2939', brand: 'MANN-FILTER',
    name: 'Угольный фильтр салона CUK 2939', category: 'filters', priceKopecks: 179000, stock: 0,
    image: '/images/air-filter.jpg', deliveryDays: 4,
    description: 'Фильтр для системы вентиляции салона. Изображение и данные демонстрационные.',
    specs: { 'Тип': 'Салонный', 'Материал': 'С активированным углём', 'Единица': '1 шт.' },
  },
  {
    id: 'brake-bosch-disc', externalId: 'demo-1c-008', sku: '0 986 479 486', brand: 'Bosch',
    name: 'Тормозной диск задний', category: 'brakes', priceKopecks: 329000, stock: 4,
    image: '/images/brake-disc.jpg', deliveryDays: 2,
    description: 'Задний тормозной диск для замены при обслуживании. Изображение и данные демонстрационные.',
    specs: { 'Ось': 'Задняя', 'Единица': '1 шт.', 'Подбор': 'По артикулу' },
  },
  {
    id: 'brake-trw-disc', externalId: 'demo-1c-009', sku: 'DF2805', brand: 'TRW',
    name: 'Тормозной диск DF2805', category: 'brakes', priceKopecks: 379000, stock: 8,
    image: '/images/brake-disc.jpg', deliveryDays: 2,
    description: 'Тормозной диск для сервисного обслуживания. Изображение и данные демонстрационные.',
    specs: { 'Тип': 'Тормозной диск', 'Единица': '1 шт.', 'Подбор': 'По артикулу' },
  },
  {
    id: 'spark-denso', externalId: 'demo-1c-011', sku: 'K20TT', brand: 'Denso',
    name: 'Свеча зажигания TT K20TT', category: 'ignition', priceKopecks: 79000, stock: 32,
    image: '/images/spark-plug.jpg', deliveryDays: 1,
    description: 'Свеча для планового обслуживания системы зажигания. Изображение и данные демонстрационные.',
    specs: { 'Тип': 'Свеча зажигания', 'Единица': '1 шт.', 'Серия': 'TT' },
  },
  {
    id: 'suspension-kyb', externalId: 'demo-1c-012', sku: '339701', brand: 'KYB',
    name: 'Амортизатор передний Excel-G', category: 'suspension', priceKopecks: 689000, stock: 4,
    image: '/images/hero-car.jpg', deliveryDays: 3,
    description: 'Передний амортизатор. Использована общая автомобильная иллюстрация; данные демонстрационные.',
    specs: { 'Ось': 'Передняя', 'Серия': 'Excel-G', 'Единица': '1 шт.' },
  },
  {
    id: 'suspension-lemforder', externalId: 'demo-1c-013', sku: '26774 01', brand: 'Lemförder',
    name: 'Стойка стабилизатора', category: 'suspension', priceKopecks: 219000, stock: 0,
    image: '/images/hero-car.jpg', deliveryDays: 5,
    description: 'Деталь подвески для замены при обслуживании. Использована общая автомобильная иллюстрация; данные демонстрационные.',
    specs: { 'Тип': 'Стойка стабилизатора', 'Единица': '1 шт.', 'Подбор': 'По артикулу' },
  },
  {
    id: 'belt-gates', externalId: 'demo-1c-014', sku: '6PK1053', brand: 'Gates',
    name: 'Поликлиновой ремень 6PK1053', category: 'belts', priceKopecks: 139000, stock: 14,
    image: '/images/hero-car.jpg', deliveryDays: 2,
    description: 'Ремень привода навесного оборудования. Использована общая автомобильная иллюстрация; данные демонстрационные.',
    specs: { 'Тип': 'Поликлиновой', 'Маркировка': '6PK1053', 'Единица': '1 шт.' },
  },
];

function normalizeSearch(value) {
  return String(value).toLocaleLowerCase('ru').replaceAll('ё', 'е').replace(/[^\p{L}\p{N}]/gu, '');
}

export function filterProducts(products, { query = '', category = '', brand = '', inStock = false, sort = 'relevance' } = {}) {
  const search = normalizeSearch(query);
  const terms = String(query).split(/\s+/).map(normalizeSearch).filter(Boolean);
  const result = products.filter(product => {
    const searchable = normalizeSearch(`${product.name} ${product.brand} ${product.sku} ${product.id ?? ''} ${product.externalId ?? ''}`);
    return (!search || searchable.includes(search) || terms.every(term => searchable.includes(term))) &&
      (!category || product.category === category) &&
      (!brand || product.brand === brand) &&
      (!inStock || product.stock > 0);
  });
  if (sort === 'price_asc') result.sort((a, b) => a.priceKopecks - b.priceKopecks);
  if (sort === 'price_desc') result.sort((a, b) => b.priceKopecks - a.priceKopecks);
  return result;
}

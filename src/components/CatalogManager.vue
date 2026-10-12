<script setup>
import { onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue';
import AppIcon from './AppIcon.vue';

const props = defineProps({
  api: { type: Object, required: true },
});
const emit = defineEmits(['changed', 'staff-error']);
const staffError = error => { if (error.status === 401) emit('staff-error', error); };

const categories = ref([]);
const categoriesLoading = ref(true);
const categoriesError = ref('');
const visibleProducts = ref([]);
const productsLoading = ref(true);
const productsError = ref('');
const total = ref(0);
const offset = ref(0);
const pageLimit = ref(24);
const categoryName = ref('');
const categoryError = ref('');
const categoryMessage = ref('');
const categoryBusy = ref(false);
const productDraft = reactive({ name: '', sku: '', brand: '', category: '', price: '', stock: '0' });
const productError = ref('');
const productMessage = ref('');
const productBusy = ref(false);
const assignmentError = ref('');
const assignmentMessage = ref('');
const assigningProducts = ref(new Set());
const query = ref('');
const categoryLabel = id => categories.value.find(category => category.id === id)?.name || id;
const money = kopecks => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(kopecks / 100);
let categoryRequest = 0;
let productRequest = 0;
let searchTimer;

async function loadCategories() {
  const request = ++categoryRequest;
  categoriesLoading.value = true;
  categoriesError.value = '';
  try {
    const result = await props.api.getCategories({ admin: true });
    if (request === categoryRequest) categories.value = result;
  } catch (error) {
    staffError(error);
    if (request === categoryRequest) categoriesError.value = error?.message || 'Не удалось загрузить категории.';
  } finally {
    if (request === categoryRequest) categoriesLoading.value = false;
  }
}

async function loadProducts(nextOffset = offset.value) {
  clearTimeout(searchTimer);
  const request = ++productRequest;
  offset.value = nextOffset;
  productsLoading.value = true;
  productsError.value = '';
  try {
    const page = await props.api.getAdminProducts({ query: query.value }, { offset: nextOffset, limit: 24 });
    if (request !== productRequest) return;
    visibleProducts.value = page.items;
    total.value = page.total;
    offset.value = page.offset;
    pageLimit.value = page.limit;
  } catch (error) {
    staffError(error);
    if (request === productRequest) productsError.value = error?.message || 'Не удалось загрузить номенклатуру.';
  } finally {
    if (request === productRequest) productsLoading.value = false;
  }
}

const reloadLists = () => Promise.all([loadCategories(), loadProducts()]);

watch(query, () => {
  clearTimeout(searchTimer);
  ++productRequest;
  offset.value = 0;
  productsLoading.value = true;
  productsError.value = '';
  searchTimer = setTimeout(() => loadProducts(0), 150);
});

onMounted(reloadLists);
onBeforeUnmount(() => {
  clearTimeout(searchTimer);
  ++categoryRequest;
  ++productRequest;
});

async function submitCategory() {
  if (categoryBusy.value) return;
  categoryBusy.value = true;
  categoryError.value = '';
  try {
    const created = await props.api.createCategory({ name: categoryName.value, position: categories.value.length, isVisible: true, showInMenu: true });
    categories.value = [...categories.value, created];
    productDraft.category = created.id;
    categoryName.value = '';
    categoryMessage.value = `Категория «${created.name}» создана. Можно добавить в неё номенклатуру.`;
    emit('changed');
    await reloadLists();
  } catch (error) {
    staffError(error);
    categoryError.value = error?.message || 'Не удалось создать категорию.';
  } finally {
    categoryBusy.value = false;
  }
}

async function submitProduct() {
  if (productBusy.value || props.api.mode !== 'demo' || !props.api.canCreateProduct) return;
  productBusy.value = true;
  productError.value = '';
  try {
    const created = await props.api.createProduct({ ...productDraft });
    Object.assign(productDraft, { name: '', sku: '', brand: '', price: '', stock: '0' });
    productMessage.value = `Номенклатура «${created.name}» добавлена. Артикул: ${created.sku}.`;
    emit('changed');
    await reloadLists();
  } catch (error) {
    staffError(error);
    productError.value = error?.message || 'Не удалось добавить номенклатуру.';
  } finally {
    productBusy.value = false;
  }
}

async function changeCategory(product, event) {
  if (assigningProducts.value.has(product.id)) return;
  const select = event.target;
  const previousCategory = product.category;
  const categoryId = select.value;
  assigningProducts.value.add(product.id);
  assignmentError.value = '';
  try {
    await props.api.assignProductCategory(product.id, categoryId);
    product.category = categoryId;
    assignmentMessage.value = `Категория артикула ${product.sku} изменена на «${categoryLabel(categoryId)}».`;
    emit('changed');
    await reloadLists();
  } catch (error) {
    staffError(error);
    select.value = previousCategory;
    assignmentError.value = error?.message || 'Не удалось изменить категорию товара.';
  } finally {
    assigningProducts.value.delete(product.id);
  }
}
</script>

<template>
  <section class="catalog-manager" aria-labelledby="catalog-manager-title">
    <div class="catalog-manager-heading">
      <div><span class="eyebrow">LAVR / НОМЕНКЛАТУРА</span><h2 id="catalog-manager-title">Каталог под контролем<span class="red-period">.</span></h2><p>{{ api.mode === 'demo' ? 'Создайте категорию, добавьте товар или измените его категорию.' : 'Создайте категорию или измените категорию существующего товара.' }}</p></div>
      <span class="catalog-count"><AppIcon name="box" /> {{ productsLoading || productsError ? '—' : total }} товаров · {{ categoriesLoading || categoriesError ? '—' : categories.length }} категорий</span>
    </div>
    <div v-if="api.mode === 'demo'" class="demo-notice catalog-local-notice"><AppIcon name="shield" /><div><strong>Локальный демонстрационный каталог</strong><p>Изменения сохраняются в этом браузере и сразу доступны на витрине. Запись в 1С не выполняется.</p></div></div>
    <p v-if="categoriesLoading" class="catalog-load-state" role="status">Загружаем категории…</p>
    <div v-else-if="categoriesError" class="catalog-load-state form-error" role="alert"><p>{{ categoriesError }}</p><button type="button" class="btn outline" @click="loadCategories">Повторить загрузку категорий</button></div>

    <div class="catalog-manager-forms">
      <form id="category-form" class="catalog-form" aria-labelledby="category-form-title" :aria-busy="categoryBusy" @submit.prevent="submitCategory">
        <div class="catalog-form-heading"><span class="catalog-form-icon"><AppIcon name="grid" /></span><h3 id="category-form-title">Создать категорию</h3></div>
        <p id="category-form-help" class="catalog-form-help">Новая категория появится в каталоге и в списке ниже.</p>
        <label for="category-name">Название категории <span>*</span></label>
        <input id="category-name" v-model="categoryName" name="categoryName" required maxlength="80" placeholder="Например, аккумуляторы" aria-describedby="category-form-help" data-testid="category-name" :disabled="categoryBusy">
        <button class="btn red" type="submit" data-testid="create-category" :disabled="categoryBusy || categoriesLoading || !!categoriesError"><AppIcon name="plus" /> Создать категорию</button>
        <p v-if="categoryBusy" class="catalog-form-help" role="status">Сохраняем категорию…</p>
        <p v-if="categoryError" id="category-error" class="form-error" role="alert">{{ categoryError }}</p>
        <p v-if="categoryMessage" class="catalog-success" role="status">{{ categoryMessage }}</p>
      </form>

      <form v-if="api.mode === 'demo' && api.canCreateProduct" id="product-form" class="catalog-form" aria-labelledby="product-form-title" :aria-busy="productBusy" @submit.prevent="submitProduct">
        <div class="catalog-form-heading"><span class="catalog-form-icon"><AppIcon name="box" /></span><h3 id="product-form-title">Добавить номенклатуру</h3></div>
        <p class="catalog-form-help">Артикул — основной идентификатор для поиска. Один товар относится к одной категории.</p>
        <div class="catalog-fields">
          <label class="catalog-field full" for="product-name">Наименование <span>*</span><input id="product-name" v-model="productDraft.name" name="productName" required maxlength="160" placeholder="Например, аккумулятор 60 А·ч" data-testid="product-name" :disabled="productBusy"></label>
          <label class="catalog-field" for="product-sku">Артикул <span>*</span><input id="product-sku" v-model="productDraft.sku" name="sku" required maxlength="80" placeholder="ABC-123" data-testid="product-sku" :disabled="productBusy"></label>
          <label class="catalog-field" for="product-brand">Бренд <span>*</span><input id="product-brand" v-model="productDraft.brand" name="brand" required maxlength="80" placeholder="Производитель" data-testid="product-brand" :disabled="productBusy"></label>
          <label class="catalog-field full" for="product-category">Категория <span>*</span><select id="product-category" v-model="productDraft.category" name="category" required data-testid="product-category" :disabled="productBusy || categoriesLoading || !!categoriesError"><option value="" disabled>Выберите категорию</option><option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}</option></select></label>
          <label class="catalog-field" for="product-price">Цена, ₽ <span>*</span><input id="product-price" v-model="productDraft.price" name="price" required inputmode="decimal" maxlength="14" pattern="[0-9]+([.,][0-9]{1,2})?" title="Цена в рублях, максимум два знака после запятой" placeholder="0,00" data-testid="product-price" :disabled="productBusy"></label>
          <label class="catalog-field" for="product-stock">Остаток, шт. <span>*</span><input id="product-stock" v-model="productDraft.stock" name="stock" type="number" required min="0" max="100000" step="1" data-testid="product-stock" :disabled="productBusy"></label>
        </div>
        <p class="catalog-photo-note"><AppIcon name="box" /> Товар без фотографии появится с нейтральной иконкой.</p>
        <button class="btn red" type="submit" data-testid="create-product" :disabled="productBusy || categoriesLoading || !!categoriesError"><AppIcon name="plus" /> Добавить номенклатуру</button>
        <p v-if="productBusy" class="catalog-form-help" role="status">Сохраняем номенклатуру…</p>
        <p v-if="productError" id="product-error" class="form-error" role="alert">{{ productError }}</p>
        <p v-if="productMessage" class="catalog-success" role="status">{{ productMessage }}</p>
      </form>
      <div v-else class="catalog-form">
        <div class="catalog-form-heading"><span class="catalog-form-icon"><AppIcon name="box" /></span><h3>Добавление номенклатуры</h3></div>
        <p class="catalog-form-help">Номенклатура поступает из 1С. Создание товаров будет доступно после согласования адаптера 1С и серверного маршрута для этой операции.</p>
      </div>
    </div>

    <div class="catalog-manager-list" :aria-busy="productsLoading">
      <div class="catalog-list-toolbar">
        <div><h3>Номенклатура <span class="heading-count">{{ productsLoading || productsError ? '—' : total }}</span></h3><p>Категорию существующего товара можно изменить в таблице.</p></div>
        <label class="catalog-admin-search" for="catalog-admin-search"><AppIcon name="search" /><span class="sr-only">Поиск номенклатуры</span><input id="catalog-admin-search" v-model="query" name="catalogQuery" type="search" maxlength="120" placeholder="Артикул, название, бренд или ID" data-testid="catalog-admin-search"></label>
      </div>
      <p v-if="assignmentError" class="catalog-assignment-message form-error" role="alert">{{ assignmentError }}</p>
      <p v-if="assignmentMessage" class="catalog-assignment-message catalog-success" role="status">{{ assignmentMessage }}</p>
      <p v-if="productsLoading" class="catalog-load-state" role="status">Загружаем номенклатуру…</p>
      <div v-else-if="productsError" class="catalog-load-state form-error" role="alert"><p>{{ productsError }}</p><button type="button" class="btn outline" @click="loadProducts()">Повторить загрузку номенклатуры</button></div>
      <template v-else-if="visibleProducts.length">
      <p id="catalog-scroll-hint" class="table-scroll-hint">Проведите по таблице в сторону, чтобы увидеть категории и остальные колонки.</p>
      <div class="catalog-table-wrap" tabindex="0" role="region" aria-label="Номенклатура: прокрутка таблицы" aria-describedby="catalog-scroll-hint" data-testid="catalog-table-scroll">
        <table class="catalog-admin-table">
          <caption class="sr-only">Номенклатура: артикул, внутренний ID, наименование, категория, цена и остаток</caption>
          <thead><tr><th scope="col">Артикул / ID</th><th scope="col">Номенклатура</th><th scope="col">Категория</th><th scope="col">Цена</th><th scope="col">Остаток</th></tr></thead>
          <tbody><tr v-for="product in visibleProducts" :key="product.id" :data-testid="`catalog-product-${product.id}`">
            <td class="catalog-identifiers"><strong>{{ product.sku }}</strong><small>ID: {{ product.id }}</small></td>
            <td class="catalog-product-name"><strong>{{ product.name }}</strong><small>{{ product.brand }}</small></td>
            <td><select :value="product.category" :aria-label="`Категория ${product.brand} ${product.sku}`" :data-testid="`product-category-${product.id}`" :disabled="assigningProducts.has(product.id) || categoriesLoading || !!categoriesError" :aria-busy="assigningProducts.has(product.id)" @change="changeCategory(product, $event)"><option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}</option></select></td>
            <td class="catalog-price">{{ money(product.priceKopecks) }}</td>
            <td><span class="catalog-stock" :class="{ unavailable: !product.stock }">{{ product.stock }} шт.</span></td>
          </tr></tbody>
        </table>
      </div>
      </template>
      <div v-else class="empty-state"><AppIcon name="search" /><h3>Номенклатура не найдена</h3><p>Проверьте артикул или измените запрос.</p><button type="button" class="btn outline" @click="query = ''">Сбросить поиск</button></div>
      <nav v-if="!productsLoading && !productsError && total > pageLimit" class="catalog-pagination" aria-label="Страницы номенклатуры">
        <button type="button" class="btn outline" :disabled="offset === 0" @click="loadProducts(Math.max(0, offset - pageLimit))">Назад</button>
        <span>{{ offset + 1 }}–{{ offset + visibleProducts.length }} из {{ total }}</span>
        <button type="button" class="btn outline" :disabled="offset + pageLimit >= total" @click="loadProducts(offset + pageLimit)">Далее</button>
      </nav>
    </div>
  </section>
</template>

<style scoped>
.catalog-manager { min-width: 0; }
.catalog-manager-heading { display: flex; align-items: end; justify-content: space-between; gap: 20px; margin-bottom: 22px; }
.catalog-manager-heading h2 { font-size: 29px; font-weight: 650; letter-spacing: -.9px; }
.catalog-manager-heading p { color: var(--muted); margin-top: 9px; font-size: 12px; line-height: 1.6; }
.catalog-count { display: flex; align-items: center; gap: 9px; flex-shrink: 0; color: var(--muted); font-size: 11px; }
.catalog-count .icon { width: 18px; height: 18px; }
.catalog-local-notice { margin: 0 0 25px; }
.catalog-load-state { padding: 0 24px 20px; font-size: 12px; line-height: 1.7; overflow-wrap: anywhere; }
.catalog-load-state .btn { margin-top: 12px; font-size: 12px; max-width: 100%; white-space: normal; }
.catalog-manager-forms { display: grid; grid-template-columns: minmax(250px, 1fr) minmax(0, 2fr); gap: 20px; align-items: start; margin-bottom: 30px; }
.catalog-form { min-width: 0; padding: 25px; border: 1px solid var(--line); border-radius: 7px; background: white; }
.catalog-form-heading { display: flex; align-items: center; gap: 12px; }
.catalog-form-heading h3 { min-width: 0; font-size: 18px; font-weight: 600; letter-spacing: -.4px; }
.catalog-form-icon { display: grid; place-items: center; width: 35px; height: 35px; color: var(--red); background: #fff0f1; border-radius: 6px; }
.catalog-form-icon .icon { width: 19px; height: 19px; }
.catalog-form-help { color: var(--muted); font-size: 11px; line-height: 1.7; margin: 13px 0 23px; }
.catalog-form label { display: block; color: var(--ink); font-size: 12px; font-weight: 500; }
.catalog-form label > span { color: var(--red); }
.catalog-form input, .catalog-form select { display: block; width: 100%; min-width: 0; min-height: 44px; padding: 11px 12px; margin-top: 9px; border: 1px solid #dadfe6; background: white; font-size: 12px; color: var(--ink); }
.catalog-form > .btn { margin-top: 22px; font-size: 12px; }
.catalog-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.catalog-field.full { grid-column: 1 / -1; }
.catalog-photo-note { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 10px; line-height: 1.6; margin-top: 18px; }
.catalog-photo-note .icon { width: 16px; height: 16px; }
.catalog-success { color: var(--green); font-size: 12px; line-height: 1.7; margin-top: 16px; overflow-wrap: anywhere; }
.catalog-form .form-error { font-size: 12px; margin-top: 16px; }
.catalog-manager-list { min-width: 0; border: 1px solid var(--line); border-radius: 7px; overflow: hidden; }
.catalog-list-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 23px 24px; }
.catalog-list-toolbar h3 { font-size: 18px; font-weight: 600; letter-spacing: -.4px; }
.catalog-list-toolbar p { color: var(--muted); font-size: 10px; line-height: 1.6; margin-top: 7px; }
.catalog-admin-search { display: flex; align-items: center; gap: 10px; width: 320px; min-height: 42px; flex-shrink: 0; border: 1px solid #e1e5eb; padding: 9px 11px; border-radius: 5px; background: white; }
.catalog-admin-search > .icon { width: 17px; height: 17px; color: #919aa6; }
.catalog-admin-search input { width: 100%; min-width: 0; padding: 3px 0; border: 0; color: var(--ink); background: transparent; font-size: 11px; }
.catalog-assignment-message { margin: 0; padding: 0 24px 18px; }
.catalog-table-wrap { position: relative; overflow-x: auto; }
.catalog-admin-table { width: 100%; min-width: 840px; border-collapse: collapse; text-align: left; }
.catalog-admin-table thead { background: #f8f9fb; border-block: 1px solid var(--line); }
.catalog-admin-table th { padding: 14px 20px; color: #848d9a; font-size: 10px; font-weight: 500; white-space: nowrap; }
.catalog-admin-table td { padding: 19px 20px; font-size: 11px; border-bottom: 1px solid var(--line); vertical-align: middle; }
.catalog-admin-table tr:last-child td { border-bottom: 0; }
.catalog-admin-table strong { display: block; font-weight: 600; line-height: 1.5; }
.catalog-admin-table small { display: block; color: #929ba8; font-size: 9px; line-height: 1.5; margin-top: 6px; }
.catalog-identifiers { min-width: 175px; max-width: 220px; overflow-wrap: anywhere; }
.catalog-product-name { min-width: 210px; max-width: 340px; overflow-wrap: anywhere; }
.catalog-admin-table select { width: 185px; min-height: 40px; padding: 9px; border: 1px solid #dadfe6; background: white; font-size: 11px; color: var(--ink); }
.catalog-price { white-space: nowrap; font-weight: 600; }
.catalog-stock { white-space: nowrap; color: var(--green); }
.catalog-stock.unavailable { color: var(--muted); }
.catalog-manager-list .empty-state { border: 0; border-top: 1px solid var(--line); border-radius: 0; background: white; }
.catalog-pagination { display: flex; justify-content: center; align-items: center; flex-wrap: wrap; gap: 16px; padding: 20px; border-top: 1px solid var(--line); font-size: 12px; }
.catalog-pagination .btn { font-size: 12px; }
@media (max-width: 1000px) {
  .catalog-manager-heading { align-items: start; flex-direction: column; gap: 14px; }
  .catalog-manager-forms { grid-template-columns: minmax(230px, 1fr) minmax(0, 1.6fr); gap: 15px; }
  .catalog-form { padding: 20px; }
  .catalog-list-toolbar { align-items: stretch; flex-direction: column; gap: 16px; }
  .catalog-admin-search { width: 100%; }
}
@media (max-width: 650px) {
  .catalog-manager-heading h2 { font-size: 25px; }
  .catalog-manager-forms { grid-template-columns: 1fr; }
  .catalog-form { padding: 20px 16px; }
  .catalog-fields { grid-template-columns: 1fr; gap: 16px; }
  .catalog-form input, .catalog-form select, .catalog-admin-search input { font-size: 16px; }
  .catalog-form > .btn { width: 100%; }
  .catalog-list-toolbar { padding: 20px 16px; }
  .catalog-assignment-message { padding-inline: 16px; }
  .catalog-load-state { padding-inline: 16px; }
}
@media (max-width: 760px), (pointer: coarse) {
  .catalog-form input, .catalog-form select, .catalog-admin-search input, .catalog-admin-table select { font-size: 16px; }
  .catalog-admin-table select { min-height: 44px; }
  .catalog-admin-search { min-height: 54px; }
}
</style>

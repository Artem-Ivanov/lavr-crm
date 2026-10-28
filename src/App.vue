<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import AppIcon from './components/AppIcon.vue';
import ProductCard from './components/ProductCard.vue';
import CatalogManager from './components/CatalogManager.vue';
import LandingPage from './components/LandingPage.vue';
import ContentManager from './components/ContentManager.vue';
import FeedbackForm from './components/FeedbackForm.vue';
import FeedbackManager from './components/FeedbackManager.vue';
import { parseCartDraft, setDraftQuantity } from './cart-draft.js';
import { ORDER_STATUSES, orderActions } from './order-labels.js';
import { checkoutKey, clearCheckoutKey } from './checkout-attempt.js';

const { api } = defineProps({ api: { type: Object, required: true } });
const isDemo = api.mode === 'demo';
const CART_KEY = 'lavr.cart.v1';
const storageIssue = ref('');
function readCart() {
  try { return parseCartDraft(JSON.parse(localStorage.getItem(CART_KEY) || '[]')); }
  catch { storageIssue.value = 'Не удалось прочитать черновик корзины. Сохранённые данные не изменены.'; return []; }
}
const cart = ref(readCart());
const site = ref(null);
const siteError = ref('');
const siteLoading = ref(true);
const categories = ref([]);
const categoriesError = ref('');
const products = ref([]);
const catalogTotal = ref(0);
const catalogOffset = ref(0);
const PAGE_SIZE = 24;
const catalogLoading = ref(true);
const catalogError = ref('');
const catalogReadFailed = ref(false);
const catalogStale = ref(false);
const productCache = ref(new Map());
const cartLoading = ref(false);
const cartError = ref('');
const orders = ref([]);
const ordersTotal = ref(0);
const ordersOffset = ref(0);
const ordersLoading = ref(false);
const ordersError = ref('');
const session = ref(null);
const authLoading = ref(false);
const authError = ref('');
const loginDraft = reactive({ login: '', password: '' });
const loginBusy = ref(false);
const statusBusy = ref(false);
const logoutBusy = ref(false);
const cartStale = ref(false);
const defaultFilters = () => ({ query: '', category: '', brand: '', inStock: false, sort: 'relevance' });
const filters = reactive(defaultFilters());
const searchQuery = ref('');
const orderQuery = ref('');
const orderSearchQuery = ref('');
const orderFilter = ref('');
const modal = ref(null);
const modalProduct = ref(null);
const modalOrder = ref(null);
const modalLoading = ref(false);
const modalError = ref('');
const detailDialog = ref(null);
const mobileMenu = ref(null);
const contentDirty = ref(false);
const feedbackDirty = ref(false);
const feedbackForm = ref(null);
const receipt = ref(null);
const submitting = ref(false);
const checkoutError = ref('');
const checkoutCleanupPending = ref(false);
const defaultDraft = () => ({ name: '', phone: '', email: '', method: 'pickup', address: '' });
const draft = reactive(defaultDraft());
const toast = ref('');
let toastTimer;
let siteRequest = 0;
let catalogRequest = 0;
let ordersRequest = 0;
let cartRequest = 0;
let authRequest = 0;
let modalRequest = 0;
const money = kopecks => kopecks === null ? '—' : new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(kopecks / 100);
const date = value => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(value));
const categoryName = id => categories.value.find(category => category.id === id)?.name || id;
const categoryCount = id => categories.value.find(category => category.id === id)?.productCount ?? null;
const brands = computed(() => [...new Set([...productCache.value.values()].map(product => product.brand))].sort());
const readRoute = () => {
  const [name, search = ''] = location.hash.slice(1).split('?');
  const page = ['catalog', 'cart', 'crm', 'crm-catalog', 'crm-content', 'crm-feedback'].includes(name) ? name : 'home';
  const params = new URLSearchParams(search);
  return { page, caseId: page === 'crm-feedback' ? params.get('case') || '' : '', orderId: page === 'crm' ? params.get('order') || '' : '' };
};
const initialRoute = readRoute();
const page = ref(initialRoute.page);
const caseId = ref(initialRoute.caseId);
const orderId = ref(initialRoute.orderId);
let acceptedHash = location.hash;
const isCrm = computed(() => ['crm', 'crm-catalog', 'crm-content', 'crm-feedback'].includes(page.value));
const isAdmin = computed(() => session.value?.user.role === 'admin');
const activeFilters = computed(() => filters.query || filters.category || filters.brand || filters.inStock);
const count = computed(() => cart.value.reduce((sum, item) => sum + item.quantity, 0));
const cartItems = computed(() => cart.value.map(item => ({ ...item, product: productCache.value.get(item.productId) || { name: 'Товар пока недоступен', sku: item.productId, brand: '', image: '', priceKopecks: 0, stock: 0 } })));
const cartReady = computed(() => !cartLoading.value && !cartError.value && !catalogReadFailed.value && !cartStale.value && cart.value.every(item => productCache.value.has(item.productId) && item.quantity <= productCache.value.get(item.productId).stock));
const total = computed(() => {
  if (!cart.value.every(item => productCache.value.has(item.productId))) return null;
  const value = cart.value.reduce((sum, item) => sum + productCache.value.get(item.productId).priceKopecks * item.quantity, 0);
  return Number.isSafeInteger(value) ? value : null;
});
const cartQuantity = id => cart.value.find(item => item.productId === id)?.quantity || 0;
const visibleOrders = computed(() => orders.value);
const activeOrders = computed(() => orders.value.filter(order => !['completed', 'cancelled'].includes(order.status)));
const orderTotal = computed(() => { const total = orders.value.filter(order => order.status !== 'cancelled').reduce((sum, order) => sum + order.totalKopecks, 0); return Number.isSafeInteger(total) ? total : null; });
const statusActions = computed(() => modalOrder.value ? orderActions(modalOrder.value, isDemo) : []);
const storeName = computed(() => site.value?.settings.storeName || 'LAVR');
const menuItems = computed(() => site.value && (!isDemo || site.value.menuConfigured || site.value.menu.length) ? site.value.menu : categories.value.slice(0, 3).map(category => ({ label: category.name, target: { type: 'category', categoryId: category.id } })));

watch(page, current => {
  document.title = `${{ home: 'Автозапчасти для вашего автомобиля', catalog: 'Каталог автозапчастей', cart: 'Корзина', crm: 'CRM · заказы', 'crm-catalog': 'CRM · каталог', 'crm-content': 'CRM · контент сайта', 'crm-feedback': 'CRM · обращения' }[current]} — ${storeName.value}`;
  if (['crm', 'crm-catalog', 'crm-content', 'crm-feedback'].includes(current)) enterCrm();
  if (current === 'cart') loadCartProducts();
}, { immediate: true });
watch([() => session.value?.user.id, page, orderId], ([staffId, current, id]) => {
  if (staffId && current === 'crm' && id) openModal('order', id);
});
watch(filters, () => { catalogOffset.value = 0; loadCatalog(); });
watch([orderQuery, orderFilter], () => { ordersOffset.value = 0; if (session.value) loadOrders(); });

function notify(message) {
  toast.value = message;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toast.value = ''; }, 3800);
}
function rememberProducts(items) {
  const next = new Map(productCache.value);
  for (const product of items) next.set(product.id, product);
  productCache.value = next;
}
async function loadSite() {
  const request = ++siteRequest;
  siteLoading.value = true; siteError.value = '';
  try { const result = await api.getSite(); if (request === siteRequest) site.value = result; }
  catch (error) { if (request === siteRequest) siteError.value = error.message; }
  finally { if (request === siteRequest) siteLoading.value = false; }
}
async function loadCategories() {
  categoriesError.value = '';
  try { categories.value = await api.getCategories(); }
  catch (error) { categoriesError.value = error.message; }
}
async function loadCatalog() {
  const request = ++catalogRequest;
  catalogLoading.value = true; catalogError.value = '';
  try {
    const result = await api.getCatalog({ ...filters }, { offset: catalogOffset.value, limit: PAGE_SIZE });
    if (request !== catalogRequest) return;
    products.value = result.items; catalogTotal.value = result.total;
    catalogReadFailed.value = Boolean(result.readOnly); catalogStale.value = result.stale;
    if (result.issue) catalogError.value = result.issue;
    rememberProducts(result.items);
  } catch (error) { if (request === catalogRequest) { catalogError.value = error.message; products.value = []; } }
  finally { if (request === catalogRequest) catalogLoading.value = false; }
}
async function loadCartProducts() {
  const request = ++cartRequest;
  cartLoading.value = true; cartError.value = '';
  const ids = cart.value.map(item => item.productId);
  const results = await Promise.allSettled(ids.map(id => api.getProduct(id)));
  if (request !== cartRequest) return;
  const checked = results.filter(result => result.status === 'fulfilled').map(result => result.value);
  rememberProducts(checked.map(result => result.product));
  cartStale.value = checked.some(result => result.stale);
  if (results.some(result => result.status === 'rejected')) cartError.value = 'Не удалось проверить товары корзины. Черновик сохранён. Повторите проверку или удалите недоступный товар.';
  cartLoading.value = false;
}
async function refreshData() {
  await Promise.allSettled([loadSite(), loadCategories(), loadCatalog(), loadCartProducts()]);
}
function handleStaffError(error) {
  if (error.status === 401) {
    contentDirty.value = false; feedbackDirty.value = false;
    authRequest++; authLoading.value = false; ordersRequest++; session.value = null; orders.value = []; ordersTotal.value = 0;
    closeModal(false);
    authError.value = 'Сессия завершена. Войдите снова.';
  }
}
async function enterCrm() {
  if (authLoading.value || loginBusy.value || logoutBusy.value) return;
  if (!session.value) {
    const request = ++authRequest;
    authLoading.value = true; authError.value = '';
    try { const result = await api.getSession(); if (request === authRequest) session.value = result; }
    catch (error) { if (request === authRequest && error.status !== 401) authError.value = error.message; }
    finally { if (request === authRequest) authLoading.value = false; }
  }
  if (session.value && page.value === 'crm') await loadOrders();
}
async function login() {
  if (loginBusy.value || logoutBusy.value) return;
  const request = ++authRequest;
  ordersRequest++; loginBusy.value = true; authLoading.value = false; authError.value = '';
  try {
    const result = await api.login({ ...loginDraft });
    if (request !== authRequest) return;
    session.value = result; loginDraft.password = ''; ordersOffset.value = 0;
    if (page.value === 'crm') await loadOrders();
  } catch (error) { if (request === authRequest) authError.value = error.message; }
  finally { loginBusy.value = false; }
}
async function logout() {
  if (logoutBusy.value || loginBusy.value) return;
  if ((contentDirty.value || feedbackDirty.value) && !window.confirm('Есть несохранённые изменения или незавершённое сохранение. Выйти из кабинета?')) return;
  const request = ++authRequest;
  logoutBusy.value = true; ordersRequest++;
  try { await api.logout(); if (request === authRequest) { contentDirty.value = false; feedbackDirty.value = false; session.value = null; orders.value = []; ordersTotal.value = 0; closeModal(false); } }
  catch (error) { authError.value = error.message; handleStaffError(error); }
  finally { logoutBusy.value = false; }
}
async function loadOrders() {
  const request = ++ordersRequest;
  ordersLoading.value = true; ordersError.value = '';
  try {
    const result = await api.getOrders({ query: orderQuery.value, status: orderFilter.value }, { offset: ordersOffset.value, limit: PAGE_SIZE });
    if (request !== ordersRequest) return;
    orders.value = result.items; ordersTotal.value = result.total;
    if (result.issue) ordersError.value = result.issue;
  } catch (error) { if (request === ordersRequest) { ordersError.value = error.message; orders.value = []; handleStaffError(error); } }
  finally { if (request === ordersRequest) ordersLoading.value = false; }
}
function requireCatalog() {
  if (catalogReadFailed.value) throw new Error('Сохранённый каталог повреждён или недоступен. Изменения не применены, чтобы сохранить исходные данные.');
}
function saveCart(next) {
  cart.value = next;
  try { localStorage.setItem(CART_KEY, JSON.stringify(next)); }
  catch { storageIssue.value = 'Не удалось сохранить корзину. Она доступна, пока открыта страница.'; notify(storageIssue.value); }
}
function addProduct(id) {
  if (submitting.value) return;
  try { requireCatalog(); const product = productCache.value.get(id); if (!product) throw new Error('Товар не загружен.'); saveCart(setDraftQuantity(cart.value, id, cartQuantity(id) + 1, product.stock)); receipt.value = null; notify('Товар добавлен в корзину'); }
  catch (error) { notify(error.message); }
}
function changeQuantity(id, quantity) {
  if (submitting.value) return;
  try { requireCatalog(); saveCart(setDraftQuantity(cart.value, id, quantity, productCache.value.get(id)?.stock || 0)); }
  catch (error) { notify(error.message); }
}
function go(target) { if (location.hash !== `#${target}`) location.hash = target; }
function openFeedback(type, event) { feedbackForm.value?.open(type, event); }
function openFeedbackCase(id) { go(id ? `crm-feedback?case=${encodeURIComponent(id)}` : 'crm-feedback'); }
function resetFilters() { Object.assign(filters, defaultFilters()); searchQuery.value = ''; }
function selectCategory(id) {
  Object.assign(filters, { category: id, query: '', brand: '' }); searchQuery.value = ''; go('catalog'); window.scrollTo({ top: 0, behavior: 'instant' });
}
function navigateTarget(target) {
  if (!target) return;
  if (target.type === 'category') selectCategory(target.categoryId);
  else if (target.type === 'product') openProduct(target.productId);
  else if (target.type === 'section') {
    const scrollToSection = () => nextTick(() => {
      const kind = { offers: 'offer', announcements: 'announcement', hot: 'hot' }[target.section];
      (document.querySelector(`.promotion-card[data-kind="${kind}"]`) || document.querySelector('#promotions'))?.scrollIntoView();
    });
    if (page.value === 'home') scrollToSection();
    else { window.addEventListener('hashchange', scrollToSection, { once: true }); go('home'); }
  }
  else { resetFilters(); go('catalog'); }
}
function searchCatalog() { Object.assign(filters, defaultFilters(), { query: searchQuery.value.trim() }); go('catalog'); }
function searchOrders() { orderQuery.value = orderSearchQuery.value.trim(); }
function resetOrders() { orderQuery.value = ''; orderSearchQuery.value = ''; orderFilter.value = ''; }
function catalogPage(delta) { catalogOffset.value += delta * PAGE_SIZE; loadCatalog(); document.querySelector('#catalog-results')?.scrollIntoView(); }
function ordersPage(delta) { ordersOffset.value += delta * PAGE_SIZE; loadOrders(); }
async function openModal(type, id, event) {
  const current = ++modalRequest;
  modal.value = { type, id, opener: event?.currentTarget?.dataset.focusKey }; modalProduct.value = null; modalOrder.value = null; modalError.value = ''; modalLoading.value = type !== 'credits';
  await nextTick(); detailDialog.value?.showModal();
  if (type === 'credits') return;
  try {
    const result = type === 'product' ? await api.getProduct(id) : await api.getOrder(id);
    if (modalRequest !== current) return;
    if (type === 'product') { modalProduct.value = result.product; rememberProducts([result.product]); } else modalOrder.value = result;
  } catch (error) { if (modalRequest === current) { modalError.value = error.message; handleStaffError(error); } }
  finally { if (modalRequest === current) modalLoading.value = false; }
}
const openProduct = (id, event) => openModal('product', id, event);
async function closeModal(restoreFocus = true) {
  const opener = modal.value?.opener;
  modalRequest++; detailDialog.value?.close(); modal.value = null; modalProduct.value = null; modalOrder.value = null;
  if (restoreFocus && opener) { await nextTick(); [...document.querySelectorAll('[data-focus-key]')].find(element => element.dataset.focusKey === opener)?.focus({ preventScroll: true }); }
}
async function updateOrderStatus(id, status) {
  if (statusBusy.value) return;
  const generation = authRequest;
  const dialogRequest = modalRequest;
  statusBusy.value = true;
  try {
    const order = await api.updateOrderStatus(modalOrder.value, status);
    if (generation !== authRequest) return;
    orders.value = orders.value.map(item => item.id === id ? order : item);
    if (dialogRequest === modalRequest && modal.value?.id === id) modalOrder.value = order;
    notify(isDemo ? 'Статус демозаказа обновлён' : 'Статус заказа обновлён');
  } catch (error) {
    if (generation !== authRequest) return;
    notify(error.message); handleStaffError(error);
    if (error.status === 412 && modal.value?.id === id) {
      try {
        const refreshed = await api.getOrder(id);
        if (generation === authRequest && dialogRequest === modalRequest) modalOrder.value = refreshed;
      } catch (refreshError) {
        if (generation === authRequest && dialogRequest === modalRequest) { modalError.value = refreshError.message; handleStaffError(refreshError); }
      }
    }
  } finally { statusBusy.value = false; if (generation === authRequest && dialogRequest === modalRequest) { await nextTick(); (detailDialog.value?.querySelector('[data-action="status"]') || detailDialog.value?.querySelector('.dialog-close'))?.focus(); } }
}
async function submitOrder() {
  if (submitting.value) return;
  submitting.value = true; checkoutError.value = '';
  try {
    requireCatalog();
    if (checkoutCleanupPending.value) { clearCheckoutKey(); checkoutCleanupPending.value = false; }
    if (!cartReady.value || total.value === null) throw new Error('Проверьте доступность и количество товаров корзины перед оформлением.');
    const body = { customer: { name: draft.name, phone: draft.phone, email: draft.email }, fulfillment: { method: draft.method, address: draft.method === 'delivery' ? draft.address : '' }, items: cart.value.map(item => ({ ...item, expectedUnitPriceKopecks: productCache.value.get(item.productId).priceKopecks })) };
    const submittedCart = JSON.stringify(cart.value);
    receipt.value = await api.createOrder(body, await checkoutKey(body));
    if (JSON.stringify(cart.value) === submittedCart) saveCart([]);
    else notify('Корзина изменилась во время оформления. Её новые изменения сохранены; проверьте состав перед следующим заказом.');
    Object.assign(draft, defaultDraft());
    try { clearCheckoutKey(); } catch { checkoutCleanupPending.value = true; storageIssue.value = 'Заказ создан. Для нового заказа восстановите доступ к хранилищу браузера; ключ завершённой попытки ещё не удалён.'; }
    window.scrollTo({ top: 0, behavior: 'instant' });
  } catch (error) {
    checkoutError.value = error.message; await nextTick(); document.querySelector('#checkout-error')?.scrollIntoView({ block: 'center' });
  } finally { submitting.value = false; }
}
function continueShopping() { receipt.value = null; go('catalog'); }
function closeMobileMenu() {
  if (!mobileMenu.value?.open) return;
  mobileMenu.value.open = false;
  mobileMenu.value.querySelector('summary')?.focus({ preventScroll: true });
}
function navigateFromMenu(target) { closeMobileMenu(); navigateTarget(target); }
function onHashChange() {
  const target = readRoute();
  if ((contentDirty.value || feedbackDirty.value) && location.hash !== acceptedHash
    && !window.confirm('Есть несохранённые изменения или незавершённое сохранение. Покинуть страницу?')) {
    history.replaceState(null, '', acceptedHash || '#home'); return;
  }
  contentDirty.value = false; feedbackDirty.value = false;
  if (page.value === 'crm' && target.page !== 'crm') { ordersRequest++; ordersLoading.value = false; }
  acceptedHash = location.hash;
  closeMobileMenu(); closeModal(false); feedbackForm.value?.close(false);
  page.value = target.page; caseId.value = target.caseId; orderId.value = target.orderId;
  window.scrollTo({ top: 0, behavior: 'instant' });
}
async function onStorage(event) {
  if (event.key === CART_KEY || event.key === null) { cart.value = readCart(); if (cart.value.length) receipt.value = null; await loadCartProducts(); }
  if (isDemo && (event.key === 'lavr.catalog.v1' || event.key === null)) await refreshData();
  if (isDemo && event.key === 'lavr.site.v1') await loadSite();
}
function onSkipLink(event) { if (event.target.closest('.skip-link')) { event.preventDefault(); document.querySelector('#main')?.focus(); } }
onMounted(() => {
  refreshData(); window.addEventListener('hashchange', onHashChange); window.addEventListener('storage', onStorage); document.addEventListener('click', onSkipLink);
});
onUnmounted(() => {
  siteRequest++; catalogRequest++; ordersRequest++; cartRequest++; window.removeEventListener('hashchange', onHashChange); window.removeEventListener('storage', onStorage); document.removeEventListener('click', onSkipLink); clearTimeout(toastTimer);
});
</script>

<template>
  <div class="utility"><div class="container utility-inner"><span>Ваш автомобиль. Наши детали.</span><span class="utility-right"><span v-if="isDemo" class="demo-label">Демонстрационный MVP</span><a href="#crm">Кабинет менеджера <AppIcon name="arrow" /></a></span></div></div>
  <header class="header">
    <div class="container header-main">
      <a class="logo" href="#home" :aria-label="`${storeName} — на главную`">{{ storeName }}<span class="logo-square"></span><small>АВТОЗАПЧАСТИ</small></a>
      <form id="search-form" class="search" role="search" @submit.prevent="searchCatalog"><AppIcon name="search" /><input id="search" v-model="searchQuery" name="query" type="search" maxlength="120" aria-label="Поиск запчастей" placeholder="Поиск по артикулу, бренду или названию" data-focus-key="search"><button type="submit">Найти <AppIcon name="arrow" /></button></form>
      <a href="#cart" class="header-cart" :class="{ active: page === 'cart' }" :aria-label="`Корзина, ${count} товаров`"><AppIcon name="cart" /><span>Корзина<small>{{ count ? money(total) : 'Пока пусто' }}</small></span><b class="cart-count">{{ count }}</b></a>
    </div>
    <nav class="navigation container" aria-label="Основная навигация">
      <a class="catalog-nav" :class="{ selected: page === 'catalog' }" href="#catalog"><AppIcon name="grid" /> Каталог запчастей</a>
      <button v-for="(item, index) in menuItems" :key="index" type="button" class="desktop-menu-item" :data-action="item.target.type === 'category' ? 'category' : undefined" :data-id="item.target.categoryId" @click="navigateTarget(item.target)">{{ item.label }}</button>
      <details v-if="menuItems.length" ref="mobileMenu" class="mobile-menu" @keydown.esc.prevent="closeMobileMenu"><summary>Меню</summary><div class="mobile-menu-items"><button v-for="(item, index) in menuItems" :key="index" type="button" @click="navigateFromMenu(item.target)">{{ item.label }}</button></div></details>
      <a class="crm-nav" :class="{ selected: isCrm }" href="#crm"><AppIcon name="user" /> CRM · заказы</a>
    </nav>
  </header>

  <main id="main" tabindex="-1">
    <div v-if="catalogReadFailed" class="container storage-alert" role="alert">Не удалось прочитать сохранённый каталог. Показан исходный демокаталог; изменения и оформление приостановлены. Данные в хранилище сохранены.</div>
    <div v-if="storageIssue" class="container storage-alert" role="alert">{{ storageIssue }}</div>
    <div v-if="categoriesError" class="container api-feedback" role="alert"><p>{{ categoriesError }}</p><button type="button" class="btn outline" @click="loadCategories">Повторить загрузку категорий</button></div>
    <template v-if="page === 'home'">
      <p v-if="siteLoading" class="container api-feedback" role="status">Загружаем главную страницу…</p>
      <div v-else-if="siteError" class="container api-feedback" role="alert"><p>{{ siteError }}</p><button type="button" class="btn outline" @click="loadSite">Повторить загрузку страницы</button></div>
      <LandingPage v-else-if="site" :categories="categories" :site="site" :demo="isDemo" @category="selectCategory" @navigate="navigateTarget" @feedback="openFeedback" />
    </template>

    <section v-if="page === 'home' || page === 'catalog'" id="catalog-results" class="catalog-section container" aria-labelledby="catalog-title">
      <div class="section-heading"><div><span class="eyebrow">{{ page === 'home' ? 'ПОДГОТОВЬТЕСЬ К НОВОМУ СЕЗОНУ' : 'ВИТРИНА / КАТАЛОГ' }}</span><h2 id="catalog-title">{{ filters.query ? 'Результаты поиска' : filters.category ? categoryName(filters.category) : site?.settings.catalogTitle || 'Каталог автозапчастей' }} <span class="heading-count">{{ catalogLoading || catalogError ? '—' : catalogTotal }}</span></h2></div><span v-if="isDemo" class="catalog-demo">Цены и остатки — демонстрационные</span></div>
      <div class="catalog-layout">
        <aside class="filters" aria-label="Фильтры каталога">
          <div class="filter-heading"><AppIcon name="grid" /> Категории</div>
          <fieldset><legend class="sr-only">Категория запчастей</legend><label class="filter-row"><input v-model="filters.category" type="radio" name="category" value="" data-focus-key="category-all">Все запчасти <small v-if="isDemo">{{ categories.reduce((sum, category) => sum + (category.productCount || 0), 0) }}</small></label><label v-for="category in categories" :key="category.id" class="filter-row"><input v-model="filters.category" type="radio" name="category" :value="category.id" :data-focus-key="`category-${category.id}`">{{ category.name }}<small v-if="categoryCount(category.id) !== null">{{ categoryCount(category.id) }}</small></label></fieldset>
          <div class="filter-divider"></div><label class="field-label" for="brand">Производитель</label><select v-if="isDemo" id="brand" v-model="filters.brand" name="brand" data-focus-key="brand"><option value="">Все бренды</option><option v-for="brand in brands" :key="brand">{{ brand }}</option></select><input v-else id="brand" v-model.lazy="filters.brand" name="brand" maxlength="80" placeholder="Название бренда" data-focus-key="brand">
          <label class="stock-check"><input v-model="filters.inStock" type="checkbox" name="inStock" data-focus-key="inStock"> Только в наличии</label>
          <button v-if="activeFilters" type="button" class="reset-link" data-action="reset" data-focus-key="reset" @click="resetFilters">Сбросить фильтры</button>
          <div class="filter-help"><AppIcon name="search" /><strong>Знаете артикул?</strong><p>Введите его в строку поиска. Так найти нужную деталь проще.</p></div>
        </aside>
        <div class="catalog-content">
          <div class="catalog-toolbar"><div class="result-label"><template v-if="filters.query">По запросу <strong>«{{ filters.query }}»</strong></template><template v-else>Детали для повседневных поездок</template></div><label class="sort-label" for="sort">Сортировка <select id="sort" v-model="filters.sort" name="sort" data-focus-key="sort"><option value="relevance">По умолчанию</option><option value="price_asc">Сначала дешевле</option><option value="price_desc">Сначала дороже</option></select></label></div>
          <p v-if="catalogLoading" class="api-feedback" role="status">Загружаем каталог…</p>
          <div v-if="catalogError" class="api-feedback" role="alert"><p>{{ catalogError }}</p><button type="button" class="btn outline" @click="loadCatalog">Повторить загрузку каталога</button></div>
          <p v-if="catalogStale" class="api-feedback" role="status">Цены и остатки ожидают обновления. Оформление станет доступно после синхронизации.</p>
          <div v-if="!catalogLoading && products.length && (!catalogError || catalogReadFailed)" class="product-grid"><ProductCard v-for="(product, index) in products" :key="product.id" :product="product" :demo="isDemo" :index="index" :quantity="cartQuantity(product.id)" :category="categoryName(product.category)" :price="money(product.priceKopecks)" @open="openProduct" @add="addProduct" /></div>
          <div v-else-if="!catalogLoading && !catalogError" class="empty-state"><AppIcon name="search" /><h3>Запчасти не найдены</h3><p>Попробуйте другой артикул или уберите часть фильтров.</p><button type="button" class="btn red" data-action="reset" @click="resetFilters">Показать весь каталог</button></div>
          <nav v-if="!catalogLoading && !catalogError && catalogTotal > PAGE_SIZE" class="pagination" aria-label="Страницы каталога"><button type="button" class="btn outline" :disabled="!catalogOffset" @click="catalogPage(-1)">Назад</button><span>{{ catalogOffset + 1 }}–{{ Math.min(catalogOffset + PAGE_SIZE, catalogTotal) }} из {{ catalogTotal }}</span><button type="button" class="btn outline" :disabled="catalogOffset + PAGE_SIZE >= catalogTotal" @click="catalogPage(1)">Далее</button></nav>
          <p class="catalog-footnote">Фотографии иллюстрируют категории. Совместимость с автомобилем и актуальные условия требуют проверки перед покупкой.</p>
        </div>
      </div>
    </section>

    <template v-else-if="page === 'cart'">
      <section v-if="receipt" class="container receipt"><span class="receipt-check"><AppIcon name="check" /></span><span class="eyebrow">{{ isDemo ? 'ДЕМОНСТРАЦИОННЫЙ СЦЕНАРИЙ ЗАВЕРШЁН' : 'ЗАЯВКА ПРИНЯТА' }}</span><h1>{{ isDemo ? 'Демозаказ создан' : 'Заказ создан' }}</h1><p class="receipt-number">№ {{ receipt.number }}</p><p v-if="isDemo">Заказ на {{ money(receipt.totalKopecks) }} сохранён в текущей вкладке.<br>Магазину и в 1С он не отправлен. Оплата не производится.</p><p v-else>Заявка на {{ money(receipt.totalKopecks) }} сохранена магазином. Менеджер уточнит наличие и условия получения. Оплата на сайте не производится.</p><div class="receipt-actions"><a v-if="isDemo" class="btn red" href="#crm">Открыть заказ в CRM <AppIcon name="arrow" /></a><button type="button" class="btn outline" data-action="continue" @click="continueShopping">Вернуться в каталог</button></div><small v-if="isDemo">Контакты хранятся в сессии браузера. Используйте тестовые данные.</small></section>
      <section v-else class="container cart-page">
        <div class="breadcrumbs"><a href="#home">Главная</a><span>/</span> Корзина</div><div class="section-heading"><div><span class="eyebrow">ЕЩЁ ОДИН ШАГ ДО ЗАКАЗА</span><h1>Ваша корзина <span class="heading-count">{{ count }}</span></h1></div><a class="text-link" href="#catalog">Продолжить покупки <AppIcon name="arrow" /></a></div>
        <div v-if="!cart.length" class="empty-state"><AppIcon name="cart" /><h2>Здесь пока пусто</h2><p>Найдите нужную запчасть и добавьте её в корзину.</p><a class="btn red" href="#catalog">Перейти в каталог <AppIcon name="arrow" /></a></div>
        <div v-else class="checkout-layout" :aria-busy="cartLoading || submitting">
          <div>
            <p v-if="cartLoading" class="api-feedback" role="status">Проверяем товары корзины…</p>
            <div v-if="cartError" class="api-feedback" role="alert"><p>{{ cartError }}</p><button type="button" class="btn outline" @click="loadCartProducts">Повторить проверку</button></div>
            <div v-else-if="!cartReady && !cartLoading && !catalogReadFailed" class="api-feedback" role="alert"><p>Остаток изменился или каталог ожидает обновления. Уменьшите количество либо повторите проверку.</p><button type="button" class="btn outline" @click="loadCartProducts">Повторить проверку</button></div>
            <div class="cart-items"><article v-for="item in cartItems" :key="item.productId" class="cart-item"><img v-if="item.product.image" :src="item.product.image" alt="Иллюстрация категории"><span v-else class="cart-item-placeholder" aria-hidden="true"><AppIcon name="box" /></span><div class="cart-item-info"><span class="product-brand">{{ item.product.brand }} · {{ item.product.sku }}</span><h3>{{ item.product.websiteTitle || item.product.name }}</h3><span class="muted">{{ money(item.product.priceKopecks) }} за шт.</span></div><div class="quantity-control"><button type="button" class="quantity-button" :disabled="submitting" data-action="quantity" :data-id="item.productId" :data-quantity="item.quantity - 1" :aria-label="`Уменьшить количество ${item.product.name}`" :data-focus-key="`minus-${item.productId}`" @click="changeQuantity(item.productId, item.quantity - 1)"><AppIcon name="minus" /></button><span aria-label="Количество">{{ item.quantity }}</span><button type="button" class="quantity-button" data-action="quantity" :data-id="item.productId" :data-quantity="item.quantity + 1" :aria-label="`Увеличить количество ${item.product.name}`" :data-focus-key="`plus-${item.productId}`" :disabled="submitting || item.quantity >= item.product.stock" @click="changeQuantity(item.productId, item.quantity + 1)"><AppIcon name="plus" /></button></div><strong>{{ money(item.product.priceKopecks * item.quantity) }}</strong><button type="button" class="remove-button" :disabled="submitting" data-action="quantity" :data-id="item.productId" data-quantity="0" :aria-label="`Удалить ${item.product.name}`" @click="changeQuantity(item.productId, 0)"><AppIcon name="close" /></button></article></div>
            <div v-if="isDemo" class="demo-notice"><AppIcon name="box" /><div><strong>Это демонстрационный заказ</strong><p>Контакты сохраняются только в текущей вкладке. Используйте тестовые данные; магазин и 1С их не получат.</p></div></div>
            <form id="checkout-form" class="checkout-form" @submit.prevent="submitOrder">
              <h2>Как с вами связаться?</h2><div class="form-grid"><label>Ваше имя <span>*</span><input v-model="draft.name" :disabled="submitting" name="name" autocomplete="name" required maxlength="100" placeholder="Алексей" data-focus-key="customer-name"></label><label>Телефон <span>*</span><input v-model="draft.phone" :disabled="submitting" name="phone" type="tel" autocomplete="tel" required maxlength="30" placeholder="+7 (999) 123-45-67" data-focus-key="customer-phone"></label><label class="full">Email <small>необязательно</small><input v-model="draft.email" :disabled="submitting" name="email" type="email" autocomplete="email" maxlength="254" placeholder="Для информации о заказе" data-focus-key="customer-email"></label></div>
              <h2>Как получить заказ?</h2><fieldset class="delivery-options"><legend class="sr-only">Способ получения</legend><label class="delivery-option" :class="{ chosen: draft.method === 'pickup' }"><input v-model="draft.method" :disabled="submitting" type="radio" name="method" value="pickup" data-focus-key="method-pickup"><AppIcon name="box" /><span><strong>Самовывоз</strong><small>Место и время согласует менеджер</small></span></label><label class="delivery-option" :class="{ chosen: draft.method === 'delivery' }"><input v-model="draft.method" :disabled="submitting" type="radio" name="method" value="delivery" data-focus-key="method-delivery"><AppIcon name="truck" /><span><strong>Доставка</strong><small>Стоимость уточняется отдельно</small></span></label></fieldset>
              <label class="address-field" :class="{ hidden: draft.method !== 'delivery' }">Адрес доставки <span>*</span><input v-model="draft.address" :disabled="submitting" name="address" autocomplete="street-address" maxlength="500" :required="draft.method === 'delivery'" placeholder="Город, улица, дом, квартира" data-focus-key="customer-address"></label>
              <p id="checkout-error" class="form-error" role="alert">{{ checkoutError }}</p><button v-if="checkoutError && !isDemo" type="button" class="btn outline" :disabled="submitting || cartLoading" @click="loadCartProducts">Проверить товары корзины</button>
            </form>
          </div>
          <aside class="order-summary"><span class="eyebrow">ВАШ ЗАКАЗ</span><h2>Итого</h2><div><span>Товары ({{ count }} шт.)</span><strong>{{ money(total) }}</strong></div><div><span>Получение</span><span>{{ draft.method === 'pickup' ? 'Самовывоз' : 'Доставка' }}</span></div><p>Стоимость доставки не включена.<br>Онлайн-оплаты в MVP нет.</p><div class="summary-total"><span>Сумма товаров</span><strong>{{ money(total) }}</strong></div><button type="submit" form="checkout-form" class="btn red checkout-submit" :disabled="submitting || (!isDemo && !cartReady)">{{ submitting ? 'Сохраняем…' : isDemo ? 'Создать демозаказ' : 'Оформить заказ' }} <AppIcon name="arrow" /></button><small>{{ isDemo ? 'Заказ будет доступен в демонстрационной CRM текущей вкладки.' : 'Итоговую цену и доступность проверяет магазин при оформлении.' }}</small></aside>
        </div>
      </section>
    </template>

    <section v-else class="container crm-page">
      <div class="breadcrumbs"><a href="#home">Витрина</a><span>/</span> Кабинет менеджера</div><div class="section-heading"><div><span class="eyebrow">LAVR / CRM</span><h1>{{ page === 'crm-catalog' ? 'Каталог магазина' : page === 'crm-content' ? 'Содержимое сайта' : page === 'crm-feedback' ? 'Обращения покупателей' : 'Заказы под контролем' }}<span class="red-period">.</span></h1><p class="section-subtitle">{{ page === 'crm-catalog' ? 'Категории и номенклатура для вашей витрины.' : page === 'crm-content' ? 'Главная, меню, публикации и изображения.' : page === 'crm-feedback' ? 'Контакты, запросы и результаты работы с клиентом.' : 'От первого обращения до выдачи запчастей.' }}</p></div><div class="integration-label"><AppIcon name="link" /><span><strong>{{ isDemo ? '1С не подключена' : 'Серверный режим' }}</strong><small>{{ isDemo ? 'Предусмотрен отдельный адаптер' : 'Данные через API' }}</small></span><i></i></div></div>
      <div v-if="isDemo && page === 'crm'" class="demo-notice crm-notice"><AppIcon name="shield" /><div><strong>Демонстрационный кабинет менеджера</strong><p>Показаны заказы текущей вкладки. Авторизация и серверное хранение предусмотрены в архитектуре; сейчас реальные клиенты не обслуживаются.</p></div></div>
      <p v-if="authLoading" class="api-feedback" role="status">Проверяем сессию сотрудника…</p>
      <form v-else-if="!session" class="staff-login" @submit.prevent="login"><h2>Вход для сотрудников</h2><label>Логин<input v-model="loginDraft.login" name="login" autocomplete="username" required maxlength="120"></label><label>Пароль<input v-model="loginDraft.password" name="password" type="password" autocomplete="current-password" required maxlength="256"></label><p v-if="authError" class="form-error" role="alert">{{ authError }}</p><button type="submit" class="btn red" :disabled="loginBusy">{{ loginBusy ? 'Входим…' : 'Войти' }}</button><button type="button" class="btn outline" :disabled="loginBusy" @click="enterCrm">Проверить сессию</button></form>
      <template v-else>
      <div v-if="!isDemo" class="staff-session"><span>{{ session.user.name }} · {{ isAdmin ? 'Администратор' : 'Менеджер' }}</span><button type="button" class="text-link" :disabled="logoutBusy" @click="logout">Выйти</button><p v-if="authError" class="form-error" role="alert">{{ authError }}</p></div>
      <nav class="crm-tabs" aria-label="Разделы CRM"><a href="#crm" :class="{ selected: page === 'crm' }" :aria-current="page === 'crm' ? 'page' : undefined"><AppIcon name="box" /> Заказы</a><a href="#crm-feedback" :class="{ selected: page === 'crm-feedback' }" :aria-current="page === 'crm-feedback' ? 'page' : undefined"><AppIcon name="user" /> Обращения</a><a v-if="isAdmin" href="#crm-catalog" :class="{ selected: page === 'crm-catalog' }" :aria-current="page === 'crm-catalog' ? 'page' : undefined"><AppIcon name="grid" /> Каталог</a><a v-if="isAdmin" href="#crm-content" :class="{ selected: page === 'crm-content' }" :aria-current="page === 'crm-content' ? 'page' : undefined"><AppIcon name="grid" /> Контент сайта</a></nav>
      <FeedbackManager v-if="page === 'crm-feedback'" :key="session.user.id" :api="api" :session="session" :case-id="caseId" @staff-error="handleStaffError" @dirty="feedbackDirty = $event" @open-case="openFeedbackCase" /><ContentManager v-else-if="page === 'crm-content' && isAdmin" :api="api" :categories="categories" @changed="loadSite" @staff-error="handleStaffError" @dirty="contentDirty = $event" /><p v-else-if="page === 'crm-content'" class="api-feedback" role="alert">Редактор сайта доступен администратору.</p><CatalogManager v-else-if="page === 'crm-catalog' && isAdmin" :api="api" @changed="refreshData" @staff-error="handleStaffError" /><p v-else-if="page === 'crm-catalog'" class="api-feedback" role="alert">Управление каталогом доступно администратору.</p>
      <template v-else>
      <div v-if="!ordersLoading && !ordersError" class="metric-grid"><article><span>Заказов в выборке</span><strong>{{ orders.length }}<small>{{ isDemo ? 'в текущей вкладке' : 'на этой странице' }}</small></strong><AppIcon name="box" /></article><article><span>В работе</span><strong>{{ activeOrders.length }}<small>в текущей выборке</small></strong><AppIcon name="clock" /></article><article><span>Сумма заказов</span><strong>{{ money(orderTotal) }}<small>в выборке, без отменённых</small></strong><AppIcon name="cart" /></article></div>
      <div class="crm-orders">
        <div class="crm-toolbar"><h2>Список заказов <span class="heading-count">{{ ordersLoading || ordersError ? '—' : ordersTotal }}</span></h2><form id="order-search-form" class="crm-search" @submit.prevent="searchOrders"><AppIcon name="search" /><input v-model="orderSearchQuery" name="orderQuery" type="search" maxlength="120" aria-label="Поиск заказов" placeholder="Номер, клиент или телефон" data-focus-key="order-search"><button type="submit" aria-label="Найти заказ"><AppIcon name="arrow" /></button></form></div>
        <div class="status-filters" role="group" aria-label="Фильтр статуса заказа"><button v-for="[status, label] in [['', 'Все заказы'], ...Object.entries(ORDER_STATUSES)]" :key="status" type="button" class="status-filter" :class="{ selected: orderFilter === status }" data-action="order-filter" :data-status="status" :aria-pressed="orderFilter === status" :data-focus-key="`status-filter-${status}`" @click="orderFilter = status">{{ label }}</button></div>
        <p v-if="ordersLoading" class="api-feedback" role="status">Загружаем заказы…</p><div v-else-if="ordersError" class="api-feedback" role="alert"><p>{{ ordersError }}</p><button type="button" class="btn outline" @click="loadOrders">Повторить загрузку заказов</button></div>
        <template v-else-if="visibleOrders.length"><p id="orders-scroll-hint" class="table-scroll-hint">Проведите по таблице в сторону, чтобы увидеть остальные колонки.</p><div class="table-wrap" role="region" tabindex="0" aria-label="Заказы: прокрутка таблицы" aria-describedby="orders-scroll-hint"><table class="orders-table"><thead><tr><th>Заказ / дата</th><th>Клиент</th><th>Статус</th><th>Сумма товаров</th><th>Обмен с 1С</th><th><span class="sr-only">Действия</span></th></tr></thead><tbody><tr v-for="order in visibleOrders" :key="order.id"><td><button type="button" class="order-number" data-action="order" :data-id="order.id" :data-focus-key="`order-${order.id}`" @click="openModal('order', order.id, $event)">№ {{ order.number }}</button><small>{{ date(order.createdAt) }}</small></td><td><strong>{{ order.customer.name }}</strong><small>{{ order.customer.phone }}</small></td><td><span class="status-badge" :class="`status-${order.status}`"><i></i>{{ ORDER_STATUSES[order.status] }}</span></td><td><strong>{{ money(order.totalKopecks) }}</strong><small>{{ order.items.reduce((sum, item) => sum + item.quantity, 0) }} шт.</small></td><td><span class="sync-muted">{{ isDemo ? 'Не подключён' : { pending_sync: 'Ожидает обмена', exporting: 'Передаётся', exported: 'Передан в 1С', error: 'Ошибка обмена', cancelled_before_sync: 'Передача отменена', not_connected: 'Не подключён' }[order.integrationStatus] }}</span></td><td><button type="button" class="table-action" data-action="order" :data-id="order.id" :data-focus-key="`detail-${order.id}`" :aria-label="`Открыть заказ ${order.number}`" @click="openModal('order', order.id, $event)"><AppIcon name="arrow" /></button></td></tr></tbody></table></div></template>
        <div v-else class="empty-state"><AppIcon name="box" /><h3>{{ orderQuery || orderFilter ? 'Заказы не найдены' : 'Первый заказ — начало пути' }}</h3><p>{{ orderQuery || orderFilter ? 'Измените запрос или фильтр статуса.' : isDemo ? 'Оформите демозаказ в витрине. Здесь появятся его состав, клиент и статус.' : 'Новые заявки покупателей появятся здесь.' }}</p><button v-if="orderQuery || orderFilter" type="button" class="btn outline" data-action="order-reset" @click="resetOrders">Показать все заказы</button><a v-else class="btn red" href="#catalog">Открыть витрину <AppIcon name="arrow" /></a></div>
        <nav v-if="!ordersLoading && !ordersError && ordersTotal > PAGE_SIZE" class="pagination" aria-label="Страницы заказов"><button type="button" class="btn outline" :disabled="!ordersOffset" @click="ordersPage(-1)">Назад</button><span>{{ ordersOffset + 1 }}–{{ Math.min(ordersOffset + PAGE_SIZE, ordersTotal) }} из {{ ordersTotal }}</span><button type="button" class="btn outline" :disabled="ordersOffset + PAGE_SIZE >= ordersTotal" @click="ordersPage(1)">Далее</button></nav>
      </div>
      </template>
      </template>
      <div class="architecture-callout"><AppIcon name="link" /><div><strong>Подготовлено к интеграции с 1С</strong><p>Каталог, цены и остатки поступают из учётной системы. Заказы уходят через серверный адаптер с защитой от повторной отправки.</p><small v-if="isDemo">Это проект будущего обмена. В этом прототипе обмен не выполняется.</small></div><span class="architecture-pill">АРХИТЕКТУРА MVP</span></div>
    </section>
  </main>

  <footer class="footer"><div class="container footer-top"><a class="logo" href="#home">{{ storeName }}<span class="logo-square"></span><small>АВТОЗАПЧАСТИ</small></a><p>Детали для дороги.<br>Внимание к вашему автомобилю.</p><div><strong>На сайте</strong><a href="#catalog">Каталог запчастей</a><a href="#cart">Корзина</a><a href="#crm">Кабинет менеджера</a></div><div v-if="isDemo"><strong>Об этом MVP</strong><span>Тестовые цены и остатки</span><span>Оформление без оплаты</span><span>Интеграция 1С: спроектирована</span></div><div><strong>Контакты</strong><template v-if="site"><span>{{ site.settings.phone }}</span><span>{{ site.settings.address }}</span><span>{{ site.settings.hours }}</span></template><button type="button" class="feedback-footer-link" data-testid="footer-feedback-contact" @click="openFeedback('contact', $event)">Свяжитесь со мной</button><button type="button" class="feedback-footer-link" data-testid="footer-feedback-request" @click="openFeedback('request', $event)">Оставить запрос</button></div></div><div class="container footer-bottom"><span>© 2026 LAVR · концепт магазина автозапчастей</span><span>Фото иллюстративные · <button type="button" class="footer-link" data-action="credits" data-focus-key="credits" @click="openModal('credits', null, $event)">Источники изображений</button></span></div></footer>

  <FeedbackForm ref="feedbackForm" :api="api" />

  <dialog v-if="modal" ref="detailDialog" class="detail-dialog" :class="{ wide: modalProduct }" aria-labelledby="dialog-title" @cancel.prevent="closeModal()">
    <button type="button" class="dialog-close" data-action="close" aria-label="Закрыть" @click="closeModal()"><AppIcon name="close" /></button>
    <p v-if="modalLoading" id="dialog-title" class="api-feedback" role="status">Загружаем данные…</p><div v-else-if="modalError" class="api-feedback" role="alert"><h2 id="dialog-title">Не удалось загрузить данные</h2><p>{{ modalError }}</p><button type="button" class="btn outline" @click="openModal(modal.type, modal.id)">Повторить загрузку</button></div>
    <div v-else-if="modalProduct" class="detail-grid"><div class="detail-image"><img v-if="modalProduct.image" :src="modalProduct.image" :alt="`Иллюстрация категории ${categoryName(modalProduct.category)}`"><AppIcon v-else name="box" /></div><div><span class="eyebrow">{{ modalProduct.brand }} / {{ modalProduct.sku }}</span><h2 id="dialog-title">{{ modalProduct.websiteTitle || modalProduct.name }}</h2><p>{{ modalProduct.websiteDescription || modalProduct.description || 'Характеристики и совместимость проверяются перед покупкой.' }}</p><dl class="product-specs"><div v-for="(value, key) in modalProduct.specs" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></div><div><dt>Внутренний ID</dt><dd>{{ modalProduct.id }}</dd></div><div v-if="modalProduct.externalId"><dt>{{ isDemo ? 'ID из 1С (демо)' : 'ID из 1С' }}</dt><dd>{{ modalProduct.externalId }}</dd></div><div><dt>{{ isDemo ? 'Остаток в демокаталоге' : 'Доступно' }}</dt><dd>{{ modalProduct.stock }} шт.</dd></div></dl><strong class="detail-price">{{ money(modalProduct.priceKopecks) }}</strong><button type="button" class="btn red" data-action="add" :data-id="modalProduct.id" :disabled="!modalProduct.stock || cartQuantity(modalProduct.id) >= modalProduct.stock" @click="addProduct(modalProduct.id)"><AppIcon name="plus" /> Добавить в корзину</button><p v-if="isDemo" class="detail-disclaimer">Фотография иллюстрирует категорию и не подтверждает внешний вид этого артикула. Подбор по VIN не реализован.</p></div></div>
    <template v-else-if="modalOrder">
      <span class="eyebrow">{{ isDemo ? 'ДЕМОНСТРАЦИОННАЯ CRM' : 'CRM / ЗАКАЗ' }}</span><h2 id="dialog-title">Заказ № {{ modalOrder.number }}</h2><div class="order-detail-meta"><span class="status-badge" :class="`status-${modalOrder.status}`"><i></i>{{ ORDER_STATUSES[modalOrder.status] }}</span><span>{{ date(modalOrder.createdAt) }} · МСК</span></div><div class="order-contact"><div><small>Клиент</small><strong>{{ modalOrder.customer.name }}</strong><span>{{ modalOrder.customer.phone }}</span><span v-if="modalOrder.customer.email">{{ modalOrder.customer.email }}</span></div><div><small>Получение</small><strong>{{ modalOrder.fulfillment.method === 'delivery' ? 'Доставка' : 'Самовывоз' }}</strong><span>{{ modalOrder.fulfillment.method === 'delivery' ? modalOrder.fulfillment.address : 'Место согласуется отдельно' }}</span></div></div>
      <div class="order-detail-items"><div v-for="item in modalOrder.items" :key="item.productId"><span><strong>{{ item.name }}</strong><small>{{ item.brand }} · {{ item.sku }} · {{ item.quantity }} шт. × {{ money(item.unitPriceKopecks) }}</small></span><strong>{{ money(item.quantity * item.unitPriceKopecks) }}</strong></div></div><div class="detail-total"><span>Сумма товаров</span><strong>{{ money(modalOrder.totalKopecks) }}</strong></div><p class="muted">Доставка не включена.<span v-if="isDemo"> Обмен с 1С не подключён.</span></p><div class="order-status-actions"><button v-for="status in statusActions" :key="status" type="button" class="btn" :class="status === 'cancelled' ? 'outline danger' : 'red'" data-action="status" :disabled="statusBusy" :data-id="modalOrder.id" :data-status="status" @click="updateOrderStatus(modalOrder.id, status)">{{ ORDER_STATUSES[status] }}</button><span v-if="!statusActions.length" class="muted">Изменение статуса сейчас недоступно.</span></div>
    </template>
    <template v-else-if="modal.type === 'credits'">
      <span class="eyebrow">ВИЗУАЛЬНЫЕ МАТЕРИАЛЫ</span><h2 id="dialog-title">Источники изображений</h2><p>Фотографии иллюстрируют категории. Они не являются проверенными фотографиями артикулов и не означают сотрудничество с изображёнными брендами.</p><ul class="credits-list"><li>Автомобиль: Samuele Errico Piccarini · <a href="https://unsplash.com/photos/black-mercedes-benz-vehicle-YSJSs78rw10" target="_blank" rel="noopener noreferrer">Unsplash</a> · <a href="https://unsplash.com/license" target="_blank" rel="noopener noreferrer">Unsplash License</a>.</li><li>Моторное масло: Devid Bianchetti, «Olio Motore.jpg» · <a href="https://commons.wikimedia.org/wiki/File:Olio_Motore.jpg" target="_blank" rel="noopener noreferrer">Wikimedia Commons</a> · <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noopener noreferrer">CC BY-SA 4.0</a>. Без изменения исходного содержимого.</li><li>Тормозной диск (3D-модель): Atharv Chandel · <a href="https://commons.wikimedia.org/wiki/File:Ventilated_Curve_Drilled_%26_Slotted_Brake_Rotor.jpg" target="_blank" rel="noopener noreferrer">Wikimedia Commons</a> · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">CC BY 4.0</a>.</li><li>Масляный и воздушный фильтры: Elwood · <a href="https://commons.wikimedia.org/wiki/File:Filtro_olio_avvitabile.JPG" target="_blank" rel="noopener noreferrer">масляный фильтр</a>, <a href="https://commons.wikimedia.org/wiki/File:Filtro_aria_pannello.JPG" target="_blank" rel="noopener noreferrer">воздушный фильтр</a> · public domain.</li><li>Свеча зажигания: Tennen-Gas · <a href="https://commons.wikimedia.org/wiki/File:Sparkplug3_H.JPG" target="_blank" rel="noopener noreferrer">Wikimedia Commons</a> · public domain.</li></ul>
    </template>
  </dialog>
  <div id="toast" class="toast" :class="{ visible: toast }" role="status" aria-live="polite">{{ toast }}</div>
</template>

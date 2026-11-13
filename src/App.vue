<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import AppIcon from './components/AppIcon.vue';
import ProductCard from './components/ProductCard.vue';
import CatalogManager from './components/CatalogManager.vue';
import LandingPage from './components/LandingPage.vue';
import ContentManager from './components/ContentManager.vue';
import FeedbackForm from './components/FeedbackForm.vue';
import FeedbackManager from './components/FeedbackManager.vue';
import AccountAccess from './components/AccountAccess.vue';
import CustomerAccount from './components/CustomerAccount.vue';
import UsersManager from './components/UsersManager.vue';
import { parseCartDraft, setDraftQuantity } from './cart-draft.js';
import { productUnit, quantityMatchesProduct, canAddQuantity, quantityStep, stepQuantity, formatQuantity, lineTotalKopecks, snapshotLineTotal, sumKopecks } from './quantity.js';
import './quantity.css';
import { ORDER_STATUSES, orderActions } from './order-labels.js';
import { checkoutKey, clearCheckoutKey } from './checkout-attempt.js';
import { parseRoute, parsePasswordResetLink, routeHash, safeReturnTo, targetHash } from './routes.js';

const { api } = defineProps({ api: { type: Object, required: true } });
const isDemo = api.mode === 'demo';
const CART_KEY = 'lavr.cart.v1';
const storageIssue = ref('');
function readCart() {
  try { return parseCartDraft(JSON.parse(localStorage.getItem(CART_KEY) || '[]')); }
  catch { storageIssue.value = 'Не удалось прочитать черновик корзины. Сохранённые данные не изменены.'; return []; }
}
const cart = ref(readCart());
const selectedCartIds = ref(null);
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
const failedCartIds = ref([]);
const checkingCartIds = ref([]);
const orders = ref([]);
const ordersTotal = ref(0);
const ordersOffset = ref(0);
const ordersLoading = ref(false);
const ordersError = ref('');
const session = ref(null);
const authLoading = ref(false);
const needsRelogin = ref(false);
const identityRevision = ref(0);
const authError = ref('');
const statusBusy = ref(false);
const logoutBusy = ref(false);
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
const authDialog = ref(null);
const mobileMenu = ref(null);
const contentDirty = ref(false);
const feedbackDirty = ref(false);
const feedbackForm = ref(null);
const receipt = ref(null);
const submitting = ref(false);
const checkoutError = ref('');
const checkoutCleanupPending = ref(false);
const defaultDraft = () => ({ name: session.value?.user.name || '', phone: session.value?.user.phone || '', email: session.value?.user.email || '' });
const draft = reactive(defaultDraft());
const toast = ref('');
const productNotice = ref('');
let toastTimer;
let siteRequest = 0;
let catalogRequest = 0;
let ordersRequest = 0;
let cartRequest = 0;
let authRequest = 0;
let checkoutRecovery = null;
let modalRequest = 0;
const money = kopecks => kopecks === null ? '—' : new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(kopecks / 100);
const cartLineTotal = item => { if ((item.expectedUnit ?? 'шт') !== productUnit(item.product)) return null; try { return lineTotalKopecks(item.product.priceKopecks, item.quantity); } catch { return null; } };
const date = value => new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Moscow' }).format(new Date(value));
const categoryName = id => categories.value.find(category => category.id === id)?.name || id;
const categoryCount = id => categories.value.find(category => category.id === id)?.productCount ?? null;
const brands = computed(() => [...new Set([...productCache.value.values()].map(product => product.brand))].sort());
const authPages = ['login', 'signup', 'activate', 'password-reset'];
const resetLink = ref(parsePasswordResetLink(location.hash));
const navigationRoute = ref(parseRoute(location.hash));
if (resetLink.value) history.replaceState(null, '', routeHash(navigationRoute.value));
const authBackground = ref(parseRoute('#home'));
const isAuth = computed(() => authPages.includes(navigationRoute.value.page));
const route = computed(() => isAuth.value ? authBackground.value : navigationRoute.value);
const page = computed(() => route.value.page);
const caseId = computed(() => route.value.caseId);
const orderId = computed(() => route.value.orderId);
filters.category = route.value.categoryId;
const targetNotice = ref('');
let detailOpener;
let authOpener;
const authHash = name => routeHash({ page: name, returnTo: navigationRoute.value.returnTo || (isCrm.value || isAccount.value ? safeReturnTo(routeHash(route.value)) : '') });
let acceptedHash = location.hash;
const isCrm = computed(() => ['crm', 'crm-catalog', 'crm-content', 'crm-feedback', 'admin-users'].includes(page.value));
const isAdmin = computed(() => session.value?.capabilities.editSite === true);
const canOperate = computed(() => session.value?.capabilities.operate === true);
const canManageUsers = computed(() => session.value?.capabilities.manageUsers === true);
const isAccount = computed(() => ['account', 'account-orders', 'account-feedback'].includes(page.value));
const activeFilters = computed(() => filters.query || filters.category || filters.brand || filters.inStock);
const count = computed(() => cart.value.length);
const cartItems = computed(() => cart.value.map(item => ({ ...item, product: productCache.value.get(item.productId) || { name: 'Товар пока недоступен', sku: item.productId, brand: '', image: '', priceKopecks: 0, stock: 0 } })));
const selectedItems = computed(() => cart.value.filter(item => selectedCartIds.value === null || selectedCartIds.value.includes(item.productId)));
const selectedCartItems = computed(() => cartItems.value.filter(item => selectedItems.value.some(selected => selected.productId === item.productId)));
const selectedCartLoading = computed(() => selectedItems.value.some(item => checkingCartIds.value.includes(item.productId)));
const cartReady = computed(() => !selectedCartLoading.value && !catalogReadFailed.value && selectedItems.value.every(item => !failedCartIds.value.includes(item.productId) && productCache.value.has(item.productId) && quantityMatchesProduct(item, productCache.value.get(item.productId))));
function totalFor(items) {
  if (!items.every(item => productCache.value.has(item.productId) && quantityMatchesProduct(item, productCache.value.get(item.productId)))) return null;
  try { return sumKopecks(items.map(item => lineTotalKopecks(productCache.value.get(item.productId).priceKopecks, item.quantity))); }
  catch { return null; }
}
const total = computed(() => totalFor(cart.value));
const selectedTotal = computed(() => totalFor(selectedItems.value));
const cartQuantity = id => cart.value.find(item => item.productId === id)?.quantity || 0;
const visibleOrders = computed(() => orders.value);
const activeOrders = computed(() => orders.value.filter(order => !['completed', 'cancelled'].includes(order.status)));
const orderTotal = computed(() => { const total = orders.value.filter(order => order.status !== 'cancelled').reduce((sum, order) => sum + order.totalKopecks, 0); return Number.isSafeInteger(total) ? total : null; });
const statusActions = computed(() => modalOrder.value ? orderActions(modalOrder.value, isDemo) : []);
const storeName = computed(() => site.value?.settings.storeName || 'MIRZAP67');
const menuItems = computed(() => site.value && (!isDemo || site.value.menuConfigured || site.value.menu.length) ? site.value.menu : categories.value.slice(0, 3).map(category => ({ label: category.name, target: { type: 'category', categoryId: category.id } })));
const activeMenuHash = computed(() => routeHash({ ...route.value, productId: '' }));

watch(page, current => {
  document.title = `${{ home: 'Автозапчасти для вашего автомобиля', catalog: 'Каталог автозапчастей', cart: 'Корзина', checkout: 'Оформление заказа', crm: 'CRM · заказы', 'crm-catalog': 'CRM · каталог', 'crm-content': 'CRM · контент сайта', 'crm-feedback': 'CRM · обращения', 'admin-users': 'Пользователи', login: 'Вход', signup: 'Регистрация', activate: 'Подтверждение email', 'password-reset': 'Восстановление пароля', account: 'Мои заказы', 'account-orders': 'Мои заказы', 'account-feedback': 'Мои обращения' }[current]} — ${storeName.value}`;
  if (['crm', 'crm-catalog', 'crm-content', 'crm-feedback', 'admin-users', 'account', 'account-orders', 'account-feedback'].includes(current)) enterCrm();
}, { immediate: true });

watch([page, () => JSON.stringify(cart.value.map(item => item.productId))], ([current]) => {
  if (['cart', 'checkout'].includes(current)) loadCartProducts();
}, { immediate: true });
watch(() => cart.value.map(item => item.productId), (ids, previous = []) => {
  if (selectedCartIds.value === null) return;
  selectedCartIds.value = selectedCartIds.value.filter(id => ids.includes(id));
  if (page.value !== 'checkout') selectedCartIds.value.push(...ids.filter(id => !previous.includes(id)));
});
watch(() => session.value?.user.id, () => {
  if (session.value) for (const key of ['name', 'phone', 'email']) if (!draft[key]) draft[key] = session.value.user[key] || '';
}, { flush: 'sync' });
watch(filters, () => { catalogOffset.value = 0; loadCatalog(); });
watch([orderQuery, orderFilter], () => { ordersOffset.value = 0; if (session.value) loadOrders(); });
watch([page, authLoading, needsRelogin], () => {
  if (!isAuth.value && !session.value && !authLoading.value && !needsRelogin.value && (isCrm.value || isAccount.value)) go(authHash('login'));
});
watch(isAuth, async visible => {
  await nextTick();
  if (visible) authDialog.value?.showModal();
  else authDialog.value?.close();
}, { immediate: true });

async function closeAuth() {
  go(!session.value && (isCrm.value || isAccount.value) ? '#home' : routeHash(authBackground.value));
  await nextTick();
  if (authOpener?.isConnected) authOpener.focus({ preventScroll: true });
}

function notify(message) {
  toast.value = message;
  if (modal.value?.type === 'product') productNotice.value = message;
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
  checkingCartIds.value = ids; failedCartIds.value = [];
  await Promise.allSettled(ids.map(async id => {
    try {
      const result = await api.getProduct(id);
      if (request === cartRequest) rememberProducts([result.product]);
    } catch (error) {
      if (request === cartRequest) failedCartIds.value.push(id);
    } finally {
      if (request === cartRequest) checkingCartIds.value = checkingCartIds.value.filter(checking => checking !== id);
    }
  }));
  if (request !== cartRequest) return;
  if (failedCartIds.value.length) cartError.value = 'Не удалось проверить товары. Корзина сохранена. Проверка повторится при следующем открытии корзины; недоступный товар можно удалить.';
  cartLoading.value = false;
}
async function refreshData() {
  await Promise.allSettled([loadSite(), loadCategories(), loadCatalog(), loadCartProducts()]);
}
function clearIdentity(preserveDraft = false) {
  contentDirty.value = false; feedbackDirty.value = false;
  authRequest++; identityRevision.value++; authLoading.value = false; ordersRequest++;
  session.value = null; orders.value = []; ordersTotal.value = 0; ordersLoading.value = false;
  receipt.value = null;
  if (!preserveDraft) Object.assign(draft, defaultDraft());
  closeModal(false);
}
function restoreSession(result) {
  clearIdentity(); session.value = result; needsRelogin.value = false; authError.value = '';
  if (checkoutRecovery?.actor === result.user.id) Object.assign(draft, checkoutRecovery.draft);
  checkoutRecovery = null;
}
function acceptSession(result) {
  restoreSession(result);
  ordersOffset.value = 0;
  go('account-orders');
}
function changingIdentity() { clearIdentity(); needsRelogin.value = true; }
function passwordResetComplete() {
  checkoutRecovery = null; clearIdentity(); needsRelogin.value = false; authError.value = '';
  go('login');
}
function handleStaffError(error) {
  if (error.status === 401 || error.status === 403) {
    if (session.value) checkoutRecovery = { actor: session.value.user.id, draft: { ...draft } };
    clearIdentity(true); needsRelogin.value = true;
    authError.value = error.status === 403 ? 'Доступ изменился. Войдите снова.' : 'Сессия завершена. Войдите снова.';
  }
}
async function enterCrm() {
  if (authLoading.value || logoutBusy.value) return;
  if (!session.value) {
    const request = ++authRequest;
    authLoading.value = true; authError.value = '';
    try { const result = await api.getUserSession(); if (request === authRequest) { session.value = result; needsRelogin.value = false; } }
    catch (error) { if (request === authRequest && error.status !== 401) authError.value = error.message; }
    finally { if (request === authRequest) authLoading.value = false; }
  }
  if (canOperate.value && page.value === 'crm') await loadOrders();
}
async function logout() {
  if (logoutBusy.value) return;
  if ((contentDirty.value || feedbackDirty.value) && !window.confirm('Есть несохранённые изменения или незавершённое сохранение. Выйти из кабинета?')) return;
  checkoutRecovery = null; clearIdentity(); needsRelogin.value = true;
  const current = authRequest; logoutBusy.value = true;
  try { await api.logout(); if (current === authRequest) { needsRelogin.value = false; authError.value = ''; if (!isCrm.value) go('login'); } }
  catch (error) { if (current === authRequest) authError.value = error.message; }
  finally { logoutBusy.value = false; }
}
async function loadOrders() {
  if (!canOperate.value) return;
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
  try { requireCatalog(); const product = productCache.value.get(id); if (!product) throw new Error('Товар не загружен.'); const existing = cart.value.find(item => item.productId === id); if (existing && (existing.expectedUnit ?? 'шт') !== productUnit(product)) throw new Error('Единица измерения изменилась. Проверьте позицию в корзине.'); saveCart(setDraftQuantity(cart.value, id, stepQuantity(cartQuantity(id), productUnit(product)), product.stock, productUnit(product))); receipt.value = null; notify('Товар добавлен в корзину'); }
  catch (error) { notify(error.message); }
}
function changeQuantity(id, quantity, confirmUnit = false) {
  if (submitting.value) return;
  try { requireCatalog(); const product = productCache.value.get(id); const existing = cart.value.find(item => item.productId === id); if (quantity !== 0 && !confirmUnit && existing && (existing.expectedUnit ?? 'шт') !== productUnit(product || {})) throw new Error('Единица измерения изменилась. Сначала выберите количество заново.'); saveCart(setDraftQuantity(cart.value, id, quantity, product?.stock || 0, productUnit(product || {}))); }
  catch (error) { notify(error.message); }
}
function go(target) {
  if (parseRoute(target).page === 'password-reset' && !parsePasswordResetLink(target)) resetLink.value = null;
  const hash = routeHash(parseRoute(target));
  if (location.hash !== hash) { location.hash = hash; onHashChange(); }
}
function openFeedback(type, event) { feedbackForm.value?.open(type, event); }
function resetFilters() { Object.assign(filters, defaultFilters()); searchQuery.value = ''; if (route.value.categoryId) go('catalog'); }
function selectCategory(id) { go(routeHash({ page: 'catalog', categoryId: id })); }
function searchCatalog() { Object.assign(filters, defaultFilters(), { query: searchQuery.value.trim() }); go('catalog'); }
function searchOrders() { orderQuery.value = orderSearchQuery.value.trim(); }
function resetOrders() { orderQuery.value = ''; orderSearchQuery.value = ''; orderFilter.value = ''; }
function catalogPage(delta) { catalogOffset.value += delta * PAGE_SIZE; loadCatalog(); document.querySelector('#catalog-results')?.scrollIntoView(); }
function ordersPage(delta) { ordersOffset.value += delta * PAGE_SIZE; loadOrders(); }
async function openModal(type, id, event) {
  const current = ++modalRequest;
  productNotice.value = '';
  modal.value = { type, id, opener: event?.currentTarget?.dataset.focusKey || detailOpener }; detailOpener = undefined; modalProduct.value = null; modalOrder.value = null; modalError.value = ''; modalLoading.value = type !== 'credits';
  await nextTick(); detailDialog.value?.showModal();
  if (type === 'credits') return;
  try {
    const result = type === 'product' ? await api.getProduct(id) : await api.getOrder(id);
    if (modalRequest !== current) return;
    if (type === 'product') { modalProduct.value = result.product; rememberProducts([result.product]); } else modalOrder.value = result;
  } catch (error) { if (modalRequest === current) { modalError.value = error.message; handleStaffError(error); } }
  finally { if (modalRequest === current) modalLoading.value = false; }
}
function openProduct(id, event) { if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button)) return; event?.preventDefault(); detailOpener = event?.currentTarget?.dataset.focusKey; go(routeHash({ page: 'catalog', categoryId: route.value.categoryId, productId: id })); }
function openOrder(id, event) { if (event && (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button)) return; event?.preventDefault(); detailOpener = event?.currentTarget?.dataset.focusKey; go(routeHash({ page: 'crm', orderId: id })); }
async function closeModal(restoreFocus = true) {
  const opener = modal.value?.opener;
  if (restoreFocus && modal.value?.type === 'product') go(routeHash({ page: 'catalog', categoryId: route.value.categoryId }));
  if (restoreFocus && modal.value?.type === 'order') go(routeHash({ page: 'crm' }));
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
  const generation = authRequest;
  const actor = session.value?.user.id || 'guest';
  submitting.value = true; checkoutError.value = '';
  try {
    if (authLoading.value || needsRelogin.value) throw new Error('Войдите снова перед оформлением. Корзина и форма сохранены.');
    requireCatalog();
    if (checkoutCleanupPending.value) { clearCheckoutKey(undefined, actor); checkoutCleanupPending.value = false; }
    if (selectedCartIds.value === null || !selectedItems.value.length || !cartReady.value || selectedTotal.value === null) throw new Error('Проверьте доступность и количество товаров корзины перед оформлением.');
    const submittedItems = selectedItems.value.map(item => ({ ...item }));
    const common = { fulfillment: { method: 'pickup', address: '' }, items: submittedItems.map(item => ({ ...item, expectedUnitPriceKopecks: productCache.value.get(item.productId).priceKopecks })) };
    const body = session.value ? { ...common, ...(draft.name && draft.name !== session.value.user.name ? { name: draft.name } : {}), ...Object.fromEntries(['phone', 'email'].filter(key => draft[key] !== (session.value.user[key] || '')).map(key => [key, draft[key]])) } : { customer: { name: draft.name, phone: draft.phone, email: draft.email }, ...common };
    const key = await checkoutKey(body, undefined, actor);
    if (generation !== authRequest) return;
    const result = await (actor === 'guest' ? api.createOrder(body, key) : api.createOwnOrder(body, key));
    if (generation !== authRequest) return;
    receipt.value = result;
    saveCart(cart.value.filter(item => !submittedItems.some(submitted => submitted.productId === item.productId && submitted.quantity === item.quantity && (submitted.expectedUnit ?? 'шт') === (item.expectedUnit ?? 'шт'))));
    Object.assign(draft, defaultDraft());
    try { clearCheckoutKey(undefined, actor); } catch { checkoutCleanupPending.value = true; storageIssue.value = 'Заказ создан. Восстановите доступ к хранилищу браузера перед новым заказом.'; }
    window.scrollTo({ top: 0, behavior: 'instant' });
  } catch (error) {
    if (generation !== authRequest) return;
    checkoutError.value = error.message; handleStaffError(error);
    if (['UNIT_CHANGED', 'PRICE_CHANGED', 'OUT_OF_STOCK', 'PRODUCT_UNAVAILABLE'].includes(error.code)) await loadCartProducts();
    if (generation !== authRequest) return;
    await nextTick(); document.querySelector('#checkout-error')?.scrollIntoView({ block: 'center' });
  } finally { submitting.value = false; }
}
function selectCartItem(id, checked) {
  const ids = selectedItems.value.map(item => item.productId);
  selectedCartIds.value = checked ? [...new Set([...ids, id])] : ids.filter(selected => selected !== id);
}
function proceedToCheckout() {
  if (!cartReady.value || !selectedItems.value.length || selectedTotal.value === null) return;
  selectedCartIds.value = selectedItems.value.map(item => item.productId);
  checkoutError.value = ''; receipt.value = null;
  go('checkout');
}
function continueShopping() { receipt.value = null; go('catalog'); }
function closeMobileMenu() {
  if (!mobileMenu.value?.open) return;
  mobileMenu.value.open = false;
  mobileMenu.value.querySelector('summary')?.focus({ preventScroll: true });
}
function onHashChange() {
  if (location.hash === acceptedHash) return;
  const nextResetLink = parsePasswordResetLink(location.hash);
  const target = parseRoute(location.hash);
  const authTransition = isAuth.value || authPages.includes(target.page);
  if (!isAuth.value && authPages.includes(target.page)) { authBackground.value = route.value; authOpener = document.activeElement; }
  const screenTarget = authPages.includes(target.page) ? authBackground.value : target;
  const sameList = page.value === screenTarget.page && (screenTarget.page === 'crm' || screenTarget.page === 'catalog' && route.value.categoryId === screenTarget.categoryId);
  if ((contentDirty.value || feedbackDirty.value) && location.hash !== acceptedHash
    && !window.confirm('Есть несохранённые изменения или незавершённое сохранение. Покинуть страницу?')) {
    history.replaceState(null, '', acceptedHash || '#home'); return;
  }
  contentDirty.value = false; feedbackDirty.value = false;
  if (page.value === 'crm' && target.page !== 'crm') { ordersRequest++; ordersLoading.value = false; }
  resetLink.value = nextResetLink;
  if (nextResetLink) history.replaceState(null, '', routeHash(target));
  acceptedHash = location.hash;
  closeMobileMenu(); closeModal(false); feedbackForm.value?.close(false);
  if (filters.category !== screenTarget.categoryId) { Object.assign(filters, { category: screenTarget.categoryId, query: '', brand: '' }); searchQuery.value = ''; }
  navigationRoute.value = target;
  if (!sameList && !authTransition) window.scrollTo({ top: 0, behavior: 'instant' });
}
async function onStorage(event) {
  if (event.key === CART_KEY || event.key === null) { cart.value = readCart(); if (cart.value.length) receipt.value = null; await loadCartProducts(); }
  if (isDemo && (event.key === 'lavr.catalog.v1' || event.key === null)) await refreshData();
  if (isDemo && event.key === 'lavr.site.v1') await loadSite();
}
function onDocumentClick(event) {
  if (event.target.closest('.skip-link')) { event.preventDefault(); document.querySelector('#main')?.focus(); return; }
  if (event.defaultPrevented || event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const link = event.target.closest('a[href]');
  if (!link || link.target || link.hasAttribute('download') || link.getAttribute('href') !== routeHash(route.value)
    || page.value !== 'home' || (!route.value.section && !route.value.publicationId)) return;
  event.preventDefault(); scrollPublicTarget();
}
watch([route, () => session.value?.user.id], () => {
  if (isAuth.value) return;
  if (page.value === 'catalog' && route.value.productId) openModal('product', route.value.productId);
  else if (canOperate.value && page.value === 'crm' && orderId.value) openModal('order', orderId.value);
}, { immediate: true });
async function scrollPublicTarget() {
  targetNotice.value = '';
  const target = route.value;
  if (target.page !== 'home' || (!target.section && !target.publicationId) || siteLoading.value || !site.value) return;
  await nextTick();
  if (route.value !== target) return;
  const element = target.publicationId
    ? [...document.querySelectorAll('[data-publication-id]')].find(node => node.dataset.publicationId === target.publicationId)
    : document.querySelector(`.promotion-card[data-kind="${{ offers: 'offer', announcements: 'announcement', hot: 'hot' }[target.section]}"]`);
  if (element) { element.scrollIntoView({ block: 'center' }); element.focus({ preventScroll: true }); }
  else targetNotice.value = target.publicationId ? 'Публикация недоступна.' : 'Раздел недоступен.';
}
watch([route, site, siteLoading], scrollPublicTarget, { immediate: true });
onMounted(() => {
  refreshData(); if (!isCrm.value && !isAccount.value) enterCrm(); window.addEventListener('hashchange', onHashChange); window.addEventListener('storage', onStorage); document.addEventListener('click', onDocumentClick);
});
onUnmounted(() => {
  siteRequest++; catalogRequest++; ordersRequest++; cartRequest++; window.removeEventListener('hashchange', onHashChange); window.removeEventListener('storage', onStorage); document.removeEventListener('click', onDocumentClick); clearTimeout(toastTimer);
});
</script>

<template>
  <div class="utility"><div class="container utility-inner"><span>Ваш автомобиль. Наши детали.</span><span v-if="isDemo" class="utility-right"><span class="demo-label">Демонстрационный MVP</span></span></div></div>
  <header class="header">
    <div class="container header-main">
      <a class="logo" href="#home" :aria-label="`${storeName} — на главную`">{{ storeName }}<span class="logo-square"></span><small>АВТОЗАПЧАСТИ</small></a>
      <form id="search-form" class="search" role="search" @submit.prevent="searchCatalog"><AppIcon name="search" /><input id="search" v-model="searchQuery" name="query" type="search" maxlength="120" aria-label="Поиск запчастей" placeholder="Поиск по артикулу, бренду или названию" data-focus-key="search"><button type="submit">Найти <AppIcon name="arrow" /></button></form>
      <div class="header-actions">
        <a href="#cart" class="header-cart" :class="{ active: page === 'cart' }" :aria-current="page === 'cart' ? 'page' : undefined" :aria-label="`Корзина, ${count} позиций`"><AppIcon name="cart" /><span>Корзина<small>{{ count ? money(total) : 'Пока пусто' }}</small></span><b class="cart-count">{{ count }}</b></a>
        <div class="header-account">
          <a v-if="session" class="crm-nav" :class="{ selected: isAccount || isAuth }" :aria-current="isAccount || isAuth ? 'page' : undefined" href="#account-orders" :title="session.user.name || session.user.email || undefined"><AppIcon name="user" /><span>Личный кабинет</span></a>
          <a v-else :href="authHash('login')" class="btn red" :aria-current="isAuth ? 'page' : undefined">Войти</a>
          <button v-if="session" type="button" class="btn red" :disabled="logoutBusy" @click="logout">Выйти</button>
        </div>
      </div>
    </div>
    <nav class="navigation container" aria-label="Основная навигация">
      <a class="catalog-nav" :class="{ selected: page === 'catalog' }" :aria-current="page === 'catalog' ? 'page' : undefined" href="#catalog"><AppIcon name="grid" /> Каталог запчастей</a>
      <a v-for="(item, index) in menuItems" :key="index" :href="targetHash(item.target)" class="desktop-menu-item" :class="{ selected: targetHash(item.target) === activeMenuHash }" :aria-current="targetHash(item.target) === activeMenuHash ? 'page' : undefined" :data-action="item.target.type === 'category' ? 'category' : undefined" :data-id="item.target.categoryId" >{{ item.label }}</a>
      <details v-if="menuItems.length" ref="mobileMenu" class="mobile-menu" @keydown.esc.prevent="closeMobileMenu"><summary>Меню</summary><div class="mobile-menu-items"><a v-for="(item, index) in menuItems" :key="index" :href="targetHash(item.target)" :class="{ selected: targetHash(item.target) === activeMenuHash }" :aria-current="targetHash(item.target) === activeMenuHash ? 'page' : undefined" @click="closeMobileMenu">{{ item.label }}</a></div></details>
    </nav>
  </header>

  <main id="main" tabindex="-1">
    <div v-if="catalogReadFailed" class="container storage-alert" role="alert">Не удалось прочитать сохранённый каталог. Показан исходный демокаталог; изменения и оформление приостановлены. Данные в хранилище сохранены.</div>
    <div v-if="storageIssue" class="container storage-alert" role="alert">{{ storageIssue }}</div>
    <div v-if="categoriesError" class="container api-feedback" role="alert"><p>{{ categoriesError }}</p><button type="button" class="btn outline" @click="loadCategories">Повторить загрузку категорий</button></div>
    <div v-if="authError && !isCrm" class="container api-feedback" role="alert">{{ authError }} <a :href="authHash('login')">Войти снова</a><button v-if="needsRelogin" type="button" class="text-link" :disabled="logoutBusy" @click="logout">Выйти и продолжить как гость</button></div>
    <template v-if="page === 'home'">
      <p v-if="targetNotice" class="container api-feedback" role="status">{{ targetNotice }}</p>
      <p v-if="siteLoading" class="container api-feedback" role="status">Загружаем главную страницу…</p>
      <div v-else-if="siteError" class="container api-feedback" role="alert"><p>{{ siteError }}</p><button type="button" class="btn outline" @click="loadSite">Повторить загрузку страницы</button></div>
      <LandingPage v-else-if="site" :categories="categories" :site="site" :demo="isDemo" @feedback="openFeedback" />
    </template>

    <section v-if="page === 'home' || page === 'catalog'" id="catalog-results" class="catalog-section container" aria-labelledby="catalog-title">
      <div class="section-heading"><div><span class="eyebrow">{{ page === 'home' ? 'ПОДГОТОВЬТЕСЬ К НОВОМУ СЕЗОНУ' : 'ВИТРИНА / КАТАЛОГ' }}</span><component :is="page === 'catalog' ? 'h1' : 'h2'" id="catalog-title">{{ filters.query ? 'Результаты поиска' : filters.category ? categoryName(filters.category) : site?.settings.catalogTitle || 'Каталог автозапчастей' }} <span class="heading-count">{{ catalogLoading || catalogError ? '—' : catalogTotal }}</span></component></div><span v-if="isDemo" class="catalog-demo">Цены и остатки — демонстрационные</span></div>
      <div class="catalog-layout">
        <aside class="filters" aria-label="Фильтры каталога">
          <div class="filter-heading"><AppIcon name="grid" /> Категории</div>
          <fieldset><legend class="sr-only">Категория запчастей</legend><label class="filter-row"><input :checked="filters.category === ''" @change="selectCategory('')" type="radio" name="category" value="" data-focus-key="category-all">Все запчасти <small v-if="isDemo">{{ categories.reduce((sum, category) => sum + (category.productCount || 0), 0) }}</small></label><label v-for="category in categories" :key="category.id" class="filter-row"><input :checked="filters.category === category.id" @change="selectCategory(category.id)" type="radio" name="category" :value="category.id" :data-focus-key="`category-${category.id}`">{{ category.name }}<small v-if="categoryCount(category.id) !== null">{{ categoryCount(category.id) }}</small></label></fieldset>
          <div class="filter-divider"></div><label class="field-label" for="brand">Производитель</label><select v-if="isDemo" id="brand" v-model="filters.brand" name="brand" data-focus-key="brand"><option value="">Все бренды</option><option v-for="brand in brands" :key="brand">{{ brand }}</option></select><input v-else id="brand" v-model.lazy="filters.brand" name="brand" maxlength="80" placeholder="Название бренда" data-focus-key="brand">
          <label class="stock-check"><input v-model="filters.inStock" type="checkbox" name="inStock" data-focus-key="inStock"> Только в наличии</label>
          <button v-if="activeFilters" type="button" class="reset-link" data-action="reset" data-focus-key="reset" @click="resetFilters">Сбросить фильтры</button>
          <div class="filter-help"><AppIcon name="search" /><strong>Знаете артикул?</strong><p>Введите его в строку поиска. Так найти нужную деталь проще.</p></div>
        </aside>
        <div class="catalog-content">
          <div class="catalog-toolbar"><div class="result-label"><template v-if="filters.query">По запросу <strong>«{{ filters.query }}»</strong></template><template v-else>Детали для повседневных поездок</template></div><label class="sort-label" for="sort">Сортировка <select id="sort" v-model="filters.sort" name="sort" data-focus-key="sort"><option value="relevance">По умолчанию</option><option value="price_asc">Сначала дешевле</option><option value="price_desc">Сначала дороже</option></select></label></div>
          <p v-if="catalogLoading" class="api-feedback" role="status">Загружаем каталог…</p>
          <div v-if="catalogError" class="api-feedback" role="alert"><p>{{ catalogError }}</p><button type="button" class="btn outline" @click="loadCatalog">Повторить загрузку каталога</button></div>
          <div v-if="!catalogLoading && products.length && (!catalogError || catalogReadFailed)" class="product-grid"><ProductCard v-for="(product, index) in products" :key="product.id" :product="product" :demo="isDemo" :index="index" :quantity="cartQuantity(product.id)" :category="categoryName(product.category)" :price="money(product.priceKopecks)" :category-id="route.categoryId" @open="openProduct" @add="addProduct" /></div>
          <div v-else-if="!catalogLoading && !catalogError" class="empty-state"><AppIcon name="search" /><h3>Запчасти не найдены</h3><p>Попробуйте другой артикул или уберите часть фильтров.</p><button type="button" class="btn red" data-action="reset" @click="resetFilters">Показать весь каталог</button></div>
          <nav v-if="!catalogLoading && !catalogError && catalogTotal > PAGE_SIZE" class="pagination" aria-label="Страницы каталога"><button type="button" class="btn outline" :disabled="!catalogOffset" @click="catalogPage(-1)">Назад</button><span>{{ catalogOffset + 1 }}–{{ Math.min(catalogOffset + PAGE_SIZE, catalogTotal) }} из {{ catalogTotal }}</span><button type="button" class="btn outline" :disabled="catalogOffset + PAGE_SIZE >= catalogTotal" @click="catalogPage(1)">Далее</button></nav>
          <p class="catalog-footnote">Фотографии иллюстрируют категории. Совместимость с автомобилем и актуальные условия требуют проверки перед покупкой.</p>
        </div>
      </div>
    </section>

    <template v-else-if="page === 'cart' || page === 'checkout'">
      <section v-if="receipt && page === 'checkout'" class="container receipt"><span class="receipt-check"><AppIcon name="check" /></span><span class="eyebrow">{{ isDemo ? 'ДЕМОНСТРАЦИОННЫЙ СЦЕНАРИЙ ЗАВЕРШЁН' : 'ЗАЯВКА ПРИНЯТА' }}</span><h1>{{ isDemo ? 'Демозаказ создан' : 'Заказ на самовывоз принят' }}</h1><p class="receipt-number">№ {{ receipt.number }}</p><p v-if="isDemo">Заказ на {{ money(receipt.totalKopecks) }} сохранён в текущей вкладке.<br>Магазину и в 1С он не отправлен. Оплата на сайте не производится.</p><p v-else>Заказ на {{ money(receipt.totalKopecks) }} принят магазином. Менеджер подтвердит наличие и согласует время самовывоза. Оплата на сайте не производится.</p><p v-if="!isDemo && site?.settings.address">Самовывоз: {{ site.settings.address }}</p><div class="receipt-actions"><a v-if="isDemo" class="btn red" href="#crm">Открыть заказ в CRM <AppIcon name="arrow" /></a><button type="button" class="btn outline" data-action="continue" @click="continueShopping">Вернуться в каталог</button></div><small v-if="isDemo">Контакты хранятся в сессии браузера. Используйте тестовые данные.</small></section>
      <section v-else class="container cart-page">
        <div class="breadcrumbs"><a href="#home">Главная</a><span>/</span><a v-if="page === 'checkout'" href="#cart">Корзина</a><span v-if="page === 'checkout'">/</span>{{ page === 'checkout' ? 'Оформление заказа' : 'Корзина' }}</div>
        <div class="section-heading"><div><span class="eyebrow">{{ page === 'checkout' ? 'КОНТАКТЫ И СОСТАВ ЗАКАЗА' : 'ВЫБЕРИТЕ ТОВАРЫ ДЛЯ ЗАКАЗА' }}</span><h1>{{ page === 'checkout' ? 'Оформление заказа' : 'Ваша корзина' }} <span class="heading-count">{{ page === 'checkout' ? selectedItems.length : count }}</span></h1></div><a class="text-link" :href="page === 'checkout' ? '#cart' : '#catalog'">{{ page === 'checkout' ? 'Изменить выбор товаров' : 'Продолжить покупки' }} <AppIcon name="arrow" /></a></div>
        <div v-if="page === 'checkout' && (selectedCartIds === null || !selectedItems.length)" class="empty-state"><AppIcon name="cart" /><h2>Выберите товары для оформления</h2><p>Вернитесь в корзину и отметьте нужные позиции.</p><a class="btn red" href="#cart">Выбрать товары в корзине</a></div>
        <div v-else-if="!cart.length" class="empty-state"><AppIcon name="cart" /><h2>Здесь пока пусто</h2><p>Найдите нужную запчасть и добавьте её в корзину.</p><a class="btn red" href="#catalog">Перейти в каталог <AppIcon name="arrow" /></a></div>
        <div v-else class="checkout-layout" :aria-busy="(page === 'cart' ? cartLoading : selectedCartLoading) || submitting">
          <div>
            <p v-if="page === 'cart' ? cartLoading : selectedCartLoading" class="api-feedback" role="status">Проверяем товары корзины…</p>
            <p v-if="cartError && page === 'cart'" class="api-feedback" role="alert">{{ cartError }}</p>
            <p v-if="!cartReady && !selectedCartLoading && !catalogReadFailed" class="api-feedback" role="alert">Проверьте выбранные товары: доступность, количество и единицы измерения. Непроверенные позиции можно исключить из заказа в корзине.</p>
            <template v-if="page === 'cart'">
              <label class="cart-select-all"><input type="checkbox" :checked="selectedItems.length === cart.length" :indeterminate="selectedItems.length > 0 && selectedItems.length < cart.length" :disabled="submitting" @change="selectedCartIds = $event.target.checked ? cart.map(item => item.productId) : []">Выбрать все</label>
              <div class="cart-items"><article v-for="item in cartItems" :key="item.productId" class="cart-item"><label class="cart-select"><input type="checkbox" :checked="selectedItems.some(selected => selected.productId === item.productId)" :disabled="submitting" :aria-label="`Выбрать ${item.product.name}`" @change="selectCartItem(item.productId, $event.target.checked)"></label><img v-if="item.product.image" :src="item.product.image" alt="Иллюстрация категории"><span v-else class="cart-item-placeholder" aria-hidden="true"><AppIcon name="box" /></span><div class="cart-item-info"><span v-if="item.product.brand || item.product.sku" class="product-brand">{{ [item.product.brand, item.product.sku].filter(Boolean).join(' · ') }}</span><h3>{{ item.product.websiteTitle || item.product.name }}</h3><p v-if="failedCartIds.includes(item.productId)" class="form-error">Не удалось проверить товар. Снимите выбор или удалите позицию.</p><span class="muted">{{ money(item.product.priceKopecks) }} за {{ productUnit(item.product) }}</span><p v-if="(item.expectedUnit ?? 'шт') !== productUnit(item.product)" class="form-error quantity-review">Единица изменилась: {{ item.expectedUnit ?? 'шт' }} → {{ productUnit(item.product) || 'не подтверждена' }}.<button v-if="canAddQuantity(item.product)" type="button" class="text-link" :disabled="submitting" @click="changeQuantity(item.productId, quantityStep(productUnit(item.product)), true)">Выбрать заново: {{ formatQuantity(quantityStep(productUnit(item.product)), productUnit(item.product)) }}</button></p></div><div class="quantity-control"><button type="button" class="quantity-button" :disabled="submitting || (item.expectedUnit ?? 'шт') !== productUnit(item.product)" data-action="quantity" :data-id="item.productId" :data-quantity="stepQuantity(item.quantity, productUnit(item.product), -1)" :aria-label="`Уменьшить количество ${item.product.name}`" :data-focus-key="`minus-${item.productId}`" @click="changeQuantity(item.productId, stepQuantity(item.quantity, productUnit(item.product), -1))"><AppIcon name="minus" /></button><input class="quantity-input" inputmode="decimal" :value="formatQuantity(item.quantity)" :min="quantityStep(productUnit(item.product))" :step="quantityStep(productUnit(item.product))" :max="Math.min(item.product.stock, 999)" :disabled="submitting || (item.expectedUnit ?? 'шт') !== productUnit(item.product)" :aria-label="`Количество ${item.product.name}, ${productUnit(item.product)}`" @change="changeQuantity(item.productId, $event.target.value); $event.target.value = formatQuantity(cartQuantity(item.productId))"><small>{{ item.expectedUnit ?? 'шт' }}</small><button type="button" class="quantity-button" data-action="quantity" :data-id="item.productId" :data-quantity="stepQuantity(item.quantity, productUnit(item.product))" :aria-label="`Увеличить количество ${item.product.name}`" :data-focus-key="`plus-${item.productId}`" :disabled="submitting || !canAddQuantity(item.product, item.quantity) || (item.expectedUnit ?? 'шт') !== productUnit(item.product)" @click="changeQuantity(item.productId, stepQuantity(item.quantity, productUnit(item.product)))"><AppIcon name="plus" /></button></div><strong>{{ money(cartLineTotal(item)) }}</strong><button type="button" class="remove-button" :disabled="submitting" data-action="quantity" :data-id="item.productId" data-quantity="0" :aria-label="`Удалить ${item.product.name}`" @click="changeQuantity(item.productId, 0)"><AppIcon name="close" /></button></article></div>
            </template>
            <template v-else>
              <h2 class="checkout-items-title">Товары в заказе</h2>
              <ul class="checkout-items"><li v-for="item in selectedCartItems" :key="item.productId"><span><strong>{{ item.product.websiteTitle || item.product.name }}</strong><small>{{ item.product.sku }} · {{ formatQuantity(item.quantity, item.expectedUnit ?? 'шт') }}</small></span><strong>{{ money(cartLineTotal(item)) }}</strong></li></ul>
              <div v-if="isDemo" class="demo-notice"><AppIcon name="box" /><div><strong>Это демонстрационный заказ</strong><p>Контакты сохраняются только в текущей вкладке. Используйте тестовые данные; магазин и 1С их не получат.</p></div></div>
              <form id="checkout-form" class="checkout-form" @submit.prevent="submitOrder">
                <h2>Как с вами связаться?</h2>
                <div class="form-grid"><label>Ваше имя <small v-if="session">необязательно</small><span v-else>*</span><input v-model="draft.name" :disabled="submitting" name="name" autocomplete="name" :required="!session" maxlength="100" placeholder="Алексей" data-focus-key="customer-name"></label><label>Телефон <span>*</span><input v-model="draft.phone" :disabled="submitting" name="phone" type="tel" autocomplete="tel" required maxlength="30" placeholder="+7 (999) 123-45-67" data-focus-key="customer-phone"></label><label class="full">Email <small>необязательно</small><input v-model="draft.email" :disabled="submitting" name="email" type="email" autocomplete="email" maxlength="254" placeholder="Для информации о заказе" data-focus-key="customer-email"></label></div>
                <p class="checkout-contact-help">Укажите телефон для связи по заказу. Контакты используются для этого заказа.</p>
                <h2>Получение заказа</h2><div class="pickup-details"><AppIcon name="box" /><div><strong>Самовывоз из магазина</strong><p v-if="site?.settings.address">{{ site.settings.address }}</p><p v-if="site?.settings.hours">{{ site.settings.hours }}</p><p>Менеджер подтвердит наличие и согласует время выдачи.</p></div></div>
                <p id="checkout-error" class="form-error" role="alert">{{ checkoutError }}</p>
              </form>
            </template>
          </div>
          <aside class="order-summary"><span class="eyebrow">ВАШ ЗАКАЗ</span><h2>Итого</h2><div><span>Выбрано позиций</span><strong>{{ selectedItems.length }} из {{ count }}</strong></div><div><span>Получение</span><span>Самовывоз из магазина</span></div><div><span>Оплата</span><span>В магазине</span></div><p>Оплата на сайте не производится.</p><div class="summary-total"><span>Сумма выбранных товаров</span><strong>{{ money(selectedTotal) }}</strong></div><button v-if="page === 'cart'" type="button" class="btn red checkout-continue" :disabled="submitting || !selectedItems.length || !cartReady || selectedTotal === null" @click="proceedToCheckout">Перейти к оформлению <AppIcon name="arrow" /></button><button v-else type="submit" form="checkout-form" class="btn red checkout-submit" :disabled="submitting || authLoading || needsRelogin || !cartReady || selectedTotal === null">{{ submitting ? 'Сохраняем…' : isDemo ? 'Создать демозаказ' : 'Заказать с самовывозом' }} <AppIcon name="arrow" /></button><small>{{ page === 'cart' ? 'Контакты для связи укажете на следующем шаге.' : isDemo ? 'Заказ будет доступен в демонстрационной CRM текущей вкладки.' : 'Итоговую цену и доступность проверяет магазин при оформлении.' }}</small></aside>
        </div>
      </section>
    </template>

    <section v-else-if="isCrm || isAccount" class="container crm-page">
      <div class="breadcrumbs"><a href="#home">Витрина</a><span>/</span> {{ isAccount ? 'Личный кабинет' : 'Кабинет менеджера' }}</div><div class="section-heading"><div><h1>{{ isAccount ? page === 'account-feedback' ? 'Мои обращения' : 'Мои заказы' : page === 'admin-users' ? 'Пользователи' : page === 'crm-catalog' ? 'Каталог магазина' : page === 'crm-content' ? 'Содержимое сайта' : page === 'crm-feedback' ? 'Обращения покупателей' : 'Заказы под контролем' }}<span class="red-period">.</span></h1><p class="section-subtitle">{{ isAccount ? 'Профиль, ваши заказы и обращения.' : page === 'crm-catalog' ? 'Категории и номенклатура для вашей витрины.' : page === 'crm-content' ? 'Главная, меню, публикации и изображения.' : page === 'crm-feedback' ? 'Контакты, запросы и результаты работы с клиентом.' : 'От первого обращения до выдачи запчастей.' }}</p></div><div v-if="isAdmin" class="integration-label"><AppIcon name="link" /><span><strong>{{ isDemo ? '1С не подключена' : 'Серверный режим' }}</strong><small>{{ isDemo ? 'Предусмотрен отдельный адаптер' : 'Данные через API' }}</small></span><i></i></div></div>
      <div v-if="isDemo && page === 'crm'" class="demo-notice crm-notice"><AppIcon name="shield" /><div><strong>Демонстрационный кабинет менеджера</strong><p>Показаны заказы текущей вкладки. Авторизация и серверное хранение предусмотрены в архитектуре; сейчас реальные клиенты не обслуживаются.</p></div></div>
      <p v-if="authLoading" class="api-feedback" role="status">Проверяем сессию…</p>
      <p v-else-if="!session && authError" class="api-feedback" role="alert">{{ authError }} <a :href="authHash('login')">Войти снова</a></p>
      <div v-else-if="session && !isAccount && !canOperate && !canManageUsers" class="api-feedback" role="alert">Этот раздел недоступен. <a href="#account-orders">Открыть личный кабинет</a></div>
      <template v-else-if="session">
      <p v-if="authError && isCrm" class="form-error" role="alert">{{ authError }}</p>
      <p v-if="canManageUsers && catalogStale" class="api-feedback" role="status">Цены и остатки ожидают обновления. Требуется синхронизация каталога. Продажи продолжаются.</p>
      <section class="account-card account-profile" data-testid="account-profile" aria-labelledby="profile-summary-title"><h2 id="profile-summary-title">Контакты профиля</h2><dl><div><dt>Имя</dt><dd>{{ session.user.name || 'Не указано' }}</dd></div><div><dt>Email</dt><dd>{{ session.user.email || 'Не указан' }}</dd></div><div><dt>Телефон</dt><dd>{{ session.user.phone || 'Не указан' }}</dd></div></dl></section>
      <nav class="crm-tabs" aria-label="Разделы кабинета"><a href="#account-orders" :class="{ selected: isAccount && page !== 'account-feedback' }" :aria-current="isAccount && page !== 'account-feedback' ? 'page' : undefined">Мои заказы</a><a href="#account-feedback" :class="{ selected: page === 'account-feedback' }" :aria-current="page === 'account-feedback' ? 'page' : undefined">Мои обращения</a><a v-if="canOperate" href="#crm" :class="{ selected: page === 'crm' }" :aria-current="page === 'crm' ? 'page' : undefined"><AppIcon name="box" /> Заказы</a><a v-if="canOperate" href="#crm-feedback" :class="{ selected: page === 'crm-feedback' }" :aria-current="page === 'crm-feedback' ? 'page' : undefined"><AppIcon name="user" /> Обращения</a><a v-if="isAdmin" href="#crm-catalog" :class="{ selected: page === 'crm-catalog' }" :aria-current="page === 'crm-catalog' ? 'page' : undefined"><AppIcon name="grid" /> Каталог</a><a v-if="isAdmin" href="#crm-content" :class="{ selected: page === 'crm-content' }" :aria-current="page === 'crm-content' ? 'page' : undefined"><AppIcon name="grid" /> Контент сайта</a><a v-if="canManageUsers" href="#admin-users" :class="{ selected: page === 'admin-users' }" :aria-current="page === 'admin-users' ? 'page' : undefined">Пользователи</a></nav>
      <CustomerAccount v-if="isAccount" :key="identityRevision" embedded :api="api" :session="session" :page="page" :record-id="page === 'account-orders' ? orderId : caseId" @staff-error="handleStaffError" @feedback="openFeedback" />
      <UsersManager v-else-if="page === 'admin-users' && canManageUsers" :key="identityRevision" :api="api" :session="session" :selected-user-id="route.userId" @select-user="id => go(routeHash({ page: 'admin-users', userId: id }))" @staff-error="handleStaffError" /><p v-else-if="page === 'admin-users'" class="api-feedback" role="alert">Управление пользователями недоступно.</p><FeedbackManager v-else-if="page === 'crm-feedback' && canOperate" :key="identityRevision" :api="api" :session="session" :case-id="caseId" @staff-error="handleStaffError" @dirty="feedbackDirty = $event" /><ContentManager v-else-if="page === 'crm-content' && isAdmin" :key="`${identityRevision}:${route.tab}:${route.publicationId}`" :route-tab="route.tab" :publication-id="route.publicationId" @navigate="go" :api="api" :store-name="storeName" :categories="categories" @changed="loadSite" @staff-error="handleStaffError" @dirty="contentDirty = $event" /><p v-else-if="page === 'crm-content'" class="api-feedback" role="alert">Редактор сайта доступен администратору.</p><CatalogManager v-else-if="page === 'crm-catalog' && isAdmin" :key="identityRevision" :api="api" :store-name="storeName" @changed="refreshData" @staff-error="handleStaffError" /><p v-else-if="page === 'crm-catalog'" class="api-feedback" role="alert">Управление каталогом доступно администратору.</p>
      <template v-else-if="canOperate">
      <div v-if="!ordersLoading && !ordersError" class="metric-grid"><article><span>Заказов в выборке</span><strong>{{ orders.length }}<small>{{ isDemo ? 'в текущей вкладке' : 'на этой странице' }}</small></strong><AppIcon name="box" /></article><article><span>В работе</span><strong>{{ activeOrders.length }}<small>в текущей выборке</small></strong><AppIcon name="clock" /></article><article><span>Сумма заказов</span><strong>{{ money(orderTotal) }}<small>в выборке, без отменённых</small></strong><AppIcon name="cart" /></article></div>
      <div class="crm-orders">
        <div class="crm-toolbar"><h2>Список заказов <span class="heading-count">{{ ordersLoading || ordersError ? '—' : ordersTotal }}</span></h2><form id="order-search-form" class="crm-search" @submit.prevent="searchOrders"><AppIcon name="search" /><input v-model="orderSearchQuery" name="orderQuery" type="search" maxlength="254" aria-label="Поиск заказов" placeholder="Номер, полный email или телефон" data-focus-key="order-search"><button type="submit" aria-label="Найти заказ"><AppIcon name="arrow" /></button></form></div>
        <div class="status-filters" role="group" aria-label="Фильтр статуса заказа"><button v-for="[status, label] in [['', 'Все заказы'], ...Object.entries(ORDER_STATUSES)]" :key="status" type="button" class="status-filter" :class="{ selected: orderFilter === status }" data-action="order-filter" :data-status="status" :aria-pressed="orderFilter === status" :data-focus-key="`status-filter-${status}`" @click="orderFilter = status">{{ label }}</button></div>
        <p v-if="ordersLoading" class="api-feedback" role="status">Загружаем заказы…</p><div v-else-if="ordersError" class="api-feedback" role="alert"><p>{{ ordersError }}</p><button type="button" class="btn outline" @click="loadOrders">Повторить загрузку заказов</button></div>
        <template v-else-if="visibleOrders.length"><p id="orders-scroll-hint" class="table-scroll-hint">Проведите по таблице в сторону, чтобы увидеть остальные колонки.</p><div class="table-wrap" role="region" tabindex="0" aria-label="Заказы: прокрутка таблицы" aria-describedby="orders-scroll-hint"><table class="orders-table"><thead><tr><th>Заказ / дата</th><th>Клиент</th><th>Статус</th><th>Сумма товаров</th><th>Обработка</th><th><span class="sr-only">Действия</span></th></tr></thead><tbody><tr v-for="order in visibleOrders" :key="order.id"><td><a :href="routeHash({ page: 'crm', orderId: order.id })" class="order-number" data-action="order" :data-id="order.id" :data-focus-key="`order-${order.id}`" @click="openOrder(order.id, $event)">№ {{ order.number }}</a><small>{{ date(order.createdAt) }}</small></td><td><strong>{{ order.customer.name || 'Имя не указано' }}</strong><small>{{ order.customer.phone }}</small></td><td><span class="status-badge" :class="`status-${order.status}`"><i></i>{{ ORDER_STATUSES[order.status] }}</span></td><td><strong>{{ money(order.totalKopecks) }}</strong><small>{{ order.items.length }} позиций</small></td><td><span class="sync-muted">{{ isDemo ? 'Не подключён' : { pending_sync: 'Ожидает обмена', exporting: 'Передаётся', exported: 'Передан в 1С', error: 'Ошибка обмена', cancelled_before_sync: 'Передача отменена', not_connected: 'Ручная обработка' }[order.integrationStatus] }}</span></td><td><a :href="routeHash({ page: 'crm', orderId: order.id })" class="table-action" data-action="order" :data-id="order.id" :data-focus-key="`detail-${order.id}`" :aria-label="`Открыть заказ ${order.number}`" @click="openOrder(order.id, $event)"><AppIcon name="arrow" /></a></td></tr></tbody></table></div></template>
        <div v-else class="empty-state"><AppIcon name="box" /><h3>{{ orderQuery || orderFilter ? 'Заказы не найдены' : 'Первый заказ — начало пути' }}</h3><p>{{ orderQuery || orderFilter ? 'Измените запрос или фильтр статуса.' : isDemo ? 'Оформите демозаказ в витрине. Здесь появятся его состав, клиент и статус.' : 'Новые заявки покупателей появятся здесь.' }}</p><button v-if="orderQuery || orderFilter" type="button" class="btn outline" data-action="order-reset" @click="resetOrders">Показать все заказы</button><a v-else class="btn red" href="#catalog">Открыть витрину <AppIcon name="arrow" /></a></div>
        <nav v-if="!ordersLoading && !ordersError && ordersTotal > PAGE_SIZE" class="pagination" aria-label="Страницы заказов"><button type="button" class="btn outline" :disabled="!ordersOffset" @click="ordersPage(-1)">Назад</button><span>{{ ordersOffset + 1 }}–{{ Math.min(ordersOffset + PAGE_SIZE, ordersTotal) }} из {{ ordersTotal }}</span><button type="button" class="btn outline" :disabled="ordersOffset + PAGE_SIZE >= ordersTotal" @click="ordersPage(1)">Далее</button></nav>
      </div>
      </template>
      </template>
    </section>
  </main>

  <footer class="footer"><div class="container footer-top"><a class="logo" href="#home">{{ storeName }}<span class="logo-square"></span><small>АВТОЗАПЧАСТИ</small></a><p>Детали для дороги.<br>Внимание к вашему автомобилю.</p><div><strong>На сайте</strong><a href="#catalog">Каталог запчастей</a><a href="#cart">Корзина</a><a v-if="session" href="#account-orders">Личный кабинет</a></div><div v-if="isDemo"><strong>Об этом MVP</strong><span>Тестовые цены и остатки</span><span>Оформление без оплаты</span><span>Интеграция 1С: спроектирована</span></div><div><strong>Контакты</strong><template v-if="site"><span>{{ site.settings.phone }}</span><span>{{ site.settings.address }}</span><span>{{ site.settings.hours }}</span></template></div></div><div class="container footer-bottom"><span>© 2026 {{ storeName }}</span><span>Фото иллюстративные · <button type="button" class="footer-link" data-action="credits" data-focus-key="credits" @click="openModal('credits', null, $event)">Источники изображений</button></span></div></footer>

  <FeedbackForm ref="feedbackForm" :api="api" :session="session" :needs-relogin="needsRelogin" :auth-loading="authLoading" @staff-error="handleStaffError" />

  <dialog v-if="modal" ref="detailDialog" class="detail-dialog" :class="{ wide: modalProduct }" aria-labelledby="dialog-title" @cancel.prevent="closeModal()">
    <button type="button" class="dialog-close" data-action="close" aria-label="Закрыть" @click="closeModal()"><AppIcon name="close" /></button>
    <p v-if="modalLoading" id="dialog-title" class="api-feedback" role="status">Загружаем данные…</p><div v-else-if="modalError" class="api-feedback" role="alert"><h2 id="dialog-title">Не удалось загрузить данные</h2><p>{{ modalError }}</p><button type="button" class="btn outline" @click="openModal(modal.type, modal.id)">Повторить загрузку</button></div>
    <div v-else-if="modalProduct" class="detail-grid"><div class="detail-image"><img v-if="modalProduct.image" :src="modalProduct.image" :alt="`Иллюстрация категории ${categoryName(modalProduct.category)}`"><AppIcon v-else name="box" /></div><div><span v-if="modalProduct.brand" class="eyebrow">{{ modalProduct.brand }}</span><h2 id="dialog-title">{{ modalProduct.websiteTitle || modalProduct.name }}</h2><p>{{ modalProduct.websiteDescription || modalProduct.description || 'Характеристики и совместимость проверяются перед покупкой.' }}</p><dl class="product-specs"><div v-for="(value, key) in modalProduct.specs" :key="key"><dt>{{ key }}</dt><dd>{{ value }}</dd></div><div><dt>Артикул</dt><dd>{{ modalProduct.sku || 'Не указан' }}</dd></div><div v-if="modalProduct.brand"><dt>Бренд</dt><dd>{{ modalProduct.brand }}</dd></div><div><dt>{{ isDemo ? 'Остаток в демокаталоге' : 'Доступно' }}</dt><dd>{{ formatQuantity(modalProduct.stock, productUnit(modalProduct)) }}</dd></div></dl><strong class="detail-price">{{ money(modalProduct.priceKopecks) }} за {{ productUnit(modalProduct) }}</strong><button type="button" class="btn red" data-action="add" :data-id="modalProduct.id" aria-live="polite" aria-atomic="true" :disabled="!canAddQuantity(modalProduct, cartQuantity(modalProduct.id))" @click="addProduct(modalProduct.id)"><AppIcon :name="cartQuantity(modalProduct.id) ? 'check' : 'plus'" /> {{ cartQuantity(modalProduct.id) ? `${formatQuantity(cartQuantity(modalProduct.id), cart.find(item => item.productId === modalProduct.id)?.expectedUnit ?? 'шт')} в корзине` : 'Добавить в корзину' }}</button><p v-if="productNotice" role="status">{{ productNotice }}</p><p v-if="isDemo" class="detail-disclaimer">Фотография иллюстрирует категорию и не подтверждает внешний вид этого артикула. Подбор по VIN не реализован.</p></div></div>
    <template v-else-if="modalOrder">
      <span class="eyebrow">{{ isDemo ? 'ДЕМОНСТРАЦИОННАЯ CRM' : 'CRM / ЗАКАЗ' }}</span><h2 id="dialog-title">Заказ № {{ modalOrder.number }}</h2><div class="order-detail-meta"><span class="status-badge" :class="`status-${modalOrder.status}`"><i></i>{{ ORDER_STATUSES[modalOrder.status] }}</span><span>{{ date(modalOrder.createdAt) }} · МСК</span></div><div class="order-contact"><div><small>Клиент</small><strong>{{ modalOrder.customer.name || 'Имя не указано' }}</strong><span>{{ modalOrder.customer.phone }}</span><span v-if="modalOrder.customer.email">{{ modalOrder.customer.email }}</span></div><div><small>Получение</small><strong>{{ modalOrder.fulfillment.method === 'delivery' ? 'Доставка' : 'Самовывоз' }}</strong><span>{{ modalOrder.fulfillment.method === 'delivery' ? modalOrder.fulfillment.address : 'Место согласуется отдельно' }}</span></div></div>
      <div class="order-detail-items"><div v-for="item in modalOrder.items" :key="item.productId"><span><strong>{{ item.name }}</strong><small>{{ item.brand }} · {{ item.sku }} · {{ formatQuantity(item.quantity, item.unit ?? 'шт') }} × {{ money(item.unitPriceKopecks) }}</small></span><strong>{{ money(snapshotLineTotal(item)) }}</strong></div></div><div class="detail-total"><span>Сумма товаров</span><strong>{{ money(modalOrder.totalKopecks) }}</strong></div><p class="muted">{{ modalOrder.fulfillment.method === 'pickup' ? 'Самовывоз из магазина.' : 'Доставка не включена.' }}<span v-if="isDemo"> Обмен с 1С не подключён.</span></p><div class="order-status-actions"><button v-for="status in statusActions" :key="status" type="button" class="btn" :class="status === 'cancelled' ? 'outline danger' : 'red'" data-action="status" :disabled="statusBusy" :data-id="modalOrder.id" :data-status="status" @click="updateOrderStatus(modalOrder.id, status)">{{ ORDER_STATUSES[status] }}</button><span v-if="!statusActions.length" class="muted">Изменение статуса сейчас недоступно.</span></div>
    </template>
    <template v-else-if="modal.type === 'credits'">
      <span class="eyebrow">ВИЗУАЛЬНЫЕ МАТЕРИАЛЫ</span><h2 id="dialog-title">Источники изображений</h2><p>Фотографии иллюстрируют категории. Они не являются проверенными фотографиями артикулов и не означают сотрудничество с изображёнными брендами.</p><ul class="credits-list"><li>Автомобиль: Samuele Errico Piccarini · <a href="https://unsplash.com/photos/black-mercedes-benz-vehicle-YSJSs78rw10" target="_blank" rel="noopener noreferrer">Unsplash</a> · <a href="https://unsplash.com/license" target="_blank" rel="noopener noreferrer">Unsplash License</a>.</li><li>Моторное масло: Devid Bianchetti, «Olio Motore.jpg» · <a href="https://commons.wikimedia.org/wiki/File:Olio_Motore.jpg" target="_blank" rel="noopener noreferrer">Wikimedia Commons</a> · <a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank" rel="noopener noreferrer">CC BY-SA 4.0</a>. Без изменения исходного содержимого.</li><li>Тормозной диск (3D-модель): Atharv Chandel · <a href="https://commons.wikimedia.org/wiki/File:Ventilated_Curve_Drilled_%26_Slotted_Brake_Rotor.jpg" target="_blank" rel="noopener noreferrer">Wikimedia Commons</a> · <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">CC BY 4.0</a>.</li><li>Масляный и воздушный фильтры: Elwood · <a href="https://commons.wikimedia.org/wiki/File:Filtro_olio_avvitabile.JPG" target="_blank" rel="noopener noreferrer">масляный фильтр</a>, <a href="https://commons.wikimedia.org/wiki/File:Filtro_aria_pannello.JPG" target="_blank" rel="noopener noreferrer">воздушный фильтр</a> · public domain.</li><li>Свеча зажигания: Tennen-Gas · <a href="https://commons.wikimedia.org/wiki/File:Sparkplug3_H.JPG" target="_blank" rel="noopener noreferrer">Wikimedia Commons</a> · public domain.</li></ul>
    </template>
  </dialog>
  <dialog ref="authDialog" class="detail-dialog auth-dialog" aria-label="Авторизация" @cancel.prevent="closeAuth" @click.self="closeAuth">
    <button type="button" class="dialog-close" aria-label="Закрыть авторизацию" @click="closeAuth"><AppIcon name="close" /></button>
    <AccountAccess v-if="isAuth" embedded :api="api" :page="navigationRoute.page" :return-to="navigationRoute.returnTo" :reset-link="resetLink" @navigate="go" @identity-changing="changingIdentity" @authenticated="acceptSession" @password-reset="passwordResetComplete" @staff-error="handleStaffError" />
  </dialog>
  <div id="toast" class="toast" :class="{ visible: toast }" role="status" aria-live="polite">{{ toast }}</div>
</template>

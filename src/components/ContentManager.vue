<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import AppIcon from './AppIcon.vue';
import ContentTarget from './ContentTarget.vue';
import MediaSelector from './MediaSelector.vue';

const props = defineProps({ api: { type: Object, required: true }, categories: { type: Array, default: () => [] } });
const emit = defineEmits(['changed', 'staff-error', 'dirty']);
const tab = ref('site');
const copy = value => JSON.parse(JSON.stringify(value));
let active = true;
let siteRequest = 0;
let categoryRequest = 0;
let publicationRequest = 0;
let detailRequest = 0;
let mediaRequest = 0;
const staffError = error => { if (active && error?.status === 401) emit('staff-error', error); };
const message = (error, fallback) => error?.message || fallback;

const categories = ref(props.categories);
const categoriesLoading = ref(false);
const categoriesError = ref('');
async function loadCategories() {
  const request = ++categoryRequest;
  categoriesLoading.value = true;
  categoriesError.value = '';
  try {
    const result = await props.api.getCategories({ admin: true });
    if (active && request === categoryRequest) categories.value = result;
  } catch (error) {
    if (!active || request !== categoryRequest) return;
    categoriesError.value = message(error, 'Не удалось загрузить категории для ссылок.');
    staffError(error);
  } finally {
    if (active && request === categoryRequest) categoriesLoading.value = false;
  }
}

const site = ref(null);
const siteBaseline = ref('');
const siteLoading = ref(true);
const siteBusy = ref(false);
const siteError = ref('');
const siteMessage = ref('');
const siteConflict = ref(false);
const siteReadOnly = ref(false);
const siteIssue = ref('');
const siteInput = () => ({ hero: site.value.hero, settings: site.value.settings, menu: site.value.menu.map((item, position) => ({ ...item, position })) });
const siteDirty = computed(() => !!site.value && JSON.stringify(siteInput()) !== siteBaseline.value);
function acceptSite(result) {
  siteReadOnly.value = !!result.readOnly;
  siteIssue.value = result.issue || '';
  site.value = copy(result);
  site.value.menu.sort((a, b) => a.position - b.position);
  siteBaseline.value = JSON.stringify(siteInput());
  siteConflict.value = false;
}
async function loadSite(discard = false) {
  if (siteBusy.value || siteLoading.value && site.value) return;
  if (siteDirty.value && !discard && !window.confirm('Загрузить актуальную версию? Несохранённые изменения главной и меню будут потеряны.')) return;
  const request = ++siteRequest;
  siteLoading.value = true;
  siteError.value = '';
  siteMessage.value = '';
  try {
    const result = await props.api.getAdminSite();
    if (active && request === siteRequest) acceptSite(result);
  } catch (error) {
    if (!active || request !== siteRequest) return;
    siteError.value = message(error, 'Не удалось загрузить настройки сайта.');
    staffError(error);
  } finally {
    if (active && request === siteRequest) siteLoading.value = false;
  }
}
async function saveSite() {
  if (!site.value || siteBusy.value || siteLoading.value || contentReadOnly.value) return;
  siteBusy.value = true;
  siteError.value = '';
  siteMessage.value = '';
  const request = ++siteRequest;
  try {
    const result = await props.api.updateSite(copy(siteInput()), site.value.version);
    if (!active || request !== siteRequest) return;
    acceptSite(result);
    siteMessage.value = 'Главная и меню сохранены. Изменения доступны на витрине.';
    emit('changed');
  } catch (error) {
    if (!active || request !== siteRequest) return;
    siteConflict.value = error?.status === 412;
    siteError.value = siteConflict.value ? 'Сайт уже изменён другим сотрудником. Ваши изменения сохранены в форме. Загрузите актуальную версию перед повторным сохранением.' : message(error, 'Не удалось сохранить сайт. Изменения остались в форме.');
    staffError(error);
  } finally {
    if (active && request === siteRequest) siteBusy.value = false;
  }
}
function moveMenu(index, direction) {
  const next = index + direction;
  if (next < 0 || next >= site.value.menu.length) return;
  [site.value.menu[index], site.value.menu[next]] = [site.value.menu[next], site.value.menu[index]];
}
function addMenuItem() {
  if (site.value.menu.length >= 12) return;
  site.value.menu.push({ label: '', target: { type: 'catalog' }, position: site.value.menu.length, isVisible: true });
}
const settingsFields = [
  { key: 'storeName', label: 'Название магазина', max: 120 },
  { key: 'phone', label: 'Телефон', max: 30, min: 10 },
  { key: 'address', label: 'Адрес', max: 500 },
  { key: 'hours', label: 'Часы работы', max: 240 },
  { key: 'categoriesTitle', label: 'Заголовок категорий', max: 120 },
  { key: 'promotionsTitle', label: 'Заголовок акций и объявлений', max: 120 },
  { key: 'catalogTitle', label: 'Заголовок каталога', max: 120 },
];

const kindLabels = { offer: 'Акция', announcement: 'Объявление', hot: 'Горячее предложение' };
const statusLabels = { draft: 'Черновик', published: 'Опубликовано', archived: 'В архиве' };
const publications = ref([]);
const publicationsTotal = ref(0);
const publicationsOffset = ref(0);
const publicationsLimit = ref(24);
const publicationsLoading = ref(true);
const publicationsError = ref('');
const publicationsReadOnly = ref(false);
const publicationsIssue = ref('');
const contentReadOnly = computed(() => siteReadOnly.value || publicationsReadOnly.value);
const contentIssue = computed(() => siteIssue.value || publicationsIssue.value);
const statusFilter = ref('');
const kindFilter = ref('');
async function loadPublications(nextOffset = publicationsOffset.value) {
  const request = ++publicationRequest;
  publicationsLoading.value = true;
  publicationsError.value = '';
  try {
    const page = await props.api.getPublications({ kind: kindFilter.value, status: statusFilter.value }, { offset: nextOffset, limit: 24 });
    if (!active || request !== publicationRequest) return;
    publications.value = page.items;
    publicationsTotal.value = page.total;
    publicationsOffset.value = page.offset;
    publicationsLimit.value = page.limit;
    publicationsReadOnly.value = !!page.readOnly;
    publicationsIssue.value = page.issue || '';
  } catch (error) {
    if (!active || request !== publicationRequest) return;
    publicationsError.value = message(error, 'Не удалось загрузить публикации.');
    staffError(error);
  } finally {
    if (active && request === publicationRequest) publicationsLoading.value = false;
  }
}
const emptyPublication = () => ({ kind: 'offer', title: '', description: '', imageIds: [], target: { type: 'catalog' }, buttonText: 'Открыть каталог', position: 0, status: 'draft', startsAt: '', endsAt: '' });
const publication = ref(emptyPublication());
const publicationBaseline = ref(JSON.stringify(publication.value));
const publicationId = ref(null);
const publicationVersion = ref(null);
const publicationDates = ref({ startsAt: null, endsAt: null });
const publicationWantedId = ref(null);
const publicationLoading = ref(false);
const publicationBusy = ref(false);
const publicationError = ref('');
const publicationMessage = ref('');
const publicationConflict = ref(false);
const publicationLoadFailed = ref(false);
const publicationDirty = computed(() => JSON.stringify(publication.value) !== publicationBaseline.value);
const dateTimeLocal = value => {
  if (!value) return '';
  const date = new Date(value);
  const pad = part => String(part).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
};
function acceptPublication(result) {
  publicationId.value = result.id;
  publicationVersion.value = result.version;
  publicationWantedId.value = result.id;
  publicationDates.value = { startsAt: result.startsAt, endsAt: result.endsAt };
  publication.value = {
    kind: result.kind, title: result.title, description: result.description, imageIds: [...result.imageIds], target: copy(result.target),
    buttonText: result.buttonText, position: result.position, status: result.status, startsAt: dateTimeLocal(result.startsAt), endsAt: dateTimeLocal(result.endsAt),
  };
  publicationBaseline.value = JSON.stringify(publication.value);
  publicationConflict.value = false;
  publicationLoadFailed.value = false;
}
const discardPublication = () => !publicationDirty.value || window.confirm('Несохранённые изменения публикации будут потеряны. Продолжить?');
function newPublication() {
  if (publicationBusy.value || publicationLoading.value || contentReadOnly.value || !discardPublication()) return;
  ++detailRequest;
  publicationId.value = null;
  publicationVersion.value = null;
  publicationDates.value = { startsAt: null, endsAt: null };
  publicationWantedId.value = null;
  publication.value = emptyPublication();
  publicationBaseline.value = JSON.stringify(publication.value);
  publicationError.value = '';
  publicationMessage.value = '';
  publicationConflict.value = false;
  publicationLoadFailed.value = false;
}
async function editPublication(id) {
  if (publicationBusy.value || publicationLoading.value || contentReadOnly.value || !discardPublication()) return;
  const request = ++detailRequest;
  publicationWantedId.value = id;
  publicationLoading.value = true;
  publicationError.value = '';
  publicationMessage.value = '';
  try {
    const result = await props.api.getPublication(id);
    if (active && request === detailRequest) acceptPublication(result);
  } catch (error) {
    if (!active || request !== detailRequest) return;
    publicationLoadFailed.value = true;
    publicationError.value = message(error, 'Не удалось загрузить публикацию.');
    staffError(error);
  } finally {
    if (active && request === detailRequest) publicationLoading.value = false;
  }
}
async function savePublication() {
  if (publicationBusy.value || publicationLoading.value || publicationLoadFailed.value || contentReadOnly.value) return;
  publicationError.value = '';
  publicationMessage.value = '';
  const input = copy(publication.value);
  for (const field of ['startsAt', 'endsAt']) {
    const date = input[field] ? new Date(input[field]) : null;
    if (date && !Number.isFinite(date.getTime())) { publicationError.value = 'Укажите корректные дату и время.'; return; }
    const unchanged = input[field] && input[field] === dateTimeLocal(publicationDates.value[field]);
    input[field] = unchanged ? publicationDates.value[field] : date?.toISOString() || null;
  }
  if (input.startsAt && input.endsAt && Date.parse(input.startsAt) >= Date.parse(input.endsAt)) { publicationError.value = 'Окончание публикации должно быть позже начала.'; return; }
  input.position = Number(input.position);
  publicationBusy.value = true;
  const request = ++detailRequest;
  try {
    const result = publicationId.value
      ? await props.api.updatePublication(publicationId.value, input, publicationVersion.value)
      : await props.api.createPublication(input);
    if (!active || request !== detailRequest) return;
    acceptPublication(result);
    publicationMessage.value = result.status === 'published' ? 'Публикация сохранена. На витрине она появится в указанные сроки.' : 'Публикация сохранена.';
    emit('changed');
    await loadPublications(0);
  } catch (error) {
    if (!active || request !== detailRequest) return;
    publicationConflict.value = error?.status === 412;
    publicationError.value = publicationConflict.value ? 'Публикация уже изменена другим сотрудником. Ваши изменения остались в форме. Загрузите актуальную версию.' : message(error, 'Не удалось сохранить публикацию. Изменения остались в форме.');
    staffError(error);
  } finally {
    if (active && request === detailRequest) publicationBusy.value = false;
  }
}

const media = ref([]);
const recentMedia = ref([]);
const selectableMedia = computed(() => [...media.value, ...recentMedia.value.filter(item => !media.value.some(loaded => loaded.id === item.id))]);
const mediaTotal = ref(0);
const mediaOffset = ref(0);
const mediaLimit = ref(24);
const mediaLoading = ref(true);
const mediaError = ref('');
async function loadMedia(nextOffset = mediaOffset.value) {
  const request = ++mediaRequest;
  mediaLoading.value = true;
  mediaError.value = '';
  try {
    const page = await props.api.getMedia({ offset: nextOffset, limit: 24 });
    if (!active || request !== mediaRequest) return;
    media.value = page.items;
    mediaTotal.value = page.total;
    mediaOffset.value = page.offset;
    mediaLimit.value = page.limit;
  } catch (error) {
    if (!active || request !== mediaRequest) return;
    mediaError.value = message(error, 'Не удалось загрузить изображения.');
    staffError(error);
  } finally {
    if (active && request === mediaRequest) mediaLoading.value = false;
  }
}
const uploadFile = ref(null);
const uploadInput = ref(null);
const uploadAlt = ref('');
const uploadBusy = ref(false);
const uploadError = ref('');
const uploadMessage = ref('');
async function uploadMedia() {
  if (uploadBusy.value || !uploadFile.value || contentReadOnly.value) return;
  uploadBusy.value = true;
  uploadError.value = '';
  uploadMessage.value = '';
  try {
    const result = await props.api.uploadMedia(uploadFile.value, uploadAlt.value);
    if (!active) return;
    recentMedia.value = [result, ...recentMedia.value.filter(item => item.id !== result.id)];
    uploadFile.value = null;
    uploadAlt.value = '';
    if (uploadInput.value) uploadInput.value.value = '';
    uploadMessage.value = 'Изображение загружено. Выберите его в главной или публикации и сохраните изменения, чтобы показать на витрине.';
    await loadMedia();
  } catch (error) {
    if (!active) return;
    uploadError.value = message(error, 'Не удалось загрузить изображение. Выбранный файл и подпись сохранены.');
    staffError(error);
  } finally {
    if (active) uploadBusy.value = false;
  }
}
const dirty = computed(() => siteDirty.value || publicationDirty.value || !!uploadFile.value || !!uploadAlt.value || siteBusy.value || publicationBusy.value || uploadBusy.value);
watch(dirty, value => emit('dirty', value), { immediate: true });
watch(siteDirty, value => { if (value) siteMessage.value = ''; });
watch(publicationDirty, value => { if (value) publicationMessage.value = ''; });
watch([statusFilter, kindFilter], () => loadPublications(0));
function warnBeforeUnload(event) {
  if (!dirty.value && !siteBusy.value && !publicationBusy.value && !uploadBusy.value) return;
  event.preventDefault();
  event.returnValue = '';
}
onMounted(() => {
  loadSite();
  loadCategories();
  loadPublications(0);
  loadMedia(0);
  window.addEventListener('beforeunload', warnBeforeUnload);
});
onBeforeUnmount(() => {
  active = false;
  window.removeEventListener('beforeunload', warnBeforeUnload);
  emit('dirty', false);
});
</script>

<template>
  <section class="content-manager" aria-labelledby="content-manager-title">
    <div class="content-heading">
      <div><span class="eyebrow">LAVR / СОДЕРЖИМОЕ САЙТА</span><h2 id="content-manager-title">Витрина под контролем<span class="red-period">.</span></h2><p>Редактируйте главную, меню и публикации. Все подписи — обычный текст.</p></div>
      <span v-if="dirty" class="content-dirty" role="status">Есть несохранённые изменения</span>
    </div>
    <div v-if="api.mode === 'demo'" class="demo-notice content-notice"><AppIcon name="shield" /><div><strong>Локальный демонстрационный редактор</strong><p>Изменения сохраняются в этом браузере и доступны на витрине после сохранения.</p></div></div>
    <nav class="content-tabs" aria-label="Разделы редактора сайта">
      <button v-for="item in [{ id: 'site', label: 'Главная и меню' }, { id: 'publications', label: 'Публикации' }, { id: 'media', label: 'Изображения' }]" :key="item.id" type="button" class="btn outline" :class="{ selected: tab === item.id }" :aria-current="tab === item.id ? 'page' : undefined" :data-testid="`content-tab-${item.id}`" @click="tab = item.id">{{ item.label }}</button>
    </nav>
    <div v-if="contentReadOnly" class="content-state form-error" data-testid="content-read-only" role="alert"><strong>Редактор доступен только для чтения.</strong><p>{{ contentIssue || 'Хранилище содержимого недоступно. Сохранение отключено.' }}</p><button type="button" class="btn outline" :disabled="siteLoading || publicationsLoading" @click="loadSite(); loadPublications(0)">Повторить загрузку содержимого</button></div>
    <div v-if="categoriesError" class="content-state form-error" role="alert"><p>{{ categoriesError }}</p><button type="button" class="btn outline" :disabled="categoriesLoading" @click="loadCategories">Повторить загрузку категорий</button></div>

    <div v-show="tab === 'site'">
      <p v-if="siteLoading" class="content-state" role="status">Загружаем главную и меню…</p>
      <div v-if="siteError" class="content-state form-error" role="alert"><p>{{ siteError }}</p><button type="button" class="btn outline" data-testid="reload-site" :disabled="siteBusy || siteLoading" @click="loadSite()">{{ site ? 'Загрузить актуальную версию' : 'Повторить загрузку сайта' }}</button></div>
      <form v-if="site" :aria-busy="siteBusy" @submit.prevent="saveSite">
        <fieldset :disabled="siteBusy || siteLoading || contentReadOnly" class="content-site-fields">
          <div class="content-card">
            <div class="content-card-heading"><AppIcon name="grid" /><h3>Первый экран</h3></div>
            <div class="content-fields">
              <label class="full" for="hero-title">Заголовок<textarea id="hero-title" v-model="site.hero.title" rows="3" required maxlength="240" data-testid="hero-title"></textarea></label>
              <label class="full" for="hero-description">Описание<textarea id="hero-description" v-model="site.hero.description" rows="4" maxlength="2000" data-testid="hero-description"></textarea></label>
              <label class="full" for="hero-button-text">Текст кнопки<input id="hero-button-text" v-model="site.hero.buttonText" required maxlength="80" data-testid="hero-button-text"></label>
            </div>
            <ContentTarget v-model="site.hero.target" :categories="categories" :disabled="siteBusy || siteLoading || contentReadOnly" prefix="hero" />
            <h4>Фоновое изображение</h4><p class="content-help">Без изображения сохранятся текст и кнопка первого экрана.</p>
            <MediaSelector v-model="site.hero.imageId" :media="selectableMedia" :disabled="siteBusy || siteLoading || contentReadOnly" prefix="hero" />
          </div>
          <div class="content-card">
            <div class="content-card-heading"><AppIcon name="pin" /><h3>Реквизиты и разделы</h3></div>
            <div class="content-fields"><label v-for="field in settingsFields" :key="field.key" :for="`site-${field.key}`" :class="{ full: field.key === 'address' }">{{ field.label }}<input :id="`site-${field.key}`" v-model="site.settings[field.key]" required :maxlength="field.max" :minlength="field.min || 1" :type="field.key === 'phone' ? 'tel' : 'text'" :data-testid="`site-${field.key}`"></label></div>
          </div>
          <div class="content-card full">
            <div class="content-card-heading"><AppIcon name="menu" /><h3>Меню <span class="heading-count">{{ site.menu.length }} / 12</span></h3></div>
            <p class="content-help">Порядок на витрине соответствует порядку в списке. Скрытый пункт остаётся в редакторе.</p>
            <div v-for="(item, index) in site.menu" :key="index" class="menu-editor-row">
              <div class="menu-editor-number">{{ index + 1 }}</div>
              <div class="menu-editor-fields"><label :for="`menu-label-${index}`">Название пункта<input :id="`menu-label-${index}`" v-model="item.label" required maxlength="80" :data-testid="`menu-label-${index}`"></label><ContentTarget v-model="item.target" :categories="categories" :disabled="siteBusy || siteLoading || contentReadOnly" :prefix="`menu-${index}`" /><label class="content-check" :for="`menu-visible-${index}`"><input :id="`menu-visible-${index}`" v-model="item.isVisible" type="checkbox">Показывать в меню</label></div>
              <div class="menu-editor-actions"><button type="button" class="btn outline" :disabled="index === 0" :aria-label="`Поднять пункт ${index + 1}`" :data-testid="`move-menu-up-${index}`" @click="moveMenu(index, -1)">↑</button><button type="button" class="btn outline" :disabled="index === site.menu.length - 1" :aria-label="`Опустить пункт ${index + 1}`" :data-testid="`move-menu-down-${index}`" @click="moveMenu(index, 1)">↓</button><button type="button" class="btn outline danger" :aria-label="`Удалить пункт ${index + 1}`" :data-testid="`remove-menu-item-${index}`" @click="site.menu.splice(index, 1)">Удалить</button></div>
            </div>
            <button type="button" class="btn outline" data-testid="add-menu-item" :disabled="site.menu.length >= 12" @click="addMenuItem"><AppIcon name="plus" /> Добавить пункт меню</button>
          </div>
        </fieldset>
        <div class="content-save-row"><button type="submit" class="btn red" data-testid="save-site" :disabled="siteBusy || siteLoading || siteConflict || contentReadOnly"><AppIcon name="check" /> {{ siteBusy ? 'Сохраняем…' : 'Сохранить главную и меню' }}</button><span v-if="siteDirty" class="content-help">Изменения ещё не опубликованы.</span></div>
        <p v-if="siteMessage" class="content-success" data-testid="site-success" role="status">{{ siteMessage }}</p>
      </form>
    </div>

    <div v-show="tab === 'publications'" class="content-publications-layout">
      <div class="content-card content-publication-list">
        <div class="content-card-heading"><AppIcon name="box" /><h3>Публикации <span class="heading-count">{{ publicationsLoading || publicationsError ? '—' : publicationsTotal }}</span></h3></div>
        <div class="content-fields"><label for="publication-kind-filter">Тип<select id="publication-kind-filter" v-model="kindFilter"><option value="">Все типы</option><option v-for="(label, kind) in kindLabels" :key="kind" :value="kind">{{ label }}</option></select></label><label for="publication-status-filter">Статус<select id="publication-status-filter" v-model="statusFilter" data-testid="publication-status-filter"><option value="">Все статусы</option><option v-for="(label, status) in statusLabels" :key="status" :value="status">{{ label }}</option></select></label></div>
        <button type="button" class="btn outline" data-testid="new-publication" :disabled="publicationBusy || publicationLoading || contentReadOnly" @click="newPublication"><AppIcon name="plus" /> Новая публикация</button>
        <p v-if="publicationsLoading" class="content-help" role="status">Загружаем публикации…</p>
        <div v-else-if="publicationsError" class="content-state form-error" role="alert"><p>{{ publicationsError }}</p><button type="button" class="btn outline" @click="loadPublications()">Повторить загрузку публикаций</button></div>
        <div v-else-if="publications.length" class="publication-list"><button v-for="item in publications" :key="item.id" type="button" class="publication-list-item" :class="{ selected: publicationId === item.id }" :disabled="publicationBusy || publicationLoading || contentReadOnly" :data-testid="`edit-publication-${item.id}`" @click="editPublication(item.id)"><strong>{{ item.title }}</strong><span>{{ kindLabels[item.kind] }} · {{ statusLabels[item.status] }}</span><small>ID: {{ item.id }}</small></button></div>
        <p v-else class="content-help">Публикаций с выбранными фильтрами пока нет.</p>
        <nav v-if="!publicationsLoading && !publicationsError && publicationsTotal > publicationsLimit" class="content-pagination" aria-label="Страницы публикаций"><button type="button" class="btn outline" :disabled="publicationsOffset === 0" @click="loadPublications(Math.max(0, publicationsOffset - publicationsLimit))">Назад</button><span>{{ publicationsOffset + 1 }}–{{ publicationsOffset + publications.length }} из {{ publicationsTotal }}</span><button type="button" class="btn outline" :disabled="publicationsOffset + publicationsLimit >= publicationsTotal" @click="loadPublications(publicationsOffset + publicationsLimit)">Далее</button></nav>
      </div>
      <form class="content-card" :aria-busy="publicationBusy || publicationLoading" @submit.prevent="savePublication">
        <div class="content-card-heading"><AppIcon name="link" /><h3>{{ publicationId ? 'Редактировать публикацию' : 'Новая публикация' }}</h3></div>
        <p v-if="publicationLoading" class="content-help" role="status">Загружаем актуальную версию…</p>
        <p v-if="publicationDirty" class="content-help">В форме есть несохранённые изменения.</p>
        <fieldset :disabled="publicationBusy || publicationLoading || publicationLoadFailed || contentReadOnly">
          <div class="content-fields">
            <label for="publication-kind">Тип<select id="publication-kind" v-model="publication.kind" data-testid="publication-kind"><option v-for="(label, kind) in kindLabels" :key="kind" :value="kind">{{ label }}</option></select></label>
            <label for="publication-status">Статус<select id="publication-status" v-model="publication.status" data-testid="publication-status"><option v-for="(label, status) in statusLabels" :key="status" :value="status">{{ label }}</option></select></label>
            <label class="full" for="publication-title">Заголовок<textarea id="publication-title" v-model="publication.title" rows="2" required maxlength="240" data-testid="publication-title"></textarea></label>
            <label class="full" for="publication-description">Описание<textarea id="publication-description" v-model="publication.description" rows="5" maxlength="4000" data-testid="publication-description"></textarea></label>
            <label class="full" for="publication-button-text">Текст кнопки<input id="publication-button-text" v-model="publication.buttonText" required maxlength="80" data-testid="publication-button-text"></label>
            <label for="publication-position">Порядок<input id="publication-position" v-model="publication.position" type="number" required min="0" max="1000000" step="1" data-testid="publication-position"></label>
          </div>
          <ContentTarget v-model="publication.target" :categories="categories" :disabled="publicationBusy || publicationLoading || publicationLoadFailed || contentReadOnly" prefix="publication" />
          <h4>Период публикации</h4><p class="content-help">Время по часовому поясу устройства. Пустые поля означают отсутствие ограничения.</p>
          <div class="content-fields"><label for="publication-starts-at">Начало<input id="publication-starts-at" v-model="publication.startsAt" type="datetime-local" step="1" data-testid="publication-starts-at"></label><label for="publication-ends-at">Окончание<input id="publication-ends-at" v-model="publication.endsAt" type="datetime-local" step="1" data-testid="publication-ends-at"></label></div>
          <h4>Изображения</h4><p class="content-help">До 8 изображений. Выбор сохраняется при переходе между страницами библиотеки.</p>
          <MediaSelector v-model="publication.imageIds" :media="selectableMedia" multiple :disabled="publicationBusy || publicationLoading || publicationLoadFailed || contentReadOnly" prefix="publication" />
        </fieldset>
        <div v-if="publicationError" class="content-state form-error" role="alert"><p>{{ publicationError }}</p><button v-if="publicationConflict || publicationLoadFailed" type="button" class="btn outline" data-testid="reload-publication" :disabled="publicationBusy || publicationLoading" @click="editPublication(publicationWantedId)">{{ publicationConflict ? 'Загрузить актуальную версию' : 'Повторить загрузку публикации' }}</button></div>
        <button type="submit" class="btn red" data-testid="save-publication" :disabled="publicationBusy || publicationLoading || publicationLoadFailed || publicationConflict || contentReadOnly"><AppIcon name="check" /> {{ publicationBusy ? 'Сохраняем…' : 'Сохранить публикацию' }}</button>
        <p v-if="publicationMessage" class="content-success" data-testid="publication-success" role="status">{{ publicationMessage }}</p>
      </form>
    </div>

    <div v-show="tab === 'media'" class="content-media-layout">
      <form class="content-card" :aria-busy="uploadBusy" @submit.prevent="uploadMedia">
        <div class="content-card-heading"><AppIcon name="plus" /><h3>Загрузить изображение</h3></div>
        <p class="content-help">JPG, PNG или WebP до 5 МБ, не больше 4096 × 4096 пикселей. Файл станет публичным после сохранения ссылки на него в главной или опубликованном материале.</p>
        <fieldset :disabled="uploadBusy || contentReadOnly"><div class="content-fields"><label class="full" for="media-file">Файл<input id="media-file" ref="uploadInput" type="file" required accept="image/jpeg,image/png,image/webp" data-testid="media-file" @change="uploadFile = $event.target.files?.[0] || null"></label><label class="full" for="media-alt">Описание изображения<input id="media-alt" v-model="uploadAlt" maxlength="240" data-testid="media-alt" placeholder="Кратко опишите, что изображено"></label></div></fieldset>
        <button type="submit" class="btn red" data-testid="upload-media" :disabled="uploadBusy || !uploadFile || contentReadOnly"><AppIcon name="plus" /> {{ uploadBusy ? 'Загружаем…' : 'Загрузить изображение' }}</button>
        <p v-if="uploadError" class="content-state form-error" role="alert">{{ uploadError }}</p><p v-if="uploadMessage" class="content-success" data-testid="upload-success" role="status">{{ uploadMessage }}</p>
      </form>
      <div class="content-card"><div class="content-card-heading"><AppIcon name="grid" /><h3>Библиотека изображений <span class="heading-count">{{ mediaLoading || mediaError ? '—' : mediaTotal }}</span></h3></div><p class="content-help">Файлы из библиотеки можно выбрать в главной и публикациях.</p><div v-if="!mediaLoading && !mediaError && selectableMedia.length" class="media-library"><article v-for="item in selectableMedia" :key="item.id" class="media-library-item" :data-testid="`media-${item.id}`"><img :src="item.path" :alt="item.alt" loading="lazy"><strong>{{ item.alt || 'Без описания' }}</strong><small>{{ item.width }} × {{ item.height }} · {{ Math.ceil(item.sizeBytes / 1024) }} КБ</small><small>ID: {{ item.id }}</small></article></div><p v-else-if="!mediaLoading && !mediaError" class="content-help">Изображений пока нет.</p></div>
    </div>

    <div class="content-media-state" :aria-busy="mediaLoading">
      <p v-if="mediaLoading" class="content-state" role="status">Загружаем библиотеку изображений…</p>
      <div v-else-if="mediaError" class="content-state form-error" role="alert"><p>{{ mediaError }}</p><button type="button" class="btn outline" @click="loadMedia()">Повторить загрузку изображений</button></div>
      <nav v-else-if="mediaTotal > mediaLimit" class="content-pagination" aria-label="Страницы библиотеки изображений"><button type="button" class="btn outline" data-testid="media-prev" :disabled="mediaOffset === 0" @click="loadMedia(Math.max(0, mediaOffset - mediaLimit))">Предыдущие изображения</button><span>{{ mediaOffset + 1 }}–{{ mediaOffset + media.length }} из {{ mediaTotal }}</span><button type="button" class="btn outline" data-testid="media-next" :disabled="mediaOffset + mediaLimit >= mediaTotal" @click="loadMedia(mediaOffset + mediaLimit)">Следующие изображения</button></nav>
    </div>
  </section>
</template>

<style scoped>
.content-manager { min-width: 0; }
.content-heading { display: flex; justify-content: space-between; align-items: end; gap: 20px; margin-bottom: 22px; }
.content-heading > div { min-width: 0; }
.content-heading h2 { font-size: 29px; font-weight: 650; letter-spacing: -.9px; }
.content-heading p { color: var(--muted); margin-top: 9px; font-size: 12px; line-height: 1.7; }
.content-dirty { color: #8c5618; font-size: 12px; }
.content-notice { margin: 0 0 25px; }
.content-tabs { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 24px; }
.content-tabs .selected { color: var(--red-dark); border-color: var(--red); background: #fff5f5; }
.content-site-fields, .content-publications-layout, .content-media-layout { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 20px; align-items: start; }
.content-publications-layout, .content-media-layout { grid-template-columns: minmax(0, 1fr) minmax(0, 2fr); }
.content-card { min-width: 0; padding: 25px; border: 1px solid var(--line); border-radius: 7px; background: white; }
.content-card.full { grid-column: 1 / -1; }
.content-card-heading { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
.content-card-heading > .icon { width: 20px; height: 20px; color: var(--red); }
.content-card h3 { min-width: 0; font-size: 18px; font-weight: 600; letter-spacing: -.4px; }
.content-card h4 { margin: 24px 0 10px; font-size: 14px; font-weight: 600; }
.content-fields, :deep(.target-fields) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.content-fields .full { grid-column: 1 / -1; }
:deep(.target-fields) { margin-top: 18px; }
.content-manager label, :deep(.target-fields label) { display: block; min-width: 0; font-size: 12px; font-weight: 500; }
.content-manager input, .content-manager select, .content-manager textarea, :deep(.target-fields input), :deep(.target-fields select) { display: block; width: 100%; min-width: 0; min-height: 44px; padding: 11px 12px; margin-top: 9px; border: 1px solid #dadfe6; background: white; border-radius: 6px; color: var(--ink); font-family: inherit; font-size: 16px; line-height: 1.45; }
.content-manager textarea { resize: vertical; }
.content-manager textarea:focus-visible { outline: 3px solid #357cbb; outline-offset: 3px; }
.content-manager input[type="file"] { padding: 9px; overflow: hidden; }
.content-manager input[type="file"]::file-selector-button { min-height: 32px; margin-right: 8px; border: 1px solid #dadfe6; border-radius: 4px; background: var(--paper); }
.content-help, :deep(.content-help) { color: var(--muted); font-size: 12px; line-height: 1.7; margin: 12px 0 18px; }
.content-state { padding: 16px 0; font-size: 12px; line-height: 1.7; }
.content-state .btn { margin-top: 12px; }
.content-success { color: var(--green); font-size: 12px; line-height: 1.7; margin-top: 16px; }
.content-save-row { display: flex; align-items: center; flex-wrap: wrap; gap: 18px; margin-top: 22px; }
.content-save-row .content-help { margin: 0; }
.content-card > .btn { margin-top: 20px; }
.content-manager .btn, :deep(.btn) { min-height: 44px; max-width: 100%; white-space: normal; font-size: 12px; }
.content-manager p, .content-manager h2, .content-manager h3, .content-manager label, :deep(.media-selector) { overflow-wrap: anywhere; }
.menu-editor-row { display: flex; align-items: start; gap: 14px; padding: 20px 0; border-top: 1px solid var(--line); }
.menu-editor-number { display: grid; place-items: center; flex-shrink: 0; width: 26px; height: 26px; color: var(--muted); background: var(--paper); border-radius: 50%; font-size: 12px; }
.menu-editor-fields { flex: 1; min-width: 0; }
.menu-editor-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.menu-editor-actions .btn { min-width: 44px; padding: 10px; }
.content-manager .content-check { display: flex; align-items: center; gap: 8px; min-height: 44px; margin-top: 10px; }
.content-manager .content-check input { width: 18px; height: 18px; min-height: 0; margin: 0; }
.publication-list { display: grid; gap: 8px; margin-top: 20px; }
.publication-list-item { display: grid; gap: 7px; min-width: 0; width: 100%; min-height: 44px; padding: 14px; text-align: left; background: white; border: 1px solid var(--line); }
.publication-list-item.selected { border-color: var(--red); background: #fff8f8; }
.publication-list-item strong { font-size: 13px; line-height: 1.5; }
.publication-list-item span, .publication-list-item small { color: var(--muted); font-size: 11px; }
.publication-list-item > * { overflow-wrap: anywhere; }
.content-pagination { display: flex; flex-wrap: wrap; align-items: center; justify-content: center; gap: 15px; margin-top: 22px; }
.content-pagination span { color: var(--muted); font-size: 12px; }
.content-media-state { margin-top: 20px; }
:deep(.media-choice-grid), .media-library { display: grid; grid-template-columns: repeat(auto-fill, minmax(120px, 1fr)); gap: 12px; }
:deep(.media-choice), .media-library-item { min-width: 0; padding: 8px; border: 1px solid var(--line); border-radius: 6px; background: white; text-align: left; }
:deep(.media-choice.selected) { border-color: var(--red); background: #fff5f5; }
:deep(.media-choice img), .media-library-item img { display: block; width: 100%; height: 90px; object-fit: contain; background: var(--paper); border-radius: 4px; }
:deep(.media-choice span), .media-library-item strong { display: block; margin-top: 8px; font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
:deep(.media-choice small), .media-library-item small { display: block; margin-top: 5px; color: var(--muted); font-size: 10px; overflow-wrap: anywhere; }
:deep(.selected-media) { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin: 15px 0; font-size: 12px; }
:deep(.selected-media-chip) { padding: 8px 12px; text-align: left; overflow-wrap: anywhere; }
@media (max-width: 900px) {
  .content-publications-layout, .content-media-layout { grid-template-columns: minmax(0, 1fr); }
  .menu-editor-row { flex-wrap: wrap; }
  .menu-editor-actions { margin-left: 40px; }
}
@media (max-width: 600px) {
  .content-heading { display: block; }
  .content-heading h2 { font-size: 25px; }
  .content-dirty { display: block; margin-top: 12px; }
  .content-card { padding: 18px; }
  .content-site-fields, .content-fields, :deep(.target-fields) { grid-template-columns: minmax(0, 1fr); }
  .content-tabs .btn { flex: 1 1 auto; }
  .content-pagination .btn { flex: 1 1 auto; }
  :deep(.media-choice-grid), .media-library { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>

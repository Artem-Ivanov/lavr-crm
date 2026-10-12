<script setup>
import { computed } from 'vue';
import AppIcon from './AppIcon.vue';
const props = defineProps({ categories: { type: Array, required: true }, site: { type: Object, required: true }, demo: Boolean });
const emit = defineEmits(['category', 'navigate']);
const categoryIcons = { __proto__: null, oils: 'oil', filters: 'filter', brakes: 'brake', ignition: 'spark', suspension: 'spring', belts: 'belt' };
const heroLines = computed(() => props.site.hero.title.split('\n'));
const publicationStyle = publication => publication.style || (publication.kind === 'announcement' ? 'notice' : publication.images.length === 1 ? 'oil' : 'service');
const publicationLabel = publication => publication.label || ({ offer: 'Акция', announcement: 'Объявление', hot: 'Горячее предложение' })[publication.kind];
</script>

<template>
  <section class="hero container" :style="{ '--hero-image': site.hero.image ? `url(${site.hero.image.path})` : 'none' }" aria-labelledby="hero-title">
    <div class="hero-copy">
      <div class="eyebrow light"><span></span> {{ site.hero.kicker || 'ДЕТАЛИ, КОТОРЫЕ ВАЖНЫ' }}</div>
      <h1 id="hero-title"><template v-for="(line, index) in heroLines" :key="index"><span v-if="heroLines.length > 1 && index === heroLines.length - 1">{{ line }}</span><template v-else>{{ line }}</template><br v-if="index < heroLines.length - 1"></template></h1>
      <p>{{ site.hero.description }}</p>
      <button v-if="site.hero.target" type="button" class="btn red hero-button" @click="emit('navigate', site.hero.target)">{{ site.hero.buttonText }} <AppIcon name="arrow" /></button>
      <span v-if="site.hero.note" class="hero-note">{{ site.hero.note }}</span>
    </div>
    <div class="hero-corner"><span class="hero-line"></span> {{ site.hero.corner || 'НА ХОДУ. КАЖДЫЙ ДЕНЬ.' }}</div>
  </section>

  <section class="benefits container landing-benefits" aria-label="О магазине">
    <div><AppIcon name="box" /><span><strong>Всё в одном каталоге</strong><small>Масла, расходники и автозапчасти</small></span></div>
    <div><AppIcon name="search" /><span><strong>Поиск по артикулу</strong><small>А также по бренду и названию</small></span></div>
    <div><AppIcon name="truck" /><span><strong>Самовывоз или доставка</strong><small>Выберите при оформлении</small></span></div>
    <div><AppIcon name="shield" /><span><strong>Заказ без онлайн-оплаты</strong><small>Условия уточняются отдельно</small></span></div>
  </section>

  <section class="categories container landing-categories" aria-labelledby="categories-title">
    <div class="section-heading"><div><span class="eyebrow">{{ site.settings.categoriesKicker || 'ПРОСТО НАЙТИ. ЛЕГКО ВЫБРАТЬ.' }}</span><h2 id="categories-title">{{ site.settings.categoriesTitle }}</h2></div><a class="text-link" href="#catalog">Весь каталог <AppIcon name="arrow" /></a></div>
    <div class="category-grid">
      <button v-for="category in categories" :key="category.id" type="button" class="category-card" data-action="category" :data-id="category.id" @click="emit('category', category.id)"><span class="category-art"><AppIcon :name="categoryIcons[category.id] || 'box'" /></span><strong>{{ category.name }}</strong><span>{{ category.productCount === undefined ? 'Открыть категорию' : `Позиций: ${category.productCount}` }} <AppIcon name="arrow" /></span></button>
    </div>
    <p v-if="!categories.length" class="landing-empty">Категории пока не добавлены. Найдите запчасть по артикулу в поиске.</p>
  </section>

  <section v-if="site.publications.length" id="promotions" class="container promotions" aria-labelledby="promotions-title">
    <div class="section-heading"><div><span class="eyebrow">{{ site.settings.promotionsKicker || 'ПОЛЕЗНО ПЕРЕД ПОЕЗДКОЙ' }}</span><h2 id="promotions-title">{{ site.settings.promotionsTitle }}</h2></div><span v-if="demo" class="promotions-note">{{ site.settings.promotionsNote }}</span></div>
    <div class="promotion-grid">
      <article v-for="publication in site.publications" :key="publication.id" class="promotion-card" :class="`promotion-${publicationStyle(publication)}`" :data-kind="publication.kind">
        <div v-if="publicationStyle(publication) === 'notice'" class="notice-top"><span class="promotion-label">{{ publicationLabel(publication) }}</span><div v-if="publication.images.length" class="notice-images"><img v-for="image in publication.images" :key="image.id" :src="image.path" :alt="image.alt" loading="lazy" width="180" height="130"></div><AppIcon v-else name="box" /></div>
        <div v-else class="promotion-art" :class="publicationStyle(publication) === 'oil' ? 'oil-art' : 'filter-art'"><img v-for="image in publication.images" :key="image.id" :src="image.path" :alt="image.alt" loading="lazy" width="180" height="130"><span class="promotion-label">{{ publicationLabel(publication) }}</span></div>
        <div class="promotion-copy"><span class="promotion-kicker">{{ publication.kicker || publicationLabel(publication) }}</span><h3>{{ publication.title }}</h3><p>{{ publication.description }}</p><button v-if="publication.target" type="button" class="promotion-link" :data-action="publication.target.type === 'category' ? 'category' : undefined" :data-id="publication.target.categoryId" @click="emit('navigate', publication.target)">{{ publication.buttonText }} <AppIcon name="arrow" /></button></div>
      </article>
    </div>
    <p v-if="demo" class="promotion-footnote">{{ site.settings.promotionsFootnote }}</p>
  </section>
</template>

<style scoped>
.landing-benefits strong { font-size: 13px; line-height: 1.4; }
.landing-benefits small { font-size: 11px; }
.landing-benefits > div { min-width: 0; }
.landing-categories { padding-block: 42px 46px; }
.landing-categories .category-grid { grid-template-columns: repeat(6, minmax(0, 1fr)); }
.landing-categories .category-card { min-width: 0; padding: 21px 18px; background: white; border-color: var(--line); }
.landing-categories .category-card:hover { background: #fff7f7; border-color: #eaa5a9; }
.landing-categories .category-art { width: 48px; height: 48px; justify-content: center; background: var(--paper); border-radius: 8px; margin-bottom: 17px; }
.landing-categories .category-art .icon { width: 31px; height: 31px; }
.landing-categories .category-card strong { min-height: 40px; font-size: 13px; line-height: 1.45; overflow-wrap: anywhere; }
.landing-categories .category-card > span:last-child { font-size: 11px; gap: 7px; }
.landing-empty { color: var(--muted); font-size: 14px; line-height: 1.6; }
.promotions { padding-bottom: 46px; scroll-margin-top: 24px; }
.promotions-note { color: var(--muted); font-size: 12px; line-height: 1.5; max-width: 230px; text-align: right; }
.promotion-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 18px; }
.promotion-card { display: flex; flex-direction: column; min-width: 0; border: 1px solid var(--line); border-radius: 9px; overflow: hidden; }
.promotion-art { position: relative; height: 170px; overflow: hidden; background: var(--paper); }
.promotion-art img { object-fit: contain; mix-blend-mode: multiply; }
.filter-art { display: flex; align-items: center; justify-content: center; gap: 2px; padding: 27px 20px 8px; }
.filter-art img:first-child { width: 52%; height: 128px; transform: rotate(-8deg); }
.filter-art img:nth-child(2) { width: 30%; height: 106px; transform: rotate(8deg); }
.oil-art { display: flex; align-items: center; justify-content: center; background: #f5f3ef; padding-top: 28px; }
.oil-art img { width: 170px; height: 146px; }
.promotion-label { display: inline-block; padding: 6px 9px; border-radius: 4px; background: white; color: #5c626d; font-size: 10px; font-weight: 600; line-height: 1.3; }
.promotion-art .promotion-label { position: absolute; top: 15px; left: 18px; }
.promotion-copy { display: flex; flex: 1; flex-direction: column; align-items: flex-start; padding: 25px 25px 20px; }
.promotion-kicker { color: var(--red-dark); font-size: 9px; font-weight: 700; letter-spacing: 1.4px; margin-bottom: 13px; }
.promotion-copy h3 { font-size: 25px; line-height: 1.16; font-weight: 650; letter-spacing: -.7px; }
.promotion-copy p { color: var(--muted); font-size: 13px; line-height: 1.65; margin-top: 14px; margin-bottom: 18px; }
.promotion-link { display: inline-flex; align-items: center; justify-content: space-between; gap: 14px; min-height: 44px; margin-top: auto; padding: 7px 0; border: 0; border-radius: 0; background: transparent; color: var(--ink); font-size: 12px; font-weight: 650; text-align: left; }
.promotion-link .icon { width: 19px; height: 19px; }
.promotion-link:hover { color: var(--red); }
.promotion-notice { color: white; background: #292d34; border-color: #292d34; }
.notice-top { display: flex; align-items: flex-start; justify-content: space-between; height: 170px; padding: 18px 25px 12px; }
.notice-top .promotion-label { background: #ffffff12; color: #d9dce1; }
.notice-top > .icon { align-self: flex-end; width: 77px; height: 77px; color: #9298a2; stroke-width: 1; }
.promotion-notice .promotion-kicker { color: #ff969a; }
.promotion-notice .promotion-copy p { color: #c2c6ce; }
.promotion-notice .promotion-link { color: white; }
.promotion-notice .promotion-link:hover { color: #ff969a; }
.promotion-footnote { color: var(--muted); font-size: 11px; line-height: 1.6; margin-top: 15px; }

@media (max-width: 1100px) {
  .landing-benefits > div { padding-inline: 12px; gap: 10px; }
  .landing-benefits strong { font-size: 12px; }
  .landing-categories .category-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .landing-categories .category-card { display: grid; grid-template-columns: 48px 1fr; gap: 0 16px; align-items: center; }
  .landing-categories .category-art { grid-row: span 2; margin: 0; }
  .landing-categories .category-card strong { min-height: 0; }
  .landing-categories .category-card > span:last-child { margin-top: 9px; }
  .promotion-grid { gap: 13px; }
  .promotion-copy { padding: 22px 19px 17px; }
  .promotion-copy h3 { font-size: 23px; }
  .notice-top { padding-inline: 19px; }
}
@media (max-width: 760px) {
  .hero p { font-size: 13px; }
  .hero-note { font-size: 11px; }
  .hero-button { font-size: 12px; }
  .landing-benefits { gap: 20px 15px; padding-block: 25px; }
  .landing-benefits > div { padding: 0; gap: 9px; }
  .landing-benefits strong { font-size: 12px; }
  .landing-benefits small { font-size: 11px; }
  .landing-categories { padding-block: 32px; }
  .landing-categories .section-heading { align-items: end; }
  .landing-categories .section-heading .text-link { font-size: 11px; margin-top: 0; }
  .landing-categories .category-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .landing-categories .category-card { display: block; padding: 16px; }
  .landing-categories .category-art { width: 43px; height: 43px; margin-bottom: 12px; }
  .landing-categories .category-card strong { min-height: 38px; font-size: 12px; }
  .landing-categories .category-card > span:last-child { font-size: 11px; }
  .promotions { padding-bottom: 32px; }
  .promotions .section-heading { flex-direction: column; gap: 10px; }
  .promotions-note { max-width: none; text-align: left; font-size: 12px; }
  .promotion-grid { grid-template-columns: 1fr; gap: 15px; }
  .promotion-card { display: grid; grid-template-columns: 34% minmax(0, 1fr); }
  .promotion-art, .notice-top { min-height: 100%; height: auto; }
  .filter-art { flex-direction: column; justify-content: flex-end; gap: 2px; padding: 46px 10px 15px; }
  .filter-art img:first-child { width: 90%; height: 105px; }
  .filter-art img:nth-child(2) { width: 65%; height: 77px; }
  .oil-art { padding: 45px 8px 12px; }
  .oil-art img { width: 100%; height: 175px; }
  .promotion-art .promotion-label { top: 13px; left: 10px; right: 10px; text-align: center; }
  .promotion-copy { padding: 23px 20px 17px; }
  .promotion-copy h3 { font-size: 22px; }
  .promotion-copy p { font-size: 12px; margin-block: 12px; }
  .promotion-kicker { font-size: 8px; letter-spacing: 1px; }
  .promotion-link { font-size: 11px; gap: 9px; width: 100%; }
  .notice-top { flex-direction: column; align-items: center; justify-content: space-between; padding: 13px 10px 30px; }
  .notice-top .promotion-label { text-align: center; }
  .notice-top > .icon { width: 62px; height: 62px; align-self: center; margin-top: 24px; }
}
@media (max-width: 390px) {
  .hero-copy { padding-inline: 22px; }
  .hero h1 { font-size: 37px; }
  .landing-benefits > div { gap: 7px; }
  .landing-benefits .icon { width: 20px; height: 20px; }
  .landing-categories .section-heading { flex-direction: column; align-items: flex-start; }
  .landing-categories .category-card { padding: 14px 12px; }
  .promotion-card { grid-template-columns: 1fr; }
  .promotion-art { height: 150px; min-height: 0; }
  .filter-art { flex-direction: row; justify-content: center; padding: 31px 24px 7px; }
  .filter-art img:first-child { width: 55%; height: 105px; }
  .filter-art img:nth-child(2) { width: 30%; height: 90px; }
  .oil-art { padding: 28px 10px 0; }
  .oil-art img { height: 127px; }
  .promotion-art .promotion-label { left: 16px; right: auto; }
  .notice-top { flex-direction: row; height: 105px; min-height: 0; padding: 16px 20px; }
  .notice-top > .icon { width: 55px; height: 55px; margin: 0; }
  .promotion-copy { padding: 20px; }
  .promotion-copy h3 { font-size: 24px; }
  .promotion-copy p { font-size: 13px; }
  .promotion-link { font-size: 12px; }
}
.notice-top:has(.notice-images) { flex-direction: column; height: auto; min-height: 170px; gap: 12px; }
.notice-images { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; width: 100%; }
.notice-images img { width: 100%; height: 80px; object-fit: contain; }
.promotion-art:has(img:nth-child(3)) { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); height: auto; min-height: 170px; padding: 52px 20px 16px; gap: 8px; }
.promotion-art:has(img:nth-child(3)) img { width: 100%; height: 80px; transform: none; }
</style>

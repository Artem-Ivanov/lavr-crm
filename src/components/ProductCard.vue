<script setup>
import { productUnit, canAddQuantity, formatQuantity } from '../quantity.js';
import AppIcon from './AppIcon.vue';
import { routeHash } from '../routes.js';

defineProps({
  product: { type: Object, required: true },
  demo: Boolean,
  index: { type: Number, default: 0 },
  quantity: { type: Number, default: 0 },
  categoryId: { type: String, default: '' },
  category: { type: String, required: true },
  price: { type: String, required: true },
});
defineEmits(['open', 'add']);
</script>

<template>
  <article class="product-card">
    <div class="product-image">
      <div class="product-image-button">
        <img v-if="product.image" :src="product.image" :alt="`Иллюстрация категории ${category}`" loading="lazy">
        <span v-else class="product-placeholder"><AppIcon name="box" /><small>Фото пока нет</small></span>
      </div>
      <span v-if="demo && index < 2" class="product-tag">ВЫБОР ДЛЯ ТО</span>
    </div>
    <div class="product-info">
      <div class="product-brand">{{ product.brand }}<span v-if="product.sku">Арт. {{ product.sku }}</span></div>
      <a :href="routeHash({ page: 'catalog', categoryId, productId: product.id })" class="product-name" data-action="product" :data-id="product.id" :data-focus-key="`view-${product.id}`" @click="$emit('open', product.id, $event)">{{ product.websiteTitle || product.name }}</a>
      <p class="product-stock" :class="{ unavailable: !product.stock }"><span></span>{{ product.stock ? `В наличии · ${formatQuantity(product.stock, productUnit(product))}` : 'Нет в наличии' }}</p>
      <small v-if="!product.stock || product.deliveryDays" class="product-delivery">{{ product.stock ? `${demo ? 'Срок в демо' : 'Ориентировочно'} · ${product.deliveryDays} дн.` : 'Срок получения уточняется' }}</small>
      <div class="product-bottom">
        <strong>{{ price }}<small class="unit-price">за {{ productUnit(product) }}</small></strong>
        <button type="button" class="btn add-button" :class="{ added: quantity }" data-action="add" :data-id="product.id" :data-focus-key="`add-${product.id}`" :disabled="!canAddQuantity(product, quantity)" @click="$emit('add', product.id)">
          <AppIcon :name="quantity ? 'check' : 'plus'" /> {{ quantity ? `${formatQuantity(quantity, productUnit(product))} в корзине` : 'В корзину' }}
        </button>
      </div>
    </div>
  </article>
</template>

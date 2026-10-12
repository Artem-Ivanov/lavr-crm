<script setup>
import AppIcon from './AppIcon.vue';

defineProps({
  product: { type: Object, required: true },
  demo: Boolean,
  index: { type: Number, default: 0 },
  quantity: { type: Number, default: 0 },
  category: { type: String, required: true },
  price: { type: String, required: true },
});
defineEmits(['open', 'add']);
</script>

<template>
  <article class="product-card">
    <div class="product-image">
      <button type="button" class="product-image-button" data-action="product" :data-id="product.id" :data-focus-key="`image-${product.id}`" :aria-label="`Подробнее: ${product.websiteTitle || product.name}`" @click="$emit('open', product.id, $event)">
        <img v-if="product.image" :src="product.image" :alt="`Иллюстрация категории ${category}`" loading="lazy">
        <span v-else class="product-placeholder"><AppIcon name="box" /><small>Фото пока нет</small></span>
      </button>
      <span v-if="index < 2" class="product-tag">ВЫБОР ДЛЯ ТО</span>
    </div>
    <div class="product-info">
      <div class="product-brand">{{ product.brand }}<span>Арт. {{ product.sku }}</span></div>
      <button class="product-name" type="button" data-action="product" :data-id="product.id" :data-focus-key="`view-${product.id}`" @click="$emit('open', product.id, $event)">{{ product.websiteTitle || product.name }}</button>
      <p class="product-stock" :class="{ unavailable: !product.stock }"><span></span>{{ product.stock ? `В наличии · ${product.stock} шт.` : 'Нет в наличии' }}</p>
      <small v-if="product.stock" class="product-delivery">{{ product.deliveryDays ? `${demo ? 'Срок в демо' : 'Ориентировочно'} · ${product.deliveryDays} дн.` : 'Срок получения уточняется' }}</small>
      <div class="product-bottom">
        <strong>{{ price }}</strong>
        <button type="button" class="btn add-button" :class="{ added: quantity }" data-action="add" :data-id="product.id" :data-focus-key="`add-${product.id}`" :disabled="!product.stock || quantity >= product.stock" @click="$emit('add', product.id)">
          <AppIcon :name="quantity ? 'check' : 'plus'" /> {{ quantity ? `${quantity} в корзине` : 'В корзину' }}
        </button>
      </div>
    </div>
  </article>
</template>

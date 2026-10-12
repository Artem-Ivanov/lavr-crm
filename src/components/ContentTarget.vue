<script setup>
const props = defineProps({
  modelValue: { type: Object, required: true },
  categories: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  prefix: { type: String, required: true },
});
const emit = defineEmits(['update:modelValue']);
function changeType(type) {
  const target = { type };
  if (type === 'category') target.categoryId = '';
  if (type === 'product') target.productId = '';
  if (type === 'section') target.section = 'offers';
  emit('update:modelValue', target);
}
const update = (key, value) => emit('update:modelValue', { ...props.modelValue, [key]: value });
</script>

<template>
  <div class="target-fields">
    <label :for="`${prefix}-target-type`">Куда ведёт ссылка
      <select :id="`${prefix}-target-type`" :value="modelValue.type" :disabled="disabled" :data-testid="`${prefix}-target-type`" @change="changeType($event.target.value)">
        <option value="catalog">Весь каталог</option>
        <option value="category">Категория</option>
        <option value="product">Товар</option>
        <option value="section">Раздел главной</option>
      </select>
    </label>
    <label v-if="modelValue.type === 'category'" :for="`${prefix}-target-category`">Категория
      <select :id="`${prefix}-target-category`" :value="modelValue.categoryId" required :disabled="disabled" :data-testid="`${prefix}-target-category`" @change="update('categoryId', $event.target.value)">
        <option value="" disabled>Выберите категорию</option>
        <option v-if="modelValue.categoryId && !categories.some(item => item.id === modelValue.categoryId)" :value="modelValue.categoryId">{{ modelValue.categoryId }} (недоступна)</option>
        <option v-for="category in categories" :key="category.id" :value="category.id">{{ category.name }}{{ category.isVisible === false ? ' (скрыта)' : '' }}</option>
      </select>
    </label>
    <label v-else-if="modelValue.type === 'product'" :for="`${prefix}-target-product`">ID товара
      <input :id="`${prefix}-target-product`" :value="modelValue.productId" required maxlength="128" :disabled="disabled" :data-testid="`${prefix}-target-product`" placeholder="Внутренний ID из номенклатуры" @input="update('productId', $event.target.value)">
    </label>
    <label v-else-if="modelValue.type === 'section'" :for="`${prefix}-target-section`">Раздел
      <select :id="`${prefix}-target-section`" :value="modelValue.section" :disabled="disabled" :data-testid="`${prefix}-target-section`" @change="update('section', $event.target.value)">
        <option value="offers">Акции</option><option value="announcements">Объявления</option><option value="hot">Горячие предложения</option>
      </select>
    </label>
  </div>
</template>

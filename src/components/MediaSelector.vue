<script setup>
const props = defineProps({
  modelValue: { type: [String, Array], default: null },
  media: { type: Array, default: () => [] },
  multiple: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  prefix: { type: String, required: true },
});
const emit = defineEmits(['update:modelValue']);
const selected = id => props.multiple ? props.modelValue.includes(id) : props.modelValue === id;
const label = id => props.media.find(item => item.id === id)?.alt || `Изображение ${id}`;
function toggle(id) {
  if (!props.multiple) { emit('update:modelValue', selected(id) ? null : id); return; }
  if (selected(id)) emit('update:modelValue', props.modelValue.filter(value => value !== id));
  else if (props.modelValue.length < 8) emit('update:modelValue', [...props.modelValue, id]);
}
</script>

<template>
  <div class="media-selector">
    <div v-if="multiple ? modelValue.length : modelValue" class="selected-media">
      <span>Выбрано{{ multiple ? ` ${modelValue.length} из 8` : '' }}:</span>
      <button v-for="id in multiple ? modelValue : [modelValue]" :key="id" type="button" class="btn outline selected-media-chip" :disabled="disabled" :aria-label="`Убрать ${label(id)}`" @click="toggle(id)">{{ label(id) }} · убрать</button>
    </div>
    <p v-else class="content-help">Изображение не выбрано.</p>
    <div v-if="media.length" class="media-choice-grid">
      <button v-for="item in media" :key="item.id" type="button" class="media-choice" :class="{ selected: selected(item.id) }" :aria-pressed="selected(item.id)" :disabled="disabled || (multiple && modelValue.length >= 8 && !selected(item.id))" :data-testid="`${prefix}-media-${item.id}`" @click="toggle(item.id)">
        <img :src="item.path" :alt="item.alt" loading="lazy"><span>{{ item.alt || item.id }}</span><small>{{ selected(item.id) ? 'Выбрано' : 'Выбрать' }}</small>
      </button>
    </div>
    <p v-else class="content-help">На этой странице нет изображений. Загрузите файл во вкладке «Изображения».</p>
  </div>
</template>

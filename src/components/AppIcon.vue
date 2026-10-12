<script setup>
defineProps({ name: { type: String, default: 'box' } });
const icons = {
  search: [{ tag: 'circle', attrs: { 'cx': '10.5', 'cy': '10.5', 'r': '6.5' } }, { tag: 'path', attrs: { 'd': 'm16 16 4 4' } }],
  cart: [{ tag: 'path', attrs: { 'd': 'M3 3h2l2.5 12h11L21 7H6' } }, { tag: 'circle', attrs: { 'cx': '9', 'cy': '20', 'r': '1' } }, { tag: 'circle', attrs: { 'cx': '18', 'cy': '20', 'r': '1' } }],
  arrow: [{ tag: 'path', attrs: { 'd': 'M4 12h15m-6-6 6 6-6 6' } }],
  down: [{ tag: 'path', attrs: { 'd': 'm6 9 6 6 6-6' } }],
  pin: [{ tag: 'path', attrs: { 'd': 'M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0Z' } }, { tag: 'circle', attrs: { 'cx': '12', 'cy': '10', 'r': '2' } }],
  box: [{ tag: 'path', attrs: { 'd': 'm3 7 9-4 9 4-9 4-9-4Zm0 0v10l9 4 9-4V7M12 11v10M7.5 5l9 4' } }],
  truck: [{ tag: 'path', attrs: { 'd': 'M3 5h11v12H3zM14 9h4l3 4v4h-7' } }, { tag: 'circle', attrs: { 'cx': '7', 'cy': '18', 'r': '2' } }, { tag: 'circle', attrs: { 'cx': '18', 'cy': '18', 'r': '2' } }],
  shield: [{ tag: 'path', attrs: { 'd': 'm12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z' } }, { tag: 'path', attrs: { 'd': 'm8 12 3 3 5-6' } }],
  grid: [{ tag: 'rect', attrs: { 'x': '3', 'y': '3', 'width': '7', 'height': '7', 'rx': '1' } }, { tag: 'rect', attrs: { 'x': '14', 'y': '3', 'width': '7', 'height': '7', 'rx': '1' } }, { tag: 'rect', attrs: { 'x': '3', 'y': '14', 'width': '7', 'height': '7', 'rx': '1' } }, { tag: 'rect', attrs: { 'x': '14', 'y': '14', 'width': '7', 'height': '7', 'rx': '1' } }],
  user: [{ tag: 'circle', attrs: { 'cx': '12', 'cy': '8', 'r': '4' } }, { tag: 'path', attrs: { 'd': 'M4 21v-2a8 8 0 0 1 16 0v2' } }],
  plus: [{ tag: 'path', attrs: { 'd': 'M12 5v14M5 12h14' } }],
  minus: [{ tag: 'path', attrs: { 'd': 'M5 12h14' } }],
  close: [{ tag: 'path', attrs: { 'd': 'm6 6 12 12M6 18 18 6' } }],
  check: [{ tag: 'path', attrs: { 'd': 'm5 12 4 4L19 6' } }],
  clock: [{ tag: 'circle', attrs: { 'cx': '12', 'cy': '12', 'r': '9' } }, { tag: 'path', attrs: { 'd': 'M12 7v5l3 2' } }],
  link: [{ tag: 'path', attrs: { 'd': 'm10 13 4-4m-6 7-2 2a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m4 0 2-2a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0' } }],
  oil: [{ tag: 'path', attrs: { 'd': 'M8 3h7v4l4 3v10H5V8h3V3Z' } }, { tag: 'path', attrs: { 'd': 'M10 3v4h5m-5 6h4m-4 3h4' } }],
  filter: [{ tag: 'path', attrs: { 'd': 'M5 6h14v12H5zM8 6v12m4-12v12m4-12v12M3 6h18M3 18h18' } }],
  brake: [{ tag: 'circle', attrs: { 'cx': '12', 'cy': '12', 'r': '9' } }, { tag: 'circle', attrs: { 'cx': '12', 'cy': '12', 'r': '3' } }, { tag: 'path', attrs: { 'd': 'M12 5v2m5 5h2m-7 5v2M5 12h2m-1-6 2 2m8 8 2 2m0-12-2 2M8 16l-2 2' } }],
  spark: [{ tag: 'path', attrs: { 'd': 'M10 2h4v6h-4zm-2 6h8v8H8zM10 16v5m4-5v4h3M8 11h8m-8 3h8' } }],
  spring: [{ tag: 'path', attrs: { 'd': 'M8 3h8M7 6l10 3-10 3 10 3-10 3M8 21h8' } }],
  belt: [{ tag: 'path', attrs: { 'd': 'M7 4a4 4 0 0 1 4 4v8a4 4 0 0 0 8 0V8a4 4 0 0 0-4-4H7Zm0 0a4 4 0 0 0-4 4v8a4 4 0 0 0 4 4h8' } }, { tag: 'circle', attrs: { 'cx': '7', 'cy': '8', 'r': '1' } }, { tag: 'circle', attrs: { 'cx': '15', 'cy': '16', 'r': '1' } }],
};
</script>

<template>
  <svg class="icon" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <component :is="shape.tag" v-for="(shape, index) in icons[name] || icons.box" :key="index" v-bind="shape.attrs" />
  </svg>
</template>

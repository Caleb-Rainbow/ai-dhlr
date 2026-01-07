<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue';

const props = defineProps<{
  data: number[];
  height?: number;
  color?: string;
  strokeWidth?: number;
}>();

const container = ref<HTMLElement | null>(null);
const containerWidth = ref(100);
const height = props.height || 40;
const strokeWidth = props.strokeWidth || 2;
const color = props.color || 'currentColor';

let resizeObserver: ResizeObserver | null = null;

onMounted(() => {
  if (container.value) {
    containerWidth.value = container.value.clientWidth;
    resizeObserver = new ResizeObserver((entries) => {
      if (entries[0]) {
        containerWidth.value = entries[0].contentRect.width;
      }
    });
    resizeObserver.observe(container.value);
  }
});

onUnmounted(() => {
  resizeObserver?.disconnect();
});

const points = computed(() => {
  if (!props.data.length) return '';

  // 获取当前数据范围以放大波动感
  const max = Math.max(...props.data);
  const min = Math.min(...props.data);
  const range = (max - min) || 1;
  const stepX = containerWidth.value / (props.data.length - 1 || 1);

  return props.data
    .map((val, index) => {
      const x = index * stepX;
      // 增加上下留白以防线条贴边
      const padding = strokeWidth * 1.5;
      const effectiveHeight = height - padding * 2;
      const y = padding + (effectiveHeight - ((val - min) / range) * effectiveHeight); 
      return `${x},${y}`;
    })
    .join(' ');
});
</script>

<template>
  <div ref="container" class="w-full">
    <svg :width="containerWidth" :height="height" class="overflow-visible block">
      <polyline
        fill="none"
        :stroke="color"
        :stroke-width="strokeWidth"
        stroke-linecap="round"
        stroke-linejoin="round"
        :points="points"
        class="transition-all duration-300"
      />
    </svg>
  </div>
</template>

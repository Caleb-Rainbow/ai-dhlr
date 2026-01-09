<script setup lang="ts">
import { useRoute } from 'vue-router';
import { onErrorCaptured } from 'vue';

const route = useRoute();

// 全局错误捕获 - 当发生严重错误时刷新页面
onErrorCaptured((err) => {
  console.error('App caught error:', err);
  // 如果是 DOM 相关的错误（如 parentNode 为 null），刷新页面
  if (err.message?.includes('parentNode') || err.message?.includes('Cannot read properties of null')) {
    console.warn('Detected stale cache error, reloading page...');
    window.location.reload();
    return false; // 阻止错误继续传播
  }
  return true;
});
</script>

<template>
  <router-view :key="route.fullPath" />
</template>

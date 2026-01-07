<script setup lang="ts">
/**
 * 骨架屏组件 - 用于数据加载时显示占位动画
 * 支持多种形状和尺寸配置
 */
defineProps<{
  variant?: 'card' | 'line' | 'circle' | 'text';
  lines?: number;  // 文本行数
  height?: string;
  width?: string;
}>();
</script>

<template>
  <!-- 卡片骨架 -->
  <div v-if="variant === 'card' || !variant" 
       class="skeleton-card animate-skeleton">
    <div class="skeleton-avatar"></div>
    <div class="skeleton-content">
      <div class="skeleton-line skeleton-title"></div>
      <div class="skeleton-line skeleton-subtitle"></div>
    </div>
    <div class="skeleton-actions">
      <div class="skeleton-button"></div>
      <div class="skeleton-button"></div>
    </div>
  </div>

  <!-- 单行骨架 -->
  <div v-else-if="variant === 'line'" 
       class="skeleton-line animate-skeleton"
       :style="{ height: height || '16px', width: width || '100%' }">
  </div>

  <!-- 圆形骨架 -->
  <div v-else-if="variant === 'circle'"
       class="skeleton-circle animate-skeleton"
       :style="{ width: width || '48px', height: width || '48px' }">
  </div>

  <!-- 多行文本骨架 -->
  <div v-else-if="variant === 'text'" class="skeleton-text-block">
    <div v-for="i in (lines || 3)" :key="i" 
         class="skeleton-line animate-skeleton"
         :style="{ width: i === (lines || 3) ? '60%' : '100%' }">
    </div>
  </div>
</template>

<style scoped>
/* 骨架屏基础动画 */
@keyframes skeleton-shimmer {
  0% {
    background-position: -200% 0;
  }
  100% {
    background-position: 200% 0;
  }
}

.animate-skeleton {
  background: linear-gradient(
    90deg,
    var(--theme-bg-input) 25%,
    rgba(255, 255, 255, 0.08) 50%,
    var(--theme-bg-input) 75%
  );
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.5s ease-in-out infinite;
}

/* 卡片骨架布局 */
.skeleton-card {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 1.25rem;
  border-radius: 1.5rem;
  background: var(--theme-glass-bg);
  border: 1px solid var(--theme-glass-border);
}

.skeleton-avatar {
  width: 48px;
  height: 48px;
  border-radius: 1rem;
  background: linear-gradient(
    90deg,
    var(--theme-bg-input) 25%,
    rgba(255, 255, 255, 0.08) 50%,
    var(--theme-bg-input) 75%
  );
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.5s ease-in-out infinite;
  flex-shrink: 0;
}

.skeleton-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.skeleton-line {
  height: 12px;
  border-radius: 6px;
}

.skeleton-title {
  width: 60%;
  height: 16px;
}

.skeleton-subtitle {
  width: 40%;
  height: 12px;
}

.skeleton-actions {
  display: flex;
  gap: 0.5rem;
  flex-shrink: 0;
}

.skeleton-button {
  width: 40px;
  height: 40px;
  border-radius: 0.75rem;
  background: linear-gradient(
    90deg,
    var(--theme-bg-input) 25%,
    rgba(255, 255, 255, 0.08) 50%,
    var(--theme-bg-input) 75%
  );
  background-size: 200% 100%;
  animation: skeleton-shimmer 1.5s ease-in-out infinite;
}

.skeleton-circle {
  border-radius: 50%;
}

.skeleton-text-block {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.skeleton-text-block .skeleton-line {
  height: 14px;
}
</style>

<script setup lang="ts">
import { computed } from 'vue';
import { RouterView, useRoute } from 'vue-router';
import {
  BarChart2,
  Camera,
  CookingPot,
  ClipboardList,
  Settings,
  SearchCheck
} from 'lucide-vue-next';

const route = useRoute();

// 根据当前路由动态生成导航路径
const basePath = computed(() => {
  if (route.path.startsWith('/device/')) {
    // 服务器模式: /device/:deviceId
    const match = route.path.match(/^\/device\/[^/]+/);
    return match ? match[0] : '';
  }
  // 设备模式
  return '/local';
});

// 在远程模式下保留 server 参数
const serverParam = computed(() => {
  const server = route.query.server;
  return server ? `?server=${encodeURIComponent(server as string)}` : '';
});

const navItems = computed(() => [
  { name: 'dashboard', label: '监控', path: `${basePath.value}/dashboard${serverParam.value}`, icon: BarChart2 },
  { name: 'cameras', label: '摄像头', path: `${basePath.value}/cameras${serverParam.value}`, icon: Camera },
  { name: 'zones', label: '灶台', path: `${basePath.value}/zones${serverParam.value}`, icon: CookingPot },
  { name: 'patrol', label: '巡检', path: `${basePath.value}/patrol${serverParam.value}`, icon: SearchCheck },
  { name: 'logs', label: '日志', path: `${basePath.value}/logs${serverParam.value}`, icon: ClipboardList },
  { name: 'settings', label: '设置', path: `${basePath.value}/settings${serverParam.value}`, icon: Settings },
]);
</script>

<template>
  <div class="flex flex-col h-full max-w-md mx-auto relative min-h-screen shadow-2xl" style="background: var(--theme-bg-primary);">
    <!-- Main Content -->
    <main class="flex-1 overflow-y-auto pt-0 pb-20 px-4 scroll-smooth" style="-webkit-overflow-scrolling: touch;">
      <RouterView v-slot="{ Component }">
        <transition name="fade" mode="out-in">
          <component :is="Component" />
        </transition>
      </RouterView>
    </main>
    
    <!-- Portal for FABs to avoid transition issues -->
    <div id="portal-target" class="fixed inset-0 pointer-events-none z-50 max-w-md mx-auto"></div>

    <!-- Bottom Nav -->
    <nav class="h-16 backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] flex items-center justify-around fixed bottom-0 w-full max-w-md z-40 border-t border-white/5 pb-[env(safe-area-inset-bottom)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all">
      <RouterLink
        v-for="item in navItems"
        :key="item.name"
        :to="item.path"
        replace
        class="flex flex-col items-center gap-1.5 px-4 py-2 text-text-muted transition-all rounded-xl active:scale-90"
        exact-active-class="!text-text-primary"
      >
        <component :is="item.icon" class="w-5 h-5 transition-transform duration-300" />
        <span class="text-[10px] font-medium tracking-wide">{{ item.label }}</span>
      </RouterLink>
    </nav>
  </div>
</template>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}

.fade-enter-from {
  opacity: 0;
  transform: translateY(10px);
}

.fade-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}
</style>

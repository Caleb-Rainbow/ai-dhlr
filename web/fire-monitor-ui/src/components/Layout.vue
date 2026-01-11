<script setup lang="ts">
import { RouterView } from 'vue-router';
import { 
  BarChart2, 
  Camera, 
  CookingPot, 
  ClipboardList, 
  Settings,
  SearchCheck
} from 'lucide-vue-next';



const navItems = [
  { name: 'dashboard', label: '监控', path: '/', icon: BarChart2 },
  { name: 'cameras', label: '摄像头', path: '/cameras', icon: Camera },
  { name: 'zones', label: '灶台', path: '/zones', icon: CookingPot },
  { name: 'patrol', label: '巡检', path: '/patrol', icon: SearchCheck },
  { name: 'logs', label: '日志', path: '/logs', icon: ClipboardList },
  { name: 'settings', label: '设置', path: '/settings', icon: Settings },
];
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
        :active-class="item.path === '/' ? '' : '!text-text-primary'"
        :exact-active-class="item.path === '/' ? '!text-text-primary' : ''"
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

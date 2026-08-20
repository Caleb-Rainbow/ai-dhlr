<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted } from 'vue';
import { RouterView, useRoute } from 'vue-router';
import { ws } from '../api/ws';
import {
  BarChart2,
  Camera,
  CookingPot,
  ClipboardList,
  Settings,
  SearchCheck,
  SquareTerminal,
  Flame
} from 'lucide-vue-next';

const route = useRoute();
const appVersion = __APP_VERSION__;

const deviceLabel = computed(() => {
  const deviceId = route.params.deviceId;
  return deviceId ? `远程设备 · ${deviceId}` : '本地设备';
});

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
  { name: 'terminal', label: '终端', path: `${basePath.value}/terminal${serverParam.value}`, icon: SquareTerminal },
  { name: 'settings', label: '设置', path: `${basePath.value}/settings${serverParam.value}`, icon: Settings },
]);

// ==================== 全局告警弹窗（alarm_event，含急停 estop） ====================
interface AlarmEventItem {
  id: number;
  alarm_type: string;
  message: string;
}
const alarmEvents = ref<AlarmEventItem[]>([]);
let alarmIdCounter = 0;

// 各告警类型的展示文案与停留时长(ms)；duration=0 表示不自动消失，需手动关闭
const alarmConfig: Record<string, { label: string; duration: number }> = {
  estop:      { label: '⚠️ 紧急急停：全部灶台已切断电源', duration: 0 },
  cutoff:     { label: '已切电',  duration: 8000 },
  alarm:      { label: '报警',    duration: 8000 },
  warning:    { label: '预警',    duration: 6000 },
  temp_alarm: { label: '温度报警', duration: 8000 },
};

function handleAlarmEvent(data: any) {
  const alarmType: string = data?.alarm_type || data?.alarmType || 'alarm';
  const cfg = alarmConfig[alarmType] || { label: '告警', duration: 6000 };
  const zoneName: string = data?.zone_name || data?.zoneName || '';
  const id = ++alarmIdCounter;
  // estop 自带完整文案；其他类型拼接 "类型·灶台名"
  const message = alarmType === 'estop'
    ? cfg.label
    : `${cfg.label}${zoneName ? '·' + zoneName : ''}`;
  alarmEvents.value.push({ id, alarm_type: alarmType, message });
  if (cfg.duration > 0) {
    setTimeout(() => dismissAlarm(id), cfg.duration);
  }
}
function dismissAlarm(id: number) {
  alarmEvents.value = alarmEvents.value.filter(a => a.id !== id);
}

let unsubAlarm: (() => void) | null = null;
onMounted(() => {
  unsubAlarm = ws.on('alarm_event', handleAlarmEvent);
});
onUnmounted(() => {
  unsubAlarm?.();
});
</script>

<template>
  <div class="flex h-[100dvh] min-h-screen w-full overflow-hidden" style="background: var(--theme-bg-primary);">
    <!-- Desktop Sidebar -->
    <aside
      class="hidden lg:flex w-64 shrink-0 flex-col border-r border-[var(--theme-glass-border)] bg-[var(--theme-glass-bg)] backdrop-blur-xl"
    >
      <div class="flex items-center gap-3 px-6 py-6 border-b border-[var(--theme-glass-border)]">
        <div class="w-11 h-11 rounded-2xl flex items-center justify-center bg-gradient-to-br from-orange-500/25 to-red-500/10 border border-orange-500/20">
          <Flame class="w-6 h-6 text-orange-500" />
        </div>
        <div class="min-w-0">
          <div class="font-bold text-text-primary tracking-tight">DHLR 安全监测</div>
          <div class="text-[10px] text-text-muted mt-0.5 truncate">{{ deviceLabel }}</div>
        </div>
      </div>

      <nav class="flex-1 px-3 py-5 space-y-1.5">
        <RouterLink
          v-for="item in navItems"
          :key="item.name"
          :to="item.path"
          replace
          class="group flex items-center gap-3 px-3.5 py-3 rounded-xl text-sm font-medium text-text-muted transition-all hover:text-text-primary hover:bg-[var(--theme-bg-input-hover)]"
          exact-active-class="!text-text-primary !bg-primary/15 ring-1 ring-primary/20 shadow-lg shadow-primary/5"
        >
          <component :is="item.icon" class="w-5 h-5 shrink-0 transition-transform group-hover:scale-110" />
          <span>{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="m-4 p-3.5 rounded-2xl border border-[var(--theme-border-input)] bg-[var(--theme-bg-input)]">
        <div class="flex items-center gap-2 text-xs text-text-secondary">
          <span class="relative flex h-2 w-2">
            <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-60"></span>
            <span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
          </span>
          系统运行中
        </div>
        <div class="text-[10px] text-text-muted mt-2">版本 v{{ appVersion }}</div>
      </div>
    </aside>

    <div class="relative flex min-w-0 flex-1 flex-col">
    <!-- 全局告警弹窗（急停 estop 红色脉冲，不自动消失） -->
    <div class="fixed inset-x-0 top-0 lg:left-64 z-[60] px-3 pt-3 pointer-events-none">
      <transition-group name="alarm" tag="div" class="w-full max-w-2xl mx-auto space-y-2">
        <div
          v-for="item in alarmEvents"
          :key="item.id"
          class="pointer-events-auto rounded-xl shadow-2xl px-4 py-3 flex items-center gap-3 text-sm font-semibold backdrop-blur"
          :class="item.alarm_type === 'estop'
            ? 'bg-red-600 text-white animate-pulse ring-2 ring-red-300'
            : (['cutoff','alarm'].includes(item.alarm_type)
                ? 'bg-red-500/95 text-white'
                : 'bg-orange-500/95 text-white')"
        >
          <span class="flex-1 leading-snug">{{ item.message }}</span>
          <button
            type="button"
            class="shrink-0 w-6 h-6 flex items-center justify-center rounded-full bg-white/20 hover:bg-white/30 transition"
            @click="dismissAlarm(item.id)"
            aria-label="关闭"
          >✕</button>
        </div>
      </transition-group>
    </div>

    <!-- Main Content -->
    <main class="flex-1 min-h-0 overflow-y-auto scroll-smooth px-4 pb-20 sm:px-6 lg:px-8 lg:pb-8 xl:px-10" style="-webkit-overflow-scrolling: touch;">
      <div class="mx-auto min-h-full w-full max-w-[1600px]">
        <RouterView v-slot="{ Component }">
          <transition name="fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </RouterView>
      </div>
    </main>
    
    <!-- Portal for FABs to avoid transition issues -->
    <div id="portal-target" class="fixed inset-0 lg:left-64 pointer-events-none z-50"></div>

    <!-- Mobile Bottom Nav -->
    <nav class="lg:hidden h-16 backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] flex items-center justify-around fixed inset-x-0 bottom-0 z-40 border-t border-white/5 pb-[env(safe-area-inset-bottom)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all">
      <RouterLink
        v-for="item in navItems"
        :key="item.name"
        :to="item.path"
        replace
        class="flex flex-col items-center gap-1.5 px-2.5 py-2 text-text-muted transition-all rounded-xl active:scale-90"
        exact-active-class="!text-text-primary"
      >
        <component :is="item.icon" class="w-5 h-5 transition-transform duration-300" />
        <span class="text-[10px] font-medium tracking-wide">{{ item.label }}</span>
      </RouterLink>
    </nav>
    </div>
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

.alarm-enter-active,
.alarm-leave-active {
  transition: all 0.3s ease;
}
.alarm-enter-from {
  opacity: 0;
  transform: translateY(-20px);
}
.alarm-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
</style>

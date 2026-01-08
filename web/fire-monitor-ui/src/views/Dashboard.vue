<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { RefreshCcw, Activity, Flame, User, Cpu, Zap } from 'lucide-vue-next';
import { ws } from '../api/ws';
import type { ZoneStatus, DeviceInfo, PerformanceStats } from '../types';
import Sparkline from '../components/Sparkline.vue';
import Skeleton from '../components/Skeleton.vue';

const deviceInfo = ref<DeviceInfo | null>(null);
const zones = ref<ZoneStatus[]>([]);
const performance = ref<PerformanceStats | null>(null);
const loading = ref(true);

// History Data for Sparklines
const fpsHistory = ref<number[]>([]);
const cpuHistory = ref<number[]>([]);
const npuHistory = ref<number[]>([]);
const historyLimit = 20;

let perfTimer: ReturnType<typeof setInterval> | null = null;
let unsubscribeStatus: (() => void) | null = null;

const enabledZones = computed(() => zones.value.filter(z => z.enabled !== false));

// Actions
const fetchDeviceInfo = async () => {
  try {
    deviceInfo.value = await ws.request<DeviceInfo>('get_device');
  } catch (e) { console.error(e); }
};

const refreshStatus = async () => {
  try {
    zones.value = await ws.request<ZoneStatus[]>('get_status');
  } catch (e) { console.error(e); } finally {
    loading.value = false;
  }
};

const refreshPerformance = async () => {
  try {
    const stats = await ws.request<PerformanceStats>('get_performance');
    performance.value = stats;
    
    // Update history
    fpsHistory.value.push(stats.fps);
    if(fpsHistory.value.length > historyLimit) fpsHistory.value.shift();
    
    cpuHistory.value.push(stats.cpu_percent);
    if(cpuHistory.value.length > historyLimit) cpuHistory.value.shift();

    npuHistory.value.push(stats.npu_percent || 0);
    if(npuHistory.value.length > historyLimit) npuHistory.value.shift();
    
  } catch (e) { console.error(e); }
};

// 注意: toggleFire、resetZone 和 resetAll 已移除，动火状态现在通过串口电流值判断

onMounted(async () => {
  // 连接 WebSocket
  await ws.connect();
  
  fetchDeviceInfo();
  refreshStatus();
  refreshPerformance();

  // 监听状态更新事件
  unsubscribeStatus = ws.on('status_update', (data: ZoneStatus[]) => {
    zones.value = data;
    loading.value = false;
  });

  perfTimer = setInterval(refreshPerformance, 1000);
});

onUnmounted(() => {
  if (unsubscribeStatus) unsubscribeStatus();
  if (perfTimer) clearInterval(perfTimer);
});
</script>

<template>
  <div class="space-y-6 pb-24">
    <!-- Header Controls -->
    <div class="sticky top-0 z-50 transition-all duration-500">
      <!-- Hyper-transparent glass with saturation boost -->
      <div class="backdrop-blur-2xl backdrop-saturate-150 bg-white/[0.01] border-b border-white/[0.05] shadow-xl shadow-black/10 px-4 py-4 -mx-4 flex items-center justify-between">
        <div>
          <h2 class="text-xl font-bold text-text-primary tracking-tight flex items-center gap-2">
            实时监控
            <span class="flex h-2 w-2">
              <span class="animate-ping absolute inline-flex h-2 w-2 rounded-full bg-emerald-400 opacity-75"></span>
              <span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
          </h2>
          <div class="text-[10px] text-text-muted mt-0.5 font-medium tracking-wider uppercase opacity-70">
            System Online / Live Data
          </div>
        </div>
        <div class="flex gap-2">
          <button @click="refreshStatus" class="glass-button p-2.5 rounded-xl text-text-secondary active:scale-90 transition-transform">
            <RefreshCcw class="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>

    <!-- Zone Cards - 统一使用 mode="out-in" 确保先消失再显示 -->
    <Transition name="fade" mode="out-in">
      <!-- Loading Skeleton -->
      <div v-if="loading" key="skeleton" class="grid grid-cols-2 gap-3">
        <Skeleton v-for="i in 4" :key="i" />
      </div>

      <!-- Empty State -->
      <div v-else-if="enabledZones.length === 0" key="empty" class="flex flex-col items-center justify-center p-12 glass-panel rounded-2xl">
        <div class="text-4xl mb-4 grayscale opacity-50">🍳</div>
        <p class="text-text-muted">暂无启用的灶台</p>
      </div>

      <!-- Zone Cards Grid with Animation -->
      <div v-else key="content" class="grid grid-cols-2 gap-3">
        <TransitionGroup name="card">
          <div v-for="(zone, index) in enabledZones" :key="zone.id" 
            class="glass-panel rounded-3xl p-4 relative overflow-hidden group transition-all duration-300 hover:border-white/10 hover-lift"
            :class="[
              { 'border-red-500/50 shadow-[0_0_20px_rgba(239,68,68,0.2)]': ['alarm', 'cutoff'].includes(zone.state) },
              { 'border-amber-500/50 shadow-[0_0_15px_rgba(245,158,11,0.15)]': zone.state === 'warning' },
            ]"
            :style="{ animationDelay: `${index * 0.05}s` }"
          >
            <!-- Background Glow for Alarm -->
            <Transition name="fade">
              <div v-if="['alarm', 'cutoff'].includes(zone.state)" class="absolute inset-0 bg-red-500/10 animate-pulse z-0 pointer-events-none"></div>
            </Transition>

            <div class="relative z-10">
              <!-- Card Header -->
              <div class="flex justify-between items-start mb-3">
                <div>
                  <h3 class="text-base font-bold text-text-primary tracking-wide">{{ zone.name }}</h3>
                  <div class="text-[9px] text-text-muted font-mono mt-0.5 opacity-60">{{ zone.id }}</div>
                </div>
                
                <div class="flex gap-2">
                   <!-- Status Icons with transition -->
                   <Transition name="pop" mode="out-in">
                     <div :key="zone.is_fire_on ? 'fire-on' : 'fire-off'" 
                       class="w-8 h-8 rounded-full flex items-center justify-center transition-colors duration-300 border border-white/5"
                       :class="zone.is_fire_on ? 'bg-orange-500/20 text-orange-500 shadow-inner shadow-orange-500/20' : 'bg-white/5 text-gray-600'">
                       <Flame class="w-4 h-4" :class="{ 'fill-current': zone.is_fire_on }" />
                     </div>
                   </Transition>
                   
                   <Transition name="pop" mode="out-in">
                     <div :key="zone.has_person ? 'person-on' : (zone.is_fire_on ? 'person-warning' : 'person-off')"
                       class="w-8 h-8 rounded-full flex items-center justify-center transition-colors duration-300 border border-white/5"
                       :class="zone.has_person ? 'bg-emerald-500/20 text-emerald-500 shadow-inner shadow-emerald-500/20' : (zone.is_fire_on ? 'bg-amber-500/20 text-amber-500 animate-pulse' : 'bg-white/5 text-gray-600')">
                       <User class="w-4 h-4" :class="{ 'fill-current': zone.has_person }" />
                     </div>
                   </Transition>
                </div>
              </div>

              <!-- Countdown / Status Text with smooth transitions -->
              <div class="mb-4 min-h-[2.5rem] flex items-center">
                 <Transition name="slide-fade" mode="out-in">
                   <div v-if="['active_no_person', 'warning', 'alarm'].includes(zone.state)" :key="zone.state + '-countdown'" class="flex items-baseline gap-1">
                      <span class="text-3xl font-bold font-mono tracking-tighter transition-colors duration-300"
                       :class="zone.state === 'alarm' ? 'text-red-500 text-glow' : 'text-amber-500'">
                        {{ Math.ceil(zone.state === 'active_no_person' ? zone.warning_remaining : (zone.state === 'warning' ? zone.alarm_remaining : zone.cutoff_remaining)) }}
                      </span>
                      <span class="text-[10px] text-text-muted font-bold uppercase tracking-wider">秒剩余</span>
                   </div>
                   <div v-else-if="zone.state === 'cutoff'" :key="'cutoff'" class="text-red-500 font-bold text-sm flex items-center gap-2">
                      <span class="w-2 h-2 rounded-full bg-red-500 animate-ping"></span>
                      已切断电源
                   </div>
                   <div v-else :key="zone.state" class="text-text-muted text-xs flex items-center gap-2">
                      <span class="w-1.5 h-1.5 rounded-full transition-colors duration-300" :class="zone.state === 'idle' ? 'bg-gray-600' : 'bg-emerald-500'"></span>
                      {{ zone.state === 'idle' ? '设备空闲' : '正常监护中' }}
                   </div>
                 </Transition>
              </div>

              <!-- Actions / Current Display -->
              <div class="flex items-center justify-between">
                <!-- 电流值显示 -->
                <div class="text-xs text-text-muted flex items-center gap-1">
                  <Zap class="w-3 h-3 text-amber-400" />
                  <span class="font-mono text-amber-400">
                    {{ zone.is_fire_on ? ((zone as any).current_value ? ((zone as any).current_value / 100).toFixed(2) : '?.??') : '0.00' }}A
                  </span>
                </div>
              </div>
            </div>
          </div>
        </TransitionGroup>
      </div>
    </Transition>

    <!-- Performance Panel with Animation -->
    <Transition name="fade" mode="out-in">
      <div v-if="performance" key="perf" class="glass-panel p-5 rounded-3xl space-y-4">
        <h3 class="flex items-center gap-2 text-xs font-bold text-text-muted uppercase tracking-wider">
          <Activity class="w-3.5 h-3.5" /> 性能监控
        </h3>
        
        <div class="grid grid-cols-2 gap-3">
           <!-- FPS Chart -->
           <div class="rounded-2xl p-3 border transition-all duration-300 hover-lift" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              <div class="flex justify-between items-end mb-2">
                 <span class="text-[10px] text-text-muted font-bold">FPS</span>
                 <span class="text-lg font-mono font-bold text-text-primary">{{ performance.fps.toFixed(0) }}</span>
              </div>
              <Sparkline :data="fpsHistory" :height="30" color="#10b981" />
           </div>

           <!-- CPU Chart -->
           <div class="rounded-2xl p-3 border transition-all duration-300 hover-lift" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
               <div class="flex justify-between items-end mb-2">
                 <span class="text-[10px] text-text-muted font-bold">CPU</span>
                 <span class="text-lg font-mono font-bold text-text-primary">{{ performance.cpu_percent.toFixed(0) }}%</span>
              </div>
              <Sparkline :data="cpuHistory" :height="30" color="#3b82f6" />
           </div>

           <!-- NPU Chart - Full Width Row 2 -->
            <div class="col-span-2 rounded-2xl p-3 border transition-all duration-300 hover-lift" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
               <div class="flex justify-between items-end mb-2">
                 <span class="text-[10px] text-purple-400 font-bold">NPU 核心占用</span>
                 <span class="text-lg font-mono font-bold text-text-primary">{{ (performance.npu_percent || 0).toFixed(0) }}%</span>
              </div>
              <Sparkline :data="npuHistory" :height="40" color="#a855f7" />
           </div>
           
           <!-- Stats Row -->
           <div class="col-span-2 grid grid-cols-3 gap-2">
              <div class="rounded-xl p-2 flex flex-col items-center justify-center transition-all duration-300 hover-lift" style="background: var(--theme-bg-input);">
                 <Zap class="w-3 h-3 text-yellow-500 mb-1" />
                 <span class="text-xs font-mono text-text-primary">{{ performance.inference_time_ms.toFixed(0) }}ms</span>
                 <span class="text-[8px] text-text-muted mt-0.5">推理延迟</span>
              </div>
              
              <div class="rounded-xl p-2 flex flex-col items-center justify-center transition-all duration-300 hover-lift" style="background: var(--theme-bg-input);">
                 <Cpu class="w-3 h-3 text-blue-500 mb-1" />
                 <span class="text-xs font-mono text-text-primary">{{ performance.memory_mb.toFixed(0) }}MB</span>
                 <span class="text-[8px] text-text-muted mt-0.5">内存使用</span>
              </div>

               <div class="rounded-xl p-2 flex flex-col items-center justify-center overflow-hidden transition-all duration-300 hover-lift" style="background: var(--theme-bg-input);">
                 <span class="text-[8px] text-text-muted uppercase mb-1">引擎</span>
                 <span class="text-xs font-bold text-text-primary truncate w-full text-center px-1" :title="performance.engine">{{ performance.engine }}</span>
              </div>
           </div>
        </div>
      </div>
    </Transition>
  </div>
</template>


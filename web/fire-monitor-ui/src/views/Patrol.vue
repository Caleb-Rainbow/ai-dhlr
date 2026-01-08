<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from 'vue';
import { 
  LogOut, SearchCheck, AlertTriangle, Bell, Zap, 
  Play, CheckCircle, XCircle, AlertCircle, Clock
} from 'lucide-vue-next';
import { ws } from '../api/ws';

interface PatrolResult {
  zone_id: string;
  zone_name: string;
  step: string;
  status: string;
  message: string;
  timestamp: number;
}

interface PatrolState {
  is_active: boolean;
  current_step: string;
  progress: number;
  message: string;
  results: PatrolResult[];
}

const patrolState = ref<PatrolState>({
  is_active: false,
  current_step: 'idle',
  progress: 0,
  message: '',
  results: []
});

const loading = ref<string | null>(null);
let unsubscribePatrol: (() => void) | null = null;

// 步骤名称映射
const stepNames: Record<string, string> = {
  'idle': '空闲',
  'self_check_person': '离人检测',
  'self_check_fire': '动火检测',
  'alarm_demo': '报警演示',
  'force_warning': '强制预警',
  'force_alarm': '强制报警',
  'force_cutoff': '强制切电'
};

// 状态图标
const statusIcons = {
  success: CheckCircle,
  warning: AlertCircle,
  error: XCircle
};

// 状态颜色
const statusColors: Record<string, string> = {
  success: 'text-emerald-500',
  warning: 'text-amber-500',
  error: 'text-red-500'
};

const isActive = computed(() => patrolState.value.is_active);
const isBusy = computed(() => patrolState.value.current_step !== 'idle');

// 获取巡检状态
const fetchStatus = async () => {
  try {
    const data = await ws.request<PatrolState>('get_patrol_status');
    patrolState.value = data;
  } catch (e) {
    console.error('获取巡检状态失败:', e);
  }
};

// 开始巡检
const startPatrol = async () => {
  loading.value = 'start';
  try {
    await ws.request('start_patrol');
    await fetchStatus();
  } catch (e) {
    console.error('开始巡检失败:', e);
  } finally {
    loading.value = null;
  }
};

// 退出巡检
const stopPatrol = async () => {
  loading.value = 'stop';
  try {
    await ws.request('stop_patrol');
    await fetchStatus();
  } catch (e) {
    console.error('退出巡检失败:', e);
  } finally {
    loading.value = null;
  }
};

// 设备自检
const selfCheck = async () => {
  loading.value = 'self_check';
  try {
    await ws.request('patrol_self_check');
  } catch (e) {
    console.error('设备自检失败:', e);
  } finally {
    loading.value = null;
  }
};

// 报警演示
const alarmDemo = async () => {
  loading.value = 'alarm_demo';
  try {
    await ws.request('patrol_alarm_demo');
  } catch (e) {
    console.error('报警演示失败:', e);
  } finally {
    loading.value = null;
  }
};

// 强制预警
const forceWarning = async () => {
  loading.value = 'force_warning';
  try {
    await ws.request('patrol_force_warning');
  } catch (e) {
    console.error('强制预警失败:', e);
  } finally {
    loading.value = null;
  }
};

// 强制报警
const forceAlarm = async () => {
  loading.value = 'force_alarm';
  try {
    await ws.request('patrol_force_alarm');
  } catch (e) {
    console.error('强制报警失败:', e);
  } finally {
    loading.value = null;
  }
};

// 强制切电
const forceCutoff = async () => {
  loading.value = 'force_cutoff';
  try {
    await ws.request('patrol_force_cutoff');
  } catch (e) {
    console.error('强制切电失败:', e);
  } finally {
    loading.value = null;
  }
};

// 格式化时间
const formatTime = (timestamp: number) => {
  const date = new Date(timestamp);
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
};

onMounted(async () => {
  await ws.connect();
  await fetchStatus();
  
  // 监听巡检事件
  unsubscribePatrol = ws.on('patrol_event', (data: any) => {
    patrolState.value = data;
  });
});

onUnmounted(() => {
  if (unsubscribePatrol) unsubscribePatrol();
});
</script>

<template>
  <div class="space-y-6 pb-24">
    <!-- Header -->
    <div class="sticky top-0 z-50 transition-all duration-500">
      <div class="backdrop-blur-2xl backdrop-saturate-150 bg-white/[0.01] border-b border-white/[0.05] shadow-xl shadow-black/10 px-4 py-4 -mx-4 flex items-center justify-between">
        <div>
          <h2 class="text-xl font-bold text-text-primary tracking-tight flex items-center gap-2">
            设备巡检
            <span v-if="isActive" class="flex h-2 w-2">
              <span class="animate-ping absolute inline-flex h-2 w-2 rounded-full bg-emerald-400 opacity-75"></span>
              <span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
          </h2>
          <div class="text-[10px] text-text-muted mt-0.5 font-medium tracking-wider uppercase opacity-70">
            Device Patrol / Inspection
          </div>
        </div>
        
        <!-- 巡检模式状态 -->
        <div class="flex items-center gap-2">
          <div v-if="isActive" 
            class="px-3 py-1.5 rounded-full text-xs font-bold bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
            巡检中
          </div>
          <div v-else 
            class="px-3 py-1.5 rounded-full text-xs font-bold bg-gray-500/20 text-gray-400 border border-gray-500/30">
            未启动
          </div>
        </div>
      </div>
    </div>

    <!-- 功能区 -->
    <div class="glass-panel rounded-3xl p-5 space-y-4">
      <h3 class="text-sm font-bold text-text-muted uppercase tracking-wider flex items-center gap-2">
        <SearchCheck class="w-4 h-4" /> 巡检功能
      </h3>

      <!-- 启动/退出巡检 -->
      <div class="flex gap-3">
        <button v-if="!isActive"
          @click="startPatrol"
          :disabled="loading !== null"
          class="flex-1 py-3 rounded-xl font-bold text-sm transition-all active:scale-95 bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/30 disabled:opacity-50">
          <Play class="w-4 h-4 inline-block mr-1" />
          开始巡检
        </button>
        <button v-else
          @click="stopPatrol"
          :disabled="loading !== null || isBusy"
          class="flex-1 py-3 rounded-xl font-bold text-sm transition-all active:scale-95 bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30 disabled:opacity-50">
          <LogOut class="w-4 h-4 inline-block mr-1" />
          退出巡检
        </button>
      </div>

      <!-- 功能按钮组 -->
      <div class="grid grid-cols-2 gap-3">
        <button
          @click="selfCheck"
          :disabled="!isActive || loading !== null || isBusy"
          class="py-4 rounded-xl font-bold text-sm transition-all active:scale-95 glass-button disabled:opacity-50 flex flex-col items-center gap-2">
          <SearchCheck class="w-5 h-5 text-blue-400" />
          <span>设备自检</span>
        </button>
        
        <button
          @click="alarmDemo"
          :disabled="!isActive || loading !== null || isBusy"
          class="py-4 rounded-xl font-bold text-sm transition-all active:scale-95 glass-button disabled:opacity-50 flex flex-col items-center gap-2">
          <Bell class="w-5 h-5 text-purple-400" />
          <span>报警演示</span>
        </button>
      </div>

      <!-- 强制动作组 -->
      <div class="pt-2 border-t border-white/5">
        <p class="text-[10px] text-text-muted uppercase tracking-wider mb-3">强制动作</p>
        <div class="grid grid-cols-3 gap-2">
          <button
            @click="forceWarning"
            :disabled="!isActive || loading !== null || isBusy"
            class="py-3 rounded-xl text-xs font-bold transition-all active:scale-95 bg-amber-500/20 text-amber-400 border border-amber-500/30 hover:bg-amber-500/30 disabled:opacity-50">
            <AlertCircle class="w-4 h-4 inline-block mr-1" />
            预警
          </button>
          
          <button
            @click="forceAlarm"
            :disabled="!isActive || loading !== null || isBusy"
            class="py-3 rounded-xl text-xs font-bold transition-all active:scale-95 bg-orange-500/20 text-orange-400 border border-orange-500/30 hover:bg-orange-500/30 disabled:opacity-50">
            <AlertTriangle class="w-4 h-4 inline-block mr-1" />
            报警
          </button>
          
          <button
            @click="forceCutoff"
            :disabled="!isActive || loading !== null || isBusy"
            class="py-3 rounded-xl text-xs font-bold transition-all active:scale-95 bg-red-500/20 text-red-400 border border-red-500/30 hover:bg-red-500/30 disabled:opacity-50">
            <Zap class="w-4 h-4 inline-block mr-1" />
            切电
          </button>
        </div>
      </div>
    </div>

    <!-- 进度显示 -->
    <Transition name="fade" mode="out-in">
      <div v-if="isActive && isBusy" class="glass-panel rounded-3xl p-5 space-y-4">
        <div class="flex items-center justify-between">
          <h3 class="text-sm font-bold text-text-muted uppercase tracking-wider flex items-center gap-2">
            <Clock class="w-4 h-4 animate-spin" /> 执行中
          </h3>
          <span class="text-xs text-text-muted">{{ patrolState.progress }}%</span>
        </div>
        
        <!-- 进度条 -->
        <div class="h-2 bg-white/5 rounded-full overflow-hidden">
          <div 
            class="h-full bg-gradient-to-r from-emerald-500 to-teal-500 transition-all duration-300 rounded-full"
            :style="{ width: `${patrolState.progress}%` }">
          </div>
        </div>
        
        <div class="text-sm text-text-secondary">
          <span class="text-emerald-400 font-bold">{{ stepNames[patrolState.current_step] || patrolState.current_step }}</span>
          <span class="text-text-muted ml-2">{{ patrolState.message }}</span>
        </div>
      </div>
    </Transition>

    <!-- 结果区域 -->
    <div class="glass-panel rounded-3xl p-5 space-y-4">
      <h3 class="text-sm font-bold text-text-muted uppercase tracking-wider flex items-center gap-2">
        <CheckCircle class="w-4 h-4" /> 巡检结果
      </h3>

      <Transition name="fade" mode="out-in">
        <div v-if="patrolState.results.length === 0" class="text-center py-8 text-text-muted text-sm">
          暂无巡检记录
        </div>
        
        <div v-else class="space-y-2 max-h-80 overflow-y-auto pr-1">
          <TransitionGroup name="slide-fade">
            <div v-for="(result, index) in [...patrolState.results].reverse()" 
              :key="result.timestamp + '-' + index"
              class="p-3 rounded-xl border transition-all"
              :class="{
                'bg-emerald-500/10 border-emerald-500/20': result.status === 'success',
                'bg-amber-500/10 border-amber-500/20': result.status === 'warning',
                'bg-red-500/10 border-red-500/20': result.status === 'error'
              }">
              <div class="flex items-start gap-3">
                <component 
                  :is="statusIcons[result.status as keyof typeof statusIcons] || AlertCircle"
                  class="w-4 h-4 mt-0.5 flex-shrink-0"
                  :class="statusColors[result.status] || 'text-gray-400'" />
                <div class="flex-1 min-w-0">
                  <div class="flex items-center justify-between gap-2">
                    <span class="text-xs font-bold text-text-primary truncate">
                      {{ result.zone_name || result.step }}
                    </span>
                    <span class="text-[10px] text-text-muted flex-shrink-0">
                      {{ formatTime(result.timestamp) }}
                    </span>
                  </div>
                  <p class="text-xs text-text-secondary mt-0.5">{{ result.message }}</p>
                </div>
              </div>
            </div>
          </TransitionGroup>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.slide-fade-enter-active,
.slide-fade-leave-active {
  transition: all 0.3s ease;
}

.slide-fade-enter-from {
  opacity: 0;
  transform: translateY(-10px);
}

.slide-fade-leave-to {
  opacity: 0;
  transform: translateY(10px);
}
</style>

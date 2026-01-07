<script setup lang="ts">
import { ref, onMounted, computed, onUnmounted } from 'vue';
import { ws } from '../api/ws';
import type { DeviceInfo, AlarmSettings, NetworkStatus, RemoteServerConfig } from '../types';
import { Save, Info, Volume2, ShieldAlert, Sun, Moon, Palette, Loader, Wifi, Globe, Server, CheckCircle, XCircle, RefreshCw, Eye, EyeOff } from 'lucide-vue-next';
import { useTheme } from '../composables/useTheme';

const deviceInfo = ref<DeviceInfo | null>(null);
const alarmSettings = ref<AlarmSettings>({
    warning_time: 5,
    alarm_time: 10,
    action_time: 15,
    broadcast_interval: 10,
    warning_message: "请注意",
    alarm_message: "警告",
    action_message: "动作"
});

// 网络状态
const networkStatus = ref<NetworkStatus>({
    interface_type: 'unknown',
    interface_name: '',
    ip_address: '',
    signal_strength: -1,
    gateway: '',
    is_connected: false
});

// 远程服务器配置
const remoteConfig = ref<RemoteServerConfig>({
    enabled: false,
    server_url: '',
    websocket_path: 'dhlr/socket',
    login_path: '/login',
    username: '',
    has_token: false,
    is_connected: false,
    is_connecting: false,
    last_error: '',
    reconnect_attempts: 0
});

// 编辑用的本地状态
const remoteForm = ref({
    server_url: '',
    websocket_path: 'dhlr/socket',
    login_path: '/login',
    username: '',
    password: '',
    enabled: false
});

const saving = ref(false);
const loading = ref(true);
const verifying = ref(false);
const verifyResult = ref<{ success: boolean; message: string } | null>(null);
const showPassword = ref(false);
const saveSuccess = ref(false);
const saveError = ref('');

// Theme management
const { theme, toggleTheme } = useTheme();
const isDarkMode = computed(() => theme.value === 'dark');

// 网络图标计算
const networkIcon = computed(() => {
    if (networkStatus.value.interface_type === 'wifi') return Wifi;
    if (networkStatus.value.interface_type === 'ethernet') return Globe;
    return Globe;
});

const networkTypeLabel = computed(() => {
    const type = networkStatus.value.interface_type;
    if (type === 'wifi') return 'WiFi';
    if (type === 'ethernet') return '以太网';
    return '未知';
});

const loadData = async () => {
    loading.value = true;
    try {
        const [dev, settings, network, remote] = await Promise.all([
            ws.request<DeviceInfo>('get_device'),
            ws.request<{ alarm?: AlarmSettings; system?: DeviceInfo }>('get_settings', { category: 'all' }),
            ws.request<NetworkStatus>('get_network').catch(() => networkStatus.value),
            ws.request<RemoteServerConfig>('get_remote_config').catch(() => remoteConfig.value)
        ]);
        deviceInfo.value = dev;
        if (settings.alarm) {
            alarmSettings.value = settings.alarm;
        }
        networkStatus.value = network;
        remoteConfig.value = remote;
        
        // 初始化表单
        remoteForm.value = {
            server_url: remote.server_url || '',
            websocket_path: remote.websocket_path || 'dhlr/socket',
            login_path: remote.login_path || '/login',
            username: remote.username || '',
            password: '',
            enabled: remote.enabled || false
        };
    } catch(e) { console.error(e); }
    finally {
        loading.value = false;
    }
};

// 刷新网络状态
const refreshNetwork = async () => {
    try {
        networkStatus.value = await ws.request<NetworkStatus>('get_network');
    } catch (e) {
        console.error('刷新网络状态失败', e);
    }
};

// 校验远程登录
const verifyRemoteLogin = async () => {
    if (!remoteForm.value.server_url || !remoteForm.value.username || !remoteForm.value.password) {
        verifyResult.value = { success: false, message: '请填写完整的服务器地址、用户名和密码' };
        return;
    }
    
    verifying.value = true;
    verifyResult.value = null;
    
    try {
        const result = await ws.request<{ success: boolean; message: string }>('verify_remote_login', {
            server_url: remoteForm.value.server_url,
            login_path: remoteForm.value.login_path,
            username: remoteForm.value.username,
            password: remoteForm.value.password
        });
        verifyResult.value = result;
        
        if (result.success) {
            // 更新远程配置状态
            remoteConfig.value.has_token = true;
        }
    } catch (e: any) {
        verifyResult.value = { success: false, message: e.message || '校验失败' };
    } finally {
        verifying.value = false;
    }
};

// 保存远程配置
const saveRemoteConfig = async () => {
    try {
        await ws.request('update_remote_config', {
            enabled: remoteForm.value.enabled,
            server_url: remoteForm.value.server_url,
            websocket_path: remoteForm.value.websocket_path,
            login_path: remoteForm.value.login_path,
            username: remoteForm.value.username,
            password: remoteForm.value.password || undefined
        });
        // 重新加载远程状态
        remoteConfig.value = await ws.request<RemoteServerConfig>('get_remote_config');
    } catch (e: any) {
        alert('保存远程配置失败: ' + (e.message || e));
    }
};

const saveSettings = async () => {
    saving.value = true;
    saveSuccess.value = false;
    saveError.value = '';
    try {
        await ws.request('update_settings', { category: 'alarm', settings: alarmSettings.value });
        await saveRemoteConfig();
        saveSuccess.value = true;
        // 3秒后自动隐藏成功提示
        setTimeout(() => {
            saveSuccess.value = false;
        }, 3000);
    } catch(e: any) {
        saveError.value = e.message || '保存失败';
        // 5秒后自动隐藏错误提示
        setTimeout(() => {
            saveError.value = '';
        }, 5000);
    }
    finally { saving.value = false; }
};

// 定时刷新网络和远程状态
let refreshInterval: number | null = null;
onMounted(async () => {
    await ws.connect();
    loadData();
    refreshInterval = window.setInterval(async () => {
        try {
            networkStatus.value = await ws.request<NetworkStatus>('get_network');
            const remote = await ws.request<RemoteServerConfig>('get_remote_config');
            // 只更新连接状态，不覆盖表单
            remoteConfig.value.is_connected = remote.is_connected;
            remoteConfig.value.is_connecting = remote.is_connecting;
            remoteConfig.value.last_error = remote.last_error;
            remoteConfig.value.reconnect_attempts = remote.reconnect_attempts;
        } catch (e) { /* ignore */ }
    }, 5000);
});

onUnmounted(() => {
    if (refreshInterval) {
        clearInterval(refreshInterval);
    }
});
</script>

<template>
  <div class="space-y-6 pb-24 pt-6">
    <div class="flex items-center justify-between">
      <h2 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-text-primary to-text-secondary">系统设置</h2>
    </div>

    <!-- Network Status - 网络状态 -->
    <div class="glass-panel p-5 rounded-3xl space-y-4 animate-fade-in-up">
       <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
           <component :is="networkIcon" class="w-4 h-4" /> 网络状态
           <button @click="refreshNetwork" class="ml-auto p-1 rounded-lg hover:bg-white/10 transition-colors">
             <RefreshCw class="w-4 h-4 text-text-muted" />
           </button>
       </h3>
       <div class="grid grid-cols-2 gap-4">
           <div class="p-3 rounded-2xl border transition-all duration-300 hover-lift" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
               <div class="text-xs text-text-muted mb-1">网络类型</div>
               <div class="flex items-center gap-2">
                   <component :is="networkIcon" class="w-5 h-5" :class="networkStatus.is_connected ? 'text-success' : 'text-text-muted'" />
                   <span class="font-medium text-text-primary">{{ networkTypeLabel }}</span>
               </div>
           </div>
           <div class="p-3 rounded-2xl border transition-all duration-300 hover-lift" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
               <div class="text-xs text-text-muted mb-1">IP 地址</div>
               <div class="font-mono text-text-primary">{{ networkStatus.ip_address || '未连接' }}</div>
           </div>
           <div v-if="networkStatus.interface_type === 'wifi'" class="p-3 rounded-2xl border col-span-2 transition-all duration-300 hover-lift" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
               <div class="text-xs text-text-muted mb-2">信号强度</div>
               <div class="flex items-center gap-3">
                   <div class="flex-1 h-2 bg-gray-700 rounded-full overflow-hidden">
                       <div class="h-full rounded-full transition-all duration-500"
                            :class="networkStatus.signal_strength > 70 ? 'bg-success' : networkStatus.signal_strength > 40 ? 'bg-warning' : 'bg-red-500'"
                            :style="{ width: Math.max(0, networkStatus.signal_strength) + '%' }"></div>
                   </div>
                   <span class="text-sm font-mono text-text-muted w-12 text-right">{{ networkStatus.signal_strength }}%</span>
               </div>
           </div>
       </div>
    </div>

    <!-- Remote Server Config - 远程服务器配置 -->
    <div class="glass-panel p-5 rounded-3xl space-y-4 animate-fade-in-up">
       <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
           <Server class="w-4 h-4" /> 远程服务器
           <div class="ml-auto flex items-center gap-2">
             <span v-if="remoteConfig.is_connecting" class="text-xs text-warning flex items-center gap-1">
               <Loader class="w-3 h-3 animate-spin" /> 连接中...
             </span>
             <span v-else-if="remoteConfig.is_connected" class="text-xs text-success flex items-center gap-1">
               <CheckCircle class="w-3 h-3" /> 已连接
             </span>
             <span v-else-if="remoteConfig.enabled && remoteConfig.last_error" class="text-xs text-red-400 truncate max-w-32" :title="remoteConfig.last_error">
               {{ remoteConfig.last_error }}
             </span>
           </div>
       </h3>
       
       <div class="space-y-4">
         <!-- 启用开关 -->
         <div class="flex items-center justify-between p-4 rounded-2xl" style="background: var(--theme-bg-input); border: 1px solid var(--theme-border-input);">
           <div class="flex items-center gap-3">
             <div class="w-10 h-10 rounded-xl flex items-center justify-center" 
                  :class="remoteForm.enabled ? 'bg-success/20 text-success' : 'bg-gray-500/20 text-gray-400'">
               <Server class="w-5 h-5" />
             </div>
             <div>
               <div class="font-medium text-text-primary">启用远程连接</div>
               <div class="text-xs text-text-muted">{{ remoteForm.enabled ? '已启用' : '已禁用' }}</div>
             </div>
           </div>
           <label class="relative inline-flex items-center cursor-pointer">
             <input type="checkbox" v-model="remoteForm.enabled" class="sr-only peer">
             <div class="w-12 h-6 rounded-full peer transition-colors duration-300"
                  :class="remoteForm.enabled ? 'bg-success' : 'bg-gray-500'">
               <div class="absolute top-[2px] w-5 h-5 bg-white rounded-full shadow-md transition-all duration-300"
                    :class="remoteForm.enabled ? 'left-[26px]' : 'left-[2px]'"></div>
             </div>
           </label>
         </div>

         <!-- 服务器地址 -->
         <div class="space-y-1">
           <label class="text-xs text-text-muted ml-1">服务器地址</label>
           <input v-model="remoteForm.server_url" type="text" 
                  placeholder="https://vis.example.com 或 http://192.168.1.100:8088"
                  class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
                  style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
         </div>

         <!-- WebSocket 和登录路径 -->
         <div class="grid grid-cols-2 gap-3">
           <div class="space-y-1">
             <label class="text-xs text-text-muted ml-1">WebSocket 路径</label>
             <input v-model="remoteForm.websocket_path" type="text" 
                    placeholder="dhlr/socket"
                    class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
                    style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
           </div>
           <div class="space-y-1">
             <label class="text-xs text-text-muted ml-1">登录接口</label>
             <input v-model="remoteForm.login_path" type="text" 
                    placeholder="/login"
                    class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
                    style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
           </div>
         </div>

         <!-- 用户名和密码 -->
         <div class="grid grid-cols-2 gap-3">
           <div class="space-y-1">
             <label class="text-xs text-text-muted ml-1">用户名</label>
             <input v-model="remoteForm.username" type="text" 
                    placeholder="admin"
                    class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
                    style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
           </div>
           <div class="space-y-1">
             <label class="text-xs text-text-muted ml-1">密码</label>
             <div class="relative">
               <input v-model="remoteForm.password" :type="showPassword ? 'text' : 'password'" 
                      :placeholder="remoteConfig.has_token ? '••••••（已保存）' : '请输入密码'"
                      class="w-full rounded-xl px-4 py-3 pr-10 border outline-none focus:border-primary/50 transition-all text-text-primary"
                      style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
               <button type="button" @click="showPassword = !showPassword" 
                       class="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                 <Eye v-if="showPassword" class="w-4 h-4" />
                 <EyeOff v-else class="w-4 h-4" />
               </button>
             </div>
           </div>
         </div>

         <!-- 校验按钮和结果 -->
         <div class="flex items-center gap-3">
           <button @click="verifyRemoteLogin" 
                   :disabled="verifying"
                   class="px-4 py-2 bg-blue-500 hover:bg-blue-600 text-white rounded-xl text-sm font-bold flex items-center gap-2 transition-all active:scale-95 disabled:opacity-50">
             <Loader v-if="verifying" class="w-4 h-4 animate-spin" />
             <CheckCircle v-else class="w-4 h-4" />
             {{ verifying ? '校验中...' : '校验登录' }}
           </button>
           <Transition name="slide-fade">
             <div v-if="verifyResult" class="flex items-center gap-2 text-sm"
                  :class="verifyResult.success ? 'text-success' : 'text-red-400'">
               <CheckCircle v-if="verifyResult.success" class="w-4 h-4" />
               <XCircle v-else class="w-4 h-4" />
               {{ verifyResult.message }}
             </div>
           </Transition>
         </div>
       </div>
    </div>

    <!-- Theme Settings -->
    <div class="glass-panel p-5 rounded-3xl space-y-4 animate-fade-in-up">
       <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
           <Palette class="w-4 h-4" /> 外观设置
       </h3>
       <div class="flex items-center justify-between p-4 rounded-2xl transition-all duration-300 hover-lift" style="background: var(--theme-bg-input); border: 1px solid var(--theme-border-input);">
           <div class="flex items-center gap-3">
              <Transition name="pop" mode="out-in">
                <div :key="isDarkMode ? 'dark' : 'light'" class="w-10 h-10 rounded-xl flex items-center justify-center" 
                     :class="isDarkMode ? 'bg-indigo-500/20 text-indigo-400' : 'bg-amber-500/20 text-amber-500'">
                  <Moon v-if="isDarkMode" class="w-5 h-5" />
                  <Sun v-else class="w-5 h-5" />
                </div>
              </Transition>
              <div>
                 <Transition name="slide-fade" mode="out-in">
                   <div :key="isDarkMode ? 'dark-text' : 'light-text'">
                     <div class="font-medium text-text-primary">{{ isDarkMode ? '深色模式' : '浅色模式' }}</div>
                     <div class="text-xs text-text-muted">{{ isDarkMode ? '适合夜间使用' : '适合日间使用' }}</div>
                   </div>
                 </Transition>
              </div>
           </div>
           <label class="relative inline-flex items-center cursor-pointer">
             <input type="checkbox" :checked="isDarkMode" @change="toggleTheme" class="sr-only peer">
             <div class="w-12 h-6 rounded-full peer transition-colors duration-300"
                  :class="isDarkMode ? 'bg-indigo-500' : 'bg-amber-400'">
               <div class="absolute top-[2px] w-5 h-5 bg-white rounded-full shadow-md transition-all duration-300 flex items-center justify-center"
                    :class="isDarkMode ? 'left-[26px]' : 'left-[2px]'">
                  <Moon v-if="isDarkMode" class="w-3 h-3 text-indigo-500" />
                  <Sun v-else class="w-3 h-3 text-amber-500" />
               </div>
             </div>
           </label>
       </div>
    </div>

    <!-- System Info -->
    <Transition name="fade" mode="out-in">
      <div v-if="loading" key="loading" class="glass-panel p-5 rounded-3xl space-y-4">
        <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
            <Info class="w-4 h-4" /> 设备信息
        </h3>
        <div class="grid grid-cols-2 gap-4">
            <div class="p-3 rounded-2xl border" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                <div class="skeleton h-3 w-16 mb-2"></div>
                <div class="skeleton h-5 w-24"></div>
            </div>
            <div class="p-3 rounded-2xl border" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                <div class="skeleton h-3 w-12 mb-2"></div>
                <div class="skeleton h-5 w-32"></div>
            </div>
        </div>
      </div>

      <div v-else-if="deviceInfo" key="content" class="glass-panel p-5 rounded-3xl space-y-4">
         <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
             <Info class="w-4 h-4" /> 设备信息
         </h3>
         <div class="grid grid-cols-2 gap-4">
             <div class="p-3 rounded-2xl border transition-all duration-300 hover-lift" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                 <div class="text-xs text-text-muted mb-1">系统版本</div>
                 <div class="font-mono text-text-primary">{{ deviceInfo.version }}</div>
             </div>
             <div class="p-3 rounded-2xl border transition-all duration-300 hover-lift" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                 <div class="text-xs text-text-muted mb-1">设备 ID</div>
                 <div class="font-mono text-text-primary truncate" :title="deviceInfo.device_id">{{ deviceInfo.device_id || 'Unknown' }}</div>
             </div>
             <div class="p-3 rounded-2xl border col-span-2 transition-all duration-300 hover-lift" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                 <div class="text-xs text-text-muted mb-1">运行时间</div>
                 <div class="font-mono text-text-primary">{{ ((deviceInfo.uptime || 0) / 3600).toFixed(1) }} 小时</div>
             </div>
         </div>
      </div>
    </Transition>

    <!-- Alarm Thresholds -->
    <Transition name="fade" mode="out-in">
      <div v-if="!loading" class="glass-panel p-5 rounded-3xl space-y-4">
         <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
             <ShieldAlert class="w-4 h-4" /> 报警阈值 (秒)
         </h3>
         <div class="grid grid-cols-3 gap-3">
             <div class="space-y-2">
                 <label class="text-xs text-warning block text-center">预警时间</label>
                 <input v-model.number="alarmSettings.warning_time" type="number" 
                        class="w-full text-center py-3 rounded-xl border border-warning/30 font-mono focus:border-warning outline-none transition-all text-text-primary"
                        style="background: var(--theme-bg-input);">
             </div>
             <div class="space-y-2">
                 <label class="text-xs text-red-400 block text-center">报警时间</label>
                 <input v-model.number="alarmSettings.alarm_time" type="number" 
                        class="w-full text-center py-3 rounded-xl border border-red-500/30 font-mono focus:border-red-500 outline-none transition-all text-text-primary"
                        style="background: var(--theme-bg-input);">
             </div>
             <div class="space-y-2">
                 <label class="text-xs text-red-600 block text-center">切电时间</label>
                 <input v-model.number="alarmSettings.action_time" type="number" 
                        class="w-full text-center py-3 rounded-xl border border-red-700/30 font-mono focus:border-red-700 outline-none transition-all text-text-primary"
                        style="background: var(--theme-bg-input);">
             </div>
         </div>
      </div>
    </Transition>

    <!-- Voice Settings -->
    <Transition name="fade" mode="out-in">
      <div v-if="!loading" class="glass-panel p-5 rounded-3xl space-y-4">
         <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
             <Volume2 class="w-4 h-4" /> 语音播报
         </h3>
         
         <div class="space-y-4">
             <div class="space-y-1">
                 <label class="text-xs text-text-muted ml-1">播报间隔 (秒)</label>
                 <input v-model.number="alarmSettings.broadcast_interval" type="number" 
                        class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
                        style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
             </div>

             <div class="space-y-1">
                 <label class="text-xs text-text-muted ml-1">预警提示语</label>
                 <input v-model="alarmSettings.warning_message" type="text" 
                        class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
                        style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
             </div>
             
             <div class="space-y-1">
                 <label class="text-xs text-text-muted ml-1">报警提示语</label>
                 <input v-model="alarmSettings.alarm_message" type="text" 
                        class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
                        style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
             </div>

             <div class="space-y-1">
                 <label class="text-xs text-text-muted ml-1">切电提示语</label>
                 <input v-model="alarmSettings.action_message" type="text" 
                        class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
                        style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
             </div>
         </div>
      </div>
    </Transition>

    <!-- 悬浮保存按钮容器 - 限制在内容区域内 -->
    <div class="fixed inset-0 pointer-events-none z-50 max-w-md mx-auto">
      <!-- 悬浮保存按钮 -->
      <button @click="saveSettings" 
         class="pointer-events-auto absolute bottom-20 right-4 px-5 py-3 bg-primary hover:bg-primary-light text-white rounded-2xl text-sm font-bold flex items-center gap-2 shadow-xl shadow-primary/30 transition-all active:scale-95 disabled:opacity-50 hover:scale-105"
         :disabled="saving">
         <Loader v-if="saving" class="w-5 h-5 animate-spin" />
         <Save v-else class="w-5 h-5" />
         <span>{{ saving ? '保存中...' : '保存配置' }}</span>
      </button>

      <!-- 保存成功提示 Toast -->
      <Transition name="toast">
        <div v-if="saveSuccess" 
             class="pointer-events-auto absolute bottom-36 right-4 px-5 py-3 bg-success text-white rounded-2xl text-sm font-bold flex items-center gap-2 shadow-xl shadow-success/30">
          <CheckCircle class="w-5 h-5" />
          <span>保存成功</span>
        </div>
      </Transition>

      <!-- 保存失败提示 Toast -->
      <Transition name="toast">
        <div v-if="saveError" 
             class="pointer-events-auto absolute bottom-36 right-4 px-5 py-3 bg-red-500 text-white rounded-2xl text-sm font-bold flex items-center gap-2 shadow-xl shadow-red-500/30">
          <XCircle class="w-5 h-5" />
          <span>{{ saveError }}</span>
        </div>
      </Transition>
    </div>
  </div>
</template>

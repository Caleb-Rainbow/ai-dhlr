<script setup lang="ts">
import { ref, onMounted, computed, onUnmounted } from 'vue';
import { ws } from '../api/ws';
import type { DeviceInfo, AlarmSettings, NetworkStatus, RemoteServerConfig, SerialConfig, LoraConfig, GpioConfig } from '../types';
import { Save, Info, Volume2, VolumeX, ShieldAlert, Sun, Moon, Palette, Loader, Wifi, Globe, Server, CheckCircle, XCircle, RefreshCw, Eye, EyeOff, Edit3, Check, Download, Lightbulb } from 'lucide-vue-next';
import { useTheme } from '../composables/useTheme';

const deviceInfo = ref<DeviceInfo | null>(null);
const alarmSettings = ref<AlarmSettings>({
  warning_time: 5,
  alarm_time: 10,
  action_time: 15,
  broadcast_interval: 10,
  warning_message: "请注意",
  alarm_message: "警告",
  action_message: "动作",
  temp_alarm_threshold: 80,
  temp_alarm_message: "温度过高，请立即处理"
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

// 串口配置
const serialConfig = ref<SerialConfig>({
  enabled: true,
  port: '/dev/ttyS3',
  baudrate: 9600,
  poll_interval: 1.0,
  is_open: false,
  debug_hex: false
});

// LoRa配置
const loraConfig = ref<LoraConfig>({
  id: 0,
  channel: 0
});

// GPIO配置
const gpioConfig = ref<GpioConfig>({
  enabled: true,
  gpio_path: '/sys/external_gpio',
  pin_fire: 'gpio0',
  pin_absence: 'gpio1',
  pin_alarm: 'gpio2'
});

// 可用GPIO引脚列表
const gpioPins = ref<string[]>([]);
const loadingGpioPins = ref(false);

// 可用串口列表
const serialPorts = ref<Array<{ device: string; name: string; description: string; hwid: string }>>([]);
const loadingSerialPorts = ref(false);

// LoRa设置状态
const settingLora = ref(false);

// 音量设置
const voiceVolume = ref(100); // 音量百分比 0-100，默认最大音量
const settingVolume = ref(false);

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
const showSaveButton = ref(false);

// 设备ID编辑状态
const editingDeviceId = ref(false);
const tempDeviceId = ref('');
const savingDeviceId = ref(false);

// 系统更新状态
const updatingSystem = ref(false);
const updateResult = ref<{ success: boolean; message: string } | null>(null);

// 监测模式状态
const zoneMode = ref<'zoned' | 'single'>('zoned');
const zoneCount = ref(0);
const switchingZoneMode = ref(false);
const zoneModeError = ref('');

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
  } catch (e) { console.error(e); }
  finally {
    loading.value = false;
  }

  // 加载串口和LoRa配置
  try {
    const serial = await ws.request<SerialConfig>('get_serial_config').catch(() => serialConfig.value);
    serialConfig.value = serial;
    const lora = await ws.request<LoraConfig>('get_lora_config').catch(() => loraConfig.value);
    loraConfig.value = lora;
    // 加载可用串口列表
    await loadSerialPorts();
  } catch (e) { console.error('Failed to load serial config', e); }

  // 加载音量配置
  try {
    const volumeData = await ws.request<{ volume: number }>('get_volume').catch(() => ({ volume: 1.0 }));
    voiceVolume.value = Math.round(volumeData.volume * 100);
  } catch (e) { console.error('Failed to load volume config', e); }

  // 加载GPIO配置
  try {
    const gpio = await ws.request<GpioConfig>('get_gpio_config').catch(() => gpioConfig.value);
    gpioConfig.value = gpio;
    await loadGpioPins();
  } catch (e) { console.error('Failed to load gpio config', e); }

  // 加载监测模式配置
  try {
    const modeData = await ws.request<{ zone_mode: 'zoned' | 'single'; zone_count: number }>('get_zone_mode');
    zoneMode.value = modeData.zone_mode;
    zoneCount.value = modeData.zone_count;
  } catch (e) { console.error('Failed to load zone mode', e); }
};

// 加载可用GPIO引脚列表
const loadGpioPins = async () => {
  loadingGpioPins.value = true;
  try {
    const result = await ws.request<{ pins: string[] }>('get_gpio_pins');
    gpioPins.value = result.pins || [];
  } catch (e) {
    console.error('Failed to load GPIO pins', e);
    gpioPins.value = [];
  } finally {
    loadingGpioPins.value = false;
  }
};

// 保存GPIO配置
const savingGpio = ref(false);
const saveGpioConfig = async () => {
  savingGpio.value = true;
  try {
    await ws.request('update_gpio_config', gpioConfig.value);
    // 显示成功提示
  } catch (e: any) {
    alert('保存GPIO配置失败: ' + (e.message || e));
  } finally {
    savingGpio.value = false;
  }
};

// 加载可用串口列表
const loadSerialPorts = async () => {
  loadingSerialPorts.value = true;
  try {
    const ports = await ws.request<Array<{ device: string; name: string; description: string; hwid: string }>>('get_serial_ports');
    serialPorts.value = ports;
  } catch (e) {
    console.error('Failed to load serial ports', e);
    serialPorts.value = [];
  } finally {
    loadingSerialPorts.value = false;
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
    // 保存串口配置
    await ws.request('update_serial_config', serialConfig.value);
    saveSuccess.value = true;
    // 3秒后自动隐藏成功提示
    setTimeout(() => {
      saveSuccess.value = false;
    }, 3000);
  } catch (e: any) {
    saveError.value = e.message || '保存失败';
    // 5秒后自动隐藏错误提示
    setTimeout(() => {
      saveError.value = '';
    }, 5000);
  }
  finally { saving.value = false; }
};

// 设置LoRa配置（编号和信道）
const setLoraConfig = async () => {
  settingLora.value = true;
  try {
    await ws.request('set_lora_config', {
      id: loraConfig.value.id,
      channel: loraConfig.value.channel
    });
    alert('LoRa配置设置成功');
  } catch (e: any) {
    alert('LoRa配置设置失败: ' + (e.message || e));
  } finally {
    settingLora.value = false;
  }
};

// 设置音量
const updateVolume = async () => {
  settingVolume.value = true;
  try {
    await ws.request('set_volume', { volume: voiceVolume.value / 100 });
  } catch (e: any) {
    console.error('设置音量失败', e);
  } finally {
    settingVolume.value = false;
  }
};

// 切换串口调试日志
const togglingSerialDebug = ref(false);
const toggleSerialDebug = async () => {
  togglingSerialDebug.value = true;
  try {
    const newValue = !serialConfig.value.debug_hex;
    await ws.request('set_serial_debug', { enabled: newValue });
    serialConfig.value.debug_hex = newValue;
  } catch (e: any) {
    alert('设置调试日志失败: ' + (e.message || e));
  } finally {
    togglingSerialDebug.value = false;
  }
};

// 开始编辑设备ID
const startEditDeviceId = () => {
  tempDeviceId.value = deviceInfo.value?.device_id || 'dhlr';
  editingDeviceId.value = true;
};

// 取消编辑设备ID
const cancelEditDeviceId = () => {
  editingDeviceId.value = false;
  tempDeviceId.value = '';
};

// 保存设备ID
const saveDeviceId = async () => {
  const newId = tempDeviceId.value.trim();
  if (!newId) {
    alert('设备ID不能为空');
    return;
  }

  savingDeviceId.value = true;
  try {
    await ws.request('set_device_id', { device_id: newId });
    if (deviceInfo.value) {
      deviceInfo.value.device_id = newId;
    }
    editingDeviceId.value = false;
    tempDeviceId.value = '';
  } catch (e: any) {
    alert('设置设备ID失败: ' + (e.message || e));
  } finally {
    savingDeviceId.value = false;
  }
};

// 触发系统更新
const triggerSystemUpdate = async () => {
  if (!confirm('确定要更新系统吗？\n\n更新将拉取最新代码并重启服务，期间连接会断开。')) {
    return;
  }

  updatingSystem.value = true;
  updateResult.value = null;

  try {
    const result = await ws.request<{ success: boolean; message: string }>('trigger_update');
    updateResult.value = result;
    // 5秒后隐藏结果提示
    setTimeout(() => {
      updateResult.value = null;
    }, 5000);
  } catch (e: any) {
    updateResult.value = { success: false, message: e.message || '更新失败' };
    // 5秒后隐藏错误提示
    setTimeout(() => {
      updateResult.value = null;
    }, 5000);
  } finally {
    updatingSystem.value = false;
  }
};

// 切换监测模式
const setZoneMode = async (newMode: 'zoned' | 'single') => {
  if (switchingZoneMode.value) return;
  if (newMode === zoneMode.value) return;

  switchingZoneMode.value = true;
  zoneModeError.value = '';

  try {
    await ws.request<{ zone_mode: string; message: string }>('set_zone_mode', {
      zone_mode: newMode
    });
    zoneMode.value = newMode;
    zoneModeError.value = '';
    // 刷新灶台数量
    const modeData = await ws.request<{ zone_mode: 'zoned' | 'single'; zone_count: number }>('get_zone_mode');
    zoneCount.value = modeData.zone_count;
  } catch (e: any) {
    zoneModeError.value = e.message || '切换失败';
    // 5秒后清除错误
    setTimeout(() => {
      zoneModeError.value = '';
    }, 5000);
  } finally {
    switchingZoneMode.value = false;
  }
};

// 定时刷新网络和远程状态
let refreshInterval: number | null = null;
onMounted(async () => {
  await ws.connect();
  loadData();
  // 延迟显示保存按钮，触发进入动画
  setTimeout(() => {
    showSaveButton.value = true;
  }, 300);
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
      <h2 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-text-primary to-text-secondary">
        系统设置</h2>
    </div>

    <!-- Network Status - 网络状态 -->
    <div
      class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] p-5 rounded-3xl space-y-4 animate-fade-in-up shadow-[0_8px_32px_var(--theme-shadow)] transition-all">
      <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
        <component :is="networkIcon" class="w-4 h-4" /> 网络状态
        <button @click="refreshNetwork" class="ml-auto p-1 rounded-lg hover:bg-white/10 transition-colors">
          <RefreshCw class="w-4 h-4 text-text-muted" />
        </button>
      </h3>
      <div class="grid grid-cols-2 gap-4">
        <div class="p-3 rounded-2xl border transition-all duration-300 hover-lift"
          style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
          <div class="text-xs text-text-muted mb-1">网络类型</div>
          <div class="flex items-center gap-2">
            <component :is="networkIcon" class="w-5 h-5"
              :class="networkStatus.is_connected ? 'text-success' : 'text-text-muted'" />
            <span class="font-medium text-text-primary">{{ networkTypeLabel }}</span>
          </div>
        </div>
        <div class="p-3 rounded-2xl border transition-all duration-300 hover-lift"
          style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
          <div class="text-xs text-text-muted mb-1">IP 地址</div>
          <div class="font-mono text-text-primary">{{ networkStatus.ip_address || '未连接' }}</div>
        </div>
        <div v-if="networkStatus.interface_type === 'wifi'"
          class="p-3 rounded-2xl border col-span-2 transition-all duration-300 hover-lift"
          style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
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
    <div
      class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all p-5 rounded-3xl space-y-4 animate-fade-in-up">
      <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
        <Server class="w-4 h-4" /> 远程服务器
        <div class="ml-auto flex items-center gap-2">
          <span v-if="remoteConfig.is_connecting" class="text-xs text-warning flex items-center gap-1">
            <Loader class="w-3 h-3 animate-spin" /> 连接中...
          </span>
          <span v-else-if="remoteConfig.is_connected" class="text-xs text-success flex items-center gap-1">
            <CheckCircle class="w-3 h-3" /> 已连接
          </span>
          <span v-else-if="remoteConfig.enabled && remoteConfig.last_error"
            class="text-xs text-red-400 truncate max-w-32" :title="remoteConfig.last_error">
            {{ remoteConfig.last_error }}
          </span>
        </div>
      </h3>

      <div class="space-y-4">
        <!-- 启用开关 -->
        <div class="flex items-center justify-between p-4 rounded-2xl"
          style="background: var(--theme-bg-input); border: 1px solid var(--theme-border-input);">
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
            <input v-model="remoteForm.websocket_path" type="text" placeholder="dhlr/socket"
              class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
              style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
          </div>
          <div class="space-y-1">
            <label class="text-xs text-text-muted ml-1">登录接口</label>
            <input v-model="remoteForm.login_path" type="text" placeholder="/login"
              class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
              style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
          </div>
        </div>

        <!-- 用户名和密码 -->
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1">
            <label class="text-xs text-text-muted ml-1">用户名</label>
            <input v-model="remoteForm.username" type="text" placeholder="admin"
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
          <button @click="verifyRemoteLogin" :disabled="verifying"
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

    <!-- Serial Port Config - 串口配置 -->
    <div v-if="!loading"
      class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all p-5 rounded-3xl space-y-4 animate-fade-in-up">
      <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
        <Server class="w-4 h-4" /> 串口配置
        <span v-if="serialConfig.is_open" class="text-xs text-success ml-auto">已连接</span>
        <span v-else class="text-xs text-red-400 ml-auto">未连接</span>
      </h3>

      <div class="space-y-4">
        <!-- 启用开关 -->
        <div class="flex items-center justify-between p-4 rounded-2xl"
          style="background: var(--theme-bg-input); border: 1px solid var(--theme-border-input);">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl flex items-center justify-center"
              :class="serialConfig.enabled ? 'bg-success/20 text-success' : 'bg-gray-500/20 text-gray-400'">
              <Server class="w-5 h-5" />
            </div>
            <div>
              <div class="font-medium text-text-primary">启用串口</div>
              <div class="text-xs text-text-muted">{{ serialConfig.enabled ? '已启用' : '已禁用' }}</div>
            </div>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="serialConfig.enabled" class="sr-only peer">
            <div class="w-12 h-6 rounded-full peer transition-colors duration-300"
              :class="serialConfig.enabled ? 'bg-success' : 'bg-gray-500'">
              <div class="absolute top-[2px] w-5 h-5 bg-white rounded-full shadow-md transition-all duration-300"
                :class="serialConfig.enabled ? 'left-[26px]' : 'left-[2px]'"></div>
            </div>
          </label>
        </div>

        <!-- 串口路径 -->
        <div class="space-y-1">
          <div class="flex items-center justify-between">
            <label class="text-xs text-text-muted ml-1">串口路径</label>
            <button @click="loadSerialPorts" :disabled="loadingSerialPorts"
              class="p-1 rounded-lg hover:bg-white/10 transition-colors disabled:opacity-50" title="刷新串口列表">
              <RefreshCw class="w-3.5 h-3.5 text-text-muted" :class="{ 'animate-spin': loadingSerialPorts }" />
            </button>
          </div>
          <select v-model="serialConfig.port"
            class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all appearance-none cursor-pointer text-text-primary"
            style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
            <!-- 当前配置的端口（如果不在列表中也显示） -->
            <option v-if="serialConfig.port && !serialPorts.some(p => p.device === serialConfig.port)"
              :value="serialConfig.port">
              {{ serialConfig.port }} (当前)
            </option>
            <!-- 可用串口列表 -->
            <option v-for="port in serialPorts" :key="port.device" :value="port.device">
              {{ port.device }} - {{ port.description || port.name }}
            </option>
            <!-- 无可用串口时显示提示 -->
            <option v-if="serialPorts.length === 0 && !serialConfig.port" value="" disabled>
              未检测到串口设备
            </option>
          </select>
        </div>

        <!-- 波特率和轮询间隔 -->
        <div class="grid grid-cols-2 gap-3">
          <div class="space-y-1">
            <label class="text-xs text-text-muted ml-1">波特率</label>
            <select v-model.number="serialConfig.baudrate"
              class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all appearance-none cursor-pointer text-text-primary"
              style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              <option value="9600">9600</option>
              <option value="19200">19200</option>
              <option value="38400">38400</option>
              <option value="115200">115200</option>
            </select>
          </div>
          <div class="space-y-1">
            <label class="text-xs text-text-muted ml-1">轮询间隔 (秒)</label>
            <input v-model.number="serialConfig.poll_interval" type="number" step="0.1" min="0.1"
              class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
              style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
          </div>
        </div>

        <!-- 16进制调试日志开关 -->
        <div class="flex items-center justify-between p-4 rounded-2xl"
          style="background: var(--theme-bg-input); border: 1px solid var(--theme-border-input);">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl flex items-center justify-center"
              :class="serialConfig.debug_hex ? 'bg-blue-500/20 text-blue-400' : 'bg-gray-500/20 text-gray-400'">
              <Eye class="w-5 h-5" />
            </div>
            <div>
              <div class="font-medium text-text-primary">16进制调试日志</div>
              <div class="text-xs text-text-muted">{{ serialConfig.debug_hex ? '开启中 - 在日志中打印串口数据' : '已关闭' }}</div>
            </div>
          </div>
          <button @click="toggleSerialDebug" :disabled="togglingSerialDebug"
            class="relative inline-flex items-center cursor-pointer disabled:opacity-50">
            <div class="w-12 h-6 rounded-full peer transition-colors duration-300"
              :class="serialConfig.debug_hex ? 'bg-blue-500' : 'bg-gray-500'">
              <div
                class="absolute top-[2px] w-5 h-5 bg-white rounded-full shadow-md transition-all duration-300 flex items-center justify-center"
                :class="serialConfig.debug_hex ? 'left-[26px]' : 'left-[2px]'">
                <Loader v-if="togglingSerialDebug" class="w-3 h-3 animate-spin text-gray-400" />
              </div>
            </div>
          </button>
        </div>
      </div>
    </div>

    <!-- LoRa Config - LoRa配置 -->
    <div v-if="!loading"
      class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all p-5 rounded-3xl space-y-4 animate-fade-in-up">
      <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
        <Wifi class="w-4 h-4" /> LoRa 配置
      </h3>

      <!-- 两个输入框一行 -->
      <div class="grid grid-cols-2 gap-3">
        <div class="space-y-1">
          <label class="text-xs text-text-muted ml-1">LoRa 编号</label>
          <input v-model.number="loraConfig.id" type="number" min="0"
            class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
            style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
        </div>
        <div class="space-y-1">
          <label class="text-xs text-text-muted ml-1">LoRa 信道</label>
          <input v-model.number="loraConfig.channel" type="number" min="0"
            class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
            style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
        </div>
      </div>

      <!-- 设置按钮单独一行 -->
      <button @click="setLoraConfig" :disabled="settingLora"
        class="w-full py-3 bg-primary hover:bg-primary-light text-white rounded-xl text-sm font-bold transition-all active:scale-95 disabled:opacity-50 flex items-center justify-center gap-2">
        <Loader v-if="settingLora" class="w-4 h-4 animate-spin" />
        <span>{{ settingLora ? '设置中...' : '应用 LoRa 配置' }}</span>
      </button>
    </div>

    <!-- GPIO Config - GPIO指示灯配置 -->
    <div v-if="!loading"
      class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all p-5 rounded-3xl space-y-4 animate-fade-in-up">
      <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
        <Lightbulb class="w-4 h-4" /> GPIO 指示灯
        <button @click="loadGpioPins" :disabled="loadingGpioPins"
          class="ml-auto p-1 rounded-lg hover:bg-white/10 transition-colors disabled:opacity-50" title="刷新引脚列表">
          <RefreshCw class="w-3.5 h-3.5 text-text-muted" :class="{ 'animate-spin': loadingGpioPins }" />
        </button>
      </h3>

      <div class="space-y-4">
        <!-- 启用开关 -->
        <div class="flex items-center justify-between p-4 rounded-2xl"
          style="background: var(--theme-bg-input); border: 1px solid var(--theme-border-input);">
          <div class="flex items-center gap-3">
            <div class="w-10 h-10 rounded-xl flex items-center justify-center"
              :class="gpioConfig.enabled ? 'bg-success/20 text-success' : 'bg-gray-500/20 text-gray-400'">
              <Lightbulb class="w-5 h-5" />
            </div>
            <div>
              <div class="font-medium text-text-primary">启用指示灯</div>
              <div class="text-xs text-text-muted">{{ gpioConfig.enabled ? '已启用' : '已禁用' }}</div>
            </div>
          </div>
          <label class="relative inline-flex items-center cursor-pointer">
            <input type="checkbox" v-model="gpioConfig.enabled" class="sr-only peer">
            <div class="w-12 h-6 rounded-full peer transition-colors duration-300"
              :class="gpioConfig.enabled ? 'bg-success' : 'bg-gray-500'">
              <div class="absolute top-[2px] w-5 h-5 bg-white rounded-full shadow-md transition-all duration-300"
                :class="gpioConfig.enabled ? 'left-[26px]' : 'left-[2px]'"></div>
            </div>
          </label>
        </div>

        <!-- 引脚选择 -->
        <div class="grid grid-cols-3 gap-3">
          <div class="space-y-1">
            <label class="text-xs text-text-muted ml-1">动火指示灯</label>
            <select v-model="gpioConfig.pin_fire"
              class="w-full rounded-xl px-3 py-3 border outline-none focus:border-primary/50 transition-all appearance-none cursor-pointer text-text-primary text-sm"
              style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              <option v-for="pin in gpioPins" :key="pin" :value="pin">{{ pin }}</option>
            </select>
          </div>
          <div class="space-y-1">
            <label class="text-xs text-text-muted ml-1">离人指示灯</label>
            <select v-model="gpioConfig.pin_absence"
              class="w-full rounded-xl px-3 py-3 border outline-none focus:border-primary/50 transition-all appearance-none cursor-pointer text-text-primary text-sm"
              style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              <option v-for="pin in gpioPins" :key="pin" :value="pin">{{ pin }}</option>
            </select>
          </div>
          <div class="space-y-1">
            <label class="text-xs text-text-muted ml-1">报警指示灯</label>
            <select v-model="gpioConfig.pin_alarm"
              class="w-full rounded-xl px-3 py-3 border outline-none focus:border-primary/50 transition-all appearance-none cursor-pointer text-text-primary text-sm"
              style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              <option v-for="pin in gpioPins" :key="pin" :value="pin">{{ pin }}</option>
            </select>
          </div>
        </div>

        <!-- 保存按钮 -->
        <button @click="saveGpioConfig" :disabled="savingGpio"
          class="w-full py-3 bg-primary hover:bg-primary-light text-white rounded-xl text-sm font-bold transition-all active:scale-95 disabled:opacity-50 flex items-center justify-center gap-2">
          <Loader v-if="savingGpio" class="w-4 h-4 animate-spin" />
          <span>{{ savingGpio ? '保存中...' : '应用 GPIO 配置' }}</span>
        </button>
      </div>
    </div>


    <!-- Zone Mode Settings - 监测模式设置 -->
    <div
      class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all p-5 rounded-3xl space-y-4 animate-fade-in-up">
      <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
        <ShieldAlert class="w-4 h-4" /> 监测模式
      </h3>
      <div class="space-y-4">
        <!-- 模式说明 -->
        <div class="text-sm text-text-muted">
          选择设备的监测模式。分区监测支持多个独立灶台区域，不分区监测仅使用单一监测区域。
        </div>

        <!-- 模式选择 -->
        <div class="grid grid-cols-2 gap-3">
          <button @click="setZoneMode('zoned')" :disabled="switchingZoneMode"
            class="p-4 rounded-2xl border transition-all duration-300 text-left" :class="zoneMode === 'zoned'
              ? 'border-primary/50 bg-primary/10'
              : 'border-[var(--theme-border-input)] bg-[var(--theme-bg-input)] hover:border-primary/30'"
            :style="{ opacity: switchingZoneMode ? 0.7 : 1 }">
            <div class="flex items-center gap-3">
              <div class="w-5 h-5 rounded-full border-2 flex items-center justify-center"
                :class="zoneMode === 'zoned' ? 'border-primary bg-primary' : 'border-text-muted'">
                <div v-if="zoneMode === 'zoned'" class="w-2 h-2 bg-white rounded-full"></div>
              </div>
              <div>
                <div class="font-medium text-text-primary">分区监测</div>
                <div class="text-xs text-text-muted">支持多个独立灶台区域</div>
              </div>
            </div>
          </button>

          <button @click="setZoneMode('single')" :disabled="switchingZoneMode"
            class="p-4 rounded-2xl border transition-all duration-300 text-left" :class="zoneMode === 'single'
              ? 'border-primary/50 bg-primary/10'
              : 'border-[var(--theme-border-input)] bg-[var(--theme-bg-input)] hover:border-primary/30'"
            :style="{ opacity: switchingZoneMode ? 0.7 : 1 }">
            <div class="flex items-center gap-3">
              <div class="w-5 h-5 rounded-full border-2 flex items-center justify-center"
                :class="zoneMode === 'single' ? 'border-primary bg-primary' : 'border-text-muted'">
                <div v-if="zoneMode === 'single'" class="w-2 h-2 bg-white rounded-full"></div>
              </div>
              <div>
                <div class="font-medium text-text-primary">不分区监测</div>
                <div class="text-xs text-text-muted">单一监测区域</div>
              </div>
            </div>
          </button>
        </div>

        <!-- 切换中提示 -->
        <div v-if="switchingZoneMode" class="flex items-center gap-2 text-sm text-primary">
          <Loader class="w-4 h-4 animate-spin" />
          <span>正在切换监测模式...</span>
        </div>

        <!-- 错误提示 -->
        <div v-if="zoneModeError" class="p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-500 text-sm">
          {{ zoneModeError }}
        </div>

        <!-- 当前灶台数量提示 -->
        <div v-if="zoneCount > 0" class="text-xs text-warning">
          当前有 {{ zoneCount }} 个灶台区域。切换监测模式需要先删除所有灶台区域。
        </div>
      </div>
    </div>


    <!-- Theme Settings -->
    <div
      class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all p-5 rounded-3xl space-y-4 animate-fade-in-up">
      <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
        <Palette class="w-4 h-4" /> 外观设置
      </h3>
      <div class="flex items-center justify-between p-4 rounded-2xl transition-all duration-300 hover-lift"
        style="background: var(--theme-bg-input); border: 1px solid var(--theme-border-input);">
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
            <div
              class="absolute top-[2px] w-5 h-5 bg-white rounded-full shadow-md transition-all duration-300 flex items-center justify-center"
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
      <div v-if="loading" key="loading"
        class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] p-5 rounded-3xl space-y-4 shadow-[0_8px_32px_var(--theme-shadow)] transition-all">
        <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
          <Info class="w-4 h-4" /> 设备信息
        </h3>
        <div class="grid grid-cols-2 gap-4">
          <div class="p-3 rounded-2xl border"
            style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
            <div class="skeleton h-3 w-16 mb-2"></div>
            <div class="skeleton h-5 w-24"></div>
          </div>
          <div class="p-3 rounded-2xl border"
            style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
            <div class="skeleton h-3 w-12 mb-2"></div>
            <div class="skeleton h-5 w-32"></div>
          </div>
        </div>
      </div>

      <div v-else-if="deviceInfo" key="content"
        class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] p-5 rounded-3xl space-y-4 shadow-[0_8px_32px_var(--theme-shadow)] transition-all">
        <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
          <Info class="w-4 h-4" /> 设备信息
        </h3>
        <div class="space-y-4">
          <!-- 设备ID 单独一行 -->
          <div class="p-3 rounded-2xl border transition-all duration-300 hover-lift"
            style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
            <div class="text-xs text-text-muted mb-1">设备 ID</div>
            <!-- 编辑模式 -->
            <div v-if="editingDeviceId" class="space-y-2">
              <input v-model="tempDeviceId" type="text" placeholder="输入设备ID"
                class="w-full px-2 py-1.5 rounded-lg border outline-none focus:border-primary/50 transition-all text-text-primary font-mono text-sm"
                style="background: var(--theme-bg-card); border-color: var(--theme-border-input);"
                @keyup.enter="saveDeviceId" @keyup.escape="cancelEditDeviceId">
              <div class="flex items-center justify-end gap-2">
                <button @click="cancelEditDeviceId"
                  class="px-3 py-1 rounded-lg bg-red-500/20 text-red-400 hover:bg-red-500/30 transition-colors text-xs font-medium flex items-center gap-1">
                  <XCircle class="w-3.5 h-3.5" />
                  取消
                </button>
                <button @click="saveDeviceId" :disabled="savingDeviceId"
                  class="px-3 py-1 rounded-lg bg-success/20 text-success hover:bg-success/30 transition-colors disabled:opacity-50 text-xs font-medium flex items-center gap-1">
                  <Loader v-if="savingDeviceId" class="w-3.5 h-3.5 animate-spin" />
                  <Check v-else class="w-3.5 h-3.5" />
                  保存
                </button>
              </div>
            </div>
            <!-- 显示模式 -->
            <div v-else class="flex items-center justify-between">
              <div class="font-mono text-text-primary truncate flex-1" :title="deviceInfo.device_id">{{
                deviceInfo.device_id || 'Unknown' }}</div>
              <button @click="startEditDeviceId"
                class="ml-2 p-1.5 rounded-lg hover:bg-white/10 text-text-muted hover:text-primary transition-colors"
                title="修改设备ID">
                <Edit3 class="w-4 h-4" />
              </button>
            </div>
          </div>
          <!-- 系统版本 -->
          <div class="p-3 rounded-2xl border transition-all duration-300 hover-lift"
            style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
            <div class="text-xs text-text-muted mb-1">系统版本</div>
            <div class="font-mono text-text-primary">{{ deviceInfo.version }}</div>
          </div>
        </div>
      </div>
    </Transition>

    <!-- System Update - 系统更新 -->
    <Transition name="fade" mode="out-in">
      <div v-if="!loading"
        class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all p-5 rounded-3xl space-y-4 animate-fade-in-up">
        <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
          <Download class="w-4 h-4" /> 系统更新
        </h3>
        <div class="space-y-3">
          <p class="text-sm text-text-muted">
            点击下方按钮将拉取最新代码并重启服务。更新过程中连接会短暂断开。
          </p>
          <button @click="triggerSystemUpdate" :disabled="updatingSystem"
            class="w-full py-3 bg-gradient-to-r from-blue-500 to-cyan-500 hover:from-blue-600 hover:to-cyan-600 text-white rounded-xl text-sm font-bold transition-all active:scale-95 disabled:opacity-50 flex items-center justify-center gap-2 shadow-lg shadow-blue-500/20">
            <Loader v-if="updatingSystem" class="w-5 h-5 animate-spin" />
            <Download v-else class="w-5 h-5" />
            <span>{{ updatingSystem ? '正在更新...' : '立即更新系统' }}</span>
          </button>
          <!-- 更新结果提示 -->
          <Transition name="slide-fade">
            <div v-if="updateResult" class="flex items-center gap-2 p-3 rounded-xl text-sm"
              :class="updateResult.success ? 'bg-success/20 text-success' : 'bg-red-500/20 text-red-400'">
              <CheckCircle v-if="updateResult.success" class="w-4 h-4 flex-shrink-0" />
              <XCircle v-else class="w-4 h-4 flex-shrink-0" />
              <span>{{ updateResult.message }}</span>
            </div>
          </Transition>
        </div>
      </div>
    </Transition>

    <!-- Alarm Thresholds -->
    <Transition name="fade" mode="out-in">
      <div v-if="!loading"
        class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all p-5 rounded-3xl space-y-4">
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

        <!-- 温度报警阈值 -->
        <div class="pt-4 border-t border-white/10">
          <h4 class="text-xs text-orange-400 font-bold uppercase tracking-wider mb-3">温度报警</h4>
          <div class="grid grid-cols-2 gap-3">
            <div class="space-y-1">
              <label class="text-xs text-text-muted ml-1">温度阈值 (°C)</label>
              <input v-model.number="alarmSettings.temp_alarm_threshold" type="number" min="0" max="200" step="1"
                class="w-full rounded-xl px-4 py-3 border border-orange-500/30 font-mono focus:border-orange-500 outline-none transition-all text-text-primary"
                style="background: var(--theme-bg-input);">
            </div>
            <!-- 暂时隐藏温度报警提示语输入框 -->
            <!-- <div class="space-y-1">
              <label class="text-xs text-text-muted ml-1">报警提示语</label>
              <input v-model="alarmSettings.temp_alarm_message" type="text" placeholder="温度过高"
                class="w-full rounded-xl px-4 py-3 border border-orange-500/30 focus:border-orange-500 outline-none transition-all text-text-primary"
                style="background: var(--theme-bg-input);">
            </div> -->
          </div>
          <p class="text-xs text-text-muted mt-2">当温度传感器读数超过阈值且区域无人时触发温度报警</p>
        </div>
      </div>
    </Transition>

    <!-- Voice Settings -->
    <Transition name="fade" mode="out-in">
      <div v-if="!loading"
        class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] shadow-[0_8px_32px_var(--theme-shadow)] transition-all p-5 rounded-3xl space-y-4">
        <h3 class="flex items-center gap-2 text-sm font-bold text-text-muted uppercase tracking-wider">
          <Volume2 class="w-4 h-4" /> 语音播报
        </h3>

        <div class="space-y-4">
          <!-- 音量调整 -->
          <div class="space-y-2">
            <div class="flex items-center justify-between">
              <label class="text-xs text-text-muted ml-1">播报音量</label>
              <span class="text-sm font-mono text-primary font-medium">{{ voiceVolume }}%</span>
            </div>
            <div class="flex items-center gap-3 p-3 rounded-2xl"
              style="background: var(--theme-bg-input); border: 1px solid var(--theme-border-input);">
              <VolumeX v-if="voiceVolume === 0" class="w-5 h-5 text-text-muted flex-shrink-0" />
              <Volume2 v-else class="w-5 h-5 text-primary flex-shrink-0" />
              <input type="range" v-model.number="voiceVolume" min="0" max="100" step="1" @change="updateVolume"
                class="volume-slider flex-1 h-2 rounded-full appearance-none cursor-pointer"
                :style="{ background: `linear-gradient(to right, var(--color-primary) ${voiceVolume}%, var(--theme-border-input) ${voiceVolume}%)` }" />
            </div>
          </div>

          <div class="space-y-1">
            <label class="text-xs text-text-muted ml-1">播报间隔 (秒)</label>
            <input v-model.number="alarmSettings.broadcast_interval" type="number"
              class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
              style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
          </div>

          <!-- 暂时隐藏预警、报警、切电提示语输入框 -->
          <!-- <div class="space-y-1">
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
          </div> -->
        </div>
      </div>
    </Transition>

    <!-- 悬浮保存按钮容器 - 限制在内容区域内 -->
    <div class="fixed inset-0 pointer-events-none z-50 max-w-md mx-auto">
      <!-- 悬浮保存按钮 -->
      <Transition name="save-btn">
        <button v-if="showSaveButton" @click="saveSettings"
          class="pointer-events-auto absolute bottom-20 right-4 px-5 py-3 bg-primary hover:bg-primary-light text-white rounded-2xl text-sm font-bold flex items-center gap-2 shadow-xl shadow-primary/30 transition-all active:scale-95 disabled:opacity-50 hover:scale-105"
          :disabled="saving">
          <Loader v-if="saving" class="w-5 h-5 animate-spin" />
          <Save v-else class="w-5 h-5" />
          <span>{{ saving ? '保存中...' : '保存配置' }}</span>
        </button>
      </Transition>

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

<style scoped>
/* 保存按钮进入动画 */
.save-btn-enter-active {
  animation: save-btn-bounce-in 0.6s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.save-btn-leave-active {
  animation: save-btn-bounce-out 0.3s ease-in forwards;
}

@keyframes save-btn-bounce-in {
  0% {
    opacity: 0;
    transform: translateX(100px) scale(0.3);
  }

  50% {
    opacity: 1;
    transform: translateX(-10px) scale(1.1);
  }

  70% {
    transform: translateX(5px) scale(0.95);
  }

  100% {
    transform: translateX(0) scale(1);
  }
}

@keyframes save-btn-bounce-out {
  0% {
    opacity: 1;
    transform: translateX(0) scale(1);
  }

  100% {
    opacity: 0;
    transform: translateX(100px) scale(0.3);
  }
}

/* 音量滑动条样式 */
.volume-slider {
  -webkit-appearance: none;
  appearance: none;
  height: 8px;
  border-radius: 4px;
  outline: none;
  transition: all 0.2s ease;
}

.volume-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--color-primary);
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
  transition: all 0.2s ease;
}

.volume-slider::-webkit-slider-thumb:hover {
  transform: scale(1.15);
  box-shadow: 0 4px 12px var(--color-primary-shadow, rgba(99, 102, 241, 0.4));
}

.volume-slider::-moz-range-thumb {
  width: 20px;
  height: 20px;
  border: none;
  border-radius: 50%;
  background: var(--color-primary);
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
}
</style>

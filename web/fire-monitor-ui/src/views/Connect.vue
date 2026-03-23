<script setup lang="ts">
import { ref, onMounted } from 'vue';
import { useRouter } from 'vue-router';
import { Server, Monitor, ArrowRight, Loader, AlertCircle, Flame } from 'lucide-vue-next';
import { useConnectConfig } from '../composables/useConnectConfig';
import { useTheme } from '../composables/useTheme';

const router = useRouter();
const { loadConfig, saveConfig } = useConnectConfig();
const { theme } = useTheme();

// Form state
const deviceId = ref('');
const serverUrl = ref('');
const connecting = ref(false);
const error = ref('');

// Validation
const isValid = () => {
    if (!deviceId.value.trim()) {
        error.value = '请输入设备 ID';
        return false;
    }
    if (!serverUrl.value.trim()) {
        error.value = '请输入服务器地址';
        return false;
    }
    // Basic WebSocket URL validation
    const wsPattern = /^wss?:\/\/.+/;
    if (!wsPattern.test(serverUrl.value.trim())) {
        error.value = '服务器地址格式错误，应以 ws:// 或 wss:// 开头';
        return false;
    }
    return true;
};

// Connect to remote device
const handleConnect = async () => {
    if (!isValid()) return;

    connecting.value = true;
    error.value = '';

    try {
        // Save to localStorage
        saveConfig({
            deviceId: deviceId.value.trim(),
            serverUrl: serverUrl.value.trim()
        });

        // Build target URL: /#/device/:deviceId/dashboard?server=encoded_url
        const encodedServer = encodeURIComponent(serverUrl.value.trim());
        const targetPath = `/device/${deviceId.value.trim()}/dashboard?server=${encodedServer}`;

        // Navigate to dashboard
        router.push(targetPath);
    } catch (e: any) {
        error.value = e.message || '连接失败';
    } finally {
        connecting.value = false;
    }
};

// Enter local mode
const handleLocalMode = () => {
    router.push('/local/dashboard');
};

// Load saved config on mount
onMounted(() => {
    const config = loadConfig();
    deviceId.value = config.deviceId;
    serverUrl.value = config.serverUrl;
});
</script>

<template>
  <div class="min-h-screen flex flex-col items-center justify-center p-6"
       style="background: var(--theme-bg-primary);">
    <!-- Logo / Title -->
    <div class="text-center mb-8">
      <div class="w-20 h-20 mx-auto mb-4 rounded-3xl flex items-center justify-center"
           style="background: var(--theme-glass-bg); border: 1px solid var(--theme-glass-border);">
        <Flame class="w-10 h-10 text-orange-500" />
      </div>
      <h1 class="text-2xl font-bold text-text-primary">动火离人安全监测</h1>
      <p class="text-sm text-text-muted mt-1">远程设备连接</p>
    </div>

    <!-- Connection Form -->
    <div class="w-full max-w-sm space-y-4">
      <!-- Device ID Input -->
      <div class="space-y-1">
        <label class="text-xs text-text-muted ml-1">设备 ID <span class="text-red-400">*</span></label>
        <input
          v-model="deviceId"
          type="text"
          placeholder="例如: C206B6AF476AEE73"
          class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
          style="background: var(--theme-bg-input); border-color: var(--theme-border-input);"
          @keyup.enter="handleConnect"
        />
      </div>

      <!-- Server URL Input -->
      <div class="space-y-1">
        <label class="text-xs text-text-muted ml-1">服务器地址 <span class="text-red-400">*</span></label>
        <input
          v-model="serverUrl"
          type="text"
          placeholder="例如: ws://192.168.1.100:8086"
          class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
          style="background: var(--theme-bg-input); border-color: var(--theme-border-input);"
          @keyup.enter="handleConnect"
        />
        <p class="text-[10px] text-text-muted ml-1">
          WebSocket 地址，支持 ws:// 或 wss:// 协议
        </p>
      </div>

      <!-- Error Message -->
      <Transition name="slide-fade">
        <div v-if="error" class="flex items-center gap-2 p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
          <AlertCircle class="w-4 h-4 flex-shrink-0" />
          <span>{{ error }}</span>
        </div>
      </Transition>

      <!-- Connect Button -->
      <button
        @click="handleConnect"
        :disabled="connecting"
        class="w-full py-3.5 bg-primary hover:bg-primary-light text-white rounded-xl font-bold transition-all active:scale-95 disabled:opacity-50 flex items-center justify-center gap-2"
      >
        <Loader v-if="connecting" class="w-5 h-5 animate-spin" />
        <Server v-else class="w-5 h-5" />
        <span>{{ connecting ? '连接中...' : '连接设备' }}</span>
      </button>

      <!-- Divider -->
      <div class="flex items-center gap-3 py-2">
        <div class="flex-1 h-px" style="background: var(--theme-border-input);"></div>
        <span class="text-xs text-text-muted">或</span>
        <div class="flex-1 h-px" style="background: var(--theme-border-input);"></div>
      </div>

      <!-- Local Mode Button -->
      <button
        @click="handleLocalMode"
        class="w-full py-3.5 rounded-xl font-bold transition-all active:scale-95 flex items-center justify-center gap-2"
        style="background: var(--theme-bg-input); border: 1px solid var(--theme-border-input); color: var(--color-text-secondary);"
      >
        <Monitor class="w-5 h-5" />
        <span>本地模式</span>
        <ArrowRight class="w-4 h-4 ml-auto opacity-50" />
      </button>
    </div>

    <!-- Footer -->
    <p class="text-xs text-text-muted mt-8 opacity-60">
      DHLR 动火离人安全监测系统 v1.0
    </p>
  </div>
</template>

<style scoped>
.slide-fade-enter-active,
.slide-fade-leave-active {
  transition: all 0.2s ease;
}

.slide-fade-enter-from,
.slide-fade-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}
</style>

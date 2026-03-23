import { ref } from 'vue';

export interface ConnectConfig {
  deviceId: string;
  serverUrl: string;
}

const STORAGE_KEY = 'dhlr_connect_config';

export function useConnectConfig() {
  const config = ref<ConnectConfig>({
    deviceId: '',
    serverUrl: ''
  });

  const loadConfig = (): ConnectConfig => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const parsed = JSON.parse(saved);
        config.value = parsed;
        return parsed;
      }
    } catch (e) {
      console.error('Failed to load connect config:', e);
    }
    return { deviceId: '', serverUrl: '' };
  };

  const saveConfig = (newConfig: ConnectConfig) => {
    config.value = newConfig;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(newConfig));
  };

  const clearConfig = () => {
    config.value = { deviceId: '', serverUrl: '' };
    localStorage.removeItem(STORAGE_KEY);
  };

  return {
    config,
    loadConfig,
    saveConfig,
    clearConfig
  };
}
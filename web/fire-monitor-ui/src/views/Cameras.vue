<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';
import { Camera, Eye, Trash2, Plus, Settings } from 'lucide-vue-next';
import { ws } from '../api/ws';
import type { Camera as CameraType } from '../types';
import Modal from '../components/Modal.vue';
import Skeleton from '../components/Skeleton.vue';

const cameras = ref<CameraType[]>([]);
const loading = ref(true);
const refreshTimer = ref<ReturnType<typeof setInterval> | null>(null);

// Modal State
const showModal = ref(false);
const showPreviewModal = ref(false);
const isEditing = ref(false);
const usbDevices = ref<any[]>([]);
const scanningUsb = ref(false);

const form = ref<Partial<CameraType>>({
  id: '',
  name: '',
  type: 'usb',
  device: 0,
  rtsp_url: '',
  username: '',
  password: ''
});

const previewCameraName = ref('');
const previewCameraId = ref('');
const previewImage = ref('');
const previewLoading = ref(true);
let previewInterval: ReturnType<typeof setInterval> | null = null;

// Actions
const loadCameras = async () => {
  try {
    cameras.value = await ws.request<CameraType[]>('get_cameras');
  } catch (e) {
    console.error(e);
  } finally {
    loading.value = false;
  }
};

const loadUsbDevices = async () => {
  scanningUsb.value = true;
  try {
    usbDevices.value = await ws.request<any[]>('get_usb_devices');
  } catch (e) {
    console.error(e);
    usbDevices.value = [];
  } finally {
    scanningUsb.value = false;
  }
};

const openAddModal = async () => {
  isEditing.value = false;
  form.value = { type: 'usb', device: undefined, width: 640, height: 480, fps: 30, rtsp_url: 'rtsp://' };
  showModal.value = true;
  await loadUsbDevices();
  // 如果有设备，默认选择第一个设备
  if (usbDevices.value.length > 0) {
    form.value.device = usbDevices.value[0].index;
  }
};

const openEditModal = async (cam: CameraType) => {
  isEditing.value = true;
  form.value = { ...cam };
  showModal.value = true;
  if (cam.type === 'usb') {
    await loadUsbDevices();
  }
};

const generateUniqueId = () => {
  // 生成基于时间戳和随机数的唯一ID
  const timestamp = Date.now();
  const random = Math.floor(Math.random() * 1000);
  return `cam_${timestamp}_${random}`;
};

const submitForm = async () => {
  if (!form.value.name) return;

  try {
    if (isEditing.value) {
      await ws.request('update_camera', { camera_id: form.value.id, ...form.value });
    } else {
      // 自动生成唯一ID
      const cameraData = {
        ...form.value,
        id: generateUniqueId()
      };
      await ws.request('create_camera', cameraData);
    }
    showModal.value = false;
    await loadCameras();
  } catch (e) {
    console.error(e);
    alert('操作失败');
  }
};

const deleteCamera = async (id: string) => {
  if (!confirm('确定删除该摄像头?')) return;
  try {
    await ws.request('delete_camera', { camera_id: id });
    await loadCameras();
  } catch (e) {
    console.error(e);
    alert('删除失败');
  }
};

const loadPreviewImage = async (cameraId: string) => {
  try {
    const result = await ws.request<{ image: string }>('preview_camera', { camera_id: cameraId });
    previewImage.value = result.image;
    previewLoading.value = false;
  } catch (e) {
    console.error('预览加载失败:', e);
  }
};

const openPreview = (cam: CameraType) => {
  previewCameraId.value = cam.id;
  previewCameraName.value = cam.name;
  previewLoading.value = true;
  previewImage.value = '';
  showPreviewModal.value = true;

  // 立即加载第一帧
  loadPreviewImage(cam.id);

  if (previewInterval) clearInterval(previewInterval);
  previewInterval = setInterval(() => {
    loadPreviewImage(cam.id);
  }, 1000);
};

const closePreview = () => {
  showPreviewModal.value = false;
  if (previewInterval) clearInterval(previewInterval);
};

onMounted(async () => {
  await ws.connect();
  loadCameras();
  refreshTimer.value = setInterval(loadCameras, 5000);
});

onUnmounted(() => {
  if (refreshTimer.value) clearInterval(refreshTimer.value);
  if (previewInterval) clearInterval(previewInterval);
});
</script>

<template>
  <div class="space-y-6 pb-20 pt-6">
    <div class="flex items-center justify-between">
      <h2 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-text-primary to-text-secondary">
        摄像头管理</h2>
    </div>

    <!-- 使用单一 Transition 和 mode="out-in" 确保骨架屏先消失再显示内容 -->
    <Transition name="fade" mode="out-in">
      <!-- Loading Skeleton -->
      <div v-if="loading" key="skeleton" class="space-y-4">
        <Skeleton v-for="i in 3" :key="i" />
      </div>

      <!-- Empty State -->
      <div v-else-if="cameras.length === 0" key="empty"
        class="flex flex-col items-center justify-center p-12 text-gray-500 bg-bg-card rounded-2xl border border-white/5">
        <Camera class="w-12 h-12 mb-4 opacity-50" />
        <p>暂无摄像头</p>
      </div>

      <!-- Camera List with Animation -->
      <div v-else key="content" class="space-y-4">
        <TransitionGroup name="list" tag="div" class="space-y-4 relative">
          <div v-for="(cam, index) in cameras" :key="cam.id"
            class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] p-4 rounded-2xl flex items-center gap-4 transition-all hover:border-white/20 hover-lift shadow-[0_8px_32px_var(--theme-shadow)]"
            :style="{ animationDelay: `${index * 0.05}s` }">

            <div class="flex-1 min-w-0">
              <div class="flex items-center justify-between mb-1">
                <h3 class="font-bold text-text-primary truncate text-base">{{ cam.name }}</h3>
                <div class="flex items-center gap-1.5 shrink-0">
                  <Transition name="pop" mode="out-in">
                    <span :key="cam.status" class="w-2 h-2 rounded-full transition-all duration-300"
                      :class="cam.status === 'online' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-red-500'">
                    </span>
                  </Transition>
                  <Transition name="slide-fade" mode="out-in">
                    <span :key="cam.status" class="text-[10px] font-bold uppercase tracking-wider"
                      :class="cam.status === 'online' ? 'text-emerald-500' : 'text-red-500'">
                      {{ cam.status === 'online' ? '在线' : '离线' }}
                    </span>
                  </Transition>
                </div>
              </div>
              <div class="text-xs text-text-muted font-mono truncate opacity-60 flex items-center gap-2">
                <Camera class="w-3 h-3" />
                {{ cam.type }} (ID: {{ cam.id }})
              </div>
            </div>

            <div class="flex gap-2 shrink-0">
              <button @click="openPreview(cam)"
                class="p-2.5 rounded-xl text-blue-400 hover:text-blue-300 hover:bg-blue-500/10 transition-all border border-blue-500/30 press-effect"
                style="background: rgba(59, 130, 246, 0.1);">
                <Eye class="w-4 h-4" />
              </button>
              <button @click="openEditModal(cam)"
                class="p-2.5 rounded-xl text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 transition-all border border-amber-500/30 press-effect"
                style="background: rgba(245, 158, 11, 0.1);">
                <Settings class="w-4 h-4" />
              </button>
              <button @click="deleteCamera(cam.id)"
                class="p-2.5 rounded-xl text-red-400 hover:text-red-300 hover:bg-red-500/10 transition-all border border-red-500/30 press-effect"
                style="background: rgba(239, 68, 68, 0.1);">
                <Trash2 class="w-4 h-4" />
              </button>
            </div>
          </div>
        </TransitionGroup>
      </div>
    </Transition>

    <!-- Floating Action Button with Animation -->
    <Teleport to="#portal-target" defer>
      <Transition name="pop">
        <div v-if="!loading" class="absolute bottom-24 right-6 pointer-events-auto">
          <button @click="openAddModal"
            class="w-14 h-14 bg-gradient-to-br from-indigo-500 to-blue-600 text-white rounded-2xl flex items-center justify-center shadow-[0_8px_25px_rgba(79,70,229,0.4)] hover:shadow-[0_10px_30px_rgba(79,70,229,0.5)] active:scale-95 transition-all border border-white/10 group hover-glow">
            <Plus class="w-7 h-7 group-hover:rotate-90 transition-transform duration-300" />
          </button>
        </div>
      </Transition>
    </Teleport>



    <!-- Add/Edit Modal -->
    <Modal :title="isEditing ? '编辑摄像头' : '添加摄像头'" :is-open="showModal" @close="showModal = false">
      <div class="space-y-4">
        <div class="space-y-1">
          <label class="text-xs text-text-muted">名称</label>
          <input v-model="form.name" type="text" placeholder="如: 厨房摄像头1"
            class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
            style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
        </div>
        <div class="space-y-1">
          <label class="text-xs text-text-muted">类型</label>
          <div class="relative">
            <select v-model="form.type"
              class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all appearance-none cursor-pointer text-text-primary"
              style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              <option value="usb" class="theme-select-option">USB摄像头</option>
              <option value="rtsp" class="theme-select-option">IP摄像头(RTSP)</option>
            </select>
            <div class="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none opacity-50">▼</div>
          </div>
        </div>

        <Transition name="slide-fade" mode="out-in">
          <div v-if="form.type === 'usb'" key="usb" class="space-y-1">
            <label class="text-xs text-text-muted">选择设备</label>
            <div v-if="scanningUsb" class="text-xs text-warning animate-pulse py-3">正在扫描设备...</div>
            <div v-else-if="usbDevices.length === 0" class="text-xs text-red-400 py-3">未检测到USB摄像头设备</div>
            <div v-else class="relative">
              <select v-model="form.device"
                class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all appearance-none cursor-pointer text-text-primary"
                style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                <option v-for="dev in usbDevices" :key="dev.index" :value="dev.index" class="theme-select-option">{{
                  dev.name }}</option>
              </select>
              <div class="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none opacity-50">▼</div>
            </div>
          </div>

          <div v-else-if="form.type === 'rtsp'" key="rtsp" class="space-y-2">
            <div class="space-y-1">
              <label class="text-xs text-text-muted">RTSP地址</label>
              <input v-model="form.rtsp_url" type="text" placeholder="rtsp://192.168.1.100:554/stream"
                class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary font-mono text-sm"
                style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              <p class="text-[10px] text-text-muted opacity-60">示例: rtsp://192.168.1.100:554/live/main</p>
            </div>
            <div class="grid grid-cols-2 gap-2">
              <div class="space-y-1">
                <label class="text-xs text-text-muted">用户名</label>
                <input v-model="form.username" type="text"
                  class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
                  style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              </div>
              <div class="space-y-1">
                <label class="text-xs text-text-muted">密码</label>
                <input v-model="form.password" type="password"
                  class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary"
                  style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              </div>
            </div>
          </div>
        </Transition>

        <button @click="submitForm"
          class="w-full py-3 bg-primary rounded-xl font-bold mt-4 hover:bg-primary-dark shadow-lg shadow-primary/20 transition-all active:scale-95 press-effect">
          {{ isEditing ? '保存' : '添加' }}
        </button>
      </div>
    </Modal>

    <!-- Preview Modal -->
    <Modal :title="previewCameraName" :is-open="showPreviewModal" @close="closePreview">
      <div class="bg-black aspect-video rounded-lg overflow-hidden relative">
        <!-- Loading Spinner -->
        <Transition name="fade">
          <div v-if="previewLoading && !previewImage" class="absolute inset-0 flex items-center justify-center">
            <div class="w-10 h-10 border-3 border-white/20 border-t-primary rounded-full animate-spin"></div>
          </div>
        </Transition>
        <img v-if="previewImage" :src="previewImage"
          class="w-full h-full object-contain transition-opacity duration-300" alt="摄像头预览">
      </div>
    </Modal>
  </div>
</template>

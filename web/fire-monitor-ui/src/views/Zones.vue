<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue';
import { CookingPot, Trash2, Pencil, Plus } from 'lucide-vue-next';
import { ws } from '../api/ws';
import { api, ApiError } from '../api';
import type { ZoneConfig, Camera } from '../types';
import Modal from '../components/Modal.vue';
import Skeleton from '../components/Skeleton.vue';

const zones = ref<ZoneConfig[]>([]);
const cameras = ref<Camera[]>([]);
const loading = ref(true);

const showAddModal = ref(false);
const showRoiModal = ref(false);

const addForm = ref({ name: '', camera_id: '' });

// ROI Editor State
const currentRoiZone = ref<ZoneConfig | null>(null);
const roiCanvas = ref<HTMLCanvasElement | null>(null);
const roiPoints = ref<number[][]>([]);
const roiImage = ref('');

// Actions
const loadData = async () => {
    try {
        const [z, c] = await Promise.all([
            ws.request<ZoneConfig[]>('get_zones'),
            ws.request<Camera[]>('get_cameras')
        ]);
        zones.value = z;
        cameras.value = c;
    } catch (e) {
        console.error(e);
    } finally {
        loading.value = false;
    }
};

const deleteZone = async (id: string) => {
    if(!confirm('确定删除该灶台配置？')) return;
    
    try {
        await ws.request('delete_zone', { zone_id: id });
        await loadData();
    } catch (e) {
        const msg = e instanceof Error ? e.message : '删除失败';
        alert(msg);
    }
};

const toggleZone = async (zone: ZoneConfig) => {
    try {
        await ws.request('update_zone', { zone_id: zone.id, enabled: zone.enabled });
    } catch (e) {
        const msg = e instanceof Error ? e.message : '更新状态失败';
        alert(msg);
        // 恢复原状态
        zone.enabled = !zone.enabled;
    }
};

const submitAdd = async () => {
    if(!addForm.value.name || !addForm.value.camera_id) {
        alert('请填写灶台名称并选择摄像头');
        return;
    }
    try {
        await ws.request('create_zone', addForm.value);
        showAddModal.value = false;
        addForm.value = { name: '', camera_id: '' };
        await loadData();
    } catch(e: unknown) {
        const msg = e instanceof Error ? e.message : '添加失败';
        alert(msg);
    }
};

// ROI Logic
const openRoiEditor = (zone: ZoneConfig) => {
    currentRoiZone.value = zone;
    roiPoints.value = zone.roi || [];
    showRoiModal.value = true;
    
    // 先设置图片URL，然后等待模态框渲染后初始化canvas
    const imageUrl = `/cameras/${zone.camera_id}/preview?t=${Date.now()}`;
    roiImage.value = imageUrl;
    
    // 预加载图片，确保图片加载完成后再初始化canvas
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = imageUrl;
    img.onload = () => {
        // 等待模态框渲染完成
        setTimeout(() => {
            initCanvas(img);
        }, 150);
    };
    img.onerror = () => {
        console.error('Failed to load camera preview image');
    };
};

// 缓存预加载的图片
const cachedImage = ref<HTMLImageElement | null>(null);

const initCanvas = (preloadedImg?: HTMLImageElement) => {
    const canvas = roiCanvas.value;
    if(!canvas) return;
    const ctx = canvas.getContext('2d');
    if(!ctx) return;
    
    if (preloadedImg) {
        // 使用预加载的图片
        cachedImage.value = preloadedImg;
        canvas.width = preloadedImg.naturalWidth || 640;
        canvas.height = preloadedImg.naturalHeight || 480;
        drawRoi();
    } else {
        // 重新加载图片
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.src = roiImage.value;
        img.onload = () => {
            cachedImage.value = img;
            canvas.width = img.naturalWidth || 640;
            canvas.height = img.naturalHeight || 480;
            drawRoi();
        };
    }
};

const drawRoi = () => {
    const canvas = roiCanvas.value;
    if(!canvas) return;
    const ctx = canvas.getContext('2d');
    if(!ctx) return;
    
    // 使用缓存的图片绘制背景
    if (cachedImage.value && cachedImage.value.complete) {
        ctx.drawImage(cachedImage.value, 0, 0, canvas.width, canvas.height);
    } else {
        // 如果缓存图片不可用，尝试重新加载
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.src = roiImage.value;
        img.onload = () => {
            cachedImage.value = img;
            ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
            // 重新绘制ROI点
            drawRoiPoints(ctx, canvas);
        };
        return; // 等待图片加载
    }
    
    drawRoiPoints(ctx, canvas);
};

const drawRoiPoints = (ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement) => {
    
    // Draw ROI
    if(roiPoints.value.length > 0) {
        ctx.beginPath();
        ctx.strokeStyle = '#27ae60';
        ctx.fillStyle = 'rgba(39, 174, 96, 0.3)';
        ctx.lineWidth = 8; // 加粗边框

        roiPoints.value.forEach((p, i) => {
            const px = p[0];
            const py = p[1];
            if (px === undefined || py === undefined) return;

            const x = px * canvas.width;
            const y = py * canvas.height;
            if(i===0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        
        if(roiPoints.value.length > 2) ctx.closePath();
        ctx.fill();
        ctx.stroke();
        
        // Draw points with order numbers
        roiPoints.value.forEach((p, i) => {
             const px = p[0];
             const py = p[1];
             if (px === undefined || py === undefined) return;
             
            const x = px * canvas.width;
            const y = py * canvas.height;
            const pointRadius = 30; // 加大点位半径
            
            // 绘制点位圆圈（带白色边框，更醒目）
            ctx.beginPath();
            ctx.fillStyle = '#27ae60';
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 5;
            ctx.arc(x, y, pointRadius, 0, Math.PI * 2);
            ctx.fill();
            ctx.stroke();
            
            // 绘制顺序号
            ctx.fillStyle = '#ffffff';
            ctx.font = 'bold 40px Arial';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText((i + 1).toString(), x, y);
        });
    }
};

const onCanvasClick = (e: MouseEvent) => {
    const canvas = roiCanvas.value;
    if(!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    
    roiPoints.value.push([x, y]);
    drawRoi();
};

const clearRoi = () => {
    roiPoints.value = [];
    // 如果缓存图片不存在，重新加载
    if (!cachedImage.value || !cachedImage.value.complete) {
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.src = roiImage.value;
        img.onload = () => {
            cachedImage.value = img;
            initCanvas(img);
        };
    } else {
        drawRoi();
    }
};

const saveRoi = async () => {
    if(!currentRoiZone.value) return;
    try {
        await ws.request('update_zone', { zone_id: currentRoiZone.value.id, roi: roiPoints.value });
        showRoiModal.value = false;
        await loadData();
    } catch(e) { 
        const msg = e instanceof Error ? e.message : '保存失败';
        alert(msg); 
    }
};

onMounted(loadData);
</script>

<template>
  <div class="space-y-6 pb-20 pt-6">
    <div class="flex items-center justify-between">
      <h2 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-text-primary to-text-secondary">灶台配置</h2>
    </div>

    <!-- 使用单一 Transition 和 mode="out-in" 确保骨架屏先消失再显示内容 -->
    <Transition name="fade" mode="out-in">
      <!-- Loading Skeleton -->
      <div v-if="loading" key="skeleton" class="space-y-4">
        <Skeleton v-for="i in 3" :key="i" />
      </div>

      <!-- Empty State -->
      <div v-else-if="zones.length === 0" key="empty" class="flex flex-col items-center justify-center p-12 glass-panel rounded-2xl">
        <div class="text-4xl mb-4 grayscale opacity-50">🔥</div>
        <p class="text-text-muted">暂无灶台配置</p>
      </div>

      <!-- Zone List with Animation -->
      <div v-else key="content" class="space-y-4">
        <TransitionGroup name="list" tag="div" class="space-y-4 relative">
          <div v-for="(zone, index) in zones" :key="zone.id" 
            class="glass-panel p-5 rounded-3xl flex items-center justify-between group transition-all hover:border-white/20 hover-lift"
            :style="{ animationDelay: `${index * 0.05}s` }"
          >
            
            <div class="flex items-center gap-4">
               <div class="w-12 h-12 rounded-2xl flex items-center justify-center bg-gradient-to-br from-orange-500/20 to-red-500/20 text-orange-400 border border-white/5">
                  <CookingPot class="w-6 h-6" />
               </div>
               <div>
                  <h3 class="font-bold text-text-primary tracking-wide text-lg">{{ zone.name }}</h3>
                  <div class="flex items-center gap-2 mt-1">
                     <span class="text-xs text-text-muted font-mono uppercase px-1.5 py-0.5 rounded" style="background: var(--theme-bg-input);">{{ zone.id }}</span>
                     <span class="w-1 h-1 rounded-full bg-text-muted"></span>
                     <span class="text-xs text-text-muted">{{ zone.camera_id }}</span>
                  </div>
               </div>
            </div>

            <div class="flex items-center gap-2">
               <div class="mr-4 flex flex-col items-end">
                 <Transition name="pop" mode="out-in">
                   <span :key="zone.enabled ? 'active' : 'disabled'" class="text-[10px] font-bold uppercase tracking-wider mb-1" :class="zone.enabled ? 'text-emerald-500' : 'text-text-muted'">
                      {{ zone.enabled ? 'ACTIVE' : 'DISABLED' }}
                   </span>
                 </Transition>
                 <label class="relative inline-flex items-center cursor-pointer">
                   <input type="checkbox" v-model="zone.enabled" class="sr-only peer" @change="toggleZone(zone)">
                   <div class="w-9 h-5 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-500"></div>
                 </label>
               </div>
               
               <button @click="openRoiEditor(zone)" class="p-2.5 rounded-xl text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 transition-all border border-amber-500/30 press-effect" style="background: rgba(245, 158, 11, 0.1);" title="编辑区域">
                  <Pencil class="w-4 h-4" />
               </button>
               <button @click="deleteZone(zone.id)" class="p-2.5 rounded-xl text-red-400 hover:text-red-300 hover:bg-red-500/10 transition-all border border-red-500/30 press-effect" style="background: rgba(239, 68, 68, 0.1);" title="删除">
                  <Trash2 class="w-4 h-4" />
               </button>
            </div>
          </div>
        </TransitionGroup>
      </div>
    </Transition>

    <!-- Floating Action Button with Animation -->
    <Teleport to="#portal-target">
      <Transition name="pop">
        <div v-if="!loading" class="absolute bottom-24 right-6 pointer-events-auto">
            <button @click="showAddModal = true" class="w-14 h-14 bg-gradient-to-br from-indigo-500 to-blue-600 text-white rounded-2xl flex items-center justify-center shadow-[0_8px_25px_rgba(79,70,229,0.4)] hover:shadow-[0_10px_30px_rgba(79,70,229,0.5)] active:scale-95 transition-all border border-white/10 group hover-glow">
              <Plus class="w-7 h-7 group-hover:rotate-90 transition-transform duration-300" />
            </button>
        </div>
      </Transition>
    </Teleport>

    <!-- Add Modal -->
    <Modal title="添加灶台" :is-open="showAddModal" @close="showAddModal = false">
        <div class="space-y-4">
            <div class="space-y-1">
                <label class="text-xs text-text-muted">名称</label>
                <input v-model="addForm.name" type="text" placeholder="如: 1号灶台" class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
            </div>
            <div class="space-y-1">
                <label class="text-xs text-text-muted">关联摄像头</label>
                <div class="relative">
                    <select v-model="addForm.camera_id" class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all appearance-none cursor-pointer text-text-primary" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                        <option v-for="cam in cameras" :key="cam.id" :value="cam.id" class="theme-select-option">{{ cam.name }}</option>
                    </select>
                    <!-- Chevron Icon -->
                    <div class="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none opacity-50">▼</div>
                </div>
            </div>
            <button @click="submitAdd" class="w-full py-3 bg-primary rounded-xl font-bold mt-4 hover:bg-primary-dark shadow-lg shadow-primary/20 transition-all active:scale-95 press-effect">
                添加
            </button>
        </div>
    </Modal>

    <!-- ROI Editor Modal -->
    <Modal title="编辑 ROI 区域" :is-open="showRoiModal" @close="showRoiModal = false">
        <div class="space-y-4">
            <div class="relative bg-black rounded-xl overflow-hidden select-none touch-none border border-white/10 aspect-video flex items-center justify-center">
                <Transition name="fade">
                  <div v-if="!roiImage" class="text-text-muted text-xs animate-pulse">加载画面中...</div>
                </Transition>
                <canvas ref="roiCanvas" @click="onCanvasClick" class="max-w-full max-h-full block cursor-crosshair"></canvas>
            </div>
            <p class="text-xs text-text-muted text-center py-2 rounded-lg" style="background: var(--theme-bg-input);">点击画面添加顶点，形成封闭区域</p>
            <div class="flex gap-3">
                <button @click="clearRoi" class="flex-1 py-2.5 rounded-xl text-sm font-medium transition-all text-text-primary press-effect" style="background: var(--theme-bg-input);">清除</button>
                <button @click="saveRoi" class="flex-1 py-2.5 bg-primary hover:bg-primary-light rounded-xl text-sm font-bold shadow-lg shadow-primary/20 transition-all press-effect">保存</button>
            </div>
        </div>
    </Modal>
  </div>
</template>

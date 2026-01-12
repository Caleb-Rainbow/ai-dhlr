<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue';
import { CookingPot, Trash2, Pencil, Plus } from 'lucide-vue-next';
import { ws } from '../api/ws';
import type { ZoneConfig, Camera } from '../types';
import Modal from '../components/Modal.vue';
import Skeleton from '../components/Skeleton.vue';

const zones = ref<ZoneConfig[]>([]);
const cameras = ref<Camera[]>([]);
const loading = ref(true);

const showAddModal = ref(false);
const showEditModal = ref(false);

const addForm = ref({ name: '', camera_id: '', serial_index: 0, fire_current_threshold: 100 });

// Edit Zone State
const currentEditZone = ref<ZoneConfig | null>(null);
const editForm = ref({ name: '', camera_id: '', serial_index: 0, fire_current_threshold: 100 });
const originalZoneName = ref(''); // 用于检测名称是否变化

// ROI Editor State
const roiCanvas = ref<HTMLCanvasElement | null>(null);
const roiPoints = ref<number[][]>([]);
const roiImage = ref('');

// Drag State
const selectedPointIndex = ref<number | null>(null);
const isDragging = ref(false);

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
        addForm.value = { name: '', camera_id: '', serial_index: 0, fire_current_threshold: 100 };
        await loadData();
    } catch(e: unknown) {
        const msg = e instanceof Error ? e.message : '添加失败';
        alert(msg);
    }
};

// Edit Zone Logic
const openEditZone = (zone: ZoneConfig) => {
    currentEditZone.value = zone;
    editForm.value = {
        name: zone.name,
        camera_id: zone.camera_id,
        serial_index: zone.serial_index || 0,
        fire_current_threshold: zone.fire_current_threshold || 100
    };
    originalZoneName.value = zone.name; // 保存原始名称用于比较
    roiPoints.value = zone.roi || [];
    showEditModal.value = true;
    
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

// 当摄像头变化时刷新预览
const onCameraChange = () => {
    if (!editForm.value.camera_id) return;
    
    const imageUrl = `/cameras/${editForm.value.camera_id}/preview?t=${Date.now()}`;
    roiImage.value = imageUrl;
    roiPoints.value = []; // 切换摄像头时清空ROI
    
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.src = imageUrl;
    img.onload = () => {
        initCanvas(img);
    };
};

// 缓存预加载的图片
const cachedImage = ref<HTMLImageElement | null>(null);

const initCanvas = (preloadedImg?: HTMLImageElement) => {
    const canvas = roiCanvas.value;
    if(!canvas) return;
    const ctx = canvas.getContext('2d');
    if(!ctx) return;
    
    // 移除现有的事件监听器，避免重复添加
    canvas.removeEventListener('mousedown', onMouseDown);
    canvas.removeEventListener('mousemove', onMouseMove);
    canvas.removeEventListener('mouseup', onMouseUp);
    canvas.removeEventListener('mouseleave', onMouseUp);
    canvas.removeEventListener('touchstart', onTouchStart);
    canvas.removeEventListener('touchmove', onTouchMove);
    canvas.removeEventListener('touchend', onTouchEnd);
    canvas.removeEventListener('touchcancel', onTouchEnd);
    
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
    
    // 添加事件监听器
    canvas.addEventListener('mousedown', onMouseDown);
    canvas.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mouseup', onMouseUp);
    canvas.addEventListener('mouseleave', onMouseUp);
    canvas.addEventListener('touchstart', onTouchStart, { passive: false });
    canvas.addEventListener('touchmove', onTouchMove, { passive: false });
    canvas.addEventListener('touchend', onTouchEnd);
    canvas.addEventListener('touchcancel', onTouchEnd);
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
            const isSelected = i === selectedPointIndex.value;
            
            // 绘制点位圆圈（带白色边框，更醒目）
            ctx.beginPath();
            ctx.fillStyle = isSelected ? '#3b82f6' : '#27ae60'; // 选中的点使用蓝色
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = isSelected ? 6 : 5; // 选中的点边框更粗
            ctx.arc(x, y, isSelected ? pointRadius + 5 : pointRadius, 0, Math.PI * 2); // 选中的点更大
            ctx.fill();
            ctx.stroke();
            
            // 绘制顺序号
            ctx.fillStyle = '#ffffff';
            ctx.font = isSelected ? 'bold 45px Arial' : 'bold 40px Arial'; // 选中的点数字更大
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillText((i + 1).toString(), x, y);
        });
    }
};

// 获取Canvas上的相对坐标
const getCanvasCoordinates = (canvas: HTMLCanvasElement, clientX: number, clientY: number) => {
    const rect = canvas.getBoundingClientRect();
    const x = (clientX - rect.left) / rect.width;
    const y = (clientY - rect.top) / rect.height;
    return { x, y };
};

// 限制坐标在边界范围内 [0, 1]
const clampCoordinates = (x: number, y: number) => {
    return {
        x: Math.max(0, Math.min(1, x)),
        y: Math.max(0, Math.min(1, y))
    };
};

// 鼠标事件处理
const onMouseDown = (e: MouseEvent) => {
    const canvas = roiCanvas.value;
    if(!canvas) return;
    
    const { x, y } = getCanvasCoordinates(canvas, e.clientX, e.clientY);
    const nearestIndex = findNearestPoint(x, y);
    
    if (nearestIndex !== null) {
        selectedPointIndex.value = nearestIndex;
        isDragging.value = true;
        // 防止默认行为，避免拖动时选中文本
        e.preventDefault();
        drawRoi();
    }
};

const onMouseMove = (e: MouseEvent) => {
    if (!isDragging.value || selectedPointIndex.value === null) return;
    
    const canvas = roiCanvas.value;
    if(!canvas) return;
    
    const { x, y } = getCanvasCoordinates(canvas, e.clientX, e.clientY);
    
    // 限制坐标在边界范围内
    const clamped = clampCoordinates(x, y);
    
    // 更新选中点位的坐标
    roiPoints.value[selectedPointIndex.value] = [clamped.x, clamped.y];
    drawRoi();
    
    // 防止默认行为，避免拖动时选中文本
    e.preventDefault();
};

const onMouseUp = () => {
    isDragging.value = false;
    selectedPointIndex.value = null;
    drawRoi();
};

const onCanvasClick = (e: MouseEvent) => {
    // 只有在没有拖动时才添加新点位
    if (isDragging.value) return;
    
    const canvas = roiCanvas.value;
    if(!canvas) return;
    
    const { x, y } = getCanvasCoordinates(canvas, e.clientX, e.clientY);
    
    // 限制坐标在边界范围内
    const clamped = clampCoordinates(x, y);
    const nearestIndex = findNearestPoint(clamped.x, clamped.y);
    
    // 如果点击位置没有接近现有点位，则添加新点位
    if (nearestIndex === null) {
        roiPoints.value.push([clamped.x, clamped.y]);
        drawRoi();
    }
};

// 触摸事件处理
const onTouchStart = (e: TouchEvent) => {
    if (e.touches.length !== 1) return; // 只处理单点触摸
    
    const canvas = roiCanvas.value;
    if(!canvas) return;
    
    const touch = e.touches[0];
    if (!touch) return;
    
    const { x, y } = getCanvasCoordinates(canvas, touch.clientX, touch.clientY);
    const nearestIndex = findNearestPoint(x, y);
    
    if (nearestIndex !== null) {
        selectedPointIndex.value = nearestIndex;
        isDragging.value = true;
        // 防止默认行为，避免拖动时页面滚动
        e.preventDefault();
        drawRoi();
    }
};

const onTouchMove = (e: TouchEvent) => {
    if (e.touches.length !== 1) return; // 只处理单点触摸
    if (!isDragging.value || selectedPointIndex.value === null) return;
    
    const canvas = roiCanvas.value;
    if(!canvas) return;
    
    const touch = e.touches[0];
    if (!touch) return;
    
    const { x, y } = getCanvasCoordinates(canvas, touch.clientX, touch.clientY);
    
    // 限制坐标在边界范围内
    const clamped = clampCoordinates(x, y);
    
    // 更新选中点位的坐标
    roiPoints.value[selectedPointIndex.value] = [clamped.x, clamped.y];
    drawRoi();
    
    // 防止默认行为，避免拖动时页面滚动
    e.preventDefault();
};

const onTouchEnd = () => {
    isDragging.value = false;
    selectedPointIndex.value = null;
    drawRoi();
};

const clearRoi = () => {
    roiPoints.value = [];
    selectedPointIndex.value = null;
    isDragging.value = false;
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

const undoRoiPoint = () => {
    if (roiPoints.value.length > 0) {
        roiPoints.value.pop();
        selectedPointIndex.value = null;
        isDragging.value = false;
        drawRoi();
    }
};

const findNearestPoint = (x: number, y: number, threshold: number = 0.05): number | null => {
    let nearestIndex: number | null = null;
    let minDistance = Infinity;
    
    for (let i = 0; i < roiPoints.value.length; i++) {
        const point = roiPoints.value[i];
        if (!point || point.length < 2) continue;
        
        // 使用类型断言确保TypeScript知道point[0]和point[1]是数字
        const px = point[0] as number;
        const py = point[1] as number;
        
        const dx = px - x;
        const dy = py - y;
        const distance = Math.sqrt(dx * dx + dy * dy);
        
        if (distance < minDistance && distance < threshold) {
            minDistance = distance;
            nearestIndex = i;
        }
    }
    
    return nearestIndex;
};

const saveEditZone = async () => {
    if(!currentEditZone.value) return;
    if(!editForm.value.name) {
        alert('灶台名称不能为空');
        return;
    }
    if(!editForm.value.camera_id) {
        alert('请选择关联摄像头');
        return;
    }
    
    try {
        // 检测名称是否变化
        const nameChanged = editForm.value.name !== originalZoneName.value;
        
        await ws.request('update_zone', { 
            zone_id: currentEditZone.value.id, 
            name: editForm.value.name,
            camera_id: editForm.value.camera_id,
            serial_index: editForm.value.serial_index,
            fire_current_threshold: editForm.value.fire_current_threshold,
            roi: roiPoints.value,
            regenerate_voice: nameChanged // 告诉后端是否需要重新合成语音
        });
        showEditModal.value = false;
        await loadData();
    } catch(e) { 
        const msg = e instanceof Error ? e.message : '保存失败';
        alert(msg); 
    }
};

onMounted(loadData);

// 清理函数：移除canvas事件监听器
const cleanupCanvas = () => {
    const canvas = roiCanvas.value;
    if (canvas) {
        canvas.removeEventListener('mousedown', onMouseDown);
        canvas.removeEventListener('mousemove', onMouseMove);
        canvas.removeEventListener('mouseup', onMouseUp);
        canvas.removeEventListener('mouseleave', onMouseUp);
        canvas.removeEventListener('touchstart', onTouchStart);
        canvas.removeEventListener('touchmove', onTouchMove);
        canvas.removeEventListener('touchend', onTouchEnd);
        canvas.removeEventListener('touchcancel', onTouchEnd);
    }
};

// 监听模态框关闭，清理canvas事件
watch(showEditModal, (isOpen) => {
    if (!isOpen) {
        cleanupCanvas();
    }
});

onUnmounted(() => {
    cleanupCanvas();
});
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
      <div v-else-if="zones.length === 0" key="empty" class="flex flex-col items-center justify-center p-12 backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] rounded-2xl shadow-[0_8px_32px_var(--theme-shadow)] transition-all">
        <div class="text-4xl mb-4 grayscale opacity-50">🔥</div>
        <p class="text-text-muted">暂无灶台配置</p>
      </div>

      <!-- Zone List with Animation -->
      <div v-else key="content" class="space-y-4">
        <TransitionGroup name="list" tag="div" class="space-y-4 relative">
          <div v-for="(zone, index) in zones" :key="zone.id" 
            class="backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] p-5 rounded-3xl flex items-center justify-between group transition-all hover:border-white/20 hover-lift shadow-[0_8px_32px_var(--theme-shadow)]"
            :style="{ animationDelay: `${index * 0.05}s` }"
          >
            
            <div class="flex items-center gap-4">
               <div class="w-12 h-12 rounded-2xl flex items-center justify-center bg-gradient-to-br from-orange-500/20 to-red-500/20 text-orange-400 border border-white/5">
                  <CookingPot class="w-6 h-6" />
               </div>
                <div>
                   <div class="flex items-center gap-3">
                      <h3 class="font-bold text-text-primary tracking-wide text-lg">{{ zone.name }}</h3>
                      <label class="relative inline-flex items-center cursor-pointer">
                        <input type="checkbox" v-model="zone.enabled" class="sr-only peer" @change="toggleZone(zone)">
                        <div class="w-8 h-4 bg-gray-700 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-emerald-500"></div>
                      </label>
                      <Transition name="pop" mode="out-in">
                        <span :key="zone.enabled ? 'active' : 'disabled'" class="text-[9px] font-bold uppercase tracking-wider" :class="zone.enabled ? 'text-emerald-500' : 'text-text-muted'">
                           {{ zone.enabled ? 'ON' : 'OFF' }}
                        </span>
                      </Transition>
                   </div>
                   <div class="flex items-center gap-2 mt-1">
                      <span class="text-xs text-text-muted">相机ID: {{ zone.camera_id }}</span>
                      <span class="w-1 h-1 rounded-full bg-text-muted"></span>
                      <span class="text-xs text-amber-400">阈值: {{ ((zone.fire_current_threshold || 100) / 100).toFixed(2) }}A</span>
                   </div>
                </div>
             </div>

             <div class="flex items-center gap-2">
               
               <button @click="openEditZone(zone)" class="p-2.5 rounded-xl text-amber-400 hover:text-amber-300 hover:bg-amber-500/10 transition-all border border-amber-500/30 press-effect" style="background: rgba(245, 158, 11, 0.1);" title="编辑灶台">
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
    <Teleport to="#portal-target" defer>
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
            <div class="space-y-1">
                <label class="text-xs text-text-muted">串口分区索引</label>
                <input v-model.number="addForm.serial_index" type="number" min="0" placeholder="从0开始" class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                <p class="text-xs text-text-muted mt-1">对应硬件接线顺序，索引0对应地址0x01</p>
            </div>
            <div class="space-y-1">
                <label class="text-xs text-text-muted">动火电流阈值</label>
                <input v-model.number="addForm.fire_current_threshold" type="number" min="0" placeholder="如: 145" class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                <p class="text-xs text-text-muted mt-1">145 表示 1.45A，实时电流超过此值则判定为动火</p>
            </div>
            <button @click="submitAdd" class="w-full py-3 bg-primary rounded-xl font-bold mt-4 hover:bg-primary-dark shadow-lg shadow-primary/20 transition-all active:scale-95 press-effect">
                添加
            </button>
        </div>
    </Modal>

    <!-- Edit Zone Modal -->
    <Modal title="编辑灶台" :is-open="showEditModal" @close="showEditModal = false">
        <div class="space-y-4">
            <!-- 名称 -->
            <div class="space-y-1">
                <label class="text-xs text-text-muted">名称</label>
                <input v-model="editForm.name" type="text" placeholder="如: 1号灶台" class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                <p v-if="editForm.name !== originalZoneName" class="text-xs text-amber-400 flex items-center gap-1">
                    <span>⚠️</span> 名称已修改，保存后将重新合成语音文件
                </p>
            </div>
            <!-- 关联摄像头 -->
            <div class="space-y-1">
                <label class="text-xs text-text-muted">关联摄像头</label>
                <div class="relative">
                    <select v-model="editForm.camera_id" @change="onCameraChange" class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all appearance-none cursor-pointer text-text-primary" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                        <option v-for="cam in cameras" :key="cam.id" :value="cam.id" class="theme-select-option">{{ cam.name }}</option>
                    </select>
                    <div class="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none opacity-50">▼</div>
                </div>
            </div>
            <!-- 串口索引和电流阈值 -->
            <div class="grid grid-cols-2 gap-3">
                <div class="space-y-1">
                    <label class="text-xs text-text-muted">串口索引</label>
                    <input v-model.number="editForm.serial_index" type="number" min="0" placeholder="0" class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                </div>
                <div class="space-y-1">
                    <label class="text-xs text-text-muted">电流阈值</label>
                    <input v-model.number="editForm.fire_current_threshold" type="number" min="0" placeholder="100" class="w-full rounded-xl px-4 py-3 border outline-none focus:border-primary/50 transition-all text-text-primary" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
                </div>
            </div>
            <p class="text-xs text-text-muted">串口索引对应硬件接线顺序 (0=0x01)；电流阈值如 145 表示 1.45A</p>
            
            <!-- ROI 区域编辑 -->
            <div class="space-y-2">
                <label class="text-xs text-text-muted">ROI 区域</label>
                <div class="relative bg-black rounded-xl overflow-hidden select-none touch-none border border-white/10 aspect-video flex items-center justify-center">
                    <Transition name="fade">
                      <div v-if="!roiImage" class="text-text-muted text-xs animate-pulse">加载画面中...</div>
                    </Transition>
                    <canvas ref="roiCanvas" @click="onCanvasClick" class="max-w-full max-h-full block cursor-crosshair"></canvas>
                </div>
                <div class="space-y-2">
                    <p class="text-xs text-text-muted">点击画面添加顶点，形成封闭区域</p>
                    <div class="flex justify-end gap-2">
                        <button @click="undoRoiPoint" class="text-xs px-3 py-1.5 rounded-lg text-text-primary hover:text-white hover:bg-primary/80 transition-all border border-primary/30" style="background: var(--theme-bg-input);">撤销</button>
                        <button @click="clearRoi" class="text-xs px-3 py-1.5 rounded-lg text-text-primary hover:text-white hover:bg-red-500/80 transition-all border border-red-500/30" style="background: var(--theme-bg-input);">清除区域</button>
                    </div>
                </div>
            </div>
            
            <button @click="saveEditZone" class="w-full py-3 bg-primary rounded-xl font-bold mt-4 hover:bg-primary-dark shadow-lg shadow-primary/20 transition-all active:scale-95 press-effect">
                保存
            </button>
        </div>
    </Modal>
  </div>
</template>

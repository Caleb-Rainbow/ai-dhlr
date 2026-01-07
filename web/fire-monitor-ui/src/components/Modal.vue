<script setup lang="ts">
import { X } from 'lucide-vue-next';
import { watch, ref } from 'vue';

const props = defineProps<{
  title: string;
  isOpen: boolean;
}>();

defineEmits(['close']);

// 用于内容动画的延迟
const contentVisible = ref(false);

watch(() => props.isOpen, (newVal) => {
  if (newVal) {
    // 模态框打开后延迟显示内容，实现交错动画
    setTimeout(() => {
      contentVisible.value = true;
    }, 150);
  } else {
    contentVisible.value = false;
  }
});
</script>

<template>
  <Teleport to="body">
    <Transition name="modal-backdrop">
      <div v-if="isOpen" class="fixed inset-0 z-50 flex items-center justify-center p-4">
          <!-- Backdrop with blur -->
          <Transition name="modal-overlay" appear>
            <div class="absolute inset-0 backdrop-blur-md" 
                 style="background: var(--theme-overlay);" 
                 @click="$emit('close')">
            </div>
          </Transition>
          
          <!-- Modal Content -->
          <Transition name="modal-content" appear>
            <div class="relative glass-panel w-full max-w-sm rounded-3xl overflow-hidden shadow-2xl">
                <!-- Header -->
                <div class="px-6 py-4 border-b border-white/5 flex justify-between items-center">
                    <h3 class="text-lg font-bold text-text-primary tracking-wide">{{ title }}</h3>
                    <button @click="$emit('close')" 
                            class="text-text-muted hover:text-white transition-all duration-200 hover:rotate-90 hover:scale-110 p-1 rounded-lg hover:bg-white/10">
                        <X class="w-5 h-5" />
                    </button>
                </div>
                <!-- Body with staggered animation -->
                <div class="p-6">
                  <Transition name="modal-body" appear>
                    <div v-if="contentVisible" class="modal-body-content">
                      <slot></slot>
                    </div>
                  </Transition>
                </div>
            </div>
          </Transition>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
/* Backdrop transitions */
.modal-backdrop-enter-active,
.modal-backdrop-leave-active {
  transition: opacity 0.35s ease;
}

.modal-backdrop-enter-from,
.modal-backdrop-leave-to {
  opacity: 0;
}

/* Overlay blur animation */
.modal-overlay-enter-active {
  transition: opacity 0.35s ease, backdrop-filter 0.35s ease;
}

.modal-overlay-leave-active {
  transition: opacity 0.25s ease, backdrop-filter 0.25s ease;
}

.modal-overlay-enter-from,
.modal-overlay-leave-to {
  opacity: 0;
  backdrop-filter: blur(0);
}

/* Modal content animation */
.modal-content-enter-active {
  transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}

.modal-content-leave-active {
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.modal-content-enter-from {
  opacity: 0;
  transform: scale(0.9) translateY(20px);
}

.modal-content-leave-to {
  opacity: 0;
  transform: scale(0.95) translateY(10px);
}

/* Body content staggered animation */
.modal-body-enter-active {
  animation: modalBodyIn 0.4s cubic-bezier(0.16, 1, 0.3, 1) forwards;
}

.modal-body-leave-active {
  animation: modalBodyIn 0.2s cubic-bezier(0.16, 1, 0.3, 1) reverse forwards;
}

@keyframes modalBodyIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* Animate form elements inside modal */
.modal-body-content :deep(> *) {
  animation: modalItemIn 0.3s cubic-bezier(0.16, 1, 0.3, 1) forwards;
  opacity: 0;
}

.modal-body-content :deep(> *:nth-child(1)) { animation-delay: 0s; }
.modal-body-content :deep(> *:nth-child(2)) { animation-delay: 0.05s; }
.modal-body-content :deep(> *:nth-child(3)) { animation-delay: 0.1s; }
.modal-body-content :deep(> *:nth-child(4)) { animation-delay: 0.15s; }
.modal-body-content :deep(> *:nth-child(5)) { animation-delay: 0.2s; }
.modal-body-content :deep(> *:nth-child(6)) { animation-delay: 0.25s; }

@keyframes modalItemIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>

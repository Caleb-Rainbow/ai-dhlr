<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue';
import { ws } from '../api/ws';
import type { LogFile } from '../types';
import { RefreshCw, ChevronLeft, ChevronRight } from 'lucide-vue-next';

const logFiles = ref<LogFile[]>([]);
const currentLog = ref('');
const currentFileName = ref('');
const loading = ref(false);
const initialLoading = ref(true);
const contentLoading = ref(false);
const logContentRef = ref<HTMLElement | null>(null);
const shouldScrollToBottom = ref(false);

// Pagination state
const currentPage = ref(1);
const totalPages = ref(1);
const totalLines = ref(0);
const pageSize = 500;

const scrollToBottom = () => {
  nextTick(() => {
    if (logContentRef.value) {
      logContentRef.value.scrollTop = logContentRef.value.scrollHeight;
    }
  });
};

const scrollToTop = () => {
  nextTick(() => {
    if (logContentRef.value) {
      logContentRef.value.scrollTop = 0;
    }
  });
};

const onContentEnter = () => {
  if (shouldScrollToBottom.value) {
    scrollToBottom();
    shouldScrollToBottom.value = false;
  }
};

const loadFiles = async () => {
  try {
    const files = await ws.request<Array<{ name: string; size: number; mtime: number }>>('get_log_files');
    logFiles.value = files.map(f => ({ name: f.name, size: f.size, modified: f.mtime }));
    if (logFiles.value.length > 0 && !currentFileName.value) {
       const firstFile = logFiles.value[0];
       if (firstFile) selectLog(firstFile.name);
    }
  } catch (e) { console.error(e); }
  finally {
    initialLoading.value = false;
  }
};

const selectLog = async (filename: string, page = 1) => {
  currentFileName.value = filename;
  loading.value = true;
  contentLoading.value = true;
  shouldScrollToBottom.value = (page === 1);
  try {
    const res = await ws.request<{
      content: string;
      filename: string;
      total_lines: number;
      page: number;
      total_pages: number;
    }>('get_log_content', { filename, page, page_size: pageSize });

    currentLog.value = res.content || '日志为空';
    currentPage.value = res.page;
    totalPages.value = res.total_pages;
    totalLines.value = res.total_lines;
  } catch (e) {
    currentLog.value = '读取失败';
  } finally {
    loading.value = false;
    setTimeout(() => {
      contentLoading.value = false;
      if (!initialLoading.value && logContentRef.value) {
        if (currentPage.value >= totalPages.value) {
          scrollToBottom();
        } else {
          scrollToTop();
        }
      }
    }, 100);
  }
};

const goToPage = (page: number) => {
  if (page < 1 || page > totalPages.value || page === currentPage.value) return;
  selectLog(currentFileName.value, page);
};

const refresh = () => {
    loadFiles();
    if(currentFileName.value) selectLog(currentFileName.value, currentPage.value);
};

onMounted(async () => {
  await ws.connect();
  loadFiles();
});
</script>

<template>
  <div class="flex flex-col h-full min-h-[calc(100dvh-5rem)] lg:min-h-full space-y-4 pb-20 lg:pb-0 overflow-hidden">
    <div class="flex items-center justify-between pt-6">
      <h2 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-text-primary to-text-secondary">系统日志</h2>
    </div>

    <!-- 使用 mode="out-in" 确保骨架屏先消失再显示内容 -->
    <Transition name="fade" mode="out-in" @after-enter="onContentEnter">
      <!-- Loading Skeleton for initial load -->
      <div v-if="initialLoading" key="skeleton" class="flex-1 flex flex-col gap-4 min-h-0 overflow-hidden">
        <div class="grid grid-cols-[1fr_auto] gap-3">
          <div class="skeleton h-12 rounded-2xl"></div>
          <div class="skeleton w-12 h-12 rounded-2xl"></div>
        </div>
        <div class="flex-1 backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] rounded-3xl overflow-hidden shadow-[0_8px_32px_var(--theme-shadow)] transition-all">
          <div class="px-4 py-3 border-b" style="border-color: var(--theme-border-input);">
            <div class="skeleton h-4 w-32"></div>
          </div>
          <div class="p-4 space-y-2">
            <div v-for="i in 8" :key="i" class="skeleton h-4" :style="{ width: `${60 + Math.random() * 40}%` }"></div>
          </div>
        </div>
      </div>

      <!-- Loaded Content -->
      <div v-else key="content" class="flex-1 flex flex-col gap-4 min-h-0 overflow-hidden">
        <!-- File Selector and Actions -->
        <div class="grid grid-cols-[1fr_auto] gap-3">
          <div class="relative group">
            <select
              :value="currentFileName"
              @change="(e) => { const v = (e.target as HTMLSelectElement).value; currentPage = 1; selectLog(v, 1); }"
              class="w-full backdrop-blur-md rounded-2xl px-4 py-3.5 border text-sm outline-none focus:border-indigo-500/50 transition-all appearance-none cursor-pointer text-text-primary"
              style="background: var(--theme-bg-input); border-color: var(--theme-border-input);"
            >
              <option v-for="file in logFiles" :key="file.name" :value="file.name" class="theme-select-option">
                {{ file.name }}
              </option>
              <option v-if="logFiles.length === 0" disabled selected value="">暂无日志文件</option>
            </select>
            <div class="absolute right-4 top-1/2 -translate-y-1/2 pointer-events-none opacity-40 group-hover:opacity-100 transition-opacity text-xs">▼</div>
          </div>
          <button @click="refresh" class="p-4 rounded-2xl text-text-muted hover:text-text-primary transition-all border active:scale-95 shadow-lg press-effect" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              <RefreshCw class="w-5 h-5" :class="{ 'animate-spin': loading }" />
          </button>
        </div>

        <!-- Log Viewer Content -->
        <div class="flex-1 backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] rounded-3xl overflow-hidden flex flex-col shadow-2xl shadow-[0_8px_32px_var(--theme-shadow)] min-h-0 transition-all">
           <div class="px-4 py-3 border-b flex justify-between items-center" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              <div class="flex items-center gap-2 overflow-hidden">
                 <Transition name="pop" mode="out-in">
                   <div :key="loading ? 'loading' : 'ready'" class="w-2 h-2 rounded-full shadow-[0_0_8px_rgba(99,102,241,0.5)]" :class="loading ? 'bg-amber-500 animate-pulse' : 'bg-indigo-500'"></div>
                 </Transition>
                 <Transition name="slide-fade" mode="out-in">
                   <span :key="currentFileName" class="text-[10px] text-text-muted font-mono uppercase tracking-widest truncate">
                     {{ currentFileName || 'Waiting for selection' }}
                   </span>
                 </Transition>
              </div>
              <Transition name="pop" mode="out-in">
                <span :key="loading ? 'loading' : 'ready'" class="text-[10px] text-text-muted uppercase tracking-tighter font-bold" :class="{ 'text-emerald-500': !loading }">
                    {{ loading ? 'loading...' : 'ready' }}
                </span>
              </Transition>
           </div>
           <div ref="logContentRef" class="flex-1 overflow-auto p-4 scroll-smooth custom-scrollbar relative" style="background: var(--theme-bg-input);">
              <!-- Content Loading Overlay -->
              <Transition name="fade">
                <div v-if="contentLoading" class="absolute inset-0 flex items-center justify-center" style="background: var(--theme-bg-input);">
                  <div class="flex flex-col items-center gap-3">
                    <div class="spinner"></div>
                    <span class="text-xs text-text-muted">加载日志内容...</span>
                  </div>
                </div>
              </Transition>

              <!-- Log Content with Animation -->
              <Transition name="slide-fade" mode="out-in">
                <pre :key="`${currentFileName}-${currentPage}`" class="font-mono text-[11px] text-text-secondary whitespace-pre-wrap break-all leading-relaxed">{{ currentLog }}</pre>
              </Transition>
           </div>
           <!-- Pagination -->
           <div v-if="totalPages > 1" class="px-4 py-2.5 border-t flex items-center justify-between" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
              <span class="text-[10px] text-text-muted">
                共 {{ totalLines }} 行，第 {{ currentPage }} / {{ totalPages }} 页
              </span>
              <div class="flex items-center gap-1">
                <button
                  @click="goToPage(currentPage - 1)"
                  :disabled="currentPage <= 1"
                  class="p-1.5 rounded-lg text-text-muted hover:text-text-primary transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  <ChevronLeft class="w-4 h-4" />
                </button>
                <template v-for="p in totalPages" :key="p">
                  <button
                    v-if="totalPages <= 7 || p === 1 || p === totalPages || Math.abs(p - currentPage) <= 1"
                    @click="goToPage(p)"
                    class="min-w-[28px] h-7 rounded-lg text-xs font-medium transition-all"
                    :class="p === currentPage ? 'bg-indigo-500/20 text-indigo-400' : 'text-text-muted hover:text-text-primary hover:bg-white/5'"
                  >
                    {{ p }}
                  </button>
                  <span
                    v-else-if="totalPages > 7 && (p === currentPage - 2 || p === currentPage + 2)"
                    class="text-[10px] text-text-muted px-0.5"
                  >...</span>
                </template>
                <button
                  @click="goToPage(currentPage + 1)"
                  :disabled="currentPage >= totalPages"
                  class="p-1.5 rounded-lg text-text-muted hover:text-text-primary transition-all disabled:opacity-30 disabled:cursor-not-allowed"
                >
                  <ChevronRight class="w-4 h-4" />
                </button>
              </div>
           </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

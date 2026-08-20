<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue';
import { ws } from '../api/ws';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { SquareTerminal, Play, Unplug } from 'lucide-vue-next';
import { useTheme } from '../composables/useTheme';
import type { TerminalStartResult, TerminalOutputEvent, TerminalExitEvent } from '../types';

const { theme } = useTheme();

const containerRef = ref<HTMLDivElement | null>(null);
const term = ref<Terminal | null>(null);
let fitAddon: FitAddon | null = null;

const sessionId = ref('');
const shellChoice = ref<'bash' | 'python'>('bash');
const activeShell = ref<'bash' | 'python'>('bash');
const starting = ref(false);
const stopping = ref(false);
const exitMessage = ref('');
// ws.connect() 失败时不会 reject（挂起等待自动重连），用状态位避免按钮卡死
const wsReady = ref(false);
// 远程模式：浏览器↔服务器断连不影响设备侧会话，置位等待重连后续用
const interrupted = ref(false);

const unsubs: Array<() => void> = [];
let resizeObserver: ResizeObserver | null = null;
let resizeTimer: number | undefined;
let stopThemeWatch: (() => void) | null = null;

const EXIT_REASONS: Record<string, string> = {
    exited: '会话已退出',
    stopped: '会话已断开',
    idle_timeout: '会话空闲超时，已自动回收',
    connection_closed: '连接断开，会话已回收',
    server_shutdown: '设备服务重启，会话已结束',
};

// 终端配色跟随应用明/暗主题
function themeOptions() {
    const dark = theme.value === 'dark';
    return dark
        ? {
            background: '#0c0c0f',
            foreground: '#e4e4e7',
            cursor: '#a5b4fc',
            cursorAccent: '#0c0c0f',
            selectionBackground: '#6366f14d'
        }
        : {
            background: '#f8fafc',
            foreground: '#18181b',
            cursor: '#6366f1',
            cursorAccent: '#f8fafc',
            selectionBackground: '#a5b4fc66'
        };
}

function b64ToBytes(b64: string): Uint8Array {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
}

// ==================== 会话操作 ====================

async function startSession() {
    if (starting.value || sessionId.value) return;
    if (!ws.isConnected) {
        exitMessage.value = '设备连接未就绪，请稍候重试';
        return;
    }
    starting.value = true;
    exitMessage.value = '';
    try {
        try { fitAddon?.fit(); } catch { /* 容器不可见时忽略 */ }
        const info = await ws.request<TerminalStartResult>('terminal_start', {
            shell: shellChoice.value,
            cols: term.value?.cols ?? 80,
            rows: term.value?.rows ?? 24
        });
        sessionId.value = info.session_id;
        activeShell.value = shellChoice.value;
        term.value?.reset();
        term.value?.focus();
    } catch (e: any) {
        exitMessage.value = `创建会话失败: ${e?.message || e}`;
    } finally {
        starting.value = false;
    }
}

async function stopSession() {
    if (!sessionId.value || stopping.value) return;
    stopping.value = true;
    try {
        await ws.request('terminal_stop', { session_id: sessionId.value });
    } catch {
        /* 会话已不存在时同样按已断开处理 */
    } finally {
        stopping.value = false;
        // 不等待 terminal_exit 推送（可能延迟或丢失），本地直接收尾；
        // 后续到达的 exit 推送会因 sessionId 已清空而被忽略
        finishSession('stopped');
    }
}

function finishSession(reason: string, exitCode?: number) {
    if (!sessionId.value) return;
    sessionId.value = '';
    interrupted.value = false;
    const label = EXIT_REASONS[reason] || `会话结束 (${reason})`;
    exitMessage.value = exitCode !== undefined && exitCode !== null
        ? `${label}，退出码 ${exitCode}`
        : label;
}

// ==================== 输入输出 ====================

function sendInput(data: string) {
    if (!sessionId.value || interrupted.value) return;
    ws.request('terminal_input', { session_id: sessionId.value, data }).catch(() => {
        /* 输入发送失败由 terminal_exit/disconnect 兜底提示 */
    });
}

function handleOutput(data: any) {
    const ev = data as TerminalOutputEvent;
    if (!ev || ev.session_id !== sessionId.value) return;
    term.value?.write(b64ToBytes(ev.chunk_b64 || ''));
}

function handleExit(data: any) {
    const ev = data as TerminalExitEvent;
    if (!ev || ev.session_id !== sessionId.value) return;
    finishSession(ev.reason, ev.exit_code);
}

function handleDisconnect() {
    if (!sessionId.value) return;
    if (ws.isRemoteMode) {
        // 远程模式：浏览器↔服务器断连不影响设备侧会话（断开期间输出丢失），
        // 保留 session_id 等待重连后探测续用
        interrupted.value = true;
    } else {
        // 本地模式：会话绑定本连接，设备侧已随断连回收
        finishSession('connection_closed');
    }
}

async function onWsReconnected() {
    wsReady.value = true;
    if (!sessionId.value || !interrupted.value) return;
    // 以空输入探测会话是否仍存活（terminal_input 允许空 data）
    try {
        await ws.request('terminal_input', { session_id: sessionId.value, data: '' });
        interrupted.value = false;
        term.value?.focus();
    } catch {
        finishSession('connection_closed');
    }
}

function onResize() {
    if (!fitAddon || !term.value) return;
    const before = `${term.value.cols}x${term.value.rows}`;
    try { fitAddon.fit(); } catch { return; }
    const changed = `${term.value.cols}x${term.value.rows}` !== before;
    if (!changed || !sessionId.value) return;
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => {
        if (!sessionId.value || !term.value) return;
        ws.request('terminal_resize', {
            session_id: sessionId.value,
            cols: term.value.cols,
            rows: term.value.rows
        }).catch(() => {});
    }, 200);
}

// ==================== 生命周期 ====================

onMounted(async () => {
    const t = new Terminal({
        fontSize: 12,
        fontFamily: 'ui-monospace, SFMono-Regular, Consolas, "Courier New", monospace',
        cursorBlink: true,
        scrollback: 5000,
        theme: themeOptions()
    });
    fitAddon = new FitAddon();
    t.loadAddon(fitAddon);
    if (containerRef.value) t.open(containerRef.value);
    try { fitAddon.fit(); } catch { /* 忽略初始尺寸异常 */ }
    term.value = t;

    t.onData(sendInput);
    unsubs.push(ws.on('terminal_output', handleOutput));
    unsubs.push(ws.on('terminal_exit', handleExit));
    unsubs.push(ws.on('disconnect', () => { wsReady.value = false; handleDisconnect(); }));
    unsubs.push(ws.on('connect', onWsReconnected));
    wsReady.value = ws.isConnected;

    if (containerRef.value && typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(onResize);
        resizeObserver.observe(containerRef.value);
    }

    stopThemeWatch = watch(theme, () => {
        if (term.value) term.value.options.theme = themeOptions();
    });

    await ws.connect();
});

onBeforeUnmount(() => {
    // 离开页面时主动结束会话，避免占用会话额度等待空闲回收
    if (sessionId.value) {
        ws.request('terminal_stop', { session_id: sessionId.value }).catch(() => {});
    }
    window.clearTimeout(resizeTimer);
    resizeObserver?.disconnect();
    resizeObserver = null;
    unsubs.forEach(u => u());
    unsubs.length = 0;
    stopThemeWatch?.();
    term.value?.dispose();
    term.value = null;
});
</script>

<template>
  <div class="flex flex-col h-full min-h-[calc(100dvh-5rem)] lg:min-h-full space-y-4 pb-20 lg:pb-0 overflow-hidden">
    <div class="flex items-center justify-between pt-6 flex-wrap gap-3">
      <h2 class="text-2xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-text-primary to-text-secondary">终端</h2>
      <div class="flex items-center gap-2">
        <div class="relative group" :class="{ 'opacity-50 pointer-events-none': !!sessionId }">
          <select
            v-model="shellChoice"
            class="backdrop-blur-md rounded-2xl pl-4 pr-9 py-3 border text-sm outline-none focus:border-indigo-500/50 transition-all appearance-none cursor-pointer text-text-primary"
            style="background: var(--theme-bg-input); border-color: var(--theme-border-input);"
          >
            <option value="bash">bash</option>
            <option value="python">Python REPL</option>
          </select>
          <div class="absolute right-3.5 top-1/2 -translate-y-1/2 pointer-events-none opacity-40 group-hover:opacity-100 transition-opacity text-[10px]">▼</div>
        </div>
        <button
          v-if="!sessionId"
          @click="startSession"
          :disabled="starting || !wsReady"
          class="flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-semibold text-white bg-primary hover:bg-primary-light transition-all active:scale-95 press-effect shadow-lg shadow-indigo-500/25 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Play class="w-4 h-4" :class="{ 'animate-pulse': starting }" />
          {{ starting ? '连接中...' : '启动会话' }}
        </button>
        <button
          v-else
          @click="stopSession"
          :disabled="stopping"
          class="flex items-center gap-2 px-4 py-3 rounded-xl text-sm font-semibold text-white bg-red-500 hover:bg-red-600 transition-all active:scale-95 press-effect shadow-lg shadow-red-500/25 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Unplug class="w-4 h-4" :class="{ 'animate-pulse': stopping }" />
          {{ stopping ? '断开中...' : '断开会话' }}
        </button>
      </div>
    </div>

    <div class="flex-1 backdrop-blur-sm bg-[var(--theme-glass-bg)] border border-[var(--theme-glass-border)] rounded-3xl overflow-hidden flex flex-col shadow-2xl shadow-[0_8px_32px_var(--theme-shadow)] min-h-0 transition-all">
      <!-- 头部状态条 -->
      <div class="px-4 py-3 border-b flex justify-between items-center" style="background: var(--theme-bg-input); border-color: var(--theme-border-input);">
        <div class="flex items-center gap-2 overflow-hidden">
          <span class="w-2 h-2 rounded-full shrink-0" :class="sessionId ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-zinc-500'"></span>
          <span class="text-[10px] text-text-muted font-mono uppercase tracking-widest truncate">
            {{ sessionId ? `${activeShell} · ${sessionId}` : '未连接' }}
          </span>
        </div>
        <span class="text-[10px] text-text-muted uppercase tracking-tighter font-bold">等效于 SSH 登录设备</span>
      </div>

      <!-- 远程模式断连等待恢复横幅 -->
      <div
        v-if="interrupted"
        class="px-4 py-2 text-xs text-amber-500 border-b"
        style="border-color: var(--theme-border-input); background: var(--theme-bg-input);"
      >
        连接已断开，正在等待重连恢复会话（设备侧会话仍在运行，断开期间的输出会丢失）…
      </div>

      <!-- 终端区域（xterm 挂载点常驻 DOM，无会话时覆盖引导层） -->
      <div class="flex-1 relative min-h-0" style="background: var(--theme-bg-input);">
        <div ref="containerRef" class="absolute inset-0 p-3"></div>

        <div
          v-if="!sessionId"
          class="absolute inset-0 flex items-center justify-center backdrop-blur-sm"
          style="background: var(--theme-bg-input);"
        >
          <div class="text-center space-y-4 max-w-md px-6">
            <SquareTerminal class="w-12 h-12 mx-auto text-text-muted" />
            <p class="text-sm text-text-secondary leading-relaxed">
              Web 终端等效于 SSH 登录设备，可直接执行命令、排查问题。<br>
              Python REPL 使用设备运行环境解释器，可 import 项目模块在线调试。
            </p>
            <p v-if="exitMessage" class="text-xs text-amber-500">{{ exitMessage }}</p>
            <p v-else-if="!wsReady" class="text-xs text-text-muted">正在连接设备…</p>
            <button
              @click="startSession"
              :disabled="starting || !wsReady"
              class="inline-flex items-center gap-2 px-5 py-3 rounded-xl text-sm font-semibold text-white bg-primary hover:bg-primary-light transition-all active:scale-95 press-effect shadow-lg shadow-indigo-500/25 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Play class="w-4 h-4" :class="{ 'animate-pulse': starting }" />
              {{ starting ? '连接中...' : '连接设备终端' }}
            </button>
            <p class="text-[10px] text-text-muted">会话上限 2 个 · 空闲 15 分钟自动回收 · 请谨慎执行命令</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

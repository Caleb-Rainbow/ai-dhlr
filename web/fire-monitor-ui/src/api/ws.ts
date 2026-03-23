/**
 * WebSocket 客户端封装
 * 实现请求-响应模式和事件监听
 * 支持本地模式和远程模式
 */

type EventHandler = (data: any) => void;

interface PendingRequest {
    resolve: (data: any) => void;
    reject: (error: Error) => void;
    timeout: ReturnType<typeof setTimeout>;
}

interface WSMessage {
    type: string;
    msg_id?: string;
    action?: string;
    params?: Record<string, any>;
    data?: any;
    success?: boolean;
    error?: string;
    event?: string;
}

interface ConnectionMode {
    isRemote: boolean;
    deviceId: string;
    serverUrl: string;
}

/**
 * 检测连接模式
 * - 本地模式: 直接连接设备端 Python 服务
 * - 远程模式: 连接 Java 服务器，由服务器转发到设备
 */
function detectConnectionMode(): ConnectionMode {
    // 解析 URL 参数
    const urlParams = new URLSearchParams(window.location.search);
    const serverParam = urlParams.get('server');

    // 检查路由是否是远程模式 /device/:deviceId
    const pathMatch = window.location.hash.match(/#\/device\/([^/]+)/);

    if (pathMatch && serverParam) {
        // 远程模式
        return {
            isRemote: true,
            deviceId: pathMatch[1],
            serverUrl: decodeURIComponent(serverParam)
        };
    }

    // 本地模式
    return {
        isRemote: false,
        deviceId: '',
        serverUrl: ''
    };
}

class WebSocketClient {
    private ws: WebSocket | null = null;
    private url: string;
    private pendingRequests: Map<string, PendingRequest> = new Map();
    private eventHandlers: Map<string, Set<EventHandler>> = new Map();
    private reconnectAttempts = 0;
    private maxReconnectDelay = 30000;
    private requestTimeout = 30000;
    private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
    private isManualClose = false;
    private messageIdCounter = 0;

    // 连接模式
    public readonly mode: ConnectionMode;

    constructor() {
        this.mode = detectConnectionMode();

        if (this.mode.isRemote) {
            // 远程模式: 连接 Java 服务器
            // URL 格式: ws://host/ws/dhlr/client/{deviceId}
            const serverUrl = this.mode.serverUrl;
            let wsUrl: string;

            if (serverUrl.startsWith('ws://') || serverUrl.startsWith('wss://')) {
                // 已经是完整的 WebSocket URL
                // 需要添加路径 /ws/dhlr/client/{deviceId}
                const baseUrl = serverUrl.replace(/\/+$/, ''); // 移除末尾斜杠
                wsUrl = `${baseUrl}/ws/dhlr/client/${this.mode.deviceId}`;
            } else {
                // HTTP URL，转换为 WebSocket
                const wsProtocol = serverUrl.startsWith('https://') ? 'wss://' : 'ws://';
                const host = serverUrl.replace(/^https?:\/\//, '').replace(/\/+$/, '');
                wsUrl = `${wsProtocol}${host}/ws/dhlr/client/${this.mode.deviceId}`;
            }

            this.url = wsUrl;
            console.log('[WS] 远程模式，连接到 Java 服务器:', this.url);
        } else {
            // 本地模式: 连接设备端 Python 服务
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const host = window.location.host;
            this.url = `${protocol}//${host}/ws/status`;
            console.log('[WS] 本地模式，连接到设备端:', this.url);
        }
    }

    /**
     * 是否是远程模式
     */
    get isRemoteMode(): boolean {
        return this.mode.isRemote;
    }

    /**
     * 连接 WebSocket
     */
    connect(): Promise<void> {
        console.log('[WS] 开始连接...');
        return new Promise((resolve, reject) => {
            if (this.ws?.readyState === WebSocket.OPEN) {
                console.log('[WS] 已经连接');
                resolve();
                return;
            }

            this.isManualClose = false;

            try {
                console.log('[WS] 创建 WebSocket 对象...');
                this.ws = new WebSocket(this.url);
                console.log('[WS] WebSocket 对象已创建, readyState:', this.ws.readyState);
            } catch (e) {
                console.error('[WS] 创建 WebSocket 失败:', e);
                reject(e);
                return;
            }

            this.ws.onopen = () => {
                console.log('[WS] 连接成功');
                this.reconnectAttempts = 0;
                this.startHeartbeat();
                this.emit('connect', {});
                resolve();
            };

            this.ws.onclose = (event) => {
                console.log('[WS] 连接关闭', event.code, event.reason);
                this.cleanup();
                this.emit('disconnect', { code: event.code, reason: event.reason });

                if (!this.isManualClose) {
                    this.scheduleReconnect();
                }
            };

            this.ws.onerror = (error) => {
                console.error('[WS] 连接错误', error);
                this.emit('error', error);
            };

            this.ws.onmessage = (event) => {
                this.handleMessage(event.data);
            };
        });
    }

    /**
     * 断开连接
     */
    disconnect(): void {
        this.isManualClose = true;
        this.cleanup();
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }

    /**
     * 发送请求并等待响应
     */
    async request<T = any>(action: string, params: Record<string, any> = {}): Promise<T> {
        // 确保已连接
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            await this.connect();
        }

        const msgId = this.generateMessageId();

        const message: WSMessage = {
            type: 'request',
            msg_id: msgId,
            action,
            params
        };

        return new Promise<T>((resolve, reject) => {
            // 设置超时
            const timeout = setTimeout(() => {
                this.pendingRequests.delete(msgId);
                reject(new Error(`请求超时: ${action}`));
            }, this.requestTimeout);

            this.pendingRequests.set(msgId, { resolve, reject, timeout });

            try {
                this.ws!.send(JSON.stringify(message));
            } catch (e) {
                this.pendingRequests.delete(msgId);
                clearTimeout(timeout);
                reject(e);
            }
        });
    }

    /**
     * 监听事件
     */
    on(event: string, handler: EventHandler): () => void {
        if (!this.eventHandlers.has(event)) {
            this.eventHandlers.set(event, new Set());
        }
        this.eventHandlers.get(event)!.add(handler);

        // 返回取消监听的函数
        return () => {
            this.eventHandlers.get(event)?.delete(handler);
        };
    }

    /**
     * 移除事件监听
     */
    off(event: string, handler?: EventHandler): void {
        if (handler) {
            this.eventHandlers.get(event)?.delete(handler);
        } else {
            this.eventHandlers.delete(event);
        }
    }

    /**
     * 发送消息（不等待响应）
     */
    send(message: WSMessage): void {
        if (this.ws?.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify(message));
        }
    }

    /**
     * 是否已连接
     */
    get isConnected(): boolean {
        return this.ws?.readyState === WebSocket.OPEN;
    }

    // ==================== 私有方法 ====================

    private handleMessage(data: string): void {
        try {
            console.log('[WS] 收到消息:', data.substring(0, 200));
            const message: WSMessage = JSON.parse(data);
            console.log('[WS] 消息类型:', message.type);

            if (message.type === 'response' && message.msg_id) {
                // 请求响应
                const pending = this.pendingRequests.get(message.msg_id);
                if (pending) {
                    clearTimeout(pending.timeout);
                    this.pendingRequests.delete(message.msg_id);

                    if (message.success) {
                        pending.resolve(message.data);
                    } else {
                        pending.reject(new Error(message.error || '请求失败'));
                    }
                }
            } else if (message.type === 'event') {
                // 事件推送
                this.emit(message.event || 'unknown', message.data);
            } else if (message.type === 'status_update') {
                // 状态更新（兼容旧协议）
                console.log('[WS] 状态更新事件:', message.data?.length, '个灶台');
                this.emit('status_update', message.data);
            } else if (message.type === 'state_change') {
                // 状态变化（兼容旧协议）
                this.emit('state_change', message.data);
            } else if (message.type === 'pong') {
                // 心跳响应，忽略
            } else if (message.type === 'alarm_notify') {
                // 报警通知（远程模式特有）
                console.log('[WS] 收到报警通知:', message);
                this.emit('alarm_notify', message);
            } else {
                // 其他消息类型
                console.log('[WS] 其他消息类型:', message.type);
                this.emit(message.type, message.data || message);
            }
        } catch (e) {
            console.error('[WS] 解析消息失败', e, data);
        }
    }

    private emit(event: string, data: any): void {
        const handlers = this.eventHandlers.get(event);
        if (handlers) {
            handlers.forEach(handler => {
                try {
                    handler(data);
                } catch (e) {
                    console.error(`[WS] 事件处理器错误 (${event})`, e);
                }
            });
        }

        // 也触发 '*' 通配符监听
        const allHandlers = this.eventHandlers.get('*');
        if (allHandlers) {
            allHandlers.forEach(handler => {
                try {
                    handler({ event, data });
                } catch (e) {
                    console.error('[WS] 通配符处理器错误', e);
                }
            });
        }
    }

    private generateMessageId(): string {
        return `msg_${Date.now()}_${++this.messageIdCounter}`;
    }

    private startHeartbeat(): void {
        this.stopHeartbeat();
        this.heartbeatTimer = setInterval(() => {
            if (this.ws?.readyState === WebSocket.OPEN) {
                this.send({ type: 'ping' });
            }
        }, 30000); // 30秒心跳
    }

    private stopHeartbeat(): void {
        if (this.heartbeatTimer) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }
    }

    private scheduleReconnect(): void {
        if (this.reconnectTimer) {
            return;
        }

        const delay = Math.min(
            Math.pow(2, this.reconnectAttempts) * 1000,
            this.maxReconnectDelay
        );

        console.log(`[WS] ${delay / 1000}秒后重连...`);

        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this.reconnectAttempts++;
            this.connect().catch(() => {
                // 连接失败会触发 onclose，自动重试
            });
        }, delay);
    }

    private cleanup(): void {
        this.stopHeartbeat();

        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }

        // 清理待处理的请求
        this.pendingRequests.forEach((pending) => {
            clearTimeout(pending.timeout);
            pending.reject(new Error('连接断开'));
        });
        this.pendingRequests.clear();
    }
}

// 导出单例
export const ws = new WebSocketClient();

// 导出类型
export type { WSMessage, EventHandler };
export const apiBase = ''; // Relative path by default

// API错误类，包含详细信息
export class ApiError extends Error {
    status: number;
    statusText: string;
    detail?: string;

    constructor(status: number, statusText: string, detail?: string) {
        const message = detail || `${status} ${statusText}`;
        super(message);
        this.name = 'ApiError';
        this.status = status;
        this.statusText = statusText;
        this.detail = detail;
    }
}

export async function api<T = unknown>(endpoint: string, method: string = 'GET', data: unknown = null): Promise<T> {
    const url = `${apiBase}${endpoint}`;
    const options: RequestInit = {
        method,
        headers: {
            'Content-Type': 'application/json'
        }
    };

    if (data) {
        options.body = JSON.stringify(data);
    }

    const response = await fetch(url, options);

    // Handle 204 No Content
    if (response.status === 204) {
        return {} as T;
    }

    if (!response.ok) {
        // 尝试解析JSON错误响应
        let detail: string | undefined;
        try {
            const errorJson = await response.json();
            // FastAPI ValidationError 格式: { detail: [...] } 或 { detail: "error message" }
            if (errorJson.detail) {
                if (Array.isArray(errorJson.detail)) {
                    // Pydantic验证错误，提取所有错误消息
                    detail = errorJson.detail.map((err: { msg?: string; loc?: string[]; message?: string }) => {
                        const field = err.loc ? err.loc.join(' → ') : '';
                        const msg = err.msg || err.message || '未知错误';
                        return field ? `${field}: ${msg}` : msg;
                    }).join('; ');
                } else {
                    detail = String(errorJson.detail);
                }
            } else if (errorJson.message) {
                detail = errorJson.message;
            } else if (errorJson.error) {
                detail = errorJson.error;
            }
        } catch {
            // 如果无法解析JSON，使用原始响应文本
            try {
                detail = await response.text();
            } catch {
                detail = undefined;
            }
        }
        throw new ApiError(response.status, response.statusText, detail);
    }

    // Attempt to parse JSON
    try {
        return await response.json();
    } catch {
        // If not JSON, return empty object
        return {} as T;
    }
}

export async function apiText(endpoint: string): Promise<string> {
    const url = `${apiBase}${endpoint}`;
    try {
        const response = await fetch(url);
        if (!response.ok) return '';
        return await response.text();
    } catch (e) {
        return '';
    }
}

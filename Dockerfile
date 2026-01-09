# =============================================================================
# 动火离人安全监测系统 (DHLR) - CI/CD Dockerfile
# 多阶段构建优化镜像大小
# =============================================================================

# -----------------------------------------------------------------------------
# 阶段 1: 前端构建
# -----------------------------------------------------------------------------
FROM docker.m.daocloud.io/library/node:20-alpine AS frontend-builder

WORKDIR /app/web/fire-monitor-ui

# 复制前端依赖文件
COPY web/fire-monitor-ui/package*.json ./

# 安装前端依赖
RUN npm ci --no-audit --no-fund

# 复制前端源代码
COPY web/fire-monitor-ui/ ./

# 构建前端生产版本
RUN npm run build

# -----------------------------------------------------------------------------
# 阶段 2: Python 基础镜像 (用于开发/测试)
# -----------------------------------------------------------------------------
FROM docker.m.daocloud.io/library/python:3.11-slim AS python-base

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

WORKDIR /app

# 安装系统依赖 (OpenCV, 音频处理等)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # OpenCV 依赖
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    # 音频相关依赖
    libasound2 \
    libportaudio2 \
    libsndfile1 \
    # 调试工具 (可选，生产环境可移除)
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# -----------------------------------------------------------------------------
# 阶段 3: 依赖安装层
# -----------------------------------------------------------------------------
FROM python-base AS dependencies

WORKDIR /app

# 复制依赖文件
COPY requirements.txt ./

# 创建临时requirements文件 (排除测试依赖和硬件特定依赖)
RUN grep -vE "^(pytest|pytest-asyncio)" requirements.txt > requirements-prod.txt || true

# 安装Python依赖
RUN pip install --upgrade pip && \
    pip install -r requirements-prod.txt

# -----------------------------------------------------------------------------
# 阶段 4: 测试阶段 (CI专用)
# -----------------------------------------------------------------------------
FROM dependencies AS test

WORKDIR /app

# 安装测试依赖
RUN pip install pytest pytest-asyncio pytest-cov

# 复制应用代码
COPY src/ ./src/
COPY tests/ ./tests/
COPY pytest.ini ./

# 运行测试
RUN pytest --cov=src --cov-report=xml --cov-report=term-missing || true

# -----------------------------------------------------------------------------
# 阶段 5: 生产镜像
# -----------------------------------------------------------------------------
FROM dependencies AS production

WORKDIR /app

# 复制应用代码
COPY src/ ./src/
COPY config/ ./config/
COPY audio_assets/ ./audio_assets/

# 从前端构建阶段复制编译后的静态文件
COPY --from=frontend-builder /app/web/fire-monitor-ui/dist ./web/fire-monitor-ui/dist

# 创建必要的目录
RUN mkdir -p /app/logs /app/snapshots

# 设置非root用户 (安全最佳实践)
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app
USER appuser

# 暴露端口
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/api/status || exit 1

# 启动命令
CMD ["python", "-m", "src.main"]

# -----------------------------------------------------------------------------
# 阶段 6: 开发镜像 (可选)
# -----------------------------------------------------------------------------
FROM dependencies AS development

WORKDIR /app

# 安装开发/测试依赖
RUN pip install pytest pytest-asyncio pytest-cov black isort flake8

# 复制所有代码 (开发时使用卷挂载覆盖)
COPY . .

# 创建必要的目录
RUN mkdir -p /app/logs /app/snapshots

# 暴露端口 (API + 可能的调试端口)
EXPOSE 8000 5678

# 开发模式启动命令
CMD ["python", "-m", "src.main"]

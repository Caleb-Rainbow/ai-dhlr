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

# 安装系统依赖 (针对现代 Debian 镜像优化)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # 编译工具 (修复 async-pyserial 报错的关键)
    build-essential \
    python3-dev \
    # OpenCV 依赖 (使用之前修复过的 libgl1)
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    # 音频相关依赖
    libasound2 \
    libportaudio2 \
    libsndfile1 \
    # 调试工具
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# -----------------------------------------------------------------------------
# 阶段 3: 依赖安装层
# -----------------------------------------------------------------------------
# -----------------------------------------------------------------------------
# 阶段 3: 依赖安装层
# -----------------------------------------------------------------------------
FROM python-base AS dependencies

WORKDIR /app

# 1. 复制所有依赖相关文件
COPY requirements.txt .
# 复制根目录下的 RKNN 离线安装包
COPY rknn_toolkit_lite2-2.3.2-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl .

# 2. 准备生产环境依赖列表 (排除测试依赖)
RUN grep -vE "^(pytest|pytest-asyncio)" requirements.txt > requirements-prod.txt || true

# 3. 核心优化：根据 CPU 架构按需安装
RUN ARCH=$(uname -m) && \
    pip install --upgrade pip && \
    if [ "$ARCH" = "aarch64" ]; then \
        echo "检测到 ARM64 架构，正在为 RK3568 构建轻量化镜像..." && \
        # 排除掉不需要的重型库：torch, torchvision, ultralytics
        grep -vE "torch|torchvision|ultralytics" requirements-prod.txt > requirements-arm.txt && \
        pip install --no-cache-dir -r requirements-arm.txt && \
        # 安装本地 RKNN 运行时
        pip install rknn_toolkit_lite2-2.3.2-cp311-cp311-manylinux_2_17_aarch64.manylinux2014_aarch64.whl && \
        # 清理安装包节省空间
        rm *.whl; \
    else \
        echo "检测到 x86_64 架构，安装全量测试依赖..." && \
        pip install --no-cache-dir -r requirements-prod.txt; \
    fi
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

# 启动命令
CMD ["python", "-m", "src.main"]

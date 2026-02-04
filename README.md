# 动火离人安全监测系统

## 项目简介

动火离人安全监测系统是一款基于计算机视觉技术的智能安全监控系统，主要用于厨房、工厂等动火区域的安全监测。系统通过摄像头实时监控，结合人形检测算法，当检测到动火区域长时间无人看管时，会触发多级报警机制，最终可自动切断电源，有效预防安全事故的发生。

## 功能特点

### 核心功能
- **人形检测**：基于YOLO模型的高精度人形检测
- **多摄像头支持**：支持USB摄像头和RTSP网络摄像头
- **区域划分**：可自定义监测区域（ROI）
- **三阶段报警机制**：
  - 预警：无人看管超过设定时间（默认90秒）
  - 报警：无人看管超过设定时间（默认180秒）
  - 切电：无人看管超过设定时间（默认300秒），自动切断电源
- **语音播报**：支持三阶段语音提醒
- **实时监控**：Web界面实时查看监控画面和系统状态
- **Web API**：提供完整的RESTful API和WebSocket通信

### 高级功能
- **边缘设备支持**：支持RK3568等边缘设备部署
- **双推理引擎**：支持PyTorch和RKNN两种推理引擎
- **GPIO控制**：支持模拟和真实GPIO控制
- **性能监控**：实时监控系统性能
- **设备管理**：自动生成设备唯一ID
- **巡检功能**：支持设备自检、报警演示、强制预警等
- **串口通讯**：支持串口配置和电流检测
- **LoRa配置**：支持LoRa模块配置和通信
- **火焰模拟**：支持模拟火焰开关状态
- **远程链路**：支持与远程服务器的WebSocket通信

## 系统架构

### 后端架构
```
┌─────────────────────────────────────────────────────────┐
│                     前端Web界面                        │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│                     API服务器                           │
│  - FastAPI框架                                          │
│  - RESTful API                                          │
│  - WebSocket通信                                        │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│                     核心控制系统                        │
│  - 检测循环                                             │
│  - 状态管理                                             │
│  - 事件处理                                             │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────┐   ┌───────────┐   ┌─────────┐   ┌─────────┐
│ 摄像头  │   │ 检测引擎  │   │ 状态机  │   │ 输出控制│
│ 管理    │   │ (YOLO)    │   │ (灶台)  │   │ (GPIO/语音)│
└─────────┘   └───────────┘   └─────────┘   └─────────┘
```

### 前端架构
- Vue 3 + TypeScript + Vite
- 响应式设计
- WebSocket实时通信

## 技术栈

### 后端
- **语言**：Python 3.11+
- **框架**：FastAPI
- **计算机视觉**：OpenCV
- **深度学习**：YOLOv11, PyTorch
- **边缘推理**：RKNN Toolkit Lite 2
- **Web服务**：Uvicorn
- **通信协议**：WebSocket（本地+远程）
- **配置管理**：PyYAML
- **日志管理**：自定义日志系统
- **串口通信**：pyserial
- **LoRa通信**：支持LoRa模块集成

### 前端
- **框架**：Vue 3
- **语言**：TypeScript
- **构建工具**：Vite
- **UI组件**：自定义组件
- **HTTP客户端**：Axios
- **WebSocket**：原生WebSocket API

## 快速开始

### 环境要求
- Python 3.11+
- Node.js 16+
- npm 8+

### 安装步骤

#### 1. 克隆项目
```bash
git clone <项目地址>
cd dhlr
```

#### 2. 安装后端依赖
```bash
pip install -r requirements.txt
```

#### 3. 安装前端依赖
```bash
cd web/fire-monitor-ui
npm install
```

#### 4. 准备配置文件
创建配置文件目录并复制默认配置：
```bash
mkdir -p config
# 复制默认配置文件（根据实际情况修改）
# 配置文件包含系统设置、摄像头配置、区域配置、串口配置、LoRa配置和远程配置等
```

#### 5. 启动后端服务
```bash
python src/main.py
```

#### 6. 启动前端服务
```bash
cd web/fire-monitor-ui
npm run dev
```

#### 7. 访问系统
- 前端界面：http://localhost:5173
- API文档：http://localhost:8000/docs

## 配置说明

### 配置文件结构

配置文件位于 `config/config.yaml`，主要包含以下部分：

```yaml
system:
  name: 动火离人安全监测系统
  version: 0.1.0
  debug: true
  device_id: ""

cameras:
  - id: cam1
    type: usb
    name: 主摄像头
    device: 0
    width: 640
    height: 480
    fps: 30

zones:
  - id: zone1
    name: 灶台1
    camera_id: cam1
    roi: [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]
    enabled: true

inference:
  engine: pytorch
  model_path: yolov11n-sim.onnx
  confidence_threshold: 0.5
  person_class_id: 0

detection:
  no_person_threshold: 3
  person_present_threshold: 2

alarm:
  warning_time: 90
  alarm_time: 180
  action_time: 300
  broadcast_interval: 15
  warning_message: "动火区域离人即将超时，请立即回到工作岗位"
  alarm_message: "动火区域离人超时，请立即回到工作岗位"
  action_message: "动火区域离人超时，已自动切断炉灶电源，请立即现场处理"

voice:
  enabled: true
  volume: 1.0

gpio:
  simulated: true

serial:
  enabled: false
  port: "COM1"
  baudrate: 9600
  poll_interval: 1000

lora:
  id: ""
  channel: 0

remote:
  enabled: false
  server_url: "wss://vis.example.com/dhlr/socket"
  token: ""

api:
  host: 0.0.0.0
  port: 8000
  cors_origins: ["*"]

logging:
  level: INFO
  log_dir: logs
  snapshot_dir: snapshots
```

### 关键配置项说明

#### 摄像头配置
- `type`: `usb` 或 `rtsp`
- `device`: USB设备索引（仅usb类型）
- `rtsp_url`: RTSP流地址（仅rtsp类型）

#### 区域配置
- `roi`: 归一化坐标列表，定义监测区域
- `camera_id`: 关联的摄像头ID

#### 报警配置
- `warning_time`: 预警时间（秒）
- `alarm_time`: 报警时间（秒）
- `action_time`: 切电时间（秒）
- `broadcast_interval`: 语音播报间隔（秒）

#### 推理配置
- `engine`: `pytorch` 或 `rknn`
- `model_path`: 模型文件路径
- `confidence_threshold`: 检测置信度阈值

#### 串口配置
- `enabled`: 是否启用串口
- `port`: 串口号
- `baudrate`: 波特率
- `poll_interval`: 轮询间隔（毫秒）

#### LoRa配置
- `id`: LoRa设备ID
- `channel`: 通信频道

#### 远程配置
- `enabled`: 是否启用远程链路
- `server_url`: 远程服务器WebSocket地址
- `token`: 认证令牌

## 部署指南

### 本地开发部署

参考「快速开始」章节。

### 边缘设备部署（RK3568）

1. **准备环境**
   - 安装RK3568驱动和SDK
   - 部署依赖库

2. **编译模型**
   - 使用RKNN Toolkit将ONNX模型转换为RKNN模型
   - 优化模型性能

3. **部署应用**
   - 复制代码和模型文件到设备
   - 安装依赖
   - 配置系统服务

4. **启动服务**
   - 设置开机自启
   - 监控运行状态

详细部署指南请参考 `docs/RK3568 边缘设备部署与内存优化指南.md`。

## 开发说明

### 后端开发

#### 目录结构
```
src/
├── api/              # API服务模块
├── camera/           # 摄像头管理
├── detection/        # 检测引擎
├── output/           # 输出控制（GPIO/语音）
├── patrol/           # 巡检功能
├── serial_port/      # 串口通信
├── static/           # 静态文件
├── utils/            # 工具类
├── zone/             # 区域管理
├── main.py           # 主入口
└── __init__.py
```

#### 核心模块
- `FireSafetySystem`：系统主类，管理整个系统的生命周期
- `PersonDetector`：人形检测器，支持PyTorch和RKNN引擎
- `ZoneManager`：区域状态管理器，处理区域状态变化
- `CameraManager`：摄像头管理器，管理多个摄像头
- `SerialManager`：串口管理器，处理串口通信和电流检测
- `LoRaManager`：LoRa管理器，处理LoRa配置和通信
- `RemoteManager`：远程管理器，处理与远程服务器的通信
- `PatrolManager`：巡检管理器，处理设备巡检和演示功能

### 前端开发

#### 目录结构
```
web/fire-monitor-ui/
├── .vscode/          # VS Code配置
├── public/           # 静态资源
├── src/
│   ├── api/          # API通信和WebSocket
│   ├── assets/       # 静态资源
│   ├── components/   # Vue组件
│   ├── composables/  # 组合式函数
│   ├── router/       # 路由配置
│   ├── types/        # TypeScript类型定义
│   ├── views/        # 页面视图
│   │   ├── Dashboard.vue     # 仪表盘
│   │   ├── Cameras.vue       # 摄像头管理
│   │   ├── Zones.vue         # 区域管理
│   │   ├── Patrol.vue        # 巡检功能
│   │   ├── Settings.vue      # 系统设置
│   │   └── Logs.vue          # 日志查看
│   ├── App.vue       # 根组件
│   ├── main.ts       # 入口文件
│   └── style.css     # 全局样式
├── index.html        # 入口HTML
├── package.json      # 项目配置
├── tsconfig.json     # TypeScript配置
└── vite.config.ts    # Vite配置
```

#### 开发命令
```bash
# 安装依赖
npm install

# 开发模式
npm run dev

# 构建生产版本
npm run build

# 预览生产版本
npm run preview
```

## WebSocket协议

系统使用WebSocket实现全双工实时通讯，支持本地链路（设备本地Web端↔Python后端）和远程链路（Python后端↔远程服务器），两种链路共用同一套协议。

### 连接地址

| 链路类型 | 地址格式 | 示例 |
|---------|---------|------|
| 本地链路 | `ws://localhost:{port}/ws/status` | `ws://localhost:8000/ws/status` |
| 远程链路 | `ws(s)://{server}/{path}` | `wss://vis.example.com/dhlr/socket` |

### 主要功能

- **实时状态更新**：灶台状态、设备性能、网络状态等实时推送
- **双向通信**：支持客户端请求和服务端主动推送
- **心跳机制**：保持连接稳定性
- **事件通知**：状态变化、报警事件、巡检事件等实时通知

### 支持的操作

WebSocket协议支持丰富的操作，包括：

- **灶台管理**：获取、创建、更新、删除灶台配置
- **摄像头管理**：获取摄像头列表、预览摄像头画面
- **状态查询**：获取系统状态、性能指标、设备信息
- **设置管理**：获取和更新系统设置
- **日志管理**：获取日志文件列表和内容
- **串口通讯**：获取和更新串口配置、读取电流值
- **LoRa配置**：获取和设置LoRa参数
- **巡检操作**：开始/停止巡检、设备自检、报警演示等

详细协议规范请参考 `docs/websocket_protocol.md`。

## 性能监控

系统内置性能监控模块，可以监控CPU、内存、GPU使用率等指标。

## 日志管理

日志文件位于 `logs/` 目录，包含以下类型：
- 系统日志
- 事件日志
- 告警日志

快照文件位于 `snapshots/` 目录，包含告警时的截图。

## 目录结构

```
dhlr/
├── config/           # 配置文件
├── docs/             # 文档
├── src/              # 后端代码
├── web/              # 前端代码
├── audio_assets/     # 语音资源
├── logs/             # 日志文件
├── snapshots/        # 快照文件
├── requirements.txt  # Python依赖
├── README.md         # 项目说明
└── .gitignore        # Git忽略文件
```

## 许可证

[MIT License](LICENSE)

## 贡献

欢迎提交Issue和Pull Request！

## 联系方式

如有问题或建议，请联系项目维护人员。

---

**版本**：0.2.0  
**更新时间**：2026-01-08  
**版权所有**：动火离人安全监测系统开发团队
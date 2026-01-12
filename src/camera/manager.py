"""
摄像头管理器
支持USB和RTSP摄像头的统一接入和管理
"""
import cv2
import threading
import time
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass
from enum import Enum
import numpy as np

from ..utils.config import CameraConfig
from ..utils.logger import get_logger


class CameraStatus(Enum):
    """摄像头状态"""
    OFFLINE = "offline"
    CONNECTING = "connecting"  # 正在连接中
    ONLINE = "online"
    ERROR = "error"


@dataclass
class CameraInfo:
    """摄像头信息"""
    id: str
    name: str
    type: str
    status: CameraStatus
    width: int
    height: int
    fps: int
    device: Optional[int] = None
    rtsp_url: Optional[str] = None


class Camera:
    """摄像头封装类"""
    
    # 帧超时时间（秒）- 超过此时间未读到帧则触发重连
    FRAME_TIMEOUT_SEC = 10
    
    def __init__(self, config: CameraConfig):
        self.config = config
        self.id = config.id
        self.name = config.name
        self.type = config.type
        
        self._cap: Optional[cv2.VideoCapture] = None
        self._status = CameraStatus.OFFLINE
        self._frame: Optional[np.ndarray] = None
        self._frame_lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_frame_time = 0
        self._force_reconnect = False  # 强制重连标志
        self._logger = get_logger()
    
    @property
    def status(self) -> CameraStatus:
        return self._status
    
    @property
    def is_online(self) -> bool:
        return self._status == CameraStatus.ONLINE
    
    def start(self, blocking: bool = False) -> bool:
        """启动摄像头
        
        Args:
            blocking: 是否阻塞式启动，默认 False（异步启动）
        
        Returns:
            bool: 阻塞模式返回是否成功，非阻塞模式返回 True（表示已开始连接）
        """
        if self._running:
            return True
        
        if blocking:
            return self._connect_sync()
        else:
            # 异步启动：在后台线程中连接
            self._status = CameraStatus.CONNECTING
            self._running = True  # 标记为运行中，防止重复启动
            connect_thread = threading.Thread(
                target=self._connect_async, 
                daemon=True,
                name=f"Camera-Connect-{self.id}"
            )
            connect_thread.start()
            self._logger.info(f"摄像头开始异步连接: {self.id} ({self.name})")
            return True
    
    def _connect_sync(self) -> bool:
        """同步连接摄像头（阻塞式）"""
        try:
            if not self._open_capture():
                return False
            
            self._status = CameraStatus.ONLINE
            
            # 启动帧读取线程
            self._thread = threading.Thread(target=self._read_frames, daemon=True)
            self._thread.start()
            
            self._logger.info(f"摄像头已启动: {self.id} ({self.name})")
            return True
            
        except Exception as e:
            self._logger.error(f"启动摄像头失败: {self.id}, 错误: {e}")
            self._status = CameraStatus.ERROR
            return False
    
    def _connect_async(self):
        """异步连接摄像头（在后台线程中执行）"""
        try:
            if self._open_capture():
                self._status = CameraStatus.ONLINE
                # 启动帧读取线程
                self._thread = threading.Thread(target=self._read_frames, daemon=True)
                self._thread.start()
                self._logger.info(f"摄像头连接成功: {self.id} ({self.name})")
            else:
                self._running = False  # 连接失败，重置运行状态
        except Exception as e:
            self._logger.error(f"摄像头连接异常: {self.id}, 错误: {e}")
            self._status = CameraStatus.ERROR
            self._running = False
    
    def _open_capture(self) -> bool:
        """打开视频捕获设备"""
        try:
            if self.type == "usb":
                self._cap = cv2.VideoCapture(self.config.device)
            elif self.type == "rtsp":
                url = self._build_rtsp_url()
                # 使用 FFMPEG 后端，通过 URL 参数设置超时（FFmpeg 在创建时读取这些参数）
                # stimeout: TCP 流超时（微秒），这里设置为 5 秒
                # rtsp_transport=tcp: 使用 TCP 传输
                if '?' in url:
                    url_with_timeout = f"{url}&stimeout=5000000&rtsp_transport=tcp"
                else:
                    url_with_timeout = f"{url}?stimeout=5000000&rtsp_transport=tcp"
                
                self._cap = cv2.VideoCapture(url_with_timeout, cv2.CAP_FFMPEG)
                # 备用：设置 OpenCV 超时属性（部分版本支持）
                self._cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                self._cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
            else:
                self._logger.error(f"不支持的摄像头类型: {self.type}")
                return False
            
            if not self._cap.isOpened():
                self._logger.error(f"无法打开摄像头: {self.id}")
                self._status = CameraStatus.ERROR
                return False
            
            # 设置摄像头参数
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
            self._cap.set(cv2.CAP_PROP_FPS, self.config.fps)
            
            self._running = True
            return True
            
        except Exception as e:
            self._logger.error(f"打开摄像头失败: {self.id}, 错误: {e}")
            self._status = CameraStatus.ERROR
            return False
    
    def stop(self):
        """停止摄像头"""
        self._running = False
        
        # 等待读取线程结束
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)  # 增加超时时间
            if self._thread.is_alive():
                self._logger.warning(f"摄像头读取线程未能在5秒内结束: {self.id}")
        
        # 释放视频捕获资源
        if self._cap:
            try:
                self._cap.release()
            except Exception as e:
                self._logger.warning(f"释放摄像头资源时出错: {self.id}, {e}")
            self._cap = None
        
        self._frame = None  # 清除缓存帧
        self._status = CameraStatus.OFFLINE
        self._logger.info(f"摄像头已停止: {self.id}")
    
    def reconnect(self):
        """
        触发摄像头重连
        用于处理摄像头断线后恢复的场景
        """
        if not self._running:
            self._logger.info(f"摄像头未运行，启动连接: {self.id}")
            self.start()
            return
        
        self._logger.info(f"触发摄像头重连: {self.id}")
        self._force_reconnect = True
        # 释放当前连接以触发重连
        if self._cap:
            try:
                self._cap.release()
            except Exception as e:
                self._logger.warning(f"释放摄像头连接时出错: {self.id}, {e}")
            self._cap = None
        self._status = CameraStatus.CONNECTING
    
    def _build_rtsp_url(self) -> str:
        """构建RTSP URL（带认证）"""
        url = self.config.rtsp_url
        if self.config.username and self.config.password:
            # 插入认证信息
            if "://" in url:
                protocol, rest = url.split("://", 1)
                url = f"{protocol}://{self.config.username}:{self.config.password}@{rest}"
        return url
    
    def _read_frames(self):
        """帧读取线程"""
        retry_count = 0
        max_retries = 5
        
        while self._running:
            try:
                # 检查是否需要强制重连
                if self._force_reconnect:
                    self._force_reconnect = False
                    self._logger.info(f"执行强制重连: {self.id}")
                    if self._cap:
                        try:
                            self._cap.release()
                        except Exception:
                            pass
                    self._cap = None
                
                # 检查帧超时（连接看起来正常但长时间无帧）
                if (self._cap is not None and self._cap.isOpened() and 
                    self._last_frame_time > 0 and 
                    time.time() - self._last_frame_time > self.FRAME_TIMEOUT_SEC):
                    self._logger.warning(f"摄像头帧超时（{self.FRAME_TIMEOUT_SEC}秒无新帧），触发重连: {self.id}")
                    if self._cap:
                        try:
                            self._cap.release()
                        except Exception:
                            pass
                    self._cap = None
                    self._status = CameraStatus.CONNECTING
                
                if self._cap is None or not self._cap.isOpened():
                    # 尝试重连
                    self._status = CameraStatus.CONNECTING
                    
                    if self.type == "usb":
                        self._cap = cv2.VideoCapture(self.config.device)
                    elif self.type == "rtsp":
                        url = self._build_rtsp_url()
                        # 使用 FFMPEG 后端，通过 URL 参数设置超时（5秒）
                        if '?' in url:
                            url_with_timeout = f"{url}&stimeout=5000000&rtsp_transport=tcp"
                        else:
                            url_with_timeout = f"{url}?stimeout=5000000&rtsp_transport=tcp"
                        
                        self._cap = cv2.VideoCapture(url_with_timeout, cv2.CAP_FFMPEG)
                        # 备用：设置 OpenCV 超时属性
                        self._cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
                        self._cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
                    
                    if self._cap and self._cap.isOpened():
                        # 重新设置参数
                        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.width)
                        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.height)
                        self._cap.set(cv2.CAP_PROP_FPS, self.config.fps)
                        self._status = CameraStatus.ONLINE
                        self._last_frame_time = time.time()  # 重置帧时间
                        self._logger.info(f"摄像头已重连: {self.id}")
                    else:
                        self._status = CameraStatus.OFFLINE
                        time.sleep(2.0)
                        continue

                ret, frame = self._cap.read()
                if ret:
                    with self._frame_lock:
                        self._frame = frame
                        self._last_frame_time = time.time()
                    self._status = CameraStatus.ONLINE
                    retry_count = 0
                else:
                    retry_count += 1
                    if retry_count >= max_retries:
                        self._status = CameraStatus.ERROR
                        self._logger.warning(f"摄像头读取连续失败，尝试重置: {self.id}")
                        # 释放资源以触发重连
                        if self._cap:
                            self._cap.release()
                        self._cap = None
                        retry_count = 0
                    time.sleep(0.1)
            except Exception as e:
                self._logger.error(f"读取帧异常: {self.id}, 错误: {e}")
                time.sleep(0.5)
    
    def get_frame(self) -> Optional[np.ndarray]:
        """获取最新帧"""
        with self._frame_lock:
            if self._frame is not None:
                return self._frame.copy()
        return None
    
    def get_snapshot(self) -> Optional[np.ndarray]:
        """获取快照"""
        return self.get_frame()
    
    def get_info(self) -> CameraInfo:
        """获取摄像头信息"""
        return CameraInfo(
            id=self.id,
            name=self.name,
            type=self.type,
            status=self._status,
            width=self.config.width,
            height=self.config.height,
            fps=self.config.fps,
            device=self.config.device,
            rtsp_url=self.config.rtsp_url
        )


class CameraManager:
    """摄像头管理器"""
    
    _instance: Optional['CameraManager'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self._cameras: Dict[str, Camera] = {}
        self._logger = get_logger()
    
    def add_camera(self, config: CameraConfig) -> Camera:
        """添加摄像头"""
        if config.id in self._cameras:
            self._logger.warning(f"摄像头已存在，将替换: {config.id}")
            self.remove_camera(config.id)
        
        camera = Camera(config)
        self._cameras[config.id] = camera
        self._logger.info(f"添加摄像头: {config.id} ({config.name})")
        return camera
    
    def remove_camera(self, camera_id: str) -> bool:
        """移除摄像头"""
        if camera_id in self._cameras:
            self._cameras[camera_id].stop()
            del self._cameras[camera_id]
            self._logger.info(f"移除摄像头: {camera_id}")
            return True
        return False
    
    def get_camera(self, camera_id: str) -> Optional[Camera]:
        """获取摄像头"""
        return self._cameras.get(camera_id)
    
    def get_all_cameras(self) -> List[Camera]:
        """获取所有摄像头"""
        return list(self._cameras.values())
    
    def start_all(self, blocking: bool = False):
        """启动所有摄像头
        
        Args:
            blocking: 是否阻塞式启动，默认 False（异步启动，不阻塞主线程）
        """
        for camera in self._cameras.values():
            camera.start(blocking=blocking)
        
        if not blocking:
            self._logger.info(f"已启动 {len(self._cameras)} 个摄像头的异步连接")
    
    def stop_all(self):
        """停止所有摄像头"""
        for camera in self._cameras.values():
            camera.stop()
    
    def get_frame(self, camera_id: str) -> Optional[np.ndarray]:
        """获取指定摄像头的帧"""
        camera = self._cameras.get(camera_id)
        if camera:
            return camera.get_frame()
        return None
    
    def initialize_from_config(self, cameras: List[CameraConfig]):
        """从配置初始化摄像头"""
        for config in cameras:
            self.add_camera(config)
        self._logger.info(f"从配置加载了 {len(cameras)} 个摄像头")
    
    def get_available_usb_cameras(self) -> List[dict]:
        """
        获取可用USB摄像头列表
        """
        import platform
        
        available_cameras = []
        
        # 获取当前正在使用（已配置）的设备索引
        used_indices = set()
        for cam in self._cameras.values():
            if cam.type == 'usb' and cam.config.device is not None:
                used_indices.add(cam.config.device)
        
        if platform.system() == "Linux":
            # Linux: 使用 v4l2-ctl 解析 USB 摄像头
            available_cameras = self._get_linux_usb_cameras(used_indices)
        else:
            # Windows: 原有逻辑
            available_cameras = self._get_windows_usb_cameras(used_indices)
        
        return available_cameras
    
    def _get_windows_usb_cameras(self, used_indices: set) -> List[dict]:
        """获取 Windows USB 摄像头列表"""
        available_cameras = []
        working_indices = []
        
        # 扫描索引 0-9
        for i in range(10):
            if i in used_indices:
                working_indices.append(i)
                continue
                
            try:
                cap = cv2.VideoCapture(i)
                if cap.isOpened():
                    ret, _ = cap.read()
                    if ret:
                        working_indices.append(i)
                    cap.release()
            except Exception as e:
                self._logger.warning(f"扫描摄像头索引 {i} 出错: {e}")
        
        # 获取设备名称
        names = self._get_camera_names_windows()
        
        for idx, device_index in enumerate(working_indices):
            name = f"摄像头设备 {device_index}"
            if idx < len(names) and names[idx]:
                name = names[idx]
            if device_index in used_indices:
                name += " (已添加)"
                
            available_cameras.append({
                "index": device_index,
                "name": name
            })
        
        return available_cameras
    
    def _get_linux_usb_cameras(self, used_indices: set) -> List[dict]:
        """
        获取 Linux USB 摄像头列表
        使用 v4l2-ctl 直接解析，排除 rkisp 等内置设备
        """
        import subprocess
        import re
        
        available_cameras = []
        usb_devices = []  # [(index, name), ...]
        
        self._logger.info("开始枚举 Linux USB 摄像头...")
        self._logger.info(f"已配置的设备索引: {used_indices}")
        
        # 1. 使用 v4l2-ctl 解析 USB 摄像头
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--list-devices"],
                capture_output=True, text=True, timeout=5
            )
            self._logger.info(f"v4l2-ctl 返回码: {result.returncode}")
            
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                self._logger.info(f"v4l2-ctl 输出行数: {len(lines)}")
                
                current_name = None
                current_is_usb = False
                
                for line in lines:
                    self._logger.debug(f"解析行: {repr(line)}")
                    
                    if not line.startswith('\t') and line.strip():
                        # 设备名称行
                        current_name = line.split('(')[0].strip()
                        # 判断是否是 USB 设备（包含 usb 关键字）
                        current_is_usb = 'usb' in line.lower()
                        self._logger.info(f"设备: {current_name}, 是USB: {current_is_usb}")
                    elif line.strip().startswith('/dev/video') and current_is_usb:
                        # USB 设备的 video 设备行
                        match = re.search(r'/dev/video(\d+)', line.strip())
                        if match:
                            video_idx = int(match.group(1))
                            usb_devices.append((video_idx, current_name))
                            self._logger.info(f"添加 USB 设备: index={video_idx}, name={current_name}")
                            # 只取每个设备的第一个 video 节点
                            current_is_usb = False
                            
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            self._logger.warning(f"v4l2-ctl 命令不可用: {e}，尝试备用方法")
        except Exception as e:
            self._logger.warning(f"v4l2-ctl 解析失败: {e}")
        
        self._logger.info(f"v4l2-ctl 解析到 {len(usb_devices)} 个 USB 设备: {usb_devices}")
        
        # 2. 如果 v4l2-ctl 失败，使用备用方法
        if not usb_devices:
            self._logger.info("v4l2-ctl 未找到设备，使用备用方法")
            usb_devices = self._get_linux_usb_cameras_fallback()
            self._logger.info(f"备用方法找到 {len(usb_devices)} 个 USB 设备: {usb_devices}")
        
        # 3. 验证设备可用性并组合结果
        for video_idx, name in usb_devices:
            self._logger.info(f"验证设备 video{video_idx} ({name})...")
            
            if video_idx in used_indices:
                # 已配置的设备，假设存在
                self._logger.info(f"  -> 已配置，跳过验证")
                available_cameras.append({
                    "index": video_idx,
                    "name": name + " (已添加)"
                })
            else:
                # 尝试打开验证
                try:
                    self._logger.info(f"  -> 尝试打开 cv2.VideoCapture({video_idx})")
                    cap = cv2.VideoCapture(video_idx)
                    opened = cap.isOpened()
                    self._logger.info(f"  -> isOpened: {opened}")
                    
                    if opened:
                        # USB 摄像头需要预热时间，尝试多次读取
                        import time
                        ret = False
                        frame = None
                        
                        for attempt in range(5):  # 最多尝试5次
                            ret, frame = cap.read()
                            if ret and frame is not None:
                                break
                            time.sleep(0.1)  # 等待100ms
                        
                        self._logger.info(f"  -> read() 返回: {ret}, frame shape: {frame.shape if ret and frame is not None else 'None'}")
                        cap.release()
                        
                        if ret:
                            available_cameras.append({
                                "index": video_idx,
                                "name": name
                            })
                            self._logger.info(f"  -> 验证成功，添加到列表")
                        else:
                            # 即使读取失败，如果能打开也添加（可能是暂时问题）
                            available_cameras.append({
                                "index": video_idx,
                                "name": name + " (待验证)"
                            })
                            self._logger.warning(f"  -> read() 失败，但设备可打开，添加为待验证")
                    else:
                        self._logger.warning(f"  -> 无法打开，跳过")
                except Exception as e:
                    self._logger.error(f"  -> 验证异常: {e}")
        
        self._logger.info(f"最终可用摄像头: {available_cameras}")
        return available_cameras
    
    def _get_linux_usb_cameras_fallback(self) -> List[tuple]:
        """
        备用方法：从 /sys/class/video4linux 获取 USB 摄像头
        """
        from pathlib import Path
        import re
        
        usb_devices = []
        
        try:
            video_path = Path("/sys/class/video4linux")
            if video_path.exists():
                for device in sorted(video_path.iterdir(), key=lambda x: x.name):
                    # 检查是否是 USB 设备
                    device_path = device / "device"
                    if device_path.exists():
                        real_path = device_path.resolve()
                        # USB 设备路径中包含 "usb"
                        if "usb" in str(real_path).lower():
                            name_file = device / "name"
                            name = f"USB Camera {device.name}"
                            if name_file.exists():
                                name = name_file.read_text().strip()
                            
                            # 过滤元数据设备
                            if "metadata" not in name.lower():
                                match = re.match(r'video(\d+)', device.name)
                                if match:
                                    video_idx = int(match.group(1))
                                    usb_devices.append((video_idx, name))
        except Exception:
            pass
        
        return usb_devices
    
    def _get_camera_names_windows(self) -> List[str]:
        """使用PowerShell获取Windows摄像头名称"""
        import subprocess
        names = []
        try:
            # 获取 Camera 和 Image 类的可用设备
            cmd = "Get-PnpDevice -Class Camera,Image -Status OK | Where-Object { $_.FriendlyName -ne $null } | Select-Object -ExpandProperty FriendlyName"
            result = subprocess.run(["powershell", "-Command", cmd], 
                                  capture_output=True, text=True, encoding='utf-8')
                                  
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                names = [line.strip() for line in lines if line.strip()]
        except Exception:
            pass
        return names


# 全局摄像头管理器实例
camera_manager = CameraManager()

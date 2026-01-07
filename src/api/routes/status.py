"""
实时状态API
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import List, Optional
import time
import platform

from ...zone.state_machine import zone_manager
from ...utils.config import get_config
from ...utils.logger import event_logger

router = APIRouter(tags=["status"])


class ZoneStatusResponse(BaseModel):
    """灶台状态响应"""
    id: str
    name: str
    state: str
    is_fire_on: bool
    has_person: bool
    no_person_duration: float
    warning_remaining: float
    alarm_remaining: float
    cutoff_remaining: float
    warning_timeout: int
    cutoff_timeout: int
    enabled: bool = True
    last_snapshot_path: Optional[str] = None


class DeviceInfoResponse(BaseModel):
    """设备信息响应"""
    name: str
    version: str
    uptime: float
    platform: str
    python_version: str


# 记录启动时间
_start_time = time.time()


@router.get("/status", response_model=List[ZoneStatusResponse])
async def get_all_status():
    """获取所有灶台的实时状态"""
    statuses = zone_manager.get_all_status()
    # 获取全局安全配置
    safety_config = get_config().safety
    
    return [
        ZoneStatusResponse(
            id=s["id"],
            name=s["name"],
            state=s["state"],
            is_fire_on=s["is_fire_on"],
            has_person=s["has_person"],
            no_person_duration=s["no_person_duration"],
            warning_remaining=s["warning_remaining"],
            alarm_remaining=s.get("alarm_remaining", 0.0),
            cutoff_remaining=s["cutoff_remaining"],
            warning_timeout=safety_config.warning_timeout,
            cutoff_timeout=safety_config.cutoff_timeout,
            enabled=s.get("enabled", True),
            last_snapshot_path=s.get("last_snapshot_path")
        )
        for s in statuses
    ]


@router.get("/status/{zone_id}", response_model=ZoneStatusResponse)
async def get_zone_status(zone_id: str):
    """获取单个灶台状态"""
    sm = zone_manager.get_zone(zone_id)
    if not sm:
        return {"error": "灶台不存在"}
    
    s = sm.get_status()
    # 获取全局安全配置
    safety_config = get_config().safety
    
    return ZoneStatusResponse(
        id=s["id"],
        name=s["name"],
        state=s["state"],
        is_fire_on=s["is_fire_on"],
        has_person=s["has_person"],
        no_person_duration=s["no_person_duration"],
        warning_remaining=s["warning_remaining"],
        alarm_remaining=s.get("alarm_remaining", 0.0),
        cutoff_remaining=s["cutoff_remaining"],
        warning_timeout=safety_config.warning_timeout,
        cutoff_timeout=safety_config.cutoff_timeout,
        enabled=s.get("enabled", True),
        last_snapshot_path=s.get("last_snapshot_path")
    )


@router.get("/device", response_model=DeviceInfoResponse)
async def get_device_info():
    """获取设备信息"""
    config = get_config()
    return DeviceInfoResponse(
        name=config.system.name,
        version=config.system.version,
        uptime=time.time() - _start_time,
        platform=platform.system(),
        python_version=platform.python_version()
    )


@router.get("/snapshots")
async def get_snapshots(zone_id: Optional[str] = None, limit: int = 10):
    """获取告警截图列表"""
    snapshots = event_logger.get_snapshots(zone_id, limit)
    return {"snapshots": snapshots}


@router.get("/logs/list")
async def list_log_files():
    """获取系统日志文件列表"""
    log_dir = event_logger._log_dir
    if not log_dir or not log_dir.exists():
        return {"files": []}
    
    files = []
    for f in sorted(log_dir.glob("*.log"), reverse=True):
        files.append({
            "name": f.name,
            "size": f.stat().st_size,
            "mtime": f.stat().st_mtime
        })
    return {"files": files}


@router.get("/logs/read")
async def read_log_file(filename: Optional[str] = None, lines: int = 200):
    """读取日志文件内容"""
    log_dir = event_logger._log_dir
    if not log_dir or not log_dir.exists():
        return {"content": "日志目录不存在"}
    
    if not filename:
        # 默认读取最新的日志
        files = sorted(log_dir.glob("*.log"), reverse=True)
        if not files:
            return {"content": "暂无日志文件"}
        file_path = files[0]
    else:
        file_path = log_dir / filename
        if not file_path.exists():
            return {"content": "日志文件不存在"}
    
    try:
        # 读取最后N行
        with open(file_path, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
            content = "".join(all_lines[-lines:])
            return {
                "filename": file_path.name,
                "content": content,
                "total_lines": len(all_lines)
            }
    except Exception as e:
        return {"content": f"读取失败: {str(e)}"}


@router.get("/performance")
async def get_performance():
    """
    获取系统性能指标
    
    返回：
    - inference_time_ms: 最近一次推理时间
    - avg_inference_time_ms: 平均推理时间
    - fps: 推理帧率
    - cpu_percent: CPU使用率
    - memory_mb: 内存使用（MB）
    """
    from ...utils.performance import performance_monitor
    
    stats = performance_monitor.get_stats_dict()
    
    return {
        "engine": get_config().inference.engine,
        "model": get_config().inference.model_path,
        **stats
    }


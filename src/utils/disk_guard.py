"""
磁盘空间看门狗

背景：设备 eMMC 仅 14G 且系统镜像缺 logrotate，曾有设备连续运行一年后
/var/log 撑满根分区，导致程序写配置/日志全部报 Errno 28 而故障。

职责（应用侧自动清理，系统侧由 deploy/log-hygiene.sh 兜底）：
- 周期巡检根分区（或应用所在分区）使用率
- 超过 warn 阈值（默认 85%）：按保留天数常规清理应用日志与告警快照，并告警
- 超过 critical 阈值（默认 92%）：激进清理——日志只留当天、快照只留 24 小时，
  同时清理 pip/http 缓存等可再生文件，尽量避免写盘失败拖垮主流程
- 清理基于文件 mtime 而非文件名日期：设备无 RTC 时钟会乱跳，按文件名解析
  日期做清理曾长期失效

系统日志（/var/log/syslog 等 root 文件）本进程（linaro 用户）无权清理，
由 log-hygiene.sh 安装的 logrotate 定时器与 journald 大小上限负责。
"""
import os
import shutil
import threading
import time
from pathlib import Path

from .config import DiskGuardConfig
from .logger import get_logger


class DiskGuard:
    """磁盘空间看门狗（单实例，后台 daemon 线程）"""

    def __init__(self, config: DiskGuardConfig, base_dir: Path):
        self._config = config
        self._base_dir = base_dir
        self._stop_event = threading.Event()
        self._thread: threading.Thread = None

    # ---------- 生命周期 ----------

    def start(self):
        if not self._config.enabled:
            get_logger().info("磁盘看门狗未启用（disk_guard.enabled=false）")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="disk-guard", daemon=True)
        self._thread.start()
        get_logger().info(
            f"磁盘看门狗已启动: 巡检周期 {self._config.check_interval_seconds}s, "
            f"warn≥{self._config.warn_usage_pct}% critical≥{self._config.critical_usage_pct}%"
        )

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _run(self):
        # 启动后先等一小段时间，避开系统初始化高峰
        if self._stop_event.wait(30):
            return
        while not self._stop_event.wait(self._config.check_interval_seconds):
            try:
                self.check_and_cleanup()
            except Exception as e:
                # 看门狗自身异常绝不外泄影响主流程
                try:
                    get_logger().error(f"磁盘看门狗巡检异常: {e}")
                except Exception:
                    pass

    # ---------- 核心逻辑 ----------

    def disk_usage_pct(self) -> float:
        usage = shutil.disk_usage(self._base_dir)
        return usage.used / usage.total * 100.0

    def check_and_cleanup(self) -> float:
        """巡检一次；返回当前使用率。可单测直接调用。"""
        logger = get_logger()
        pct = self.disk_usage_pct()

        if pct >= self._config.critical_usage_pct:
            logger.critical(
                f"磁盘使用率 {pct:.1f}% 超过临界阈值 {self._config.critical_usage_pct}%，执行激进清理"
            )
            self._emergency_cleanup()
        elif pct >= self._config.warn_usage_pct:
            logger.warning(
                f"磁盘使用率 {pct:.1f}% 超过告警阈值 {self._config.warn_usage_pct}%，执行常规清理"
            )
            self._routine_cleanup()
        else:
            logger.debug(f"磁盘使用率 {pct:.1f}%，正常")

        after = self.disk_usage_pct()
        if after < pct:
            logger.info(f"磁盘清理完成: {pct:.1f}% -> {after:.1f}%")
        return after

    # ---------- 清理策略 ----------

    def _routine_cleanup(self):
        """常规清理：按保留天数清理过期日志与快照（与 EventLogger 保留期一致，兜底其清理失效）"""
        self._cleanup_logs_by_mtime(max_age_days=7)
        self._cleanup_snapshots(max_age_days=3)

    def _emergency_cleanup(self):
        """激进清理：只保最近 24h 的日志与快照，并清可再生缓存"""
        self._cleanup_logs_by_mtime(max_age_days=1)
        self._cleanup_snapshots(max_age_days=1)
        # pip 缓存可再生（升级时重新下载），bootstrap 日志同理
        shutil.rmtree(Path.home() / ".cache" / "pip", ignore_errors=True)
        for pycache in self._base_dir.rglob("__pycache__"):
            shutil.rmtree(pycache, ignore_errors=True)

    def _cleanup_logs_by_mtime(self, max_age_days: int):
        """按 mtime 清理 logs/ 下过期 .log 文件（不依赖文件名日期，抗时钟乱跳）"""
        log_dir = self._base_dir / "logs"
        cutoff = time.time() - max_age_days * 86400
        removed = 0
        for f in log_dir.glob("*.log"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        if removed:
            get_logger().info(f"磁盘看门狗清理过期日志 {removed} 个（>{max_age_days}天）")

    def _cleanup_snapshots(self, max_age_days: int):
        """按 mtime 清理 snapshots/ 下过期快照"""
        snap_dir = self._base_dir / "snapshots"
        cutoff = time.time() - max_age_days * 86400
        removed = 0
        for f in snap_dir.glob("*.jpg"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        if removed:
            get_logger().info(f"磁盘看门狗清理过期快照 {removed} 个（>{max_age_days}天）")

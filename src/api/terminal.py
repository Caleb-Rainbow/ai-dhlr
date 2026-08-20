"""
Web 终端会话管理

在设备本机通过 PTY 创建交互式会话（bash / Python REPL），前端经
WebSocket 终端协议交互（terminal_start/input/resize/stop 请求 +
terminal_output/terminal_exit 定向推送），效果等效于 SSH 登录设备
（运行用户与主服务一致）。

输出只推送给创建会话的连接（本地 send_personal / 远程 send_to_remote），
不广播，避免 shell 输出泄露给同一设备的其他浏览器。

PTY 相关模块（pty/termios/fcntl/pwd）为 Linux 标准库，在此延迟导入；
Windows 开发机上 start_session 返回明确错误而非崩溃。
"""
import asyncio
import base64
import os
import signal
import struct
import sys
import time
import uuid
from collections import deque
from typing import Any, Dict, Optional

from starlette.websockets import WebSocketState

from ..utils.config import config_manager
from ..utils.logger import get_logger
from .websocket import message_dispatcher, ws_manager

logger = get_logger()

# 远程链路来源标记：请求经 Java 网关转发而来，输出走 message_dispatcher.send_to_remote
REMOTE_SENDER = "remote"

# 并发会话上限（设备仅 2GB 内存；Python REPL import 项目依赖时可达数十 MB）
MAX_SESSIONS = 2
# 单次读取 PTY 输出的最大字节数
READ_CHUNK = 65536
# 待推送输出缓冲上限：超出后丢弃最旧数据并插入截断标记（防止 cat 大文件撑爆内存）
MAX_PENDING_BYTES = 256 * 1024
# 空闲回收：无输入且无输出持续该时长后自动结束会话
IDLE_TIMEOUT_SECONDS = 15 * 60


class TerminalSession:
    """单个 PTY 终端会话"""

    def __init__(self, manager: "TerminalManager", session_id: str, shell: str,
                 master_fd: int, proc, owner: Any):
        self.manager = manager
        self.session_id = session_id
        self.shell = shell
        self.master_fd = master_fd
        self.proc = proc
        # 会话归属：创建该会话的本地 WebSocket 对象，或 REMOTE_SENDER
        self.owner = owner
        self.last_activity = time.time()

        self._out_deque: deque = deque()
        self._pending_bytes = 0
        self._out_event = asyncio.Event()
        self._exit_reason: Optional[str] = None
        self._finished = False
        self._drain_task: Optional[asyncio.Task] = None
        self._exit_task: Optional[asyncio.Task] = None
        self._stop_task: Optional[asyncio.Task] = None

    def start(self):
        """启动输出读取与退出监听（须在事件循环内调用）"""
        loop = asyncio.get_running_loop()
        loop.add_reader(self.master_fd, self._on_readable)
        self._drain_task = asyncio.create_task(self._drain_output())
        self._exit_task = asyncio.create_task(self._wait_exit())

    # ==================== 输入 ====================

    def write_input(self, data: str):
        if self._finished:
            return
        self.last_activity = time.time()
        # errors=replace：JSON 可携带孤立代理对，直接 encode 会抛 UnicodeEncodeError
        raw = data.encode("utf-8", errors="replace")
        while raw:
            try:
                written = os.write(self.master_fd, raw)
                raw = raw[written:]
            except BlockingIOError:
                # PTY 输入缓冲满（前台进程暂不读输入），丢弃剩余避免阻塞事件循环
                break
            except OSError:
                break

    def resize(self, cols: int, rows: int):
        if self._finished:
            return
        import fcntl
        import termios
        try:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)
            # SIGWINCH 为 POSIX 专属；本方法只在已创建会话（Linux）后可达
            os.kill(self.proc.pid, signal.SIGWINCH)
        except (OSError, ProcessLookupError):
            pass

    # ==================== 输出 ====================

    def _on_readable(self):
        try:
            chunk = os.read(self.master_fd, READ_CHUNK)
        except BlockingIOError:
            return
        except OSError:
            # EIO：子进程已退出（Linux PTY 的 EOF 表现），停止读取；退出事件由 _wait_exit 上报
            self._remove_reader()
            return
        if not chunk:
            self._remove_reader()
            return
        self._enqueue(chunk)

    def _remove_reader(self):
        try:
            asyncio.get_running_loop().remove_reader(self.master_fd)
        except Exception:
            pass

    def _enqueue(self, chunk: bytes):
        """输出入队，超过缓冲上限时丢弃最旧数据并插入截断标记"""
        self._out_deque.append(chunk)
        self._pending_bytes += len(chunk)
        dropped = 0
        while self._pending_bytes > MAX_PENDING_BYTES and len(self._out_deque) > 1:
            old = self._out_deque.popleft()
            self._pending_bytes -= len(old)
            dropped += len(old)
        if dropped:
            marker = f"\r\n[终端输出超出缓冲上限，已丢弃最旧的 {dropped} 字节]\r\n".encode("utf-8")
            self._out_deque.appendleft(marker)
            self._pending_bytes += len(marker)
        self._out_event.set()

    async def _drain_output(self):
        while not self._finished:
            await self._out_event.wait()
            self._out_event.clear()
            while self._out_deque and not self._finished:
                chunk = self._out_deque.popleft()
                self._pending_bytes -= len(chunk)
                await self._send_output(chunk)

    async def _send_output(self, chunk: bytes):
        if self._finished:
            return
        self.last_activity = time.time()
        message = {
            "type": "terminal_output",
            "timestamp": int(time.time() * 1000),
            "device_id": config_manager.config.system.device_id,
            "data": {
                "session_id": self.session_id,
                "chunk_b64": base64.b64encode(chunk).decode("ascii"),
            },
        }
        if not await self._push(message):
            # 所属连接已断开，异步回收（不在推送协程内嵌套停止逻辑）
            self._schedule_stop("connection_closed")

    async def _push(self, message: dict) -> bool:
        """向会话所属连接定向推送消息，返回连接是否仍然存活"""
        try:
            if self.owner == REMOTE_SENDER:
                remote = message_dispatcher.remote_client
                if not (remote and remote.is_connected):
                    return False
                await message_dispatcher.send_to_remote(message)
                return True
            if self.owner.client_state != WebSocketState.CONNECTED:
                return False
            # send_personal 内部吞掉发送异常（连接已死时由 server.py 的
            # 断连钩子回收会话），单次失败在此无法感知
            await ws_manager.send_personal(self.owner, message)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 单次发送失败不视为连接死亡（如瞬时缓冲满）
            logger.warning(f"终端消息推送失败 session={self.session_id}: {e}")
            return True

    # ==================== 生命周期 ====================

    async def _wait_exit(self):
        try:
            exit_code = await self.proc.wait()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.warning(f"等待终端进程退出失败 session={self.session_id}: {e}")
            exit_code = None
        reason = self._exit_reason or "exited"
        await self._finish(reason, exit_code)

    def _schedule_stop(self, reason: str):
        # 保存引用：create_task 的裸任务仅被事件循环弱引用，可能被 GC 中途回收
        self._stop_task = asyncio.create_task(self._stop_quietly(reason))

    async def _stop_quietly(self, reason: str):
        try:
            await self.stop(reason)
        except Exception as e:
            logger.warning(f"结束终端会话失败 session={self.session_id}: {e}")

    async def stop(self, reason: str):
        """结束会话：杀掉进程组，等待收尾完成（推送 terminal_exit、清理 fd）"""
        if self._finished:
            return
        self._exit_reason = reason
        killed = self._kill_process_group()
        if not killed:
            await self._finish(reason, None)
            return
        # SIGKILL 后进程随即退出，_wait_exit 会完成收尾；最多等 5 秒兜底
        for _ in range(50):
            if self._finished:
                return
            await asyncio.sleep(0.1)
        await self._finish(reason, None)

    def _kill_process_group(self) -> bool:
        """杀掉会话进程组（含前台子进程），返回是否视为已终止"""
        killpg = getattr(os, "killpg", None)  # Windows 无 killpg（仅单测会走到）
        if killpg is None:
            return True
        try:
            # start_new_session 使子进程为进程组长，pgid == pid
            killpg(self.proc.pid, signal.SIGKILL)
            return True
        except ProcessLookupError:
            return True  # 进程已退出
        except OSError as e:
            logger.warning(f"终止终端进程组失败 session={self.session_id}: {e}")
            return False

    async def _finish(self, reason: str, exit_code: Optional[int]):
        """会话收尾（幂等）：停止读取、清理进程组、推送结束事件、释放资源"""
        if self._finished:
            return
        self._finished = True
        self._remove_reader()
        # 先等协程任务真正退出再推送 terminal_exit，避免 exit 抢在最后一块
        # terminal_output 之前到达；不得取消当前任务自身（_finish 可能从
        # _stop_task 内被调用，自取消会中断后续清理）
        current = asyncio.current_task()
        for task in (self._drain_task, self._stop_task):
            if task and not task.done() and task is not current:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self._kill_process_group()

        message = {
            "type": "terminal_exit",
            "timestamp": int(time.time() * 1000),
            "device_id": config_manager.config.system.device_id,
            "data": {"session_id": self.session_id, "reason": reason},
        }
        if exit_code is not None:
            message["data"]["exit_code"] = exit_code
        await self._push(message)

        if self._exit_task and not self._exit_task.done() \
                and asyncio.current_task() is not self._exit_task:
            self._exit_task.cancel()
        try:
            os.close(self.master_fd)
        except OSError:
            pass
        self.manager.on_session_finished(self)
        logger.info(
            f"终端会话结束: session={self.session_id}, shell={self.shell}, "
            f"reason={reason}, exit_code={exit_code}"
        )


class TerminalManager:
    """终端会话注册表与生命周期管理"""

    def __init__(self):
        self._sessions: Dict[str, TerminalSession] = {}
        self._lock = asyncio.Lock()
        self._watchdog_task: Optional[asyncio.Task] = None

    async def start_session(self, shell: str, cols: int, rows: int, owner: Any) -> dict:
        api_cfg = getattr(config_manager.config, "api", None)
        if api_cfg is not None and not getattr(api_cfg, "terminal_enabled", True):
            raise ValueError("终端功能已被禁用")
        if len(self._sessions) >= MAX_SESSIONS:
            raise ValueError(f"终端会话数已达上限 ({MAX_SESSIONS})")
        if sys.platform.startswith("win"):
            raise ValueError("终端功能仅支持 Linux 设备")

        import fcntl
        import pty as pty_module
        import termios

        async with self._lock:
            if len(self._sessions) >= MAX_SESSIONS:
                raise ValueError(f"终端会话数已达上限 ({MAX_SESSIONS})")

            try:
                master_fd, slave_fd = pty_module.openpty()
            except Exception as e:
                raise ValueError(f"创建 PTY 失败: {e}")

            try:
                # 初始窗口尺寸设在 slave 上，子进程启动即可见
                fcntl.ioctl(slave_fd, termios.TIOCSWINSZ,
                            struct.pack("HHHH", rows, cols, 0, 0))
                # master 非阻塞：读回调不阻塞事件循环，写满时返回 BlockingIOError
                os.set_blocking(master_fd, False)
                argv, env = self._build_shell_command(shell)
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                    start_new_session=True, env=env,
                )
            except Exception as e:
                os.close(master_fd)
                os.close(slave_fd)
                raise ValueError(f"创建终端会话失败: {e}")
            # 父进程关闭 slave：子进程退出后 master 才会读到 EOF/EIO
            os.close(slave_fd)

            # 注册必须在锁内完成：否则并发 start_session 可能双双通过
            # 上限检查，导致会话数突破 MAX_SESSIONS
            session_id = f"term_{uuid.uuid4().hex[:12]}"
            session = TerminalSession(self, session_id, shell, master_fd, proc, owner)
            self._sessions[session_id] = session

        try:
            session.start()
        except Exception as e:
            # 读取/监听任务启动失败时立即回收，避免僵尸会话占用并发名额
            self._sessions.pop(session_id, None)
            session._kill_process_group()
            try:
                os.close(master_fd)
            except OSError:
                pass
            raise ValueError(f"启动终端会话失败: {e}")

        self._ensure_watchdog()
        owner_desc = "remote" if owner == REMOTE_SENDER else "local"
        logger.info(
            f"终端会话已创建: session={session_id}, shell={shell}, "
            f"pid={proc.pid}, owner={owner_desc}"
        )
        return {"session_id": session_id, "shell": shell, "pid": proc.pid}

    @staticmethod
    def _build_shell_command(shell: str):
        """构造 shell 启动命令与环境变量"""
        env = dict(os.environ)
        env.setdefault("TERM", "xterm-256color")
        if shell == "python":
            env["PYTHONUNBUFFERED"] = "1"
            argv = [sys.executable, "-i"]
        else:
            argv = ["/bin/bash", "-i"]
        try:
            import pwd
            env.setdefault("HOME", pwd.getpwuid(os.getuid()).pw_dir)
        except (ImportError, KeyError):
            pass
        # 把运行环境解释器目录前置到 PATH，终端里 python/pip 即为设备运行环境
        # （用 os.pathsep 而非 sys.pathsep，兼容属性被裁剪的定制解释器）
        env["PATH"] = os.path.dirname(os.path.abspath(sys.executable)) + \
            os.pathsep + env.get("PATH", "")
        return argv, env

    def _require(self, session_id, owner) -> TerminalSession:
        session = self._sessions.get(session_id)
        if session is None or session.owner != owner:
            raise ValueError(f"终端会话 '{session_id}' 不存在或已结束")
        return session

    async def write_input(self, session_id: str, data: str, owner) -> dict:
        session = self._require(session_id, owner)
        session.write_input(data)
        return {}

    async def resize_session(self, session_id: str, cols: int, rows: int, owner) -> dict:
        session = self._require(session_id, owner)
        session.resize(cols, rows)
        return {}

    async def stop_session(self, session_id: str, owner) -> dict:
        session = self._require(session_id, owner)
        await session.stop("stopped")
        return {}

    async def on_connection_closed(self, owner):
        """某连接断开后回收其创建的全部终端会话（幂等）"""
        for session in list(self._sessions.values()):
            if session.owner == owner and not session._finished:
                await session.stop("connection_closed")

    async def shutdown(self):
        """应用关闭时回收全部会话"""
        for session in list(self._sessions.values()):
            if not session._finished:
                await session.stop("server_shutdown")
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
            self._watchdog_task = None

    def on_session_finished(self, session: TerminalSession):
        self._sessions.pop(session.session_id, None)

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    def _ensure_watchdog(self):
        if self._watchdog_task is None or self._watchdog_task.done():
            self._watchdog_task = asyncio.create_task(self._idle_watchdog())

    async def _idle_watchdog(self):
        try:
            while self._sessions:
                await asyncio.sleep(60)
                now = time.time()
                for session in list(self._sessions.values()):
                    try:
                        if now - session.last_activity > IDLE_TIMEOUT_SECONDS \
                                and not session._finished:
                            logger.info(f"终端会话空闲超时: session={session.session_id}")
                            await session.stop("idle_timeout")
                    except Exception as e:
                        # 单个会话回收失败不能杀死看门狗，否则其余会话再无回收机会
                        logger.warning(f"空闲回收终端会话失败 session={session.session_id}: {e}")
        except asyncio.CancelledError:
            pass


# 全局终端管理器单例
terminal_manager = TerminalManager()

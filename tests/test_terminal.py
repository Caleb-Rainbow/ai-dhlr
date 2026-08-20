"""
终端会话管理单元测试

覆盖会话注册表、并发上限、所有权校验、输出缓冲截断、结束事件推送等
纯逻辑（mock PTY，Windows 可跑）；真实 PTY 交互仅在 Linux 上执行。
"""
import asyncio
import base64
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.api import terminal as terminal_module
from src.api.terminal import (
    MAX_PENDING_BYTES,
    MAX_SESSIONS,
    REMOTE_SENDER,
    TerminalManager,
    TerminalSession,
)


def make_session(manager, session_id="term_test", owner=None, shell="bash"):
    """构造不触碰真实 fd/进程的会话对象"""
    session = TerminalSession(
        manager=manager,
        session_id=session_id,
        shell=shell,
        master_fd=-1,
        proc=SimpleNamespace(pid=-999999),
        owner=owner if owner is not None else SimpleNamespace(),
    )
    # 单测环境无 killpg/真实进程，直接视为已终止
    session._kill_process_group = lambda: True
    return session


# ==================== shell 命令构造 ====================

def test_build_shell_command_bash():
    argv, env = TerminalManager._build_shell_command("bash")
    assert argv == ["/bin/bash", "-i"]
    assert env["TERM"] == "xterm-256color"
    # 运行环境解释器目录应前置到 PATH
    assert env["PATH"].split(os.pathsep)[0] == os.path.dirname(sys.executable)


def test_build_shell_command_python():
    argv, env = TerminalManager._build_shell_command("python")
    assert argv[0] == sys.executable
    assert "-i" in argv
    assert env["PYTHONUNBUFFERED"] == "1"


def test_build_shell_command_keeps_existing_term():
    import os
    old = os.environ.get("TERM")
    os.environ["TERM"] = "vt100"
    try:
        _, env = TerminalManager._build_shell_command("bash")
        assert env["TERM"] == "vt100"
    finally:
        if old is None:
            os.environ.pop("TERM", None)
        else:
            os.environ["TERM"] = old


# ==================== 输出缓冲截断 ====================

async def test_enqueue_truncates_when_over_limit():
    manager = TerminalManager()
    session = make_session(manager)

    big = b"x" * 60000
    for _ in range(10):  # 600KB，远超 256KB 上限
        session._enqueue(big)

    assert session._pending_bytes <= MAX_PENDING_BYTES + 200  # 截断标记自身可略超
    chunks = list(session._out_deque)
    # 最旧数据被丢弃，队首是截断标记
    assert b"\r\n" in chunks[0] and "已丢弃" in chunks[0].decode("utf-8")
    # 总量明显小于入队总量
    assert sum(len(c) for c in chunks) < 60000 * 10 - 300000


async def test_enqueue_small_output_kept_intact():
    manager = TerminalManager()
    session = make_session(manager)
    session._enqueue(b"hello")
    session._enqueue(b"world")
    assert list(session._out_deque) == [b"hello", b"world"]


async def test_enqueue_wakes_drain_and_pushes_output():
    """回归测试：_enqueue 必须置位 _out_event，否则 drain 协程永久挂起、
    terminal_output 永远不会被推送（曾在设备上表现为终端黑屏）"""
    manager = TerminalManager()
    session = make_session(manager)
    pushed = []

    async def fake_push(message):
        pushed.append(message)
        return True

    session._push = fake_push
    session._drain_task = asyncio.create_task(session._drain_output())

    session._enqueue(b"hello")
    await asyncio.sleep(0.1)
    assert len(pushed) == 1
    assert pushed[0]["type"] == "terminal_output"
    assert pushed[0]["data"]["session_id"] == session.session_id
    assert pushed[0]["data"]["chunk_b64"] == base64.b64encode(b"hello").decode("ascii")
    assert session._pending_bytes == 0

    await session._finish("stopped", 0)


# ==================== 所有权与会话校验 ====================

async def test_write_input_rejects_wrong_owner():
    manager = TerminalManager()
    owner_a, owner_b = object(), object()
    session = make_session(manager, owner=owner_a)
    manager._sessions[session.session_id] = session

    with pytest.raises(ValueError, match="不存在或已结束"):
        await manager.write_input(session.session_id, "ls", owner_b)
    with pytest.raises(ValueError, match="不存在或已结束"):
        await manager.write_input("term_unknown", "ls", owner_a)

    await manager.write_input(session.session_id, "ls", owner_a)  # 归属者可写


async def test_stop_session_rejects_wrong_owner():
    manager = TerminalManager()
    session = make_session(manager, owner=object())
    manager._sessions[session.session_id] = session
    with pytest.raises(ValueError, match="不存在或已结束"):
        await manager.stop_session(session.session_id, object())


# ==================== 并发上限与开关 ====================

async def test_start_session_limit():
    manager = TerminalManager()
    for i in range(MAX_SESSIONS):
        manager._sessions[f"term_fake_{i}"] = make_session(manager)
    with pytest.raises(ValueError, match="终端会话数已达上限"):
        await manager.start_session("bash", 80, 24, owner=object())


async def test_start_session_disabled(monkeypatch):
    # config_manager.config 是只读 property，整体替换 terminal 模块内的引用
    monkeypatch.setattr(
        terminal_module, "config_manager",
        SimpleNamespace(config=SimpleNamespace(
            api=SimpleNamespace(terminal_enabled=False))),
    )
    with pytest.raises(ValueError, match="终端功能已被禁用"):
        await TerminalManager().start_session("bash", 80, 24, owner=object())


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="仅验证 Windows 上的报错")
async def test_start_session_windows_error():
    with pytest.raises(ValueError, match="仅支持 Linux"):
        await TerminalManager().start_session("bash", 80, 24, owner=object())


# ==================== 结束事件与清理 ====================

class _Capture:
    def __init__(self):
        self.messages = []

    async def __call__(self, owner, message):
        self.messages.append(message)


async def test_finish_pushes_exit_and_unregisters(monkeypatch):
    from starlette.websockets import WebSocketState

    manager = TerminalManager()
    owner = SimpleNamespace(client_state=WebSocketState.CONNECTED)
    capture = _Capture()
    monkeypatch.setattr(terminal_module.ws_manager, "send_personal", capture)

    session = make_session(manager, owner=owner)
    manager._sessions[session.session_id] = session
    assert manager.session_count == 1

    await session._finish("stopped", exit_code=0)
    await session._finish("stopped", exit_code=0)  # 幂等：不重复推送

    assert manager.session_count == 0
    assert len(capture.messages) == 1
    msg = capture.messages[0]
    assert msg["type"] == "terminal_exit"
    assert msg["data"]["session_id"] == session.session_id
    assert msg["data"]["reason"] == "stopped"
    assert msg["data"]["exit_code"] == 0
    assert "timestamp" in msg and "device_id" in msg


async def test_push_local_dead_owner_returns_false():
    from starlette.websockets import WebSocketState

    manager = TerminalManager()
    session = make_session(manager, owner=SimpleNamespace(
        client_state=WebSocketState.DISCONNECTED))
    assert await session._push({"type": "terminal_output"}) is False


async def test_push_remote_owner_without_link_returns_false(monkeypatch):
    manager = TerminalManager()
    session = make_session(manager, owner=REMOTE_SENDER)
    # remote_client 是只读 property，整体替换分发器引用；无远程连接时视为断开
    monkeypatch.setattr(
        terminal_module, "message_dispatcher",
        SimpleNamespace(remote_client=SimpleNamespace(is_connected=False)))
    assert await session._push({"type": "terminal_output"}) is False


async def test_finish_after_finish_is_noop():
    manager = TerminalManager()
    session = make_session(manager)
    await session._finish("exited", 0)
    await session.stop("stopped")  # 已结束，直接返回
    assert session._finished


async def test_idle_watchdog_kills_expired_session(monkeypatch):
    manager = TerminalManager()
    session = make_session(manager)
    stopped_reasons = []

    async def fake_stop(reason):
        stopped_reasons.append(reason)

    session.stop = fake_stop
    manager._sessions[session.session_id] = session
    session.last_activity = time.time() - 16 * 60  # 超过 15 分钟空闲

    # 驱动真实 watchdog 循环：两轮 sleep 后清空会话使其自然退出
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 2:
            manager._sessions.clear()

    monkeypatch.setattr(terminal_module.asyncio, "sleep", fake_sleep)
    await manager._idle_watchdog()
    assert sleeps == [60, 60]  # 循环跑了整两轮
    assert stopped_reasons == ["idle_timeout"]


async def test_on_connection_closed_stops_owned_sessions():
    manager = TerminalManager()
    owner = object()
    mine = make_session(manager, session_id="term_mine", owner=owner)
    other = make_session(manager, session_id="term_other", owner=object())
    stopped = []
    for s in (mine, other):
        s.stop = lambda reason, _s=s: stopped.append((_s.session_id, reason)) or asyncio.sleep(0)
    manager._sessions.update({mine.session_id: mine, other.session_id: other})

    await manager.on_connection_closed(owner)
    assert stopped == [("term_mine", "connection_closed")]


# ==================== 真实 PTY 交互（仅 Linux） ====================

@pytest.mark.skipif(sys.platform.startswith("win"), reason="PTY 仅 Linux")
async def test_real_bash_session_roundtrip(monkeypatch):
    from starlette.websockets import WebSocketState

    manager = TerminalManager()
    owner = SimpleNamespace(client_state=WebSocketState.CONNECTED)
    capture = _Capture()
    monkeypatch.setattr(terminal_module.ws_manager, "send_personal", capture)

    info = await manager.start_session("bash", 80, 24, owner=owner)
    session_id = info["session_id"]
    assert info["shell"] == "bash"
    assert manager.session_count == 1

    try:
        await manager.write_input(session_id, "echo DHLR_TEST_OK\n", owner)
        deadline = time.time() + 10
        combined = b""
        while time.time() < deadline:
            await asyncio.sleep(0.2)
            for msg in list(capture.messages):
                if msg["type"] == "terminal_output":
                    import base64
                    combined += base64.b64decode(msg["data"]["chunk_b64"])
                    capture.messages.remove(msg)
            if b"DHLR_TEST_OK" in combined:
                break
        assert b"DHLR_TEST_OK" in combined
    finally:
        await manager.stop_session(session_id, owner)

    assert manager.session_count == 0
    exit_msgs = [m for m in capture.messages if m["type"] == "terminal_exit"]
    assert exit_msgs and exit_msgs[0]["data"]["reason"] == "stopped"

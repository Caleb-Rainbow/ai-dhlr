"""远程状态广播订阅判断回归测试。"""

from types import SimpleNamespace

from src.api import server


def test_status_broadcast_enabled_for_local_client(monkeypatch):
    monkeypatch.setattr(server.ws_manager, "active_connections", {object()})
    monkeypatch.setattr(
        server.message_dispatcher, "_remote_client", SimpleNamespace(is_connected=False)
    )

    assert server._has_status_subscribers() is True


def test_status_broadcast_enabled_for_remote_without_local_client(monkeypatch):
    monkeypatch.setattr(server.ws_manager, "active_connections", set())
    monkeypatch.setattr(
        server.message_dispatcher, "_remote_client", SimpleNamespace(is_connected=True)
    )

    assert server._has_status_subscribers() is True


def test_status_broadcast_disabled_without_any_client(monkeypatch):
    monkeypatch.setattr(server.ws_manager, "active_connections", set())
    monkeypatch.setattr(
        server.message_dispatcher, "_remote_client", SimpleNamespace(is_connected=False)
    )

    assert server._has_status_subscribers() is False

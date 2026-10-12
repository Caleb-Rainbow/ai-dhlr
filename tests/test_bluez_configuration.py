"""Old-device migration must preserve site settings and recover from restart failure."""

import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest

from deploy.configure_bluez import configure_content, migrate


@pytest.mark.parametrize("original", [
    b"", b"# site config\n", b"[General]", b"[General]\nName=Kitchen\n",
    b"[Policy]\nAutoEnable=true\n",
])
def test_add_missing_setting(original):
    updated = configure_content(original)
    assert b"[General]\nReverseServiceDiscovery=false\n" in updated
    assert configure_content(updated) == updated


def test_change_general_only_and_preserve_comments_bom_crlf():
    original = (
        b"\xef\xbb\xbf# kitchen\r\n[General]\r\n"
        b"#ReverseServiceDiscovery=true\r\n"
        b"  ReverseServiceDiscovery = true\r\nName=Kitchen\r\n"
        b"[Other]\r\nReverseServiceDiscovery=true\r\n"
    )
    expected = original.replace(b"  ReverseServiceDiscovery = true", b"  ReverseServiceDiscovery = false")
    assert configure_content(original) == expected


def test_duplicate_general_settings_cannot_override_migration():
    original = b"[General]\nReverseServiceDiscovery=false\nReverseServiceDiscovery=true\n"
    assert configure_content(original).count(b"ReverseServiceDiscovery=false") == 2


def test_backup_permissions_restart_and_repeat(tmp_path):
    path = tmp_path / "main.conf"
    original = b"[General]\nName=Kitchen\nReverseServiceDiscovery=true\n"
    path.write_bytes(original)
    path.chmod(0o640)
    before_mode = stat.S_IMODE(path.stat().st_mode)
    calls = []
    backup = migrate(path, lambda: calls.append("restart"))
    assert backup.read_bytes() == original
    assert stat.S_IMODE(path.stat().st_mode) == before_mode
    assert calls == ["restart"]
    assert migrate(path, lambda: calls.append("restart")) is None
    assert calls == ["restart"]
    assert len(list(tmp_path.glob("*.dhlr-backup-*"))) == 1


@pytest.mark.parametrize("existed", [False, True])
def test_restart_failure_rolls_back_and_can_retry(tmp_path, existed):
    path = tmp_path / "main.conf"
    original = b"[General]\nName=Kitchen\n"
    if existed:
        path.write_bytes(original)
    calls = []

    def failing_restart():
        calls.append("restart")
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("systemctl", 30)

    with pytest.raises(RuntimeError, match="已恢复原配置"):
        migrate(path, failing_restart)
    assert calls == ["restart", "restart"]
    assert path.exists() == existed
    if existed:
        assert path.read_bytes() == original
    migrate(path, lambda: calls.append("retry"))
    assert calls[-1] == "retry"
    assert b"ReverseServiceDiscovery=false" in path.read_bytes()


def test_invalid_config_is_not_changed_or_restarted(tmp_path):
    path = tmp_path / "main.conf"
    original = b"[General\nName=Kitchen\n"
    path.write_bytes(original)
    calls = []
    with pytest.raises(ValueError, match="未修改文件"):
        migrate(path, lambda: calls.append("restart"))
    assert path.read_bytes() == original
    assert not calls
    assert not list(tmp_path.glob("*.dhlr-backup-*"))


@pytest.mark.skipif(os.name == "nt", reason="Windows symlink creation requires elevated rights")
def test_symlink_target_is_updated_without_replacing_link(tmp_path):
    target = tmp_path / "site.conf"
    target.write_bytes(b"[General]\nName=Kitchen\n")
    link = tmp_path / "main.conf"
    link.symlink_to(target)
    migrate(link)
    assert link.is_symlink()
    assert b"ReverseServiceDiscovery=false" in target.read_bytes()


@pytest.mark.skipif(os.name == "nt", reason="Deployment integration uses Linux paths and commands")
def test_old_updater_bootstrap_migrates_then_restores_ble(tmp_path):
    root = Path(__file__).resolve().parents[1]
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    for name in ("bootstrap-ble.sh", "configure_bluez.py", "ai-dhlr-ble.service"):
        shutil.copy2(root / "deploy" / name, deploy / name)
    systemd = tmp_path / "systemd"
    systemd.mkdir()
    path = tmp_path / "main.conf"
    path.write_bytes(b"[General]\nName=Kitchen\nReverseServiceDiscovery=true\n")
    commands = tmp_path / "bin"
    commands.mkdir()
    trace = tmp_path / "commands.log"
    systemctl = commands / "systemctl"
    systemctl.write_text(
        '#!/bin/bash\n'
        'echo "$*" >> "$TEST_TRACE"\n'
        'if [ "$1" = restart ] && [ "$2" = bluetooth.service ]; then\n'
        '  grep -q "^ReverseServiceDiscovery=false$" "$BLUEZ_CONFIG_PATH" || exit 1\n'
        'fi\n', encoding="utf-8"
    )
    systemctl.chmod(0o755)
    dpkg = commands / "dpkg"
    dpkg.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    dpkg.chmod(0o755)
    env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ["PATH"],
               BLE_SYSTEMD_DIR=str(systemd), BLUEZ_CONFIG_PATH=str(path), TEST_TRACE=str(trace))
    result = subprocess.run(["bash", str(deploy / "bootstrap-ble.sh")], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    first = trace.read_text().splitlines()
    assert first.index("restart bluetooth.service") < first.index("restart ai-dhlr-ble.service")
    assert "Name=Kitchen" in path.read_text()
    trace.write_text("")
    result = subprocess.run(["bash", str(deploy / "bootstrap-ble.sh")], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    second = trace.read_text().splitlines()
    assert "restart bluetooth.service" not in second
    assert "restart ai-dhlr-ble.service" in second
    assert "daemon-reload" not in second
    path.write_bytes(b"invalid config\n")
    trace.write_text("")
    result = subprocess.run(["bash", str(deploy / "bootstrap-ble.sh")], env=env,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert "配置迁移失败" in result.stdout
    assert "restart ai-dhlr-ble.service" not in trace.read_text()

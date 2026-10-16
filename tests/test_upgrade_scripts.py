"""在临时仓库和命令替身中验证升级；绝不连接设备、安装软件或操作真实服务。"""

import os
from pathlib import Path
import shlex
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bash():
    candidates = [shutil.which("bash"), r"C:\Program Files\Git\bin\bash.exe"]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    pytest.skip("Bash is required for isolated deployment tests")


def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


MOCKS = r'''
trace() { printf '%s\n' "$*" >> "$TEST_TRACE"; }
id() {
    case "$*" in
        -un|-gn*) echo linaro ;;
        *) echo 1000 ;;
    esac
}
df() { printf 'Filesystem Blocks Used Available Use%% Mounted\nmock 100 10 90 10%% /\n'; }
git() {
    trace "git $*"
    case "$*" in
        'symbolic-ref --quiet --short HEAD')
            [ "${DETACHED:-0}" = 0 ] || return 1
            echo main ;;
        fetch*) return "${FETCH_RC:-0}" ;;
        'rev-parse --verify refs/remotes/origin/main^{commit}') echo 1234567890 ;;
        'reset --hard 1234567890') return 0 ;;
        'rev-parse --short HEAD') echo 1234567 ;;
        pull) return "${PULL_RC:-0}" ;;
        *) echo "Unexpected git command: $*" >&2; return 88 ;;
    esac
}
timeout() { shift; "$@"; }
sudo() {
    if [ "${1:-}" = -n ] && [ "${2:-}" = true ]; then return 0; fi
    [ "${1:-}" != -n ] || shift
    [ "${1:-}" != -- ] || shift
    trace "sudo $*"
    case "$*" in
        chown*) return 0 ;;
        cmp*) return "${CMP_RC:-0}" ;;
        cp*) return "${COPY_RC:-0}" ;;
        'systemctl daemon-reload') return "${RELOAD_RC:-0}" ;;
        'systemctl restart ai-dhlr.service') return "${RESTART_RC:-0}" ;;
        'bash update.sh --skip-pull') command bash update.sh --skip-pull ;;
        'bash deploy/'*)
            if [ "${FAIL_SCRIPT:-}" = "${2:-}" ]; then return 1; fi
            return 0 ;;
        journalctl*) return 0 ;;
        *) echo "Unexpected sudo command: $*" >&2; return 89 ;;
    esac
}
'''


@pytest.fixture
def device(tmp_path):
    trace = tmp_path / "trace.log"
    mocks = tmp_path / "mocks.sh"
    write(mocks, MOCKS)
    fake_python = tmp_path / "target-python"
    write(fake_python, '''#!/bin/bash
printf 'python %s\n' "$*" >> "$TEST_TRACE"
case "$*" in
    '-m pip install '*) exit "${PIP_RC:-0}" ;;
    '-m pip check') exit "${PIP_CHECK_RC:-0}" ;;
    'deploy/upgrade_check.py --preflight') exit "${PREFLIGHT_RC:-0}" ;;
    'deploy/upgrade_check.py') exit "${HEALTH_RC:-0}" ;;
    '-c '*) exit "${BLE_CHECK_RC:-0}" ;;
    *) exit 87 ;;
esac
''')
    for unit in ("ai-dhlr.service", "ai-dhlr-ble.service"):
        write(tmp_path / "deploy" / unit, f"ExecStart={fake_python.as_posix()} main.py\n")
    for script in ("log-hygiene.sh", "time-sync.sh", "usb-otg-persist.sh",
                   "bootstrap-hotspot.sh", "bootstrap-ble.sh"):
        write(tmp_path / "deploy" / script, "exit 86\n")  # 必须经 sudo 替身调用
    write(tmp_path / "update.sh", (ROOT / "update.sh").read_text(encoding="utf-8"))
    write(tmp_path / "VERSION", "9.8.7\n")
    write(tmp_path / "requirements.txt", "bless==0.3.0\n")
    remote = (ROOT / "deploy/upgrade-old-device.sh").read_text(encoding="utf-8")
    remote = remote.split("cat <<'REMOTE'\n", 1)[1].split("\nREMOTE\n", 1)[0]
    remote = remote.replace("APP_DIR=/home/linaro/ai-dhlr", f"APP_DIR={shlex.quote(tmp_path.as_posix())}")
    write(tmp_path / "remote.sh", remote)
    env = dict(os.environ, BASH_ENV=mocks.as_posix(), TEST_TRACE=trace.as_posix(), SUDO_PASS="test")
    return tmp_path, env, trace


def run(bash, device, script="remote.sh", args=(), **overrides):
    root, env, trace = device
    result = subprocess.run([bash, str(root / script), *args], cwd=root,
                            env=dict(env, **overrides), capture_output=True, timeout=15)
    output = (result.stdout + result.stderr).decode("utf-8")
    return result.returncode, output, trace.read_text(encoding="utf-8") if trace.exists() else ""


def test_complete_upgrade_uses_target_python_requirements_and_single_deploy(bash, device):
    rc, output, trace = run(bash, device)
    assert rc == 0, output
    assert "系统版本: 9.8.7" in output
    assert "git pull" not in trace
    assert "pip install --disable-pip-version-check --timeout 30 --retries 2 -r requirements.txt" in trace
    assert trace.count("pip install") == 1
    assert trace.count("bash deploy/bootstrap-ble.sh") == 1
    assert trace.count("bash deploy/bootstrap-hotspot.sh") == 1
    assert "try-restart" not in trace
    assert trace.index("pip check") < trace.index("bash update.sh --skip-pull")
    assert trace.index("restart ai-dhlr.service") < trace.index("python deploy/upgrade_check.py\n")


@pytest.mark.parametrize("failure", ["PIP_RC", "PIP_CHECK_RC", "PREFLIGHT_RC", "BLE_CHECK_RC"])
def test_dependency_failure_never_restarts_services(bash, device, failure):
    rc, output, trace = run(bash, device, **{failure: "1"})
    assert rc != 0
    assert "升级完成" not in output
    assert "bash update.sh" not in trace
    assert "systemctl restart" not in trace


def test_missing_target_interpreter_does_not_fall_back_to_system_python(bash, device):
    root, _, _ = device
    write(root / "deploy/ai-dhlr.service", "ExecStart=/missing/target/python main.py\n")
    rc, output, trace = run(bash, device)
    assert rc != 0
    assert "解释器不可用" in output
    assert "pip install" not in trace


@pytest.mark.parametrize("failure", ["DETACHED", "FETCH_RC"])
def test_git_failure_stops_before_reset_and_deployment(bash, device, failure):
    rc, _, trace = run(bash, device, **{failure: "1"})
    assert rc != 0
    assert "git reset" not in trace
    assert "pip install" not in trace


@pytest.mark.parametrize("failure", ["COPY_RC", "RELOAD_RC", "RESTART_RC"])
def test_critical_deployment_failure_is_not_hidden(bash, device, failure):
    rc, output, trace = run(bash, device, CMP_RC="1", **{failure: "1"})
    assert rc == 1
    assert "升级完成" not in output
    assert "python deploy/upgrade_check.py\n" not in trace


@pytest.mark.parametrize("script", ["bootstrap-ble.sh", "bootstrap-hotspot.sh", "time-sync.sh"])
def test_partial_failure_still_restarts_main_but_returns_nonzero(bash, device, script):
    rc, output, trace = run(bash, device, FAIL_SCRIPT=f"deploy/{script}")
    assert rc == 2
    assert "systemctl restart ai-dhlr.service" in trace
    assert "python deploy/upgrade_check.py\n" in trace
    assert "升级完成" not in output


def test_unhealthy_service_or_api_fails_final_acceptance(bash, device):
    rc, output, _ = run(bash, device, HEALTH_RC="1")
    assert rc == 1
    assert "升级验收失败" in output
    assert "升级完成" not in output


def test_standalone_update_still_pulls_and_reports_pull_failure(bash, device):
    rc, _, trace = run(bash, device, script="update.sh", PULL_RC="1")
    assert rc == 1
    assert "git pull" in trace
    assert "systemctl restart" not in trace


def test_standalone_update_success_deploys_hotspot_and_ble_once(bash, device):
    rc, output, trace = run(bash, device, script="update.sh")
    assert rc == 0, output
    assert trace.count("git pull") == 1
    assert trace.count("bash deploy/bootstrap-hotspot.sh") == 1
    assert trace.count("bash deploy/bootstrap-ble.sh") == 1
    assert trace.count("systemctl restart ai-dhlr.service") == 1


@pytest.mark.parametrize("password", ["linaro", "x' y\"$HOME;$(touch NEVER);`echo no`\\end"])
def test_ssh_password_is_data_and_no_neighbor_probe(bash, tmp_path, password):
    capture = tmp_path / "password.txt"
    mocks = tmp_path / "ssh-mocks.sh"
    write(mocks, r'''
ssh() { return 85; }
timeout() { echo 'Unexpected network probe' >&2; return 84; }
sshpass() {
    [ "$1" = -e ] || return 83
    [ "${!#}" != 'printf dhlr-ssh-ok' ] || { printf dhlr-ssh-ok; return; }
    # 执行真实的远端命令，仅替换其 exec，捕获密码并消费后续脚本。
    command bash -c 'exec() { printf "%s" "$SUDO_PASS" > "$CAPTURE"; cat >/dev/null; }; eval "$1"' mock "${!#}"
}
''')
    result = subprocess.run(
        [bash, str(ROOT / "deploy/upgrade-old-device.sh"), "device.example", "linaro", password],
        cwd=tmp_path, env=dict(os.environ, BASH_ENV=mocks.as_posix(), CAPTURE=capture.as_posix()),
        capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8")
    assert capture.read_text(encoding="utf-8") == password
    assert not (tmp_path / "NEVER").exists()


@pytest.mark.parametrize("command", ["cp", "chmod", "nmcli"])
def test_hotspot_deployment_propagates_failures(bash, tmp_path, command):
    deploy = tmp_path / "deploy"
    write(deploy / "bootstrap-hotspot.sh", (ROOT / "deploy/bootstrap-hotspot.sh").read_text(encoding="utf-8"))
    write(deploy / "auto_hotspot.sh", "exit 0\n")
    mocks = tmp_path / "mocks.sh"
    write(mocks, '''
cmp() { return 1; }
cp() { [ "$FAIL_COMMAND" != cp ]; }
chmod() { [ "$FAIL_COMMAND" != chmod ]; }
nmcli() { [ "$FAIL_COMMAND" != nmcli ]; }
''')
    result = subprocess.run([bash, str(deploy / "bootstrap-hotspot.sh")], cwd=tmp_path,
                            env=dict(os.environ, BASH_ENV=mocks.as_posix(), FAIL_COMMAND=command),
                            capture_output=True, timeout=15)
    assert result.returncode != 0

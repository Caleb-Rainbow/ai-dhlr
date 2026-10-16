"""旧设备升级的运行依赖预检与启动验收，不操作摄像头、GPIO 或设备配置。

设备项目目录内使用部署 unit 指定的 Python 执行：
    python deploy/upgrade_check.py --preflight  # 导入依赖、检查推理引擎及模型文件
    python deploy/upgrade_check.py              # 验证双服务稳定运行及应用版本

启动验收在 90 秒观察窗口内要求至少连续 15 秒：两服务 active/running、
MainPID 为正且不变、NRestarts 不增加，设备识别接口的 firmware 等于 VERSION。
返回 0 仅证明上述条件通过，返回 1 表示检查失败；模型存在不代表硬件推理成功，
BLE 进程运行不代表客户端可完成配网。完整现场验收标准见升级脚本文件头。
"""

import argparse
import importlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
UNITS = ("ai-dhlr.service", "ai-dhlr-ble.service")


def read_settings(root=PROJECT_ROOT):
    """只读原始 YAML，避免预检触发 ConfigManager.load 的配置创建、迁移或保存。"""
    import yaml
    from src.utils.config import ApiConfig, InferenceConfig

    path = root / "config" / "config.yaml"
    if not path.exists():
        path = root / "config" / "default_config.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"配置不是有效字典: {path}")
    api = raw.get("api", {})
    inference = raw.get("inference", {})
    api_defaults, inference_defaults = ApiConfig(), InferenceConfig()
    host = api.get("host", api_defaults.host)
    if host == "0.0.0.0":
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host:
        host = f"[{host}]"
    url = f"http://{host}:{int(api.get('port', api_defaults.port))}/api/device/identify"
    engine = inference.get("engine", inference_defaults.engine)
    model = Path(inference.get("model_path", inference_defaults.model_path))
    return url, engine, model if model.is_absolute() else root / model


def preflight(root=PROJECT_ROOT):
    # pip 的元数据检查无法发现二进制加载错误，重启前实际导入关键依赖。
    for module in ("fastapi", "uvicorn", "cv2", "numpy", "yaml", "websockets",
                   "aiohttp", "multipart", "pygame", "serial", "psutil"):
        importlib.import_module(module)
    _, engine, model = read_settings(root)
    if engine == "rknn":
        importlib.import_module("rknnlite.api")
    elif engine == "pytorch":
        importlib.import_module("ultralytics")
    else:
        raise ValueError(f"不支持的推理引擎: {engine}")
    if not model.is_file():
        raise ValueError(f"模型文件不存在: {model}")
    print("[OK] 运行依赖、推理引擎及模型文件预检通过（尚未验证硬件推理）")


def service_signature(unit):
    """记录当前进程与累计重启次数；启动中、无进程或缺少状态字段均不能通过。"""
    result = subprocess.run(
        ["systemctl", "show", unit, "--no-pager",
         "--property=ActiveState,SubState,MainPID,NRestarts"],
        check=True, capture_output=True, text=True, timeout=5,
    )
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if fields.get("ActiveState") != "active" or fields.get("SubState") != "running":
        raise RuntimeError(f"{unit} 未运行: {fields.get('ActiveState')}/{fields.get('SubState')}")
    pid = int(fields.get("MainPID", "0"))
    if pid <= 0:
        raise RuntimeError(f"{unit} 没有主进程")
    return pid, int(fields["NRestarts"])


def check_api(url, expected_version):
    """核对应用身份和版本，避免误将其他 HTTP 服务或旧进程视为升级成功。"""
    # 本机验收不能被 HTTP_PROXY/TUN 的代理响应替代。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=3) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or payload.get("magic") != "DHLR_DEVICE":
        raise RuntimeError("应用接口未返回 DHLR 设备信息")
    if payload.get("firmware") != expected_version:
        raise RuntimeError(f"应用版本不匹配: {payload.get('firmware')}，期望 {expected_version}")


def wait_healthy(url, expected_version, timeout=90, stable_seconds=15):
    """两个进程及接口连续正常，且 PID/重启次数在观察窗口内不变才算成功。"""
    deadline = time.monotonic() + timeout
    stable_since = None
    previous = None
    last_error = "等待服务启动"
    while time.monotonic() < deadline:
        try:
            signature = tuple(service_signature(unit) for unit in UNITS)
            check_api(url, expected_version)
            now = time.monotonic()
            if now >= deadline:
                break
            if signature != previous or stable_since is None:
                # 任一服务换了进程或增加了重启计数，都从当前采样重新计算稳定时间。
                stable_since = now
            previous = signature
            if now - stable_since >= stable_seconds:
                print(f"[OK] 主服务、BLE 及应用接口持续正常，版本 {expected_version}")
                return
            last_error = "服务已响应，但尚未满足连续稳定时间"
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            # 接口短暂不可达也会清空稳定窗口；只在后续重新持续正常后才通过。
            last_error = str(exc)
            stable_since, previous = None, None
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise RuntimeError(f"启动验收超时: {last_error}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", help="重启前仅检查依赖和模型")
    args = parser.parse_args()
    try:
        if args.preflight:
            preflight()
        else:
            version = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
            if not version:
                raise ValueError("VERSION 为空")
            url, _, _ = read_settings()
            wait_healthy(url, version)
        return 0
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""dhlr 应用配置下发客户端（root 进程 → 127.0.0.1 dhlr 内部接口）。

把配网下发的 remote/system 配置发给 dhlr 热应用（落 config.yaml + 重连远程），
无需重启主程序。poster 可注入便于单测。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

INTERNAL_URL = "http://127.0.0.1:8000/internal/apply-provisioning"

# (url, payload) -> (status_code, body_text)
Poster = Callable[[str, dict], Tuple[int, str]]


def _default_poster(url: str, payload: dict) -> Tuple[int, str]:
    # 用标准库而非 httpx：设备侧 git pull 即可用，无需 pip 装额外依赖
    # （曾因 httpx 未进 requirements 导致设备配网报 No module named httpx）
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # urlopen 对 4xx/5xx 抛 HTTPError 而非返回响应，读出响应体维持契约
        return e.code, e.read().decode("utf-8", "replace")


class DhlrClient:
    def __init__(self, url: str = INTERNAL_URL, poster: Poster = _default_poster) -> None:
        self.url = url
        self._post = poster

    def apply_config(
        self, remote: Optional[dict] = None, system: Optional[dict] = None
    ) -> dict:
        """下发 remote/system 配置。无内容时跳过。返回 dhlr 的响应 dict。"""
        payload: dict = {}
        if remote is not None:
            payload["remote"] = remote
        if system is not None:
            payload["system"] = system
        if not payload:
            return {"ok": True, "skipped": True}

        status, text = self._post(self.url, payload)
        if status != 200:
            logger.error("dhlr apply 失败 status=%s body=%s", status, text[:200])
            return {"ok": False, "error": f"http {status}", "body": text[:200]}
        try:
            return json.loads(text)
        except ValueError:
            return {"ok": True, "raw": text[:200]}

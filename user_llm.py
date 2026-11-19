#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · 用户个人空间的「大模型 API」配置（自带密钥 / BYOK）

边界：
1. 每个登录身份（session 的 sub）一套配置，按 sub 隔离；仅本人可读写，管理员不能代读。
2. API key 以 AES-256-GCM 加密后落盘在 .privacy/user_llm.json（已 gitignore），
   永不下发浏览器、不打印、不进日志、不进错误响应。public_view() 只返回是否配置 /
   厂商 / 模型 / 更新时间 / key 形态。
3. Embedding 不在此模块：向量化是服务器固定本地 BGE 512 维，用户无法选择或覆盖。
4. 端点校验：只允许 https（开发期可放行 http 回环），拒绝 URL 内凭据与私网 / 元数据地址，
   防止把用户引导到内网网关造成 SSRF。
5. 加密密钥优先取 AUTH_SECRET / ZHIYIN_USER_LLM_KEY；两者都没有时用进程内随机密钥，
   此时重启会失去已保存的配置（status 会如实标注 persistent=false）。正式部署请配 AUTH_SECRET。
"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import secure_config

ROOT = Path(__file__).resolve().parent
STORE_DIR = ROOT / ".privacy"
STORE_FILE = STORE_DIR / "user_llm.json"
_LOCK = threading.RLock()

# 每个身份最长保留一套配置；字段长度上限在 save() 里二次校验
_MAX_KEY = 512
_MAX_URL = 300
_MAX_MODEL = 80

PROVIDERS: dict[str, dict[str, str]] = {
    "qwen": {
        "label": "通义千问 / DashScope",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "moonshot": {
        "label": "Moonshot / Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "custom": {
        "label": "自定义 OpenAI 兼容端点",
        "base_url": "",
        "model": "",
    },
}


class UserLLMError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _load_key() -> tuple[bytes, str, bool]:
    raw = (os.environ.get("ZHIYIN_USER_LLM_KEY")
           or secure_config.secret("AUTH_SECRET") or "").strip()
    if raw:
        try:
            decoded = bytes.fromhex(raw)
            if len(decoded) == 32:
                return decoded, "env:hex32", True
        except ValueError:
            pass
        return hashlib.sha256(raw.encode("utf-8")).digest(), "env:derived", True
    return AESGCM.generate_key(bit_length=256), "ephemeral:process-memory", False


_KEY, _KEY_SOURCE, _PERSISTENT = _load_key()
_AES = AESGCM(_KEY)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slot(sub: str) -> str:
    """sub 不落明文：只写截断哈希作为索引。"""
    return hashlib.sha256(("user-llm:" + sub).encode("utf-8")).hexdigest()[:24]


def _read() -> dict[str, Any]:
    if not STORE_FILE.is_file():
        return {}
    try:
        data = json.loads(STORE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict[str, Any]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STORE_FILE)


def _decrypt(blob: str, sub: str) -> dict[str, Any] | None:
    try:
        raw = base64.b64decode(blob)
        nonce, ct = raw[:12], raw[12:]
        out = json.loads(_AES.decrypt(nonce, ct, _slot(sub).encode("ascii")))
        return out if isinstance(out, dict) else None
    except Exception:
        return None


def _encrypt(cfg: dict[str, Any], sub: str) -> str:
    nonce = secrets.token_bytes(12)
    body = json.dumps(cfg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = _AES.encrypt(nonce, body, _slot(sub).encode("ascii"))
    return base64.b64encode(nonce + ct).decode("ascii")


def _shape(key: str) -> str:
    if not key:
        return "empty"
    if key.startswith("sk-") and len(key) > 12:
        return "sk-prefixed-key"
    if re.match(r"^[A-Za-z0-9._\-]{8,}$", key):
        return "opaque-key"
    return "custom-key"


def providers_view() -> dict[str, Any]:
    return {
        "ok": True,
        "providers": {k: {"label": v["label"], "base_url": v["base_url"],
                          "model": v["model"]} for k, v in PROVIDERS.items()},
        "embedding": {"server_fixed": True, "user_selectable": False,
                      "backend": "bge", "dim": 512},
    }


def validate_endpoint(url: str) -> str:
    """只允许 https 或开发回环 http；拒绝 URL 凭据、内网 / 元数据地址。"""
    url = (url or "").strip().rstrip("/")
    if not url or len(url) > _MAX_URL:
        raise UserLLMError("BAD_BASE_URL", "接口地址长度不合法")
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http"):
        raise UserLLMError("BAD_BASE_URL", "接口地址必须使用 https（本地开发可用 http 回环）")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise UserLLMError("BAD_BASE_URL", "接口地址不能包含账号 / 查询串 / 片段")
    host = (parts.hostname or "").lower()
    if not host or len(host) > 253:
        raise UserLLMError("BAD_BASE_URL", "接口地址缺少有效主机名")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        raise UserLLMError("BAD_BASE_URL", "接口地址端口不合法") from None
    loopback = host in ("127.0.0.1", "::1", "localhost", "ip6-localhost")
    if parts.scheme == "http" and not loopback:
        raise UserLLMError("BAD_BASE_URL", "非本机地址必须使用 https")
    if not loopback:
        try:
            addr = ipaddress.ip_address(host.strip("[]"))
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                raise UserLLMError("BAD_BASE_URL", "接口地址不能指向内网 / 元数据地址")
        except ValueError:
            pass  # 域名由 DNS 解析，交由运行期处理；此处只拦 IP 字面量
    if host.endswith((".localhost", ".local", ".internal")):
        raise UserLLMError("BAD_BASE_URL", "接口地址不能指向内网主机名")
    if loopback and port not in (80, 443, 8000, 8080, 8848, 11434, 1234, 3000, 5000):
        raise UserLLMError("BAD_BASE_URL", "本机端口不在允许列表")
    return url


def save(sub: str, *, provider: str, api_key: str, base_url: str | None = None,
         model: str | None = None, timeout: int | None = 30) -> dict[str, Any]:
    if provider not in PROVIDERS:
        raise UserLLMError("BAD_PROVIDER", "不支持的厂商")
    api_key = (api_key or "").strip()
    if not (8 <= len(api_key) <= _MAX_KEY) or any(ord(c) < 32 for c in api_key):
        raise UserLLMError("BAD_API_KEY", "API key 长度或格式不合法")
    preset = PROVIDERS[provider]
    endpoint = validate_endpoint(base_url or preset["base_url"] or "")
    name = (model or preset["model"] or "").strip()
    if not (1 <= len(name) <= _MAX_MODEL) or any(ord(c) < 32 for c in name):
        raise UserLLMError("BAD_MODEL", "模型名长度或格式不合法")
    seconds = 30 if timeout is None else int(timeout)
    if not (3 <= seconds <= 60):
        raise UserLLMError("BAD_TIMEOUT", "超时须在 3–60 秒之间")

    record = {
        "provider": provider,
        "api_key": api_key,
        "base_url": endpoint,
        "model": name,
        "timeout": seconds,
        "updated_at": _now(),
    }
    with _LOCK:
        data = _read()
        data[_slot(sub)] = _encrypt(record, sub)
        _write(data)
    return public_view(sub)


def get(sub: str) -> dict[str, Any] | None:
    """仅服务端内部使用：返回含 api_key 的明文，绝不可放进任何响应。"""
    with _LOCK:
        blob = _read().get(_slot(sub))
    return _decrypt(blob, sub) if blob else None


def delete(sub: str) -> None:
    with _LOCK:
        data = _read()
        data.pop(_slot(sub), None)
        _write(data)


def public_view(sub: str) -> dict[str, Any]:
    cfg = get(sub)
    if not cfg:
        return {"ok": True, "configured": False, "provider": None, "model": None,
                "base_url": None, "key_shape": "empty", "updated_at": None,
                "persistent": _PERSISTENT, "key_source": _KEY_SOURCE,
                "embedding": {"server_fixed": True, "user_selectable": False,
                              "backend": "bge", "dim": 512}}
    return {
        "ok": True,
        "configured": True,
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "key_shape": _shape(cfg.get("api_key", "")),
        "updated_at": cfg.get("updated_at"),
        "persistent": _PERSISTENT,
        "key_source": _KEY_SOURCE,
        "embedding": {"server_fixed": True, "user_selectable": False,
                      "backend": "bge", "dim": 512},
    }

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缓存 / Broker 适配器：Redis 优先，缺失时降级进程内 TTL 字典（零新增依赖）。

Redis 同时作为 Celery 的 broker / backend。降级字典只在进程内、重启即清空，
不落盘、不跨进程——因此 Celery 在无 Redis 时会被强制切到 eager 本地执行器。

只允许缓存非敏感的公开聚合结果与任务状态；**禁止缓存 JD 原文、简历、
OCR 文本或任何 PII**（key 规范见 schema.REDIS_KEY_SPEC）。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from secure_config import cfg

from .schema import REDIS_KEY_SPEC

_LOCK = threading.Lock()
_CLIENT: Any = None
_BACKEND = "uninitialized"
_REASON = ""
_MEMORY: dict[str, tuple[str, float | None]] = {}
_TRIED = False


def _connect() -> Any:
    global _CLIENT, _BACKEND, _REASON, _TRIED
    if _TRIED:
        return _CLIENT
    _TRIED = True
    try:
        import redis  # type: ignore
        _CLIENT = redis.Redis.from_url(cfg.get("REDIS_URL"),
                                       socket_connect_timeout=2,
                                       socket_timeout=2, decode_responses=True)
        _CLIENT.ping()
        _BACKEND = "redis"
    except Exception as exc:
        _CLIENT = None
        _BACKEND = "memory-fallback"
        _REASON = "Redis 不可用（%s）" % type(exc).__name__
    return _CLIENT


def _guard_key(key: str) -> bool:
    """拒绝明显的用户数据键，防止误把 C 级文本塞进缓存。"""
    low = (key or "").lower()
    return not any(w in low for w in ("jd_text", "resume", "ocr", "raw_text", "pii", "cv_text"))


def set(key: str, value: Any, ttl: int = 900) -> bool:
    if not key or not _guard_key(key):
        return False
    raw = json.dumps(value, ensure_ascii=False)
    client = _connect()
    if client is not None:
        try:
            client.setex(key, max(int(ttl), 1), raw)
            return True
        except Exception:
            pass
    with _LOCK:
        _MEMORY[key] = (raw, time.time() + max(int(ttl), 1))
    return True


def get(key: str) -> Any | None:
    if not key or not _guard_key(key):
        return None
    client = _connect()
    if client is not None:
        try:
            raw = client.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception:
            return None
    with _LOCK:
        item = _MEMORY.get(key)
        if not item:
            return None
        raw, expires = item
        if expires is not None and time.time() > expires:
            _MEMORY.pop(key, None)
            return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def delete(key: str) -> bool:
    client = _connect()
    if client is not None:
        try:
            client.delete(key)
        except Exception:
            pass
    with _LOCK:
        return _MEMORY.pop(key, None) is not None


def clear() -> int:
    with _LOCK:
        n = len(_MEMORY)
        _MEMORY.clear()
    return n


def status() -> dict[str, Any]:
    client = _connect()
    with _LOCK:
        n = len(_MEMORY)
        expired = sum(1 for _, e in _MEMORY.values() if e and time.time() > e)
    return {
        "backend": _BACKEND,
        "connected": client is not None,
        "broker_url_configured": bool(cfg.get("REDIS_URL")),
        "fallback_reason": _REASON,
        "memory_keys": n,
        "memory_expired_pending_purge": expired,
        "persistent": _BACKEND == "redis",
        "key_spec": REDIS_KEY_SPEC,
        "celery_effect": "Redis 不可用时 Celery 强制 eager 本地执行" if client is None
                         else "可作为 Celery broker / result backend",
    }

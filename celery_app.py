#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · Celery 装配层（可用则真异步，不可用则 eager 本地执行）

判定顺序：
  1. celery 未安装 → eager
  2. Redis 不可用（stores.cache 降级到内存）→ eager（内存字典不能跨进程做 broker）
  3. CELERY_TASK_ALWAYS_EAGER=true → eager
  4. 否则创建 Celery(broker=REDIS_URL, backend=REDIS_URL)，注册白名单任务

无论哪条路径，/api/tasks/dispatch 的返回结构一致，且每次派发都会把任务状态
写入关系库（PostgreSQL 或 SQLite 降级）。任务函数本身在 tasks.py，保持纯函数、
可重跑、可单测。
"""
from __future__ import annotations

import importlib
from typing import Any

import stores
from secure_config import cfg
from tasks import TASK_REGISTRY, TASK_WHITELIST, task_id as new_task_id


class UnknownTask(Exception):
    pass


_APP: Any = None
_ENGINE = "uninitialized"
_EAGER_REASON = ""
_REGISTERED: list[str] = []


def _celery_available() -> bool:
    try:
        importlib.import_module("celery")
        return True
    except Exception:
        return False


def _setup() -> None:
    global _APP, _ENGINE, _EAGER_REASON, _REGISTERED
    if _ENGINE != "uninitialized":
        return
    _REGISTERED = list(TASK_WHITELIST)
    redis_ok = stores.cache.status()["connected"]
    if not _celery_available():
        _ENGINE, _EAGER_REASON = "eager-local", "celery 未安装"
        return
    if not redis_ok:
        _ENGINE = "eager-local"
        _EAGER_REASON = "Redis 不可用（内存字典不能作为跨进程 broker）"
        return
    if cfg.flag("CELERY_TASK_ALWAYS_EAGER"):
        _ENGINE, _EAGER_REASON = "eager-local", "CELERY_TASK_ALWAYS_EAGER=true"
        return
    try:
        from celery import Celery  # type: ignore
        url = cfg.get("REDIS_URL")
        app = Celery("zhiyin", broker=url, backend=url)
        app.conf.update(
            task_always_eager=False,
            task_serializer="json",
            result_serializer="json",
            accept_content=["json"],
            timezone="Asia/Shanghai",
            worker_concurrency=cfg.integer("CELERY_CONCURRENCY", 2),
            task_track_started=True,
            result_expires=3600,
        )
        for name, fn in TASK_REGISTRY.items():
            app.task(name="zhiyin." + name)(fn)
        _APP, _ENGINE = app, "celery-redis"
    except Exception as exc:
        _APP = None
        _ENGINE, _EAGER_REASON = "eager-local", "Celery 装配失败（%s）" % type(exc).__name__


def dispatch(name: str, kwargs: dict[str, Any] | None = None) -> dict[str, Any]:
    """派发白名单任务。eager 时同步执行并返回结果；真 Celery 时返回 task_id。"""
    _setup()
    if name not in TASK_REGISTRY:
        raise UnknownTask(name)
    kwargs = kwargs or {}
    tid = new_task_id()
    fn = TASK_REGISTRY[name]
    if _ENGINE == "celery-redis" and _APP is not None:
        try:
            async_result = _APP.tasks["zhiyin." + name].apply_async(kwargs=kwargs)
            state, result = "PENDING", {"task_id": async_result.id}
        except Exception as exc:
            state, result = "FAILURE", {"error": type(exc).__name__}
    else:
        try:
            result = fn(**kwargs)
            state = "SUCCESS" if result.get("ok", False) else "FAILURE"
        except Exception as exc:
            state, result = "FAILURE", {"error": type(exc).__name__}
        result["mode"] = "eager-local"
    stores.relational.upsert_task(tid, name, state, _ENGINE,
                                  detail={k: str(v)[:200] for k, v in result.items()})
    stores.cache.set("zhiyin:task:" + tid, {"name": name, "state": state}, 3600)
    return {"ok": state != "FAILURE", "task_id": tid, "name": name,
            "state": state, "engine": _ENGINE, "result": result}


def status() -> dict[str, Any]:
    _setup()
    return {
        "ok": True,
        "engine": _ENGINE,
        "eager": _ENGINE == "eager-local",
        "eager_reason": _EAGER_REASON,
        "celery_installed": _celery_available(),
        "redis_connected": stores.cache.status()["connected"],
        "broker": cfg.get("REDIS_URL").split("://")[0] + "://(host-redacted)",
        "worker_concurrency": cfg.integer("CELERY_CONCURRENCY", 2),
        "registered_tasks": _REGISTERED,
        "beat_schedule": "未启用：受控研究采集执行体仅限练习沙盒、生产采集未解锁，暂无周期性抓取任务",
        "note": "eager 模式任务在同一进程同步执行，返回结构与非 eager 一致",
    }

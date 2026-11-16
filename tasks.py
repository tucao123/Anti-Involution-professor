#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · 任务函数（纯函数，可被 Celery 包装，也可本地 eager 直接调用）

约束：
1. 任务只处理 D 级公开样本或已脱敏的结构化字段；不接收 JD / 简历 / OCR 原文。
2. 每个任务都必须可安全重跑（幂等），且失败不外泄凭据或上游细节。
3. 任务注册表是白名单，/api/tasks/dispatch 只允许这里的名字。
"""
from __future__ import annotations

import uuid
from typing import Any, Callable

import market
import privacy
import stores
import vector_store


def refresh_market_snapshot(major: str = "计算机科学与技术") -> dict[str, Any]:
    """刷新公开岗位聚合快照并写入缓存（D 级、无 PII、TTL 900s）。"""
    try:
        data = market.get_market_profile(major)
        stores.cache.set("zhiyin:market:" + major, {"ok": True, "source": data.get("source")}, 900)
        stores.relational.record_event("market_snapshot", source_id="jobicy", grade="D",
                                       detail={"major": major[:40], "ok": True})
        return {"ok": True, "major": major, "source": data.get("source")}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__,
                "note": "公开源不可用，未写入缓存；前端继续使用内置演示集"}


def vectorize_public_samples(items: list[dict[str, Any]],
                             source_id: str = "jobicy") -> dict[str, Any]:
    """把 D 级公开岗位样本向量化；内部仍会过来源闸门与 PII 扫描。"""
    if not items:
        return {"ok": False, "error": "NO_ITEMS"}
    try:
        return vector_store.ingest(items, scope="public", source_id=source_id)
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def sync_skill_graph(result: dict[str, Any]) -> dict[str, Any]:
    """把编排结果的结构化技能字段同步到图谱（C 级，已脱敏，不含原文）。"""
    return stores.sync_from_jd(result)


def purge_expired_privacy() -> dict[str, Any]:
    n = privacy.purge_expired()
    stores.relational.record_event("privacy_purge", grade="C", detail={"removed": n})
    return {"ok": True, "removed": n}


def clear_vector_local() -> dict[str, Any]:
    n = vector_store.clear_local()
    return {"ok": True, "deleted": n, "scope": "local-memory-only"}


def verify_audit_chain() -> dict[str, Any]:
    ok, count, first_bad = privacy.verify_chain()
    stores.relational.record_event("audit_verify", detail={"chain_ok": ok, "entries": count})
    return {"ok": ok, "entries": count, "first_bad_seq": first_bad}


TASK_REGISTRY: dict[str, Callable[..., dict[str, Any]]] = {
    "refresh_market_snapshot": refresh_market_snapshot,
    "vectorize_public_samples": vectorize_public_samples,
    "sync_skill_graph": sync_skill_graph,
    "purge_expired_privacy": purge_expired_privacy,
    "clear_vector_local": clear_vector_local,
    "verify_audit_chain": verify_audit_chain,
}

TASK_WHITELIST = tuple(TASK_REGISTRY)


def task_id() -> str:
    return uuid.uuid4().hex[:16]

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由域：向量与检索（拆分自 server.py，行为保持一致）。"""
from __future__ import annotations

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from .common import (_optional, _metrics, _set_session_cookie, _require_identity,
                     _privacy_identity, _privacy_owns, _need)  # noqa: F401

router = APIRouter()

@router.get("/api/embedding/status")
def embedding_status():
    return embedding.status()

@router.post("/api/vector/clear-local")
def vector_clear_local():
    return {"ok": True, "deleted": vector_store.clear_local(), "scope": "local-memory-only"}

@router.post("/api/vector/delete")
def vector_delete(req: VectorDeleteRequest):
    return vector_store.delete(req.ids)

@router.post("/api/vector/ingest")
def vector_ingest(req: VectorIngestRequest):
    if req.scope == "user":
        return fail(403, "CLOUD_USER_DATA_BLOCKED",
                    "C 级用户自带数据默认禁止写入云向量库；请使用本地 /api/rag/ingest")
    if not req.purpose_ack:
        return fail(403, "CONSENT_REQUIRED", "请先确认「仅 D 级公开样本入云」的用途授权")
    try:
        result = vector_store.ingest(
            [it.model_dump() for it in req.items], scope="public", source_id=req.source_id)
    except vector_store.VectorRejected as exc:
        return fail(400, str(exc), "来源未获批准 / 分级不符 / 检出个人信息，已整批拒绝")
    except vector_store.CloudUserDataBlocked:
        return fail(403, "CLOUD_USER_DATA_BLOCKED")
    except Exception:
        return fail(502, "VECTOR_INGEST_FAILED", "向量化入库失败，未写入任何数据")
    sources.log_assessment("vector_public_ingested", req.source_id,
                           {"count": result["ingested"], "backend": result["backend"]})
    return result

@router.post("/api/vector/guard/check")
def vector_outbound_guard_check(req: VectorGuardCheckRequest):
    """dry-run：这份 payload 能不能出境？**绝不产生境外写入，也不联网**。

    用于在不新增数据出境的前提下验证集合 / 分级 / 来源 / 字段 / PII / 体积的拦截效果。
    """
    try:
        clearance = vector_store.guard_check(
            [it.model_dump() for it in req.items],
            source_id=req.source_id, scope=req.scope)
    except (vector_store.VectorRejected, vector_store.CloudUserDataBlocked) as exc:
        return {"ok": True, "allowed": False, "reason": str(exc),
                "network_called": False, "data_written": False}
    except outbound_guard.OutboundBlocked as exc:
        return {"ok": True, "allowed": False, "reason": str(exc),
                "network_called": False, "data_written": False}
    return {"ok": True, "allowed": True, "clearance": clearance,
            "network_called": False, "data_written": False,
            "note": "仅完成出站校验，未向量化、未发起网络请求、未写入 Qdrant 或本地索引。"}

@router.get("/api/vector/guard")
def vector_outbound_guard_status():
    """只读：Q18 出境写入防护状态。不返回 QDRANT_URL / API key / 任何业务文本。"""
    return vector_store.status()["outbound_guard"]

@router.get("/api/vector/probe")
def vector_probe(force: bool = False):
    """只读连通性探测：不写入、不传输任何业务数据。"""
    return vector_store.probe(force=force)

@router.post("/api/vector/search")
def vector_search(req: VectorSearchRequest):
    try:
        return vector_store.search(req.q, req.top_k, scope="public")
    except Exception:
        return fail(502, "VECTOR_SEARCH_FAILED", "检索失败，可改用 /api/rag/query 本地索引")

@router.get("/api/vector/status")
def vector_status():
    return vector_store.status()

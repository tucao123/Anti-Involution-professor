#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由域：隐私保险库（拆分自 server.py，行为保持一致）。"""
from __future__ import annotations

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from .common import (_optional, _metrics, _set_session_cookie, _require_identity,
                     _privacy_identity, _privacy_owns, _need)  # noqa: F401

router = APIRouter()

@router.get("/api/privacy/audit")
def privacy_audit(request: Request):
    _privacy_identity(request)
    ok, count, first_bad = privacy.verify_chain()
    return {"ok": True, "entries": count, "chain_ok": ok, "first_bad_seq": first_bad}

@router.post("/api/privacy/delete")
def privacy_delete(req: DeleteRequest, request: Request):
    ident = _privacy_identity(request)
    if not _privacy_owns(privacy.read_record(req.id[:64], False), ident):
        return fail(404, "NOT_FOUND")
    if not privacy.delete_record(req.id):
        return fail(404, "NOT_FOUND")
    return {"ok": True, "deleted": req.id}

@router.get("/api/privacy/record/{rid}")
def privacy_read(rid: str, request: Request, reveal: bool = False):
    ident = _privacy_identity(request)
    rid = rid[:64]
    # 先用脱敏视图做归属校验，避免匿名/他人凭 id 触发明文解密与审计
    owned = _privacy_owns(privacy.read_record(rid, False), ident)
    if not owned:
        return fail(404, "NOT_FOUND")
    rec = privacy.read_record(rid, reveal)
    if not rec:
        return fail(404, "NOT_FOUND")
    return {"ok": True, "record": rec}

@router.get("/api/privacy/status")
def privacy_status():
    return privacy.status()

@router.post("/api/privacy/store")
def privacy_store(req: StoreRequest, request: Request):
    ident = _privacy_identity(request)
    meta = dict(req.meta or {})
    meta["_owner"] = ident.get("sub", "")
    try:
        result = privacy.store_record(req.fields, meta)
    except ValueError:
        return fail(400, "NO_PII_FIELD", "未包含可加密的 PII 字段")
    return {"ok": True, **result}

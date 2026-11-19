#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · Qdrant 出境写入防护层（2026-11-16）

背景：Q18 已依据项目所有者回执开启境外 Qdrant 写入，且已实际发生写入。
所有者要求「做好 Qdrant 防护机制，避免数据写泄露」。本模块是**出站前的最后一道
硬校验**，与 vector_store.cloud_writes_allowed() 的「配置 + 回执」双闸门叠加：

    双闸门（能不能写） → 本模块（写什么、写到哪） → Qdrant REST

防护目标（全部为代码常量，改代码即需重新评审，不能用 .env 绕过）：
  1. **目标锁定**：端点必须 https、不得含 URL userinfo、host 必须落在 Qdrant Cloud
     官方域或 QDRANT_ALLOWED_HOSTS 显式白名单。防止配置被改到任意主机导致数据外发。
  2. **集合锁定**：只能写 ^zhiyin_public_[a-z0-9_]+$，禁止任意集合名 / 注入路径。
  3. **分级锁定**：scope 只能 public、grade 只能 D；C 级用户数据、A/B 级境内数据
     一律拒绝，即使调用方伪造 payload 中的 grade 也会与 sources 注册表交叉核对。
  4. **来源锁定**：source_id 必须在注册表中且 grade=D、legal_status=approved。
  5. **字段白名单**：payload 只允许固定键集，多余键 / 缺必需键 / 超长文本一律拒绝，
     防止调用方顺手把 JD 原文、用户标识、内部字段塞进出站 payload。
  6. **逐条 PII 扫描**：任何字符串字段命中即整批拒绝（向量不等于匿名化）。
  7. **体积与批量上限**：单条文本 / payload 字节 / 批次条数 / 向量维度均有上限。
  8. **凭据与内容不回显**：异常只保留状态码或错误码；审计只写摘要（sha256、
     长度、字段名、计数），不写原文、不写向量、不写 URL、不写 api-key。
  9. **读操作不夹带原文**：向 Qdrant 发送的查询只含向量与 limit，不含查询原文。

本模块是纯校验 + 本地摘要审计，**自身不发起任何网络请求**，也不产生境外写入。
工程过程控制，不构成法律意见；Q18 的数据出境评估与法务书面结论仍待回。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

import sources
from privacy import scan_pii
from secure_config import cfg


class OutboundBlocked(Exception):
    """出站校验未通过；str(exc) 为稳定的错误码，不含任何业务内容。"""


GUARD_ID = "QDRANT-OUTBOUND-GUARD-2026-11-16"

OUTBOUND_POLICY: dict[str, Any] = {
    "id": GUARD_ID,
    "purpose": "Q18 境外写入已发生，出站前锁定目标 / 集合 / 分级 / 字段 / 体积，防止数据写泄露",
    "endpoint": {
        "https_only": True,
        "deny_url_userinfo": True,
        "deny_url_query": True,
        "official_host_suffix": ".cloud.qdrant.io",
        "explicit_host_allowlist_key": "QDRANT_ALLOWED_HOSTS",
        "note": "host 不属于官方域且不在显式白名单时，任何请求（含只读探测）都不发出",
    },
    "collection_pattern": r"^zhiyin_public_[a-z0-9_]{1,32}$",
    "allowed_scopes": ("public",),
    "allowed_grades": ("D",),
    "allow_user_data_to_cloud": False,
    "payload_key_allowlist": frozenset({
        "text", "title", "grade", "region", "source_id", "source_name",
        "attribution", "attribution_url", "applicability", "forbidden_claims",
        "embedding_backend", "semantic", "scope", "ingested_at", "material_kind",
    }),
    "required_payload_keys": frozenset({
        "text", "title", "grade", "region", "source_id", "scope",
    }),
    "max_text_chars": 2000,
    "max_title_chars": 80,
    "max_payload_bytes": 8192,
    "max_batch_points": 20,
    "max_vector_dim": 4096,
    "max_delete_ids": 100,
    "pii_scan": True,
    "pii_action": "reject_entire_batch",
    "audit": {
        "content": "digest_only",
        "never_log": ("raw_text", "vector_values", "api_key", "full_url", "response_body"),
        "exception_detail": "status_code_or_error_code_only",
    },
    "search_sends": "vector + limit only（不发送查询原文）",
    "legal_review": "pending：数据出境评估与法务书面结论仍待回",
    "is_legal_opinion": False,
}


# ---------------------------------------------------------------------------
# 目标 / 集合锁定
# ---------------------------------------------------------------------------
def validate_endpoint(url: str | None) -> dict[str, Any]:
    """只校验端点形态，不发起请求；返回脱敏后的 host，绝不返回完整 URL。"""
    from urllib.parse import urlparse
    raw = str(url or "").strip()
    if not raw:
        raise OutboundBlocked("ENDPOINT_NOT_CONFIGURED")
    try:
        p = urlparse(raw)
    except ValueError:
        raise OutboundBlocked("ENDPOINT_MALFORMED") from None
    if p.scheme.lower() != "https":
        raise OutboundBlocked("ENDPOINT_NOT_HTTPS")
    if p.username or p.password:
        raise OutboundBlocked("ENDPOINT_CONTAINS_CREDENTIALS")
    if p.query or p.fragment:
        raise OutboundBlocked("ENDPOINT_CONTAINS_QUERY")
    host = (p.hostname or "").lower()
    if not host:
        raise OutboundBlocked("ENDPOINT_HOST_MISSING")
    suffix = OUTBOUND_POLICY["endpoint"]["official_host_suffix"]
    explicit = [h.strip().lower() for h in
                cfg.get(OUTBOUND_POLICY["endpoint"]["explicit_host_allowlist_key"], "").split(",")
                if h.strip()]
    if not (host.endswith(suffix) or host in explicit):
        raise OutboundBlocked("ENDPOINT_HOST_NOT_ALLOWED")
    if p.path not in ("", "/"):
        raise OutboundBlocked("ENDPOINT_PATH_NOT_ALLOWED")
    return {"ok": True, "host": host, "https": True, "userinfo": False,
            "host_allowlist_basis": "official_qdrant_cloud" if host.endswith(suffix)
                                    else "explicit_config_allowlist"}


def validate_collection(name: str | None) -> dict[str, Any]:
    raw = str(name or "")
    if not re.match(OUTBOUND_POLICY["collection_pattern"], raw):
        raise OutboundBlocked("COLLECTION_NOT_ALLOWED")
    return {"ok": True, "collection": raw}


def allowed_source_ids() -> list[str]:
    """动态取注册表里 grade=D 且 legal_status=approved 的来源，不硬编码具体站点。"""
    return sorted(k for k, v in sources.SOURCES.items()
                  if v.get("grade") == "D" and v.get("legal_status") == "approved")


# ---------------------------------------------------------------------------
# payload 逐条校验
# ---------------------------------------------------------------------------
def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]


def validate_point(point: dict[str, Any], *, scope: str, grade: str,
                   source_id: str) -> dict[str, Any]:
    payload = point.get("payload")
    if not isinstance(payload, dict):
        raise OutboundBlocked("PAYLOAD_NOT_OBJECT")
    keys = set(payload.keys())
    allow = OUTBOUND_POLICY["payload_key_allowlist"]
    required = OUTBOUND_POLICY["required_payload_keys"]
    if keys - allow:
        raise OutboundBlocked("PAYLOAD_KEY_NOT_ALLOWED")
    if required - keys:
        raise OutboundBlocked("PAYLOAD_KEY_MISSING")
    if payload.get("scope") != scope or payload.get("grade") != grade:
        raise OutboundBlocked("PAYLOAD_SCOPE_OR_GRADE_MISMATCH")
    if payload.get("source_id") != source_id:
        raise OutboundBlocked("PAYLOAD_SOURCE_MISMATCH")
    material_kind = payload.get("material_kind")
    if material_kind is not None and material_kind not in ("job_info", "interview_talk"):
        raise OutboundBlocked("MATERIAL_KIND_NOT_ALLOWED")
    text = str(payload.get("text", ""))
    title = str(payload.get("title", ""))
    if not text.strip():
        raise OutboundBlocked("EMPTY_TEXT")
    if len(text) > OUTBOUND_POLICY["max_text_chars"]:
        raise OutboundBlocked("TEXT_TOO_LONG")
    if len(title) > OUTBOUND_POLICY["max_title_chars"]:
        raise OutboundBlocked("TITLE_TOO_LONG")
    size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if size > OUTBOUND_POLICY["max_payload_bytes"]:
        raise OutboundBlocked("PAYLOAD_TOO_LARGE")
    # 任意字符串字段命中 PII 即整批拒绝，不只是 text
    for key in sorted(keys):
        val = payload[key]
        if isinstance(val, str) and val and key not in ("attribution_url",):
            if scan_pii({key: val}):
                raise OutboundBlocked("PII_RESIDUAL")
        elif isinstance(val, list):
            for v in val:
                if isinstance(v, str) and scan_pii({key: v}):
                    raise OutboundBlocked("PII_RESIDUAL")
    vector = point.get("vector")
    if not isinstance(vector, (list, tuple)) or not vector:
        raise OutboundBlocked("VECTOR_MISSING")
    if len(vector) > OUTBOUND_POLICY["max_vector_dim"]:
        raise OutboundBlocked("VECTOR_DIM_TOO_LARGE")
    if not all(isinstance(x, (int, float)) and math.isfinite(float(x)) for x in vector):
        raise OutboundBlocked("VECTOR_NOT_FINITE")
    return {"text_sha256_16": _digest(text), "text_len": len(text),
            "payload_bytes": size, "keys": sorted(keys), "vector_dim": len(vector)}


def validate_outbound(points: list[dict[str, Any]], *, collection: str,
                      scope: str, source_id: str, grade: str) -> dict[str, Any]:
    """出站前总校验。通过才返回 clearance；任何一条不过都整批拒绝。

    纯函数：不联网、不落原文、不产生任何境外写入。
    """
    if not isinstance(points, list) or not points:
        raise OutboundBlocked("NO_POINTS")
    if len(points) > OUTBOUND_POLICY["max_batch_points"]:
        raise OutboundBlocked("BATCH_TOO_LARGE")
    if scope not in OUTBOUND_POLICY["allowed_scopes"]:
        raise OutboundBlocked("SCOPE_NOT_ALLOWED")
    if grade not in OUTBOUND_POLICY["allowed_grades"]:
        raise OutboundBlocked("GRADE_NOT_ALLOWED")
    if source_id not in allowed_source_ids():
        raise OutboundBlocked("SOURCE_NOT_ALLOWED")
    src = sources.SOURCES.get(source_id, {})
    if src.get("grade") != grade:
        raise OutboundBlocked("REGISTRY_GRADE_MISMATCH")
    validate_collection(collection)
    digests = [validate_point(p, scope=scope, grade=grade, source_id=source_id)
               for p in points]
    batch_hash = hashlib.sha256(
        "|".join(d["text_sha256_16"] for d in digests).encode("ascii")
    ).hexdigest()[:20]
    return {
        "ok": True,
        "guard_id": GUARD_ID,
        "collection": collection,
        "scope": scope, "grade": grade, "source_id": source_id,
        "points": len(points),
        "total_payload_bytes": sum(d["payload_bytes"] for d in digests),
        "batch_sha256_20": batch_hash,
        "digests": digests,
        "pii_scan": "passed (逐条、逐字符串字段)",
        "endpoint_lock": "见 vector_store._request 每次请求前 validate_endpoint()",
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "network_called": False,
        "data_written": False,
    }


def validate_delete(ids: list[str], collection: str) -> dict[str, Any]:
    if not isinstance(ids, list) or not ids:
        raise OutboundBlocked("NO_DELETE_IDS")
    if len(ids) > OUTBOUND_POLICY["max_delete_ids"]:
        raise OutboundBlocked("DELETE_BATCH_TOO_LARGE")
    validate_collection(collection)
    return {"ok": True, "collection": collection, "ids": len(ids),
            "digest": _digest("|".join(str(i) for i in ids)),
            "network_called": False, "data_written": False}


def audit(event: str, clearance: dict[str, Any] | None, ok: bool,
          reason: str = "") -> None:
    """append-only 本地审计：只写摘要，不写原文 / 向量 / URL / 凭据。"""
    detail = {
        "guard_id": GUARD_ID, "ok": bool(ok), "reason": reason or "ALLOWED",
        "points": (clearance or {}).get("points", 0),
        "batch_sha256_20": (clearance or {}).get("batch_sha256_20")
                           or (clearance or {}).get("digest"),
        "total_payload_bytes": (clearance or {}).get("total_payload_bytes", 0),
        "collection": (clearance or {}).get("collection"),
        "source_id": (clearance or {}).get("source_id"),
        "data_written": False,  # 审计发生在出站之前
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    try:
        sources.log_assessment(event, "vector_store", detail)
    except Exception:
        pass  # 审计失败不应改变出站判定；出站由 validate_outbound 决定


def status() -> dict[str, Any]:
    """只读防护状态。绝不返回 QDRANT_URL / API key / 任何业务文本。"""
    from urllib.parse import urlparse
    from secure_config import secret
    url = secret("QDRANT_URL") or ""
    host = ""
    endpoint_check: dict[str, Any] = {"ok": False, "reason": "NOT_CONFIGURED"}
    if url:
        host = (urlparse(url).hostname or "").lower()
        try:
            endpoint_check = validate_endpoint(url)
        except OutboundBlocked as exc:
            endpoint_check = {"ok": False, "reason": str(exc), "host": host}
    try:
        collection_check = validate_collection(cfg.get("QDRANT_COLLECTION"))
    except OutboundBlocked as exc:
        collection_check = {"ok": False, "reason": str(exc)}
    return {
        "ok": True,
        "guard_id": GUARD_ID,
        "endpoint": endpoint_check,
        "endpoint_host": host,          # 仅 host，不含端口 / 路径 / 凭据
        "api_key_present": bool(secret("QDRANT_API_KEY")),
        "api_key_never_returned": True,
        "collection": collection_check,
        "allowed_scopes": list(OUTBOUND_POLICY["allowed_scopes"]),
        "allowed_grades": list(OUTBOUND_POLICY["allowed_grades"]),
        "allowed_source_ids": allowed_source_ids(),
        "payload_key_allowlist": sorted(OUTBOUND_POLICY["payload_key_allowlist"]),
        "limits": {k: OUTBOUND_POLICY[k] for k in
                   ("max_text_chars", "max_title_chars", "max_payload_bytes",
                    "max_batch_points", "max_vector_dim", "max_delete_ids")},
        "pii_action": OUTBOUND_POLICY["pii_action"],
        "audit_content": OUTBOUND_POLICY["audit"]["content"],
        "audit_never_log": list(OUTBOUND_POLICY["audit"]["never_log"]),
        "search_sends": OUTBOUND_POLICY["search_sends"],
        "user_data_to_cloud": OUTBOUND_POLICY["allow_user_data_to_cloud"],
        "legal_review": OUTBOUND_POLICY["legal_review"],
        "network_called_by_this_module": False,
        "disclaimer": "工程过程控制，不构成法律意见；Q18 数据出境评估与法务书面结论仍待回。",
    }

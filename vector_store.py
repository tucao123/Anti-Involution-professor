#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · Qdrant 向量库适配层（纯标准库 REST + 本地降级）

链路：D 级公开岗位样本 → 来源闸门 → PII 扫描/脱敏 → embedding.embed(scope=public)
      → Qdrant Cloud collection（Cosine）→ 相似度检索 → 带 grade/region 回传。

合规约束（不可通过配置绕过，需改代码即需重新过法务评审）：
1. **C 级用户自带数据（JD / 简历 / 截图 OCR）默认禁止写入云向量库**。
   ALLOW_CLOUD_FOR_USER_DATA 默认 false；打开前须有法务书面结论与用户单独授权。
2. 写入前逐条过 sources.assert_fetchable()；未登记 / 未批准的来源直接拒绝。
   非 D 级来源不得进入 zhiyin_public_d 集合。
3. 逐条 PII 扫描 + 脱敏；命中个人信息即整批拒绝（embedding 存在反演风险，
   向量不等于匿名化）。
4. 云端写入另有 QDRANT_ALLOW_WRITE 开关，默认 false。只读连通性探测始终允许，
   因为它不传输任何数据。
5. Qdrant 不可达时自动降级到本地进程内索引（不落盘、重启即清空），
   接口与返回结构完全一致；status() 如实标注实际使用的后端。
6. 检索结果强制携带 grade / region / applicability / forbidden_claims，
   D 级样本不得据此得出国内就业形势、薪资或蓝海岗位结论。

本模块是工程过程控制，不构成法律意见。
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any

import approval_gate   # Q18 书面回执双闸门
import embedding
import outbound_guard  # 2026-11-16 Q18 出境写入防护：目标 / 集合 / 分级 / 字段 / PII / 体积
import sources
import user_jd
from privacy import scan_pii
from secure_config import cfg, secret


class CloudUserDataBlocked(Exception):
    """C 级用户数据试图上云。"""


class VectorRejected(Exception):
    """来源 / PII / 分级校验未通过，code 见 str(exc)。"""


class VectorUnavailable(Exception):
    """Qdrant 不可达；调用方应降级，不向上游泄露细节。"""


_LOCAL: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_PROBE_CACHE: dict[str, Any] = {"ts": 0.0, "result": None}
_COLLECTION_DIM: int | None = None

FORBIDDEN_CLAIMS = [
    "不得据此推断中国境内招聘市场需求、同比或薪资水平",
    "不得据此得出蓝海岗位 / 被替代风险排序",
    "全球远程岗样本，不代表国内市场",
]

# 同一集合内用 payload 字段区分两个逻辑索引；旧数据没有该字段时按标题前缀归类。
MATERIAL_KINDS = ("job_info", "interview_talk")
_TALK_TITLE_PREFIXES = ("话术·", "流程·")


def material_kind_of(title: str = "", explicit: str = "") -> str:
    """把入库条目标成岗位信息索引或面试话术索引（只存枚举，不存用户文本）。"""
    value = str(explicit or "").strip()
    if value in MATERIAL_KINDS:
        return value
    return "interview_talk" if str(title).strip().startswith(_TALK_TITLE_PREFIXES) else "job_info"


# ---------------------------------------------------------------------------
# Qdrant REST（不依赖 qdrant-client）
# ---------------------------------------------------------------------------
def _endpoint() -> str | None:
    url = secret("QDRANT_URL")
    return url.rstrip("/") if url else None


def _request(method: str, path: str, body: dict[str, Any] | None = None,
             timeout: int | None = None) -> dict[str, Any]:
    base = _endpoint()
    key = secret("QDRANT_API_KEY")
    if not base or not key:
        raise VectorUnavailable("NOT_CONFIGURED")
    # 出站目标锁定：每次请求（含只读探测）前都校验，防止配置被改到任意主机后
    # 把数据发过去。host 不在官方域 / 显式白名单时直接拒绝，不发出请求。
    try:
        outbound_guard.validate_endpoint(base)
    except outbound_guard.OutboundBlocked:
        raise VectorUnavailable("ENDPOINT_NOT_ALLOWED") from None
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"api-key": key, "Content-Type": "application/json",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or cfg.integer("QDRANT_TIMEOUT", 8)) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as exc:
        # 只保留状态码，绝不回显响应体（可能含上游配置细节）
        raise VectorUnavailable("HTTP_%d" % exc.code) from None
    except Exception as exc:
        raise VectorUnavailable(type(exc).__name__) from None


def probe(force: bool = False) -> dict[str, Any]:
    """只读探测：GET /collections，不写入、不传输任何业务数据。"""
    import time
    now = time.time()
    if not force and _PROBE_CACHE["result"] and now - _PROBE_CACHE["ts"] < 30:
        return dict(_PROBE_CACHE["result"])
    result: dict[str, Any] = {
        "ok": True, "reachable": False, "configured": bool(_endpoint() and secret("QDRANT_API_KEY")),
        "collection": cfg.get("QDRANT_COLLECTION"), "collections": [], "error": None,
        "target_collection_exists": None, "target_collection": None,
        "read_only_probe": True, "data_sent": False,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if not result["configured"]:
        result["error"] = "NOT_CONFIGURED"
    else:
        try:
            data = _request("GET", "/collections")
            result["reachable"] = True
            result["collections"] = [c.get("name") for c in
                                     data.get("result", {}).get("collections", [])]
            name = cfg.get("QDRANT_COLLECTION")
            result["target_collection_exists"] = name in result["collections"]
            if result["target_collection_exists"]:
                # 只读集合元数据；不读取任何 point 的内容或向量
                try:
                    info = _request("GET", "/collections/" +
                                    urllib.parse.quote(name, safe=""), timeout=6)
                    r = info.get("result") or {}
                    vectors = ((r.get("config") or {}).get("params") or {}).get("vectors") or {}
                    result["target_collection"] = {
                        "dim": vectors.get("size"), "distance": vectors.get("distance"),
                        "points_count": r.get("points_count"), "status": r.get("status"),
                    }
                except VectorUnavailable as exc:
                    result["target_collection_error"] = str(exc)
        except VectorUnavailable as exc:
            result["error"] = str(exc)
    _PROBE_CACHE.update(ts=now, result=result)
    return dict(result)


def ensure_collection(dim: int) -> dict[str, Any]:
    """按实际 embedding 维度建集合；已存在且维度不符时明确报错而非静默混用。"""
    global _COLLECTION_DIM
    name = cfg.get("QDRANT_COLLECTION")
    try:
        outbound_guard.validate_collection(name)
    except outbound_guard.OutboundBlocked:
        raise VectorUnavailable("COLLECTION_NOT_ALLOWED") from None
    if not cloud_writes_allowed():
        raise VectorUnavailable("CLOUD_WRITES_GATED")
    state = probe()
    if not state["reachable"]:
        raise VectorUnavailable(state["error"] or "UNREACHABLE")
    if name not in state["collections"]:
        _request("PUT", "/collections/" + name,
                 {"vectors": {"size": dim, "distance": "Cosine"}}, timeout=10)
        sources.log_assessment("qdrant_collection_created", "jobicy",
                               {"collection": name, "dim": dim, "grade": "D"})
    else:
        # 2026-11-16：BGE 恢复后本地语义向量为 512 维，而既有集合按
        # DashScope 1024 维建立。维度不符时明确拒绝，避免把错误维度的向量
        # 发给云端后被 400 拒绝、再静默降级为本地写入。
        existing = state.get("target_collection") or {}
        existing_dim = existing.get("dim")
        if isinstance(existing_dim, int) and existing_dim != dim:
            raise VectorUnavailable(
                "COLLECTION_DIM_MISMATCH:%d!=%d" % (existing_dim, dim))
    _COLLECTION_DIM = dim
    _PROBE_CACHE["result"] = None  # 强制下次重新读集合列表
    return {"ok": True, "collection": name, "dim": dim, "distance": "Cosine"}


def _point_id(raw: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "zhiyin:" + raw))


def _assert_scope(items: list[dict[str, Any]], scope: str, source_id: str) -> None:
    if scope == "user":
        if not cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"):
            raise CloudUserDataBlocked()
        raise VectorRejected("USER_CLOUD_INGEST_REQUIRES_LEGAL_SIGNOFF")
    if scope != "public":
        raise VectorRejected("SCOPE_INVALID")
    if not cfg.flag("ALLOW_PUBLIC_SAMPLE_TO_CLOUD", True):
        raise VectorRejected("PUBLIC_TO_CLOUD_DISABLED")
    try:
        sources.assert_fetchable(source_id)
    except sources.SourceBlocked:
        raise VectorRejected("SOURCE_NOT_APPROVED") from None
    src = sources.SOURCES.get(source_id, {})
    if src.get("grade") != "D":
        raise VectorRejected("GRADE_NOT_D")
    for item in items:
        text = str(item.get("text", ""))
        if not text.strip():
            raise VectorRejected("EMPTY_TEXT")
        if scan_pii({"text": text}):
            raise VectorRejected("PII_RESIDUAL")


def cloud_writes_allowed() -> bool:
    """Q18 双闸门：书面回执有效 + QDRANT_ALLOW_WRITE=true，缺一不可。

    回执由人线下放置到 .privacy/approvals/Q18.json（gitignored），工程侧只读；
    光把 .env 开关置 true 而删掉回执，写入会立即被拒绝。
    """
    return bool(cfg.flag("QDRANT_ALLOW_WRITE") and approval_gate.is_enabled("Q18"))


def ingest(items: list[dict[str, Any]], scope: str = "public",
           source_id: str = "jobicy") -> dict[str, Any]:
    """入云或降级入库。items: [{id, text, title?}]，只允许 D 级公开样本。"""
    if not items:
        raise VectorRejected("NO_ITEMS")
    _assert_scope(items, scope, source_id)
    # redact() 返回 (脱敏文本, 处数, 命中类别)，这里只需要文本
    texts = [user_jd.redact(str(it["text"]))[0] for it in items]
    packed = embedding.embed(texts, scope=scope)
    vectors = packed["vectors"]
    src = sources.SOURCES[source_id]
    points = []
    for it, vec in zip(items, vectors):
        pid = _point_id(str(it["id"]))
        payload = {
            "text": user_jd.redact(str(it["text"]))[0],
            "title": str(it.get("title", ""))[:80],
            "grade": src["grade"], "region": src["region"],
            "source_id": source_id,
            "source_name": src["name"],
            "attribution": src["attribution"]["text"],
            "attribution_url": src["attribution"]["url"],
            "applicability": "全球远程岗样本 · 非国内市场",
            "forbidden_claims": FORBIDDEN_CLAIMS,
            "embedding_backend": packed["backend"],
            "semantic": packed["semantic"],
            "scope": scope,
            "material_kind": material_kind_of(
                str(it.get("title", "")), str(it.get("material_kind", ""))),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        points.append({"id": pid, "vector": vec, "payload": payload})

    # 2026-11-16 出站硬校验：无论最终写云还是只写本地，payload 都必须先过
    # 目标 / 集合 / 分级 / 来源 / 字段白名单 / PII / 体积校验。任何一条不过整批拒绝，
    # 不允许「云端被拒但本地悄悄收下」这种绕过。
    try:
        clearance = outbound_guard.validate_outbound(
            points, collection=cfg.get("QDRANT_COLLECTION"),
            scope=scope, source_id=source_id, grade=src["grade"])
    except outbound_guard.OutboundBlocked as exc:
        outbound_guard.audit("qdrant_outbound_blocked", None, False, str(exc))
        raise VectorRejected("OUTBOUND_GUARD_" + str(exc)) from None
    outbound_guard.audit("qdrant_outbound_check", clearance, True)

    cloud_ok = (cloud_writes_allowed() and probe()["reachable"])
    backend = "local-memory"
    detail = ""
    if cloud_ok:
        try:
            ensure_collection(packed["dim"])
            _request("PUT", "/collections/%s/points?wait=true" % cfg.get("QDRANT_COLLECTION"),
                     {"points": points}, timeout=12)
            backend = "qdrant-cloud"
        except VectorUnavailable as exc:
            detail = "云端写入失败已降级本地：%s" % exc
    else:
        detail = ("云端写入闸门未通过（回执 / 配置）" if not cloud_writes_allowed()
                  else "Qdrant 当前不可达（只读探测结果）")
    with _LOCK:
        for p in points:
            _LOCAL[p["id"]] = {"vector": p["vector"], "payload": p["payload"]}
    return {
        "ok": True, "ingested": len(points), "backend": backend,
        "embedding": {k: packed[k] for k in ("backend", "dim", "semantic", "cloud_used")},
        "collection": cfg.get("QDRANT_COLLECTION"),
        "scope": scope, "grade": "D", "detail": detail,
        "cloud_user_data_allowed": cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"),
        "outbound_guard": {
            "guard_id": clearance["guard_id"],
            "allowed": True,
            "points": clearance["points"],
            "total_payload_bytes": clearance["total_payload_bytes"],
            "batch_sha256_20": clearance["batch_sha256_20"],
            "pii_scan": clearance["pii_scan"],
            "audit": "digest_only（不写原文 / 向量 / URL / 凭据）",
        },
    }


def _local_search(vec: list[float], top_k: int) -> list[dict[str, Any]]:
    def cos(a: list[float], b: list[float]) -> float:
        n = min(len(a), len(b))
        if not n:
            return 0.0
        dot = sum(a[i] * b[i] for i in range(n))
        na = math.sqrt(sum(x * x for x in a[:n])) * math.sqrt(sum(x * x for x in b[:n]))
        return dot / na if na else 0.0
    with _LOCK:
        rows = [{"id": pid, "score": cos(vec, row["vector"]), "payload": row["payload"]}
                for pid, row in _LOCAL.items()]
    return sorted(rows, key=lambda r: r["score"], reverse=True)[:top_k]


def search(q: str, top_k: int = 5, scope: str = "public") -> dict[str, Any]:
    if scope == "user" and not cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"):
        raise CloudUserDataBlocked()
    packed = embedding.embed([q], scope=scope)
    vec = packed["vectors"][0]
    backend = "local-memory"
    hits: list[dict[str, Any]] = []
    detail = ""
    if cloud_writes_allowed() and probe()["reachable"]:
        # 境外链路偶发 URLError（首包常失败），短超时 + 有限重试，避免慢等
        for attempt in range(2):
            try:
                data = _request("POST", "/collections/%s/points/search" % cfg.get("QDRANT_COLLECTION"),
                                {"vector": vec, "limit": top_k, "with_payload": True}, timeout=6)
                hits = data.get("result", [])[:top_k]
                backend = "qdrant-cloud"
                break
            except VectorUnavailable as exc:
                detail = "云端检索失败已降级本地：%s" % exc
                if attempt < 1:
                    time.sleep(0.2)
    if not hits:
        hits = _local_search(vec, top_k)
        if not detail:
            detail = "使用本地进程内索引（不落盘、重启清空）"
    out = []
    for h in hits:
        p = h.get("payload", {}) or {}
        out.append({
            "id": h.get("id"), "score": round(float(h.get("score", 0)), 4),
            "title": p.get("title", ""), "text": p.get("text", ""),
            "grade": p.get("grade", "D"), "region": p.get("region", "global_remote"),
            "source_id": p.get("source_id", ""),
            "attribution": p.get("attribution", ""),
            "attribution_url": p.get("attribution_url", ""),
            "applicability": p.get("applicability", "全球远程岗样本 · 非国内市场"),
            "forbidden_claims": p.get("forbidden_claims", FORBIDDEN_CLAIMS),
            "semantic": p.get("semantic", False),
            "material_kind": material_kind_of(
                str(p.get("title", "")), str(p.get("material_kind", ""))),
        })
    return {
        "ok": True, "backend": backend, "hits": out, "count": len(out),
        "embedding": {k: packed[k] for k in ("backend", "dim", "semantic")},
        "detail": detail,
        "market_scope": "global_remote / grade D（不代表中国境内招聘市场）",
    }


def delete(ids: list[str]) -> dict[str, Any]:
    pids = [_point_id(str(i)) for i in ids]
    try:
        dclear = outbound_guard.validate_delete(pids, cfg.get("QDRANT_COLLECTION"))
    except outbound_guard.OutboundBlocked as exc:
        raise VectorRejected("OUTBOUND_GUARD_" + str(exc)) from None
    outbound_guard.audit("qdrant_delete_check", dclear, True)
    removed = 0
    with _LOCK:
        for pid in pids:
            if _LOCAL.pop(pid, None) is not None:
                removed += 1
    cloud = 0
    if cloud_writes_allowed() and probe()["reachable"]:
        try:
            _request("POST", "/collections/%s/points/delete?wait=true" % cfg.get("QDRANT_COLLECTION"),
                     {"points": pids}, timeout=10)
            cloud = len(pids)
        except VectorUnavailable:
            pass
    return {"ok": True, "deleted_local": removed, "deleted_cloud": cloud,
            "outbound_guard": dclear}


def guard_check(items: list[dict[str, Any]], source_id: str = "jobicy",
                scope: str = "public") -> dict[str, Any]:
    """只读 dry-run：完整走出站校验，**不 embed、不联网、不写入任何地方**。

    用于验证「这份 payload 能不能出境」而不产生新的境外写入。
    vector 用占位 [0.0]，仅用于通过维度校验，绝不发送。
    """
    _assert_scope(items, scope, source_id)
    src = sources.SOURCES[source_id]
    points = []
    for it in items:
        payload = {
            "text": user_jd.redact(str(it["text"]))[0],
            "title": str(it.get("title", ""))[:80],
            "grade": src["grade"], "region": src["region"],
            "source_id": source_id, "source_name": src["name"],
            "attribution": src["attribution"]["text"],
            "attribution_url": src["attribution"]["url"],
            "applicability": "全球远程岗样本 · 非国内市场",
            "forbidden_claims": FORBIDDEN_CLAIMS,
            "embedding_backend": "dry-run", "semantic": False,
            "scope": scope,
            "material_kind": material_kind_of(
                str(it.get("title", "")), str(it.get("material_kind", ""))),
            "ingested_at": datetime.now(timezone.utc).isoformat(),
        }
        points.append({"id": "dry-run", "vector": [0.0], "payload": payload})
    return outbound_guard.validate_outbound(
        points, collection=cfg.get("QDRANT_COLLECTION"),
        scope=scope, source_id=source_id, grade=src["grade"])


def clear_local() -> int:
    with _LOCK:
        n = len(_LOCAL)
        _LOCAL.clear()
    return n


def status() -> dict[str, Any]:
    p = probe()
    with _LOCK:
        local_count = len(_LOCAL)
    return {
        "ok": True,
        "qdrant": p,
        "cloud_writes_enabled": cloud_writes_allowed(),
        "q18_approval": approval_gate.gate("Q18"),
        "cloud_reachable_but_writes_disabled": bool(
            p["reachable"] and not cloud_writes_allowed()),
        "cloud_write_gate": ("已开启（依据项目所有者 2026-11-16 回执 OWNER-2026-11-16-Q18；"
                             "法务书面结论 / 数据出境评估仍待回，legal_review=pending）"
                             if cloud_writes_allowed()
                             else "关闭：回执或配置开关未通过（Q18 双闸门），"
                                  "只读连通性探测不受限制"),
        "backend_in_use": "qdrant-cloud" if (p["reachable"] and cloud_writes_allowed())
                          else "local-memory",
        "local_index_points": local_count,
        "local_index_persistent": False,
        "collection": cfg.get("QDRANT_COLLECTION"),
        "collection_dim": _COLLECTION_DIM,
        "embedding": embedding.status(),
        "allowed_scope": "D 级公开岗位样本 / 脱敏技能标签（grade D）",
        "material_kinds": list(MATERIAL_KINDS),
        "user_data_to_cloud": cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"),
        "pii_policy": "逐条扫描 + 脱敏；命中即整批拒绝；向量不等于匿名化",
        "outbound_guard": outbound_guard.status(),
        "cloud_write_gates": {
            "config_flag": bool(cfg.flag("QDRANT_ALLOW_WRITE")),
            "q18_receipt": approval_gate.is_enabled("Q18"),
            "outbound_payload_guard": outbound_guard.GUARD_ID,
            "all_required": True,
        },
        "forbidden_claims": FORBIDDEN_CLAIMS,
    }

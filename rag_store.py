#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 本地研究用向量检索库（纯标准库，零新增依赖）

链路：用户手动截图 → 浏览器本地 OCR → 用户核对后的**文本** → PII 前置脱敏
      → 分块 → 字符 2/3-gram + 技能本体稀疏 TF-IDF → 本地索引 → 余弦检索

为什么可以不装 faiss / qdrant：
  研究原型只需要「把相关 JD 片段找回来」，不需要稠密语义向量。
  中文按字符 n-gram 切分 + 技能本体加权，配合动态 IDF 与余弦相似度，
  对「技能词 → 岗位职责原文」这类检索足够，且完全确定性、可复现、可审计。

合规约束（对应 PIPL 第 13 条第(六)项「合理范围内处理已合法公开 / 用户主动提供的信息」，
以及第 5、6、7、17 条的最小必要、目的限定与告知义务）：
1. **服务端没有图片上传端点**：只收文本。位图 OCR 在浏览器本地完成后即从内存释放。
2. 入库前必须过 sources.assert_fetchable 闸门；未登记 / 未批准 / 已封禁的来源直接拒绝。
3. purpose 只能是 research，purpose_ack 必须显式为 True；不得用于个人画像、
   重新识别、对外提供或模型训练。
4. **向量不等于匿名化**（embedding 存在反演风险）：因此片段文本用 AES-256-GCM
   加密后落盘，密钥沿用 privacy.py 的进程内 / 环境变量密钥；默认 30 天 TTL，
   可单文档删除、可整库清空，每次写入 / 检索 / 删除都写 append-only 审计哈希链。
5. 检索结果强制携带 grade / region / forbidden_claims：C、D 级数据不得据此得出
   国内就业形势、需求同比、薪资或蓝海岗位结论。
6. 不对外分发、不外发云端模型、不做跨文档市场级聚合统计。

本模块是工程过程控制，不构成法律意见。
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import job_info     # 截图链路：只留岗位信息行、隐私信息整行不入库
import privacy
import sources
import user_jd

ROOT = Path(__file__).resolve().parent
STORE_DIR = ROOT / ".rag"
STORE_FILE = STORE_DIR / "index.json"

MIN_TEXT = 20
MAX_TEXT = 12000
CHUNK_SIZE = 220
CHUNK_OVERLAP = 40
MIN_CHUNK = 12
MAX_DOCS = 200
MAX_CHUNKS = 2000
MAX_HIT_CHARS = 300
DEFAULT_RETENTION = 30
MAX_RETENTION = 90

ORIGINS = ("pasted_text", "local_ocr", "public_api")
# 来源与文本形态必须一致：不能把用户粘贴 / 截图的 JD 标成「公开 API 抓取样本」，
# 也不能把公开 API 样本伪装成用户自带数据——这是数据可追溯性的硬约束。
PUBLIC_API_SOURCES = frozenset({"jobicy", "remoteok"})
USER_TEXT_SOURCES = frozenset({"user_jd", "user_screenshot", "demo"})
CANARY = "zhiyin-rag-v1"

_AES = AESGCM(privacy.KEY)
_LOCK = threading.RLock()
_IDF: dict[str, float] | None = None
_NORMS: dict[str, float] | None = None

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.\-]{1,23}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_SENT_SPLIT = re.compile(r"(?<=[。；;！!？?\n])\s*")
_BINARY_RE = re.compile(r"data:image/|base64,|<img\b|\\x89PNG|/9j/4A", re.I)
_BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{400,}={0,2}")


class ConsentRequired(RuntimeError):
    """未取得研究用途授权；默认不同意。"""


class InvalidInput(RuntimeError):
    """输入不合规（长度、格式、用途、容量或夹带图片数据）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _enc(text: str) -> dict[str, str]:
    nonce = os.urandom(12)
    cipher = _AES.encrypt(nonce, text.encode("utf-8"), None)
    return {"n": _b64(nonce), "c": _b64(cipher)}


def _dec(obj: Any) -> str | None:
    try:
        return _AES.decrypt(base64.b64decode(obj["n"]),
                            base64.b64decode(obj["c"]), None).decode("utf-8")
    except Exception:
        return None


def _empty() -> dict[str, Any]:
    return {"version": 1, "canary": _enc(CANARY), "docs": {}, "chunks": []}


def _write(store: dict[str, Any]) -> None:
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    tmp.replace(STORE_FILE)


def _invalidate() -> None:
    global _IDF, _NORMS
    _IDF = None
    _NORMS = None


def _read() -> dict[str, Any]:
    """密钥失效（未配置持久密钥时服务重启）即视作不可恢复数据，整库清空。"""
    if not STORE_FILE.is_file():
        return _empty()
    try:
        store = json.loads(STORE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(store, dict) or store.get("version") != 1:
        return _empty()
    if _dec(store.get("canary")) != CANARY:
        privacy.audit("rag_key_rotated", "index", "密钥不可解，研究索引按不可恢复数据整体清空")
        fresh = _empty()
        _write(fresh)
        _invalidate()
        return fresh
    store.setdefault("docs", {})
    store.setdefault("chunks", [])
    return store


def _purge(store: dict[str, Any]) -> int:
    """TTL 到期自动删除文档及其全部片段与向量。"""
    now = datetime.now(timezone.utc)
    expired = []
    for did, doc in list(store["docs"].items()):
        try:
            if datetime.fromisoformat(str(doc.get("expires_at"))) < now:
                expired.append(did)
        except (ValueError, TypeError):
            expired.append(did)
    if not expired:
        return 0
    for did in expired:
        store["docs"].pop(did, None)
        privacy.audit("rag_doc_expired", did, "研究留存期届满，片段与向量已删除")
    store["chunks"] = [c for c in store["chunks"] if c["doc_id"] not in expired]
    _write(store)
    _invalidate()
    return len(expired)


# ---------------------------------------------------------------------------
# 向量化：确定性稀疏 TF-IDF
# ---------------------------------------------------------------------------
def _hash_feature(feat: str) -> str:
    """blake2b 而非内置 hash()，保证跨进程 / 跨重启特征键稳定。"""
    return hashlib.blake2b(feat.encode("utf-8"), digest_size=4).hexdigest()


def _features(text: str) -> dict[str, float]:
    low = (text or "").lower()
    tf: dict[str, float] = {}

    def add(feat: str, weight: float = 1.0) -> None:
        key = _hash_feature(feat)
        tf[key] = tf.get(key, 0.0) + weight

    for m in _TOKEN_RE.finditer(low):
        add("w:" + m.group(0))
    for seg in _CJK_RE.findall(low):
        for i in range(len(seg) - 1):
            add("b:" + seg[i:i + 2])
        for i in range(len(seg) - 2):
            add("t:" + seg[i:i + 3], 0.6)
    # 技能本体词命中加权：让「RAG / 检索增强 / LangChain」等术语更易召回
    for skill in user_jd.SKILLS:
        for word in skill["words"]:
            if len(word) >= 2 and word in low:
                add("s:" + word, 2.0)
    return {k: 1.0 + math.log(v) for k, v in tf.items()}


def _chunks(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p.strip()]
    pieces: list[str] = []
    for p in parts:
        while len(p) > CHUNK_SIZE:
            pieces.append(p[:CHUNK_SIZE])
            p = p[CHUNK_SIZE - CHUNK_OVERLAP:]
        if p:
            pieces.append(p)
    out: list[str] = []
    cur = ""
    for p in pieces:
        if not cur:
            cur = p
        elif len(cur) + len(p) + 1 <= CHUNK_SIZE:
            cur += " " + p
        else:
            if len(cur) >= MIN_CHUNK:
                out.append(cur)
                cur = cur[-CHUNK_OVERLAP:] + " " + p
            else:
                cur += " " + p
    if len(cur) >= MIN_CHUNK:
        out.append(cur)
    return [c[:CHUNK_SIZE + CHUNK_OVERLAP] for c in out]


def _ensure_stats(store: dict[str, Any]) -> None:
    """动态 IDF 与加权范数，写 / 删 / 过期后失效重建。"""
    global _IDF, _NORMS
    if _IDF is not None and _NORMS is not None:
        return
    df: dict[str, int] = {}
    for c in store["chunks"]:
        for key in c["vec"]:
            df[key] = df.get(key, 0) + 1
    n = max(1, len(store["chunks"]))
    idf = {k: math.log(1.0 + n / (1.0 + d)) for k, d in df.items()}
    norms = {}
    for c in store["chunks"]:
        norm = math.sqrt(sum((w * idf.get(k, 1.0)) ** 2 for k, w in c["vec"].items()))
        norms[c["id"]] = norm or 1.0
    _IDF, _NORMS = idf, norms


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------
def ingest(text: str, source_id: str, title: str = "研究文本",
           purpose: str = "research", purpose_ack: bool = False,
           retain_days: int = DEFAULT_RETENTION,
           origin: str = "pasted_text") -> dict[str, Any]:
    if purpose != "research":
        raise InvalidInput("PURPOSE_NOT_RESEARCH")
    if purpose_ack is not True:
        sources.log_assessment("rag_consent_missing", source_id, {})
        raise ConsentRequired("PURPOSE_ACK_REQUIRED")
    if origin not in ORIGINS:
        raise InvalidInput("ORIGIN_INVALID")

    # fail-closed 优先：未批准来源先被安全闸门拒绝，不被口径校验掩盖
    src = sources.assert_fetchable(source_id)
    if origin == "public_api":
        if source_id not in PUBLIC_API_SOURCES:
            raise InvalidInput("ORIGIN_MISMATCH")
    elif source_id not in USER_TEXT_SOURCES:
        raise InvalidInput("ORIGIN_MISMATCH")

    raw = (text or "").strip()
    if not (MIN_TEXT <= len(raw) <= MAX_TEXT):
        raise InvalidInput("TEXT_LENGTH_INVALID")
    if _BINARY_RE.search(raw) or _BASE64_BLOB_RE.search(raw):
        raise InvalidInput("IMAGE_PAYLOAD_REJECTED")

    # PII 先脱敏，再分块，再向量化；明文原文从不进入索引
    clean, n_pii, kinds = user_jd.redact(raw)
    if privacy.scan_pii(clean):
        raise InvalidInput("PII_RESIDUAL")

    # 2026-11-16 用户约束：截图 / 本地 OCR 链路的隐私信息不要入库。
    # 个人信息语境行整行剔除（不是留占位符），只把岗位信息行送进索引；
    # pii_redacted / pii_kinds 仍按原始全文统计，作为「检出并排除」的举证。
    job_only = False
    if origin == "local_ocr":
        kept = job_info.job_only_lines(clean)
        filtered = "\n".join(kept)
        if not filtered.strip():
            raise InvalidInput("NO_JOB_INFO")
        clean = filtered
        job_only = True
        if privacy.scan_pii(clean):
            raise InvalidInput("PII_RESIDUAL")

    chunks = _chunks(clean)
    if not chunks:
        raise InvalidInput("NO_CHUNK")

    days = max(1, min(MAX_RETENTION, int(retain_days or DEFAULT_RETENTION)))
    did = secrets.token_hex(8)
    created = datetime.now(timezone.utc)
    expires = created + timedelta(days=days)

    with _LOCK:
        store = _read()
        _purge(store)
        if len(store["docs"]) >= MAX_DOCS:
            raise InvalidInput("DOC_LIMIT")
        if len(store["chunks"]) + len(chunks) > MAX_CHUNKS:
            raise InvalidInput("CHUNK_LIMIT")
        for i, chunk in enumerate(chunks):
            store["chunks"].append({
                "id": did + "-" + str(i),
                "doc_id": did,
                "chars": len(chunk),
                "text": _enc(chunk),          # 落盘密文，不含明文片段
                "vec": _features(chunk),      # 稀疏向量，不含原文
            })
        store["docs"][did] = {
            "id": did,
            "title": (title or "研究文本").strip()[:80],
            "source_id": source_id,
            "grade": src["grade"],
            "region": src["region"],
            "origin": origin,
            "created_at": created.isoformat(timespec="seconds"),
            "expires_at": expires.isoformat(timespec="seconds"),
            "retain_days": days,
            "chars": len(clean),
            "chunk_count": len(chunks),
            "pii_redacted": n_pii,
            "pii_kinds": kinds,
            "job_only": job_only,
            "pii_lines_excluded": job_only,
        }
        _write(store)
        _invalidate()
        privacy.audit("rag_ingest", did,
                      "来源 %s / %s 级 / %d 块 / 脱敏 %d 处 / 仅岗位=%s / 留存 %d 天"
                      % (source_id, src["grade"], len(chunks), n_pii, job_only, days))
    sources.log_assessment("rag_ingest", source_id,
                           {"grade": src["grade"], "chunks": len(chunks),
                            "pii_redacted": n_pii, "retain_days": days})
    return {
        "ok": True,
        "doc": store["docs"][did],
        "vectorization": "字符 2/3-gram + 技能本体 · 稀疏 TF-IDF · 余弦相似度",
        "encrypted_at_rest": True,
        "image_uploaded": False,
        "note": "位图自始未上传；截图 / 本地 OCR 链路的隐私信息整行剔除、不入库，"
                "只保留岗位信息。个人画像由用户主动提供 JD / 简历的本地 /api/jd/analyze 链路提供，"
                "两条链路分离。向量不等同于匿名化，故同时提供 TTL、单文档删除与整库清空。",
    }


def query(q: str, top_k: int = 5) -> dict[str, Any]:
    text = (q or "").strip()[:200]
    if len(text) < 2:
        raise InvalidInput("QUERY_TOO_SHORT")
    clean, _n, _kinds = user_jd.redact(text)
    k = max(1, min(10, int(top_k or 5)))
    with _LOCK:
        store = _read()
        _purge(store)
        _ensure_stats(store)
        qvec = _features(clean)
        qnorm = math.sqrt(sum((w * (_IDF or {}).get(key, 1.0)) ** 2
                              for key, w in qvec.items())) or 1.0
        scored: list[tuple[float, dict[str, Any]]] = []
        for c in store["chunks"]:
            common = qvec.keys() & c["vec"].keys()
            if not common:
                continue
            dot = sum(qvec[key] * c["vec"][key]
                      * ((_IDF or {}).get(key, 1.0) ** 2) for key in common)
            score = dot / (qnorm * (_NORMS or {}).get(c["id"], 1.0))
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: (-x[0], x[1]["id"]))
        hits = []
        for score, c in scored[:k]:
            doc = store["docs"].get(c["doc_id"], {})
            plain = _dec(c["text"]) or "[片段密钥已失效，请重新入库]"
            rule = sources.GRADE_RULES.get(doc.get("grade", "D"), sources.GRADE_RULES["D"])
            hits.append({
                "chunk_id": c["id"],
                "doc_id": c["doc_id"],
                "title": doc.get("title", ""),
                "text": plain[:MAX_HIT_CHARS],
                "score": round(float(score), 4),
                "source_id": doc.get("source_id", ""),
                "grade": doc.get("grade", "D"),
                "region": doc.get("region", "n/a"),
                "origin": doc.get("origin", ""),
                "created_at": doc.get("created_at", ""),
                "forbidden_claims": rule["forbidden"],
            })
        privacy.audit("rag_query", "", "检索 %d 字 / 命中 %d 片段" % (len(clean), len(hits)))

    forbidden = sorted({f for h in hits for f in h["forbidden_claims"]})
    grades = sorted({h["grade"] for h in hits})
    return {
        "ok": True,
        "query": clean,
        "hits": hits,
        "total_chunks": len(store["chunks"]),
        "retrieval": "稀疏 TF-IDF · 余弦相似度 · 确定性排序，不含模型生成",
        "scope": {
            "grades": grades,
            "representative_of_cn": False,
            "forbidden_claims": forbidden,
            "note": "检索返回的是脱敏后的原文片段，不是分析结论。"
                    "C / D 级片段只服务单份 JD 的能力对齐或技能词频参考，"
                    "不得据此得出国内就业形势、需求同比、薪资或蓝海岗位排序。",
        },
        "disclaimer": "本结果仅用于本人研究检索；不得对外分发、不得用于个人画像或重新识别。",
    }


def delete_document(doc_id: str) -> bool:
    did = (doc_id or "").strip()
    with _LOCK:
        store = _read()
        if did not in store["docs"]:
            return False
        store["docs"].pop(did, None)
        store["chunks"] = [c for c in store["chunks"] if c["doc_id"] != did]
        _write(store)
        _invalidate()
        privacy.audit("rag_doc_deleted", did, "按文档撤回，片段与向量已删除")
    sources.log_assessment("rag_delete", "rag_store", {"doc": did})
    return True


def delete_all() -> int:
    with _LOCK:
        store = _read()
        count = len(store["docs"])
        fresh = _empty()
        _write(fresh)
        _invalidate()
        privacy.audit("rag_purged_all", "index", "用户清空研究索引，共 %d 份文档" % count)
    sources.log_assessment("rag_purge_all", "rag_store", {"docs": count})
    return count


def status() -> dict[str, Any]:
    with _LOCK:
        store = _read()
        expired = _purge(store)
        ok, count, first_bad = privacy.verify_chain()
        return {
            "ok": True,
            "purpose": "research",
            "consent_required": ["purpose_ack"],
            "docs": len(store["docs"]),
            "chunks": len(store["chunks"]),
            "expired_purged": expired,
            "retention_default_days": DEFAULT_RETENTION,
            "retention_max_days": MAX_RETENTION,
            "fetchable_sources": [k for k, v in sources.SOURCES.items()
                                  if v["legal_status"] == "approved"],
            "allowed_origins": list(ORIGINS),
            "screenshot_pii_to_store": False,
            "local_ocr_job_only": True,
            "user_profiling_project_wide": True,
            "profiling_path": "/api/jd/analyze",
            "image_upload": False,
            "cloud_model": False,
            "external_vector_db": False,
            "encrypted_at_rest": True,
            "cipher": "AES-256-GCM",
            "key_source": privacy.KEY_SOURCE,
            "persists_across_restart": privacy.KEY_SOURCE != "ephemeral:process-memory",
            "vectorization": "字符 2/3-gram + 技能本体 · 稀疏 TF-IDF · 余弦相似度（纯标准库）",
            "audit": {"entries": count, "chain_ok": ok, "first_bad_seq": first_bad},
            "note": "未配置 ZHIYIN_PII_KEY 时密钥仅存于进程内存，服务重启后旧索引按不可恢复数据自动清空；"
                    "这既是安全默认，也符合研究用途的最小留存原则。向量不等同于匿名化，"
                    "因此片段加密、TTL 与删除权同时提供。",
        }

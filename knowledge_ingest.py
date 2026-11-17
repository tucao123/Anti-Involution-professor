#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · 管理员知识库入库（文本 / 截图 OCR 文本 → Qdrant 云 RAG）

取代此前的自动爬取 / 自动截图执行体：系统不再访问任何第三方页面。
知识的唯一来源是**管理员主动提供**：
  1. 直接粘贴文本（origin=admin_text）；
  2. 上传截图，由浏览器本地 OCR 识别成文本后再提交（origin=admin_ocr）。
    位图只在浏览器内存中处理，服务端自始至终只收到文本。

处理链路（纯本地、确定性、可审计）：
    管理员文本 → PII 前置脱敏 → 仅保留岗位信息行（个人信息整行剔除）
              → 分块 → 向量化（public scope，与既有 Qdrant 集合同维）
              → Qdrant 云向量库（经 outbound_guard + Q18 双闸门）

合规约束（工程过程控制，不构成法律意见）：
- 服务端没有图片上传端点，只收文本；origin 仅作来源标注，不能夹带位图。
- 无论文本还是截图链路，**一律**先脱敏再按岗位语境行过滤，隐私信息不入库。
- 入库仍走 vector_store.ingest 的全部闸门：来源注册 + D 级 + payload 白名单 +
  逐字段 PII 扫描 + 出站目标锁定 + Q18 回执 / 配置双闸门。
- 检索结果沿用 D 级口径：只作研究参考，不产出市场级 / 画像类结论。
"""
from __future__ import annotations

import re
import secrets
from typing import Any

import job_info
import privacy
import sources
import user_jd
import vector_store

ROOT = None  # 占位：本模块不落盘任何原文，无需路径

ORIGINS = ("admin_text", "admin_ocr")
SOURCE_ID = "admin_curated"

MIN_TEXT = 20
MAX_TEXT = 12000
CHUNK_SIZE = 480
CHUNK_OVERLAP = 60
MIN_CHUNK = 24
MAX_CHUNKS = 120
MAX_BATCH = 20  # outbound_guard.max_batch_points

_SENT_SPLIT = re.compile(r"(?<=[。；;！!？?\n])\s*")
_BINARY_RE = re.compile(r"data:image/|base64,|<img\b|\\x89PNG|/9j/4A", re.I)
_BASE64_BLOB_RE = re.compile(r"[A-Za-z0-9+/]{400,}={0,2}")


class ConsentRequired(RuntimeError):
    """未显式确认研究用途授权。"""


class KnowledgeRejected(RuntimeError):
    """输入不合规；str(exc) 为稳定错误码，不含任何原文。"""


def _chunks(text: str) -> list[str]:
    """按句切分 + 定长滑窗；只处理已脱敏且仅岗位信息的文本。"""
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


def prepare(text: str, origin: str, title: str = "管理员知识条目",
            purpose_ack: bool = False) -> dict[str, Any]:
    """只做本地加工，不联网、不写入。供入库与只读校验共用。"""
    if purpose_ack is not True:
        raise ConsentRequired("RESEARCH_PURPOSE_ACK_REQUIRED")
    if origin not in ORIGINS:
        raise KnowledgeRejected("ORIGIN_INVALID")
    # fail-closed：来源未登记 / 未批准先被闸门拒绝
    sources.assert_fetchable(SOURCE_ID)

    raw = (text or "").strip()
    if not (MIN_TEXT <= len(raw) <= MAX_TEXT):
        raise KnowledgeRejected("TEXT_LENGTH_INVALID")
    if _BINARY_RE.search(raw) or _BASE64_BLOB_RE.search(raw):
        raise KnowledgeRejected("IMAGE_PAYLOAD_REJECTED")

    clean, n_pii, kinds = user_jd.redact(raw)
    if privacy.scan_pii(clean):
        raise KnowledgeRejected("PII_RESIDUAL")

    # 用户在两种来源下都选择「脱敏 PII + 仅保留岗位信息」
    kept = job_info.job_only_lines(clean)
    filtered = "\n".join(kept)
    if not filtered.strip():
        raise KnowledgeRejected("NO_JOB_INFO")
    if privacy.scan_pii(filtered):
        raise KnowledgeRejected("PII_RESIDUAL")

    chunks = _chunks(filtered)
    if not chunks:
        raise KnowledgeRejected("NO_CHUNK")
    if len(chunks) > MAX_CHUNKS:
        raise KnowledgeRejected("TOO_MANY_CHUNKS")

    # 标题同样由管理员提供：先脱敏，避免个人信息混入出站 payload 触发整批拒绝
    safe_title = user_jd.redact((title or "管理员知识条目").strip())[0][:80].strip()
    if privacy.scan_pii({"title": safe_title}):
        raise KnowledgeRejected("PII_RESIDUAL")

    return {
        "origin": origin,
        "title": safe_title or "管理员知识条目",
        "text": filtered,
        "chunks": chunks,
        "pii_redacted": n_pii,
        "pii_kinds": kinds,
        "lines_kept": len(kept),
    }


def ingest(text: str, origin: str = "admin_text", title: str = "管理员知识条目",
           purpose_ack: bool = False) -> dict[str, Any]:
    """脱敏 + 仅岗位信息 → 分块 → 向量化 → Qdrant 云（分批以符合出站上限）。"""
    prep = prepare(text, origin, title, purpose_ack)
    doc_id = "kb-" + secrets.token_hex(8)
    batches = [prep["chunks"][i:i + MAX_BATCH]
               for i in range(0, len(prep["chunks"]), MAX_BATCH)]
    results: list[dict[str, Any]] = []
    chunk_ids: list[str] = []
    for batch_no, batch in enumerate(batches):
        items: list[dict[str, Any]] = []
        for i, chunk in enumerate(batch):
            cid = "%s-%d" % (doc_id, batch_no * MAX_BATCH + i)
            chunk_ids.append(cid)
            items.append({"id": cid, "text": chunk, "title": prep["title"]})
        results.append(vector_store.ingest(items, scope="public", source_id=SOURCE_ID))

    backends = sorted({r.get("backend") for r in results})
    return {
        "ok": True,
        "doc_id": doc_id,
        "chunk_ids": chunk_ids,
        "origin": prep["origin"],
        "title": prep["title"],
        "chunks": len(prep["chunks"]),
        "batches": len(batches),
        "lines_kept": prep["lines_kept"],
        "pii_redacted": prep["pii_redacted"],
        "pii_kinds": prep["pii_kinds"],
        "job_only": True,
        "source_id": SOURCE_ID,
        "grade": "D",
        "backend": ",".join(backends),
        "embeddings": [r.get("embedding") for r in results],
        "collection": results[0].get("collection"),
        "outbound_guard": results[0].get("outbound_guard"),
        "note": ("管理员提供的文本 / 截图 OCR 文本已脱敏并仅保留岗位信息后入库；"
                 "位图自始未上传。D 级知识只作研究参考，不产出市场级或个人画像结论。"),
    }


def delete(ids: list[str]) -> dict[str, Any]:
    """按入库返回的 chunk id 删除；空列表直接返回，不触发出站。"""
    if not ids:
        return {"ok": True, "deleted_local": 0, "deleted_cloud": 0}
    return vector_store.delete(ids)


def status() -> dict[str, Any]:
    """只读状态：来源、处理链、向量库与闸门（不含任何原文或凭据）。"""
    vs = vector_store.status()
    return {
        "ok": True,
        "source_id": SOURCE_ID,
        "origins": list(ORIGINS),
        "image_upload_endpoint": False,
        "ocr_location": "browser-local (Tesseract.js)",
        "pipeline": ["admin text / screenshot OCR text", "PII redaction",
                     "job-info-only filter", "chunk", "vectorize", "Qdrant cloud RAG"],
        "pii_to_store": False,
        "job_only": True,
        "grade": "D",
        "collection": vs.get("qdrant", {}).get("collection"),
        "cloud_writes_enabled": vs.get("cloud_writes_enabled"),
        "backend_in_use": vs.get("backend_in_use"),
        "outbound_guard": vs.get("outbound_guard"),
        "legal_review": "pending：数据出境评估与法务书面结论仍待回（工程过程控制，非法律意见）",
    }

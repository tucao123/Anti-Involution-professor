#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 混合检索与 RRF 重排（ROADMAP A4）

两路召回：
  - 稀疏：rag_store.query —— 字符 n-gram + 技能本体 TF-IDF，纯本地、加密片段、TTL 可删；
  - 稠密：vector_store.search —— BGE / DashScope / hash 投影 + Qdrant 或本地内存索引，
          只查 public scope（D 级公开样本）。
融合：Reciprocal Rank Fusion，score = Σ 1 / (k + rank)，k=60；再按来源分级过滤与去重。

C 级边界：用户自带 JD / 本地 OCR 片段**只允许出现在稀疏路**，且默认不返回；
稠密路永远不接收 C 级文本，也不把查询原文写入任何库。查询先经 PII 前置脱敏。
"""
from __future__ import annotations

from typing import Any

import rag_store
import sources
import user_jd
import vector_store

RRF_K = 60
MAX_TOP_K = 10
DEFAULT_GRADES = ("D",)


class InvalidQuery(RuntimeError):
    """查询过短或为空。"""


def _key(hit: dict[str, Any]) -> tuple[str, str, str]:
    """跨两路统一的去重键；不依赖 chunk_id，因为两路的分片编号不同。"""
    return (
        str(hit.get("source_id") or ""),
        str(hit.get("title") or ""),
        str(hit.get("text") or "")[:60],
    )


def _rrf(hits: list[dict[str, Any]], channel: str,
         acc: dict[tuple[str, str, str], dict[str, Any]]) -> None:
    for rank, h in enumerate(hits, 1):
        k = _key(h)
        row = acc.setdefault(k, {
            "source_id": h.get("source_id", ""),
            "title": h.get("title", ""),
            "text": str(h.get("text") or "")[:rag_store.MAX_HIT_CHARS]
                    if hasattr(rag_store, "MAX_HIT_CHARS") else str(h.get("text") or "")[:240],
            "grade": h.get("grade", "D"),
            "region": h.get("region", "n/a"),
            "attribution": h.get("attribution", ""),
            "attribution_url": h.get("attribution_url", ""),
            "forbidden_claims": list(h.get("forbidden_claims") or []),
            "channels": [],
            "sparse_rank": None,
            "dense_rank": None,
            "sparse_score": None,
            "dense_score": None,
            "rrf_score": 0.0,
        })
        if channel not in row["channels"]:
            row["channels"].append(channel)
        row[channel + "_rank"] = rank
        row[channel + "_score"] = round(float(h.get("score") or 0), 4)
        row["rrf_score"] += 1.0 / (RRF_K + rank)
        for claim in h.get("forbidden_claims") or []:
            if claim not in row["forbidden_claims"]:
                row["forbidden_claims"].append(claim)
        if h.get("attribution") and not row["attribution"]:
            row["attribution"] = h["attribution"]
        if h.get("attribution_url") and not row["attribution_url"]:
            row["attribution_url"] = h["attribution_url"]
        # 稀疏路保留 chunk/doc 标识，便于单文档撤回
        if h.get("chunk_id"):
            row["chunk_id"] = h["chunk_id"]
        if h.get("doc_id"):
            row["doc_id"] = h["doc_id"]


def search(q: str, top_k: int = 5, include_user_local: bool = False,
           use_dense: bool = True) -> dict[str, Any]:
    clean, pii_hits, _kinds = user_jd.redact((q or "").strip()[:200])
    if len(clean) < 2:
        raise InvalidQuery("QUERY_TOO_SHORT")
    k = max(1, min(MAX_TOP_K, int(top_k or 5)))
    fetch = min(10, k * 2)

    sparse = rag_store.query(clean, fetch)
    dense: dict[str, Any] = {"hits": [], "backend": "disabled", "embedding": {}}
    if use_dense:
        try:
            dense = vector_store.search(clean, fetch, scope="public")
        except Exception as exc:  # 稠密路失败不影响整体可用性
            dense = {"hits": [], "backend": "unavailable:" + type(exc).__name__, "embedding": {}}

    acc: dict[tuple[str, str, str], dict[str, Any]] = {}
    _rrf(sparse.get("hits", []), "sparse", acc)
    _rrf(dense.get("hits", []), "dense", acc)

    allowed = set(DEFAULT_GRADES)
    if include_user_local:
        allowed.add("C")
    rows: list[dict[str, Any]] = []
    filtered = 0
    for row in acc.values():
        grade = row.get("grade", "D")
        # C 级只允许来自本地稀疏路；含 dense 通道的一律排除，防止用户文本外溢
        if grade not in allowed or (grade == "C" and "dense" in row["channels"]):
            filtered += 1
            continue
        row["rrf_score"] = round(row["rrf_score"], 6)
        if grade == "C":
            row["user_data_local_only"] = True
        rows.append(row)
    rows.sort(key=lambda x: (-x["rrf_score"], x["title"], x["source_id"]))
    rows = rows[:k]

    sparse_keys = {_key(h) for h in sparse.get("hits", [])}
    dense_keys = {_key(h) for h in dense.get("hits", [])}
    overlap = len(sparse_keys & dense_keys)
    union = len(sparse_keys | dense_keys) or 1
    forbidden = sorted({c for r in rows for c in r["forbidden_claims"]})
    rule = sources.GRADE_RULES.get("D", {})

    return {
        "ok": True,
        "query": clean,
        "query_pii_redacted": pii_hits,
        "hits": rows,
        "count": len(rows),
        "retrieval": "稀疏 TF-IDF + 稠密向量双路召回 · RRF(k=60) 融合 · 来源分级过滤 · 归一化去重",
        "comparison": {
            "sparse_hits": len(sparse.get("hits", [])),
            "dense_hits": len(dense.get("hits", [])),
            "fused_candidates": len(acc),
            "overlap": overlap,
            "jaccard": round(overlap / union, 4),
            "grade_filtered": filtered,
            "rrf_k": RRF_K,
        },
        "backends": {
            "sparse": "rag_store / local-encrypted",
            "dense": dense.get("backend", "unavailable"),
            "embedding": dense.get("embedding", {}),
        },
        "scope": {
            "grades": sorted({r["grade"] for r in rows}),
            "representative_of_cn": False,
            "forbidden_claims": forbidden or rule.get("forbidden", []),
            "note": "混合检索只提升召回与排序，不改变数据代表性；"
                    "C 级用户自带文本仅走本地稀疏路，且默认不返回。",
        },
        "query_scope_note": ("查询已前置脱敏；稠密路 embedding 可能使用云端公开样本向量后端，"
                             "但查询文本不入库、不落盘、不进入用户画像。"),
        "disclaimer": "检索结果是脱敏片段，不是分析结论；不得据此得出国内市场、薪资或录用判断。",
    }


def compare(q: str, top_k: int = 5) -> dict[str, Any]:
    """检索质量对比用例：稀疏单路、稠密单路、RRF 融合三者的可核查数字。"""
    clean, _n, _k = user_jd.redact((q or "").strip()[:200])
    if len(clean) < 2:
        raise InvalidQuery("QUERY_TOO_SHORT")
    fetch = min(10, max(1, int(top_k or 5)) * 2)
    try:
        s = rag_store.query(clean, fetch)
        sparse_titles = [h["title"] for h in s.get("hits", [])]
    except Exception as exc:
        sparse_titles, s = [], {"error": type(exc).__name__}
    try:
        d = vector_store.search(clean, fetch, scope="public")
        dense_titles = [h["title"] for h in d.get("hits", [])]
    except Exception as exc:
        dense_titles, d = [], {"error": type(exc).__name__}
    fused = search(clean, top_k)
    fused_titles = [h["title"] for h in fused["hits"]]
    return {
        "ok": True,
        "query": clean,
        "sparse_only": {"count": len(sparse_titles), "titles": sparse_titles},
        "dense_only": {"count": len(dense_titles), "titles": dense_titles},
        "rrf_fused": {"count": len(fused_titles), "titles": fused_titles,
                      "channels_per_hit": [h["channels"] for h in fused["hits"]]},
        "metrics": fused["comparison"],
        "note": "对比只统计召回与融合，不对「相关性」主观打分；无评测集时不声称质量提升。",
    }


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "algorithm": "Reciprocal Rank Fusion (k=%d)" % RRF_K,
        "rrf_k": RRF_K,
        "channels": ["rag_store 稀疏 TF-IDF（本地加密片段）",
                     "vector_store 稠密向量（public scope 仅 D 级）"],
        "default_grades": list(DEFAULT_GRADES),
        "user_data_policy": "C 级仅本地稀疏路，默认不返回；稠密路永不接收 C 级文本",
        "query_redaction": True,
        "deduplication": "(source_id, title, text[:60]) 归一去重",
        "fallback": "稠密路不可用时仅用稀疏结果，不返回空页或假数据",
        "max_top_k": MAX_TOP_K,
    }

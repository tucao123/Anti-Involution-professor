#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 混合检索与 RRF 重排（ROADMAP A4）

两路召回：
  - 稀疏：rag_store.query —— 字符 n-gram + 技能本体 TF-IDF，纯本地、加密片段、TTL 可删；
  - 稠密：vector_store.search —— BGE / DashScope / hash 投影 + Qdrant 或本地内存索引，
          只查 public scope（D 级公开样本）。
融合：Reciprocal Rank Fusion，score = Σ 1 / (k + rank)，k=60；再按来源分级过滤与去重。

面向专家 agent 的两个逻辑索引（同一 D 级集合内用 material_kind 区分）：
  - job_info       岗位信息：目标岗位 / 技能 / 项目场景，供岗位专家与 AI 辅导；
  - interview_talk 面试话术：常见问法 / 应答结构 / 追问流程，供面试专家。
两者都是「切片 → 检索召回 → 确定性重排 → 脱敏 materials → 专家 agent」，
不把用户字段写库、不落盘、不进画像；查询文本先经 PII 前置脱敏。

C 级边界：用户自带 JD / 本地 OCR 片段**只允许出现在稀疏路**，且默认不返回；
稠密路永远不接收 C 级文本，也不把查询原文写入任何库。查询先经 PII 前置脱敏。
"""
from __future__ import annotations

import re
from typing import Any

import rag_store
import sources
import user_jd
import vector_store

RRF_K = 60
MAX_TOP_K = 10
DEFAULT_GRADES = ("D",)

JOB_KIND = "job_info"
TALK_KIND = "interview_talk"
DATA_POLICY_NOTE = (
    "岗位字段与提问只作查询条件，不写向量库、不落盘、不进用户画像；"
    "返回片段均为 D 级公开样本（岗位信息 / 面试话术）。"
)

# 精排权重固定、无随机、无模型调用；结果可复现可核查。
JOB_RERANK_WEIGHTS = {"cover": 0.60, "dual": 0.25, "rrf": 0.15, "rank": 0.0}
TALK_RERANK_WEIGHTS = {"cover": 0.45, "dual": 0.0, "rrf": 0.15, "rank": 0.40}
JOB_RERANK_NOTE = "词项覆盖率 60% + 双通道一致 25% + RRF 15%（确定性，可复现）"
TALK_RERANK_NOTE = "词项覆盖率 45% + 召回名次 40% + RRF 15%（话术子索引，确定性可复现）"


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
        "query_scope_note": ("查询已前置脱敏；稠密路 embedding 使用服务器固定本地 BGE，"
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


# ---------------------------------------------------------------------------
# 专家 agent 统一 RAG 组装：结构化字段 → 查询意图 → 召回 → 双索引精排 → materials
# ---------------------------------------------------------------------------
_STOP = frozenset({
    "的", "了", "和", "与", "及", "在", "是", "有", "对", "把", "为", "从", "到",
    "这", "那", "个", "些", "并", "也", "都", "而", "或", "等", "中", "上", "下",
    "the", "and", "for", "with", "you", "are", "this", "that", "our", "from",
})


def _query_terms(text: str) -> set[str]:
    """从中英混合文本里抽检索词项，用于精排时的覆盖率打分（纯本地、确定性）。"""
    s = (text or "").lower()
    en = re.findall(r"[a-z][a-z0-9+#.]{1,}", s)
    zh = re.findall(r"[\u4e00-\u9fff]{2,4}", s)
    return {t for t in (en + zh) if len(t) >= 2 and t not in _STOP}


def _intent_queries(payload: dict[str, Any], focus: str = "",
                    stage: str = "", hint: str = "",
                    gaps: list[str] | None = None) -> list[str]:
    """把结构化字段拼成单条检索意图。

    focus/stage 只来自结构化意图标签或用户岗位技能，不是用户原文；
    search() 内部再统一 PII 脱敏。一次综合查询，语义覆盖靠精排词项匹配补足，
    避免每条意图都是一次云端往返。
    """
    role = str(payload.get("target_role") or "").strip()[:40]
    major = str(payload.get("major") or "").strip()[:40]
    skills = payload.get("skills") or []
    exp = str(payload.get("experience") or "").strip()[:300]
    jds = payload.get("jds") or []

    skills_txt = " ".join(str(s).strip()[:24] for s in skills if str(s).strip())[:100]
    head = " ".join(x for x in (role, major) if x)
    gap_txt = " ".join(str(g).strip()[:40] for g in (gaps or []) if str(g).strip())[:120]
    scene = " ".join(x for x in (exp[:90], *(str(j).strip()[:80] for j in jds[:1])) if x)
    intent = " ".join(str(x).strip()[:80] for x in (hint, stage, focus) if str(x).strip())[:120]
    # 岗位信息索引与面试话术索引是两条独立子索引（靠 material_kind 区分），因此查询也要分开：
    # 岗位字段（head/skills/scene）语义强，若混进话术查询会把向量召回整体带向岗位信息，
    # 导致话术路命中被 material_kind 过滤成 0。话术路（hint 非空）只用意图词检索通用面试话术
    # （常见问法 / 应答结构 / 追问 / 压力面）；岗位路（hint 为空）拼装顺序不变。
    # 洞察缺口 gap_txt 只进岗位路（用于围绕用户待补能力发问），绝不进话术路，
    # 否则技术词会稀释话术意图、重演话术召回为 0 的老问题。
    if hint:
        query = intent
    else:
        query = " ".join(x for x in (head, skills_txt, scene, gap_txt, intent) if x).strip()
    return [query[:200]] if len(query) >= 2 else []


def _empty_context(scene: str, reason: str, hint: str = "") -> dict[str, Any]:
    return {
        "ok": True, "agent": scene, "materials": [], "count": 0,
        "trace": {"scene": scene, "queries": [], "retrieval": reason,
                  "rerank": "未执行", "data_policy": DATA_POLICY_NOTE,
                  "query_hint": hint},
        "disclaimer": "未提供岗位字段，未做检索；用户字段只作查询，不入库不落盘。",
    }


def _rerank_rows(rows: list[dict[str, Any]], all_terms: set[str],
                 weights: dict[str, float], fetch_size: int, note: str) -> list[dict[str, Any]]:
    """确定性精排：词项覆盖 + 双通道一致 / 召回名次 + RRF，固定权重，可复现。"""
    for row in rows:
        terms = _query_terms(row["title"] + " " + row["text"])
        cover = len(terms & all_terms) / (len(all_terms) or 1) if all_terms else 0.0
        dual = 1.0 if len(row["channels"]) >= 2 else 0.0
        rrf_norm = min(1.0, row["rrf_score"] / 0.1)
        rank_score = max(0.0, 1.0 - (row["best_rank"] - 1) / max(1, fetch_size - 1))
        row["rerank_score"] = round(100.0 * (
            weights["cover"] * cover + weights["dual"] * dual
            + weights["rrf"] * rrf_norm + weights["rank"] * rank_score), 2)
        row["coverage"] = round(cover, 4)
        row["matched_intents"] = row["hit_intents"]
        row["rrf_score"] = round(row["rrf_score"], 6)
        row["rerank_note"] = note
    rows.sort(key=lambda r: (-r["rerank_score"], -r["rrf_score"], r["title"], r["source_id"]))
    return rows


def _retrieve(payload: dict[str, Any], top_k: int, scene: str,
              kind: str, weights: dict[str, float], rerank_note: str,
              focus: str = "", stage: str = "", hint: str = "",
              gaps: list[str] | None = None) -> dict[str, Any]:
    """岗位信息 / 面试话术共用的检索与重排骨架，避免两套重复实现。"""
    k = max(1, min(MAX_TOP_K, int(top_k or 6)))
    queries = _intent_queries(payload, focus=focus, stage=stage, hint=hint, gaps=gaps)
    if not queries:
        return _empty_context(scene, "无可用检索意图（字段为空）", hint)

    all_terms: set[str] = set()
    fused: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    per_query: list[dict[str, Any]] = []
    backend = "unknown"
    fetch = min(MAX_TOP_K, max(k * 3, 10))

    for q in queries:
        all_terms |= _query_terms(q)
        try:
            res = search(q, top_k=fetch)
        except InvalidQuery:
            per_query.append({"query": q, "hits": 0, "skipped": "too_short"})
            continue
        except Exception as exc:
            per_query.append({"query": q, "hits": 0, "skipped": type(exc).__name__})
            continue
        backend = res.get("backends", {}).get("dense", backend)
        hits = res.get("hits", [])
        per_query.append({"query": q, "hits": len(hits)})
        for rank, h in enumerate(hits, 1):
            hit_kind = vector_store.material_kind_of(
                str(h.get("title") or ""), str(h.get("material_kind") or ""))
            if hit_kind != kind:
                continue
            key = _key(h) + (hit_kind,)
            row = fused.setdefault(key, {
                "source_id": h.get("source_id", ""),
                "title": h.get("title", ""),
                "text": str(h.get("text") or "")[:rag_store.MAX_HIT_CHARS],
                "grade": h.get("grade", "D"),
                "region": h.get("region", "n/a"),
                "attribution": h.get("attribution", ""),
                "kind": hit_kind,
                "forbidden_claims": list(h.get("forbidden_claims") or []),
                "channels": [], "rrf_score": 0.0, "hit_intents": 0,
                "best_rank": rank,
            })
            if h.get("grade") not in DEFAULT_GRADES:
                continue
            for ch in (h.get("channels") or []):
                if ch not in row["channels"]:
                    row["channels"].append(ch)
            for claim in (h.get("forbidden_claims") or []):
                if claim not in row["forbidden_claims"]:
                    row["forbidden_claims"].append(claim)
            row["rrf_score"] += 1.0 / (RRF_K + rank)
            row["hit_intents"] += 1
            row["best_rank"] = min(row["best_rank"], rank)

    rows = [r for r in fused.values() if r["hit_intents"] > 0]
    rows = _rerank_rows(rows, all_terms, weights, fetch, rerank_note)[:k]
    materials = [{
        "title": r["title"],
        "text": r["text"],
        "source": r["source_id"],
        "attribution": r["attribution"],
        "grade": r["grade"],
        "kind": r["kind"],
        "score": r["rerank_score"],
        "coverage": r["coverage"],
        "matched_intents": r["matched_intents"],
        "rank": r["best_rank"],
        "channels": r["channels"],
        "forbidden_claims": r["forbidden_claims"],
    } for r in rows]

    return {
        "ok": True,
        "agent": scene,
        "materials": materials,
        "count": len(materials),
        "trace": {
            "scene": scene,
            "queries": [q["query"] for q in per_query],
            "per_query": per_query,
            "fused_candidates": len(rows),
            "material_kind": kind,
            "rerank": rerank_note,
            "backend": backend,
            "retrieval": "稀疏 TF-IDF + 稠密向量双路召回 · RRF(k=60) 融合 · "
                         "D 级过滤 · %s 子索引精排" % ("岗位信息" if kind == JOB_KIND else "面试话术"),
            "query_hint": hint,
            "data_policy": DATA_POLICY_NOTE,
        },
        "disclaimer": "片段是脱敏后的公开样本，供专家解读方向用；"
                      "不代表国内市场、薪资或录用判断。",
    }


def for_expert(payload: dict[str, Any], top_k: int = 6, focus: str = "",
               scene: str = "job_expert") -> dict[str, Any]:
    """岗位专家 / AI 辅导共用的 RAG 入口：岗位信息索引召回 + 确定性精排。"""
    return _retrieve(payload, top_k, scene, JOB_KIND,
                     JOB_RERANK_WEIGHTS, JOB_RERANK_NOTE, focus=focus)


def for_insight(payload: dict[str, Any], top_k: int = 6) -> dict[str, Any]:
    """洞察对齐复用同一岗位专家 RAG 流程（保留旧调用名）。"""
    return for_expert(payload, top_k, scene="insight")


def for_interview(payload: dict[str, Any], top_k: int = 6, focus: str = "",
                  stage: str = "", gaps: list[str] | None = None) -> dict[str, Any]:
    """面试专家 RAG：岗位信息索引 + 面试话术索引，两路各自召回重排后合并为上下文。

    gaps 为洞察对齐产出的补学缺口短句：只参与岗位信息路检索（围绕待补能力找岗位证据），
    不进话术路，避免技术词稀释话术意图。缺口同时随返回值上送前端展示"围绕哪些缺口在问"。
    """
    gap_list = [str(g).strip()[:80] for g in (gaps or []) if str(g).strip()][:8]
    job_ctx = _retrieve(payload, top_k, "interview_expert/job", JOB_KIND,
                        JOB_RERANK_WEIGHTS, JOB_RERANK_NOTE,
                        focus=focus, stage=stage, gaps=gap_list)
    talk_ctx = _retrieve(payload, top_k, "interview_expert/talk", TALK_KIND,
                         TALK_RERANK_WEIGHTS, TALK_RERANK_NOTE,
                         focus=focus, stage=stage,
                         hint="面试 常见问法 应答结构 追问 压力面")
    materials = job_ctx["materials"] + talk_ctx["materials"]
    queries = job_ctx["trace"].get("queries", []) + talk_ctx["trace"].get("queries", [])
    return {
        "ok": True,
        "agent": "interview_expert",
        "materials": materials,
        "job_materials": job_ctx["materials"],
        "talk_materials": talk_ctx["materials"],
        "count": len(materials),
        "gaps": gap_list,
        "trace": {
            "scene": "interview",
            "gaps_in_job_query": bool(gap_list),
            "queries": queries,
            "rerank": "岗位信息：" + JOB_RERANK_NOTE + "；面试话术：" + TALK_RERANK_NOTE,
            "retrieval": "岗位信息索引 + 面试话术索引，双路检索重排后合并给面试专家",
            "data_policy": DATA_POLICY_NOTE,
            "indexes": {
                "job": job_ctx["trace"],
                "talk": talk_ctx["trace"],
            },
        },
        "disclaimer": "片段是脱敏后的公开样本，供面试专家组织追问与反馈；"
                      "不预测录用结果，不代表公司真实面试标准。",
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
        "deduplication": "(source_id, title, text[:60], material_kind) 归一去重",
        "fallback": "稠密路不可用时仅用稀疏结果，不返回空页或假数据",
        "expert_agents": {
            "job_expert": "岗位信息索引 → 确定性重排 → 岗位专家 / AI 辅导",
            "interview_expert": "岗位信息索引 + 面试话术索引 → 双路重排 → 面试专家",
        },
        "material_kinds": list(vector_store.MATERIAL_KINDS),
        "max_top_k": MAX_TOP_K,
    }

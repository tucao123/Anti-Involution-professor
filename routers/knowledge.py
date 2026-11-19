#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由域：知识库 / RAG / JD（拆分自 server.py，行为保持一致）。"""
from __future__ import annotations

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from .common import (_optional, _metrics, _set_session_cookie, _require_identity,
                     _privacy_identity, _privacy_owns, _need)  # noqa: F401

router = APIRouter()

@router.post("/api/jd/analyze")
def jd_analyze(req: JdAnalyzeRequest):
    consent = {"purpose_ack": req.purpose_ack, "aggregate_opt_in": req.aggregate_opt_in}
    try:
        result = user_jd.analyze(req.jd_text, req.resume_text, consent)
    except user_jd.ConsentRequired:
        sources.log_assessment("user_jd_rejected", "user_jd", {"reason": "purpose_ack_missing"})
        return fail(403, "CONSENT_REQUIRED", "请先确认用途授权后再提交；默认不视为同意")
    except user_jd.InvalidInput as exc:
        code = str(exc)
        message = {
            "JD_LENGTH_INVALID": "JD 文本长度需在 40–8000 字之间",
            "NO_SKILL_DETECTED": "未能从文本中识别到技能词，请补充「岗位职责 / 任职要求」原文",
        }.get(code, "输入不符合要求")
        return fail(400, code, message)
    except sources.SourceBlocked:
        return fail(403, "SOURCE_NOT_APPROVED", "该数据源未获批准，已停止处理")
    sources.log_assessment("user_jd_served", "user_jd",
                           {"grade": "C", "skills": len(result["skills"])})
    return result

@router.post("/api/jd/delete")
def jd_delete(req: DeleteRequest):
    if not user_jd.delete_session(req.id):
        return fail(404, "NOT_FOUND", "会话不存在或已删除（服务重启也会清空）")
    return {"ok": True, "deleted": req.id, "raw_jd_persist": False}

@router.get("/api/jd/status")
def jd_status():
    return user_jd.status()

@router.post("/api/admin/knowledge/delete")
def knowledge_delete_route(req: KnowledgeDeleteRequest):
    return knowledge_ingest.delete(req.ids)

@router.post("/api/admin/knowledge/ingest")
def knowledge_ingest_route(req: KnowledgeIngestRequest):
    try:
        return knowledge_ingest.ingest(
            text=req.text, origin=req.origin, title=req.title,
            purpose_ack=req.purpose_ack, job_only=req.job_only,
        )
    except knowledge_ingest.ConsentRequired:
        return fail(403, "CONSENT_REQUIRED", "请先确认研究用途授权（默认不视为同意）")
    except sources.SourceBlocked:
        return fail(403, "SOURCE_NOT_APPROVED", "该来源未获批准，已拒绝入库")
    except knowledge_ingest.KnowledgeRejected as exc:
        code = str(exc)
        message = {
            "TEXT_LENGTH_INVALID": "文本长度需在 20–12000 字之间",
            "IMAGE_PAYLOAD_REJECTED": "检测到图片 / base64 数据；服务端只接受 OCR 后的文本",
            "PII_RESIDUAL": "脱敏后仍检出个人信息，已拒绝入库",
            "NO_JOB_INFO": "未检出岗位信息；本库只收岗位信息，隐私行整行剔除",
            "NO_TEXT": "文本无有效内容",
            "NO_CHUNK": "文本无法切分出有效片段",
            "ORIGIN_INVALID": "来源形态不合法",
            "TOO_MANY_CHUNKS": "文本切片过多，请精简后分批提交",
        }.get(code, "输入不符合要求")
        return fail(400, code, message)
    except vector_store.VectorRejected as exc:
        return fail(400, "OUTBOUND_GUARD_REJECTED", "出站防护拒绝写入：" + str(exc))
    except vector_store.CloudUserDataBlocked:
        return fail(403, "CLOUD_USER_DATA_BLOCKED", "用户数据默认禁止写入云向量库")

@router.get("/api/admin/knowledge/status")
def knowledge_status():
    return knowledge_ingest.status()

@router.post("/api/rag/delete")
def rag_delete(req: DeleteRequest):
    if req.id == "__all__":
        return {"ok": True, "deleted_docs": rag_store.delete_all(), "scope": "all"}
    if not rag_store.delete_document(req.id):
        return fail(404, "NOT_FOUND", "文档不存在或已过期删除")
    return {"ok": True, "deleted": req.id}

@router.post("/api/rag/ingest")
def rag_ingest(req: RagIngestRequest):
    try:
        return rag_store.ingest(
            text=req.text, source_id=req.source_id, title=req.title,
            purpose=req.purpose, purpose_ack=req.purpose_ack,
            retain_days=req.retain_days, origin=req.origin,
        )
    except sources.SourceBlocked:
        return fail(403, "SOURCE_NOT_APPROVED", "该数据源未获批准，已拒绝向量化入库")
    except rag_store.ConsentRequired:
        return fail(403, "CONSENT_REQUIRED", "请先确认研究用途授权（默认不视为同意）")
    except rag_store.InvalidInput as exc:
        code = str(exc)
        message = {
            "PURPOSE_NOT_RESEARCH": "本索引只接受 research 用途",
            "TEXT_LENGTH_INVALID": "文本长度需在 20–12000 字之间",
            "IMAGE_PAYLOAD_REJECTED": "检测到图片 / base64 数据；服务端只接受 OCR 后的文本",
            "PII_RESIDUAL": "脱敏后仍检出个人信息，已拒绝入库",
            "NO_CHUNK": "文本无法切分出有效片段",
            "DOC_LIMIT": "研究索引文档数已达上限",
            "CHUNK_LIMIT": "研究索引片段数已达上限",
            "ORIGIN_INVALID": "来源形态不合法",
            "ORIGIN_MISMATCH": "来源与文本形态不匹配：公开 API 样本只能由服务端抓取路径入库；"
                               "用户粘贴 / 截图的文本请选择 C 级「用户自带数据」来源",
        }.get(code, "输入不符合要求")
        return fail(400, code, message)

@router.post("/api/rag/query")
def rag_query(req: RagQueryRequest):
    try:
        return rag_store.query(req.q, req.top_k)
    except rag_store.InvalidInput as exc:
        return fail(400, str(exc), "检索词长度需在 2–200 字之间")

@router.get("/api/rag/status")
def rag_status():
    return rag_store.status()

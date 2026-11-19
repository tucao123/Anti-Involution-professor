#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由域：简历解析与本地 VLM（拆分自 server.py，行为保持一致）。"""
from __future__ import annotations

from fastapi import APIRouter
from .common import *  # noqa: F401,F403
from .common import (_optional, _metrics, _set_session_cookie, _require_identity,
                     _privacy_identity, _privacy_owns, _need)  # noqa: F401

router = APIRouter()

@router.post("/api/local-vlm/extract")
def local_vlm_extract(req: LocalVLMExtractRequest):
    """统一解析入口：图片 / PDF / DOCX / 文本都只在本机模型里做语义抽取。
    图片直接视觉识别；PDF/DOCX 先在内存提取纯文本再交模型；后端不做正则字段抽取。
    不落盘、不外发；输出过 PII + 画像词双闸门，命中即整批拒绝。"""
    import base64 as _b64

    file_bytes: bytes = b""
    raw_text = ""
    payload: dict[str, Any] = {}
    try:
        if req.mode == "image":
            if not req.image_base64:
                return fail(400, "IMAGE_MISSING", "未提供图片数据")
            payload = {"image_base64": req.image_base64, "image_mime": req.image_mime}
            task = "resume_extract_image"
        elif req.mode == "file":
            # PDF / DOCX：内存解码 + 文本提取，绝不落盘；文本再交本机模型做语义抽取
            name = req.file_name or ""
            ext = resume_parse._ext(name)
            if ext not in ("pdf", "docx") or not req.file_base64:
                return fail(400, "FILE_TYPE_NOT_ALLOWED", "仅支持 pdf / docx 文件")
            try:
                file_bytes = _b64.b64decode(req.file_base64, validate=True)
            except Exception:
                return fail(400, "FILE_DECODE_FAILED", "文件内容无法解码")
            if not file_bytes or len(file_bytes) > 10 * 1024 * 1024:
                return fail(413, "FILE_SIZE_INVALID", "文件需在 1 B – 10 MiB 之间")
            try:
                resume_parse._check_magic(ext, file_bytes)
                raw_text = resume_parse._extract_text(ext, file_bytes)
            except resume_parse.ResumeRejected as exc:
                return fail(400, str(exc), "文件不合规或无法提取文本")
            if not raw_text.strip():
                return fail(400, "NO_TEXT_EXTRACTED", "未提取到可解析文本")
            payload = {"resume_text": raw_text[:12000]}
            task = "resume_extract_text"
        else:
            if not req.text or len(req.text.strip()) < 12:
                return fail(400, "NO_TEXT_EXTRACTED", "至少提供 12 字的简历 / JD 文本")
            payload = {"resume_text": req.text.strip()}
            task = "resume_extract_text"
        data = local_vlm.extract(task, payload)
        profile = local_vlm.coerce_profile(data)
    except local_vlm.LocalVLMUnavailable as exc:
        code = str(exc)
        privacy.audit("local_vlm_unavailable", detail=code)
        return fail(503, code, local_vlm.unavailable_message(code))
    except job_info.JobInfoRejected:
        privacy.audit("local_vlm_output_rejected", detail="PII_OR_PROFILING")
        return fail(400, "OUTPUT_CONTAINS_PERSONAL_OR_PROFILING_DATA",
                    "模型输出含个人信息或画像语义，已整批拒绝")
    finally:
        raw_text = ""
        file_bytes = b""
        payload = {}
        req.image_base64 = None
        req.file_base64 = None
    return {
        "ok": True,
        "engine": "local-vlm",
        "model": local_vlm.model_name(),
        "data_leaves_device": False,
        "job_profile": profile,
        "data_minimization": job_info.POLICY,
        "retention": {"raw_text": "none", "image": "none", "file": "none",
                      "derived_profile_ttl_s": 0},
        "note": "本机模型完成识别，图片 / 文件 / 原文不落盘、不入库、不上传；"
                "字段已过个人信息与画像语义双校验。请核对结果后再使用。",
    }

@router.get("/api/local-vlm/status")
def local_vlm_status():
    return local_vlm.status()

@router.get("/api/resume/consent")
def resume_consent_notice():
    return resume_parse.consent_notice()

@router.post("/api/resume/delete")
def resume_delete(req: ResumeSessionRequest):
    return resume_parse.delete(req.session_id)

@router.post("/api/resume/parse")
async def resume_parse_endpoint(file: UploadFile = File(...), cloud_enrich: bool = False,
                                cloud_ack: bool = False,
                                third_party_notice_ack: bool = False):
    """B1：只返回粗粒度岗位信息；文件与原文不落盘、不留存。"""
    data = await file.read()
    filename = file.filename or ""
    try:
        # 解析含同步文件解码 / 可选云端 enrich，放线程池避免阻塞事件循环
        return await run_in_threadpool(resume_parse.parse, filename, data, cloud_ack,
                                       third_party_notice_ack, cloud_enrich)
    except resume_parse.ResumeRejected as exc:
        code = str(exc)
        if code.startswith("APPROVAL_GATE_CLOSED"):
            return fail(403, "APPROVAL_GATE_CLOSED", "B1 回执或双闸门未通过，未解析任何内容")
        if code == "SEPARATE_CONSENT_REQUIRED":
            return fail(403, code, "云端解析需逐次单独告知同意（cloud_ack + third_party_notice_ack）")
        if code == "FILE_SIZE_INVALID":
            return fail(413, code, "文件大小需在 1 B – 3 MiB 之间")
        msg = {
            "FILE_TYPE_NOT_ALLOWED": "仅允许 pdf / docx",
            "FILE_MAGIC_MISMATCH": "文件内容与扩展名不符，已拒绝",
            "NOT_A_DOCX": "不是有效的 docx 文件",
            "PDF_BACKEND_UNAVAILABLE": "本地 PDF 解析依赖未安装，未上传任何内容",
            "PDF_PARSE_FAILED": "PDF 无法解析，未留存任何内容",
            "DOCX_PARSE_FAILED": "DOCX 无法解析，未留存任何内容",
            "NO_TEXT_EXTRACTED": "未提取到可解析文本",
            "OUTPUT_CONTAINS_PERSONAL_OR_PROFILING_DATA": "输出中检出个人信息或画像语义，整批拒绝",
        }.get(code, "文件不符合要求")
        return fail(400, code, msg)
    finally:
        del data

@router.post("/api/resume/parse-text")
def resume_parse_text(req: ResumeTextRequest):
    """OCR / 粘贴文本的岗位字段抽取：返回结构化摘要，不接收文件、不写原文与向量库。"""
    try:
        return resume_parse.parse_text(req.text, req.purpose)
    except resume_parse.ResumeRejected as exc:
        code = str(exc)
        if code == "NO_TEXT_EXTRACTED":
            return fail(400, code, "未提取到可解析文本")
        if code == "OUTPUT_CONTAINS_PERSONAL_OR_PROFILING_DATA":
            return fail(400, code, "输出中检出个人信息或画像语义，整批拒绝")
        return fail(400, code, "文本解析失败")

@router.get("/api/resume/status")
def resume_status():
    return resume_parse.status()

@router.post("/api/resume/withdraw")
def resume_withdraw(req: ResumeSessionRequest):
    return resume_parse.withdraw(req.session_id)

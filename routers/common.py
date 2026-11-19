#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路由共享层：请求模型、共享 helper、业务模块导入。

拆分自原 server.py（按域拆 routers/）。所有路由与各域 router 共用这里的符号，
保证行为与原单文件版本完全一致。业务逻辑不在此文件实现，仅为共享定义。
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any, Literal

import approval_gate
import auth
import local_accounts
import embedding
import job_info
import jtbd
import knowledge_ingest
import llm
import local_vlm
import market
import outbound_guard
import privacy
import rag_store
import resume_parse
import secure_config
import sources
import usage_policy
import user_jd
import user_llm
import vector_store
try:
    from fastapi import FastAPI, File, Request, UploadFile
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
    from pydantic import BaseModel, ConfigDict, Field, ValidationError
    from starlette.concurrency import run_in_threadpool
    import uvicorn
except ImportError as exc:
    print("缺少依赖：%s" % exc)
    print("请在项目目录执行： python -m pip install -r requirements.txt")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY = 192 * 1024
RESUME_UPLOAD_MAX_BODY = 4 * 1024 * 1024
LOCAL_VLM_MAX_BODY = 15 * 1024 * 1024
SCHEMAS = llm.SCHEMAS

Text = Annotated[str, Field(min_length=2, max_length=400)]
LongText = Annotated[str, Field(min_length=10, max_length=900)]

class LLMRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: Literal["insight", "coach", "interview_question", "answer_feedback", "report"]
    payload: dict[str, Any] = Field(default_factory=dict, description="只含演示数据或用户文本，不含凭据")

class LocalVLMExtractRequest(BaseModel):
    """本地 VLM 统一解析：图片 / PDF / DOCX（均 base64）或文本，绝不接收凭据 / 远程地址。
    所有字段语义抽取都由本机模型完成，后端不做正则字段抽取、不落盘。"""
    model_config = ConfigDict(extra="forbid")
    mode: Literal["image", "text", "file"]
    # 图片 / PDF / DOCX 三种二进制输入统一上限：10 MiB 文件 base64 后约 13.3 MiB，留足余量。
    image_base64: str | None = Field(default=None, max_length=14 * 1024 * 1024)
    image_mime: Literal["image/jpeg", "image/png", "image/webp", "image/bmp"] = "image/jpeg"
    text: str | None = Field(default=None, max_length=12000)
    file_base64: str | None = Field(default=None, max_length=14 * 1024 * 1024)
    file_name: str | None = Field(default=None, max_length=200)

class UserLLMConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["qwen", "deepseek", "moonshot", "openai", "custom"]
    api_key: str = Field(min_length=8, max_length=512)
    base_url: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=80)
    timeout: int | None = Field(default=30, ge=3, le=60)

class StoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fields: dict[str, str] = Field(description="PII 白名单字段：name / phone / email / id_card")
    meta: dict[str, Any] = Field(default_factory=dict)

class DeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=64)

class ResumeSessionRequest(BaseModel):
    """B1 撤回 / 删除：只带会话 id，不含任何文件内容或个人信息。"""
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64)

class ResumeTextRequest(BaseModel):
    """浏览器 OCR / 用户粘贴文本的字段抽取：只提取岗位相关结构化字段。"""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=12, max_length=20000)
    purpose: Literal["resume", "jd"] = "resume"

class KnowledgeIngestRequest(BaseModel):
    """管理员知识入库：只收文本（截图由浏览器本地 OCR / CSV 解析后传文本），不收图片。

    job_only=True（默认）：岗位知识条目，仅保留岗位信息行。
    job_only=False：模拟面试素材（STAR 案例 / 追问范式），脱敏后整段保留。
    """
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=20, max_length=12000)
    title: str = Field(default="管理员知识条目", max_length=80)
    origin: Literal["admin_text", "admin_ocr", "admin_csv", "interview_material"] = "admin_text"
    purpose_ack: bool = Field(default=False)
    job_only: bool = Field(default=True)

class KnowledgeDeleteRequest(BaseModel):
    """按入库返回的 chunk id 删除；不含原文。"""
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=200)

class JdAnalyzeRequest(BaseModel):
    """C 级用户自带 JD：只收文本，不收文件；用途授权必须显式为 True。"""
    model_config = ConfigDict(extra="forbid")
    jd_text: str = Field(min_length=1, max_length=8000)
    resume_text: str = Field(default="", max_length=4000)
    purpose_ack: bool = Field(default=False)
    aggregate_opt_in: bool = Field(default=False)

class RagIngestRequest(BaseModel):
    """研究检索入库：只收文本。extra=forbid 直接拒绝 image / data URI / 文件字段。"""
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=20, max_length=12000)
    source_id: str = Field(min_length=1, max_length=48)
    title: str = Field(default="研究文本", max_length=80)
    purpose: Literal["research"] = "research"
    purpose_ack: bool = Field(default=False)
    retain_days: int = Field(default=30, ge=1, le=90)
    origin: Literal["pasted_text", "local_ocr", "public_api"] = "pasted_text"

class RagQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=2, max_length=200)
    top_k: int = Field(default=5, ge=1, le=10)

class VectorItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=96)
    text: str = Field(min_length=4, max_length=2000)
    title: str = Field(default="", max_length=80)

class VectorIngestRequest(BaseModel):
    """云向量库只收 D 级公开样本；user scope 在路由层直接拒绝，不进入向量化。"""
    model_config = ConfigDict(extra="forbid")
    items: list[VectorItem] = Field(min_length=1, max_length=50)
    source_id: str = Field(default="jobicy", min_length=1, max_length=48)
    scope: Literal["public", "user"] = "public"
    purpose_ack: bool = Field(default=False)

class VectorSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=2, max_length=200)
    top_k: int = Field(default=5, ge=1, le=10)

class VectorDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ids: list[str] = Field(min_length=1, max_length=100)

class VectorGuardCheckRequest(BaseModel):
    """出站防护 dry-run：只校验、不 embed、不联网、不写入。"""
    model_config = ConfigDict(extra="forbid")
    items: list[VectorItem] = Field(min_length=1, max_length=20)
    source_id: str = Field(default="jobicy", min_length=1, max_length=48)
    scope: Literal["public", "user"] = "public"

class GraphRunRequest(BaseModel):
    """LangGraph 编排入口：C 级文本只走本地规则节点，不外发云端模型。"""
    model_config = ConfigDict(extra="forbid")
    jd_text: str = Field(min_length=1, max_length=8000)
    resume_text: str = Field(default="", max_length=4000)
    purpose_ack: bool = Field(default=False)
    use_cloud_llm: bool = Field(default=False)

class TaskDispatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_name: str = Field(min_length=1, max_length=64)
    kwargs: dict[str, Any] = Field(default_factory=dict)

class CoachChatRequest(BaseModel):
    """A1：会话仅内存，原文零落盘、零外发；purpose_ack 必须显式为 true。"""
    model_config = ConfigDict(extra="forbid")
    session_id: str | None = Field(default=None, max_length=80)
    message: str = Field(min_length=1, max_length=600)
    purpose_ack: bool = Field(default=False)

class CoachDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=80)

class HybridSearchRequest(BaseModel):
    """A4：C 级用户片段默认不返回；稠密路只查 public scope。"""
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=2, max_length=200)
    top_k: int = Field(default=5, ge=1, le=10)
    include_user_local: bool = Field(default=False)
    use_dense: bool = Field(default=True)

class HybridCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=2, max_length=200)
    top_k: int = Field(default=5, ge=1, le=10)

class InsightContextRequest(BaseModel):
    """洞察对齐的 RAG 输入：岗位字段只作检索条件，不入库不落盘。"""
    model_config = ConfigDict(extra="forbid")
    target_role: str = Field(default="", max_length=80)
    major: str = Field(default="", max_length=80)
    skills: list[str] = Field(default_factory=list, max_length=20)
    experience: str = Field(default="", max_length=1200)
    jds: list[str] = Field(default_factory=list, max_length=5)
    top_k: int = Field(default=6, ge=1, le=10)

class ExpertContextRequest(BaseModel):
    """所有专家 agent 共用的 RAG 上下文入口。

    agent=insight/coach：岗位专家，走岗位信息索引；
    agent=interview：面试专家，走岗位信息 + 面试话术双索引各自重排。
    岗位字段与用户问题只作检索条件，不入库不落盘不进画像。
    """
    model_config = ConfigDict(extra="forbid")
    agent: Literal["insight", "coach", "interview"] = "coach"
    target_role: str = Field(default="", max_length=80)
    major: str = Field(default="", max_length=80)
    skills: list[str] = Field(default_factory=list, max_length=20)
    experience: str = Field(default="", max_length=1200)
    jds: list[str] = Field(default_factory=list, max_length=5)
    focus: str = Field(default="", max_length=120)
    stage: str = Field(default="", max_length=60)
    # 洞察对齐产出的补学缺口（本地提炼的短句），面试专家用来对齐发问/反馈/报告方向。
    # 只作检索与提示词约束，不入库不落盘；每条限长，避免把大段用户原文外发。
    gaps: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=8)
    top_k: int = Field(default=6, ge=1, le=10)

class JtbdJdItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="", max_length=60)
    text: str = Field(min_length=1, max_length=8000)

class JtbdCompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jds: list[JtbdJdItem] = Field(min_length=1, max_length=10)
    resume_text: str = Field(default="", max_length=4000)

class JtbdEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jd_text: str = Field(min_length=20, max_length=8000)
    resume_text: str = Field(default="", max_length=4000)

class JtbdDiagnoseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: dict[str, Any]
    resume_text: str = Field(default="", max_length=4000)

class JtbdActionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence: dict[str, Any]
    override: dict[str, str] = Field(default_factory=dict)

class JtbdDrillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement: str = Field(min_length=4, max_length=200)
    project_text: str = Field(min_length=10, max_length=1200)
    depth: int = Field(default=3, ge=1, le=3)

class JtbdReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: dict[str, Any]
    answer: str = Field(min_length=1, max_length=2000)

class AuthStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(min_length=16, max_length=80)

class AuthDemoConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(min_length=16, max_length=80)

class AuthWechatCallbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=8, max_length=128)
    state: str = Field(min_length=16, max_length=80)

class AuthAdminLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=128)

class AuthRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str = Field(min_length=3, max_length=120, description="用户名或邮箱")
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=40)

class AuthPasswordLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=128)

class AuthPasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)

class AuthGithubDemoConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(min_length=16, max_length=80)

def _optional(name: str):
    """Phase 2 组件按名称惰性加载；模块缺失时端点返回 503 而不是让服务起不来。"""
    try:
        return importlib.import_module(name)
    except Exception:
        return None

def _metrics():
    return _optional("metrics")

def fail(status: int, error: str, message: str | None = None) -> JSONResponse:
    """统一错误响应：只回显错误码，不回显上游内容、请求头或凭据。"""
    body: dict[str, Any] = {"ok": False, "error": error}
    if message:
        body["message"] = message
    return JSONResponse(content=body, status_code=status)

def _set_session_cookie(response: JSONResponse, token: str) -> None:
    # 传输属性按环境配置：本地 http 默认 secure=false；正式 https 部署须设 AUTH_COOKIE_SECURE=true
    samesite = (secure_config.cfg.get("AUTH_COOKIE_SAMESITE", "lax") or "lax").strip().lower()
    if samesite not in ("lax", "strict", "none"):
        samesite = "lax"
    response.set_cookie(
        key=auth.COOKIE_NAME, value=token, httponly=True, samesite=samesite,
        max_age=cfg_session_ttl(), path="/",
        secure=secure_config.cfg.flag("AUTH_COOKIE_SECURE", False),
    )

def cfg_session_ttl() -> int:
    return secure_config.cfg.integer("AUTH_SESSION_TTL", 86400)

def _require_identity(request: Request) -> dict[str, Any]:
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    if not ident:
        raise auth.AuthError("AUTH_REQUIRED", "请先登录，再在个人空间配置自己的大模型 API", 401)
    return ident

def _privacy_identity(request: Request) -> dict[str, Any]:
    """PII 端点统一身份闸门：fail-closed，与 AUTH_MODE 无关。

    auth.authorize() 在 optional 模式下匿名会放行，保险库不能依赖它；
    这里直接校验会话，匿名一律 401，避免 PII 明文被匿名读取或写入。
    """
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    if not ident:
        raise auth.AuthError("AUTH_REQUIRED", "请先登录后再访问隐私保险库", 401)
    return ident

def _privacy_owns(rec: dict[str, Any] | None, ident: dict[str, Any]) -> bool:
    """记录归属校验：meta._owner 必须匹配当前 sub；管理员可越过（审计需要）。"""
    if not rec:
        return False
    if ident.get("role") == auth.ROLE_ADMIN:
        return True
    return str((rec.get("meta") or {}).get("_owner") or "") == str(ident.get("sub") or "")

def _need(name: str):
    mod = _optional(name)
    if mod is None:
        return None, fail(503, "MODULE_NOT_READY", "组件 %s 尚未加载" % name)
    return mod, None

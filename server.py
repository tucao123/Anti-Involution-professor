#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · FastAPI BFF（Phase 3）

安全约定：
1. 语言模型层默认 qwen（通义千问）；未配置 / LLM_ENABLED=false / 端点域不可信时自动离线降级。
   端点可信性按目标域判定，key 有效性以真实 HTTP 结果为准（不按字符串形态否决）；
   启用时云端也只收结构化技能字段，C 级原文与含 PII 字段前置拦截。
   凭据只在本进程内存：不写入项目文件、不返回浏览器、不打印日志、不回显错误响应。
2. 前端只与 127.0.0.1:<port>/api/* 通信，永远接触不到凭据；静态文件仅白名单放行。
3. 所有模型输出先经 Pydantic（JSON Schema）校验，校验不通过即降级。
4. C 级用户自带数据（JD / 简历 / 本地 OCR）：原文零落盘、PII 前置脱敏、可撤回删除，
   **默认不出本机**；云端向量库 / 云端模型都有独立闸门。B1 已按所有者 2026-11-16
   决策开放 pdf/docx 内存解析：文件与原文只在内存、解析完即释放，只输出粗粒度岗位信息，
   不提取个人信息、不做用户画像；云端解析需逐次单独告知同意且只发去标识化岗位字段。
5. Qdrant Cloud 只收 D 级去标识化岗位知识；集合名 zhiyin_public_d；结果强制带
   grade / region / forbidden_claims，不得据此得出国内市场结论。
6. 行业洞察双模式：仅 A 级境内授权数据可解锁 cn_market，当前 pending、零数值。
7. 研究检索库只收文本、仅 research；片段 AES-256-GCM 加密、TTL 30 天、可删除、审计哈希链。
8. **自动爬取 / 自动截图执行体已整体移除**（原 `research_capture.py` /
   `capture_executor.py` / `production_capture.py` 与 `/api/capture/*` 全部删除）：
   系统不访问任何第三方页面、不发截图请求。知识的唯一来源是**管理员主动提供**——
   直接粘贴文本（origin=admin_text），或上传截图由浏览器本地 Tesseract.js OCR 成文本
   （origin=admin_ocr）。位图自始不上传服务端，服务端只收文本。两种来源统一经
   PII 脱敏 + 只保留岗位信息行后分块、向量化，写入 Qdrant 云向量库
   （`knowledge_ingest.py` + `/api/admin/knowledge/*`，管理员角色闸门）。
9. **Q18 出境写入防护**（`outbound_guard.py`）：每次请求前锁定 Qdrant Cloud 官方域 /
   集合名 / 分级 D / 已批准来源 / payload 字段白名单 / 逐字段 PII / 体积批量上限；
   凭据与原文不回显，审计只写 digest。只读 `GET /api/vector/guard`，
   不联网不写入的 dry-run `POST /api/vector/guard/check`。管理员知识入库同样过此闸门。
10. **全项目使用限制条款**（2026-11-16 所有者确立，见 usage_policy.py）：
   「本项目仅用于研究与学习，不得用于任何商业行为」。该条款对 B1 / Q18 / Q19 /
   Q21 / B3 同理适用，收紧目的限定并禁止商业化 / 对外分发；但它**不是合规豁免**、
   不替代法务书面结论 / 站点许可 / 商务合同、**不改变任何回执 decision 或闸门状态**。
11. Phase 2 新增组件全部「可选依赖 + 本地降级」：Qdrant / BGE / LangGraph /
    PostgreSQL / Redis / Neo4j / Celery / Next.js 任一缺失，视图仍可运行。
12. JTBD v2.0 决策工作台（jtbd.py + workbench.html）为面向用户的主入口
    （`/workbench.html`，根路径 `/` 因 E2E 契约保留旧单页）：方向 → 核对 → 行动 →
    演练 → 看板。C 级用户文本只在本次请求内存里处理，零落盘、零外发、不进 RAG / 向量库；
    硬条件单独阻断、无百分比总分、「没提供」一律标未知。看板 / 材料库存浏览器 localStorage。
    旧单页 index.html 保留为 /index.html 与 /legacy/index.html，仅供既有 E2E 契约与历史回放，
    不是并行产品；两套页面共用同一后端 API，不重复实现业务逻辑。
13. 登录与分角色鉴权：个人账号密码（local_accounts.py 注册/登录/改密，恒 personal）、
    微信扫码与 GitHub 授权快捷登录（auth.py 真实 OAuth / 演示降级）、管理员独立密码入口。
    页面级强制登录（AUTH_REQUIRE_LOGIN）：未登录访问工作台/首页 302 到 /login.html?next=，登录完才能操作。
    /login.html 为 Tab 式登录/注册/改密/管理员页；会话令牌存 httpOnly cookie；
    写端点角色映射集中在 _ROLE_FOR_WRITE，由中间件统一鉴权（off / optional / required）。
    第三方凭据缺失时明确降级并全程标注「演示 / 非真实登录」，绝不伪装。

启动：python server.py                    （默认端口 8848）
      python server.py --port 9000
      python server.py --env-file <路径>  （显式指定凭据文件，优先级最高）
配置：进程环境变量 > --env-file / ZHIYIN_ENV_FILE > 项目 .env > .zhiyin_key_path 指向的外部文件。
      模板见 .env.example；密钥绝不入库。
依赖：python -m pip install -r requirements.txt          （核心，必需）
      python -m pip install -r requirements-optional.txt（可选增强，缺了自动降级）
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

import approval_gate    # 书面结论回执闸门（只读 / 双闸门 / fail-closed）
import auth             # 认证与分角色鉴权（微信/GitHub OAuth 真实/演示 / 管理员密码 / 会话）
import local_accounts   # 本地账号体系（注册 / 密码登录 / 改密），恒为 personal
import embedding        # BGE / DashScope / hash 统一 embedding 适配层
import job_info         # 仅岗位信息粗粒度抽取（不提个人信息 / 不做用户画像）
import jtbd             # JTBD v2.0 决策工作台（本地规则 / 三态对照 / 行动分流）
import knowledge_ingest # 管理员知识库入库（文本 / 截图 OCR 文本 → 脱敏 → 岗位信息 → Qdrant）
import resume_parse     # B1 文件解析（不留存 / 可撤回删除 / 云端逐次单独同意）
import llm              # qwen 语言模型层 + 结构化输出契约
import local_vlm        # 本地多模态模型层（Ollama / Qwen3-VL，图片不出机）
import market           # 公开数据适配层（仅标准库）
import privacy          # PIPL 合规基础设施（AES-256-GCM / 脱敏 / 删除 / 审计链）
import rag_store        # 本地研究检索库（稀疏 TF-IDF / 加密 / TTL / 可删除）
import outbound_guard   # Q18 出境写入防护（目标 / 集合 / 分级 / 字段 / PII / 体积）
import secure_config    # 统一安全配置（凭据仅在内存）
import sources          # 数据源注册 / fail-closed 合规闸门 / 数据分级
import usage_policy     # 全项目「仅限研究学习 / 禁止商业行为」统一条款
import user_jd          # 用户自带 JD（C 级）：确定性抽取 / PII 脱敏 / 原文零落盘
import user_llm         # 个人空间自带大模型 API（加密存储 / 仅本人可读写 / 不下发密钥）
import vector_store     # Qdrant Cloud 适配（D 级公开样本）+ 本地降级索引

try:
    from fastapi import FastAPI, File, Request, UploadFile
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
    from pydantic import BaseModel, ConfigDict, Field, ValidationError
    import uvicorn
except ImportError as exc:  # pragma: no cover
    print("缺少依赖：%s" % exc)
    print("请在项目目录执行： python -m pip install -r requirements.txt")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent
MAX_BODY = 192 * 1024  # 研究文本最长 12000 字，UTF-8 + JSON 转义后留足余量
RESUME_UPLOAD_MAX_BODY = 4 * 1024 * 1024  # 仅 /api/resume/parse 放行 multipart（文件上限 3 MiB + 开销）
LOCAL_VLM_MAX_BODY = 15 * 1024 * 1024     # 本地 VLM 图片/PDF/DOCX base64（原文件 ≤ 10 MiB，编码后留余量）

# 结构化输出契约统一由 llm.py 提供，避免模型侧与校验侧口径漂移
SCHEMAS = llm.SCHEMAS


def _optional(name: str):
    """Phase 2 组件按名称惰性加载；模块缺失时端点返回 503 而不是让服务起不来。"""
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _metrics():
    return _optional("metrics")


# ---------------------------------------------------------------------------
# 请求模型：一律 extra="forbid"，杜绝 image / 文件 / 任意扩展字段
# ---------------------------------------------------------------------------
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


# --- Phase 3：本地辅导 / 混合检索 / 公开仓库只读元数据 ---
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
    top_k: int = Field(default=6, ge=1, le=10)


# --- JTBD v2.0 决策工作台（C 级文本仅本次请求内存处理，不落盘不外发） ---
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


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(title="职引者 ZHIYIN · BFF", version="2.0",
              docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/api/routes", include_in_schema=False)
def route_manifest():
    """只给回归脚本核对路由是否注册，不返回任何 JSON Schema / 字段定义。"""
    paths: dict[str, dict[str, Any]] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if not path.startswith("/api") and path not in ("/metrics", "/"):
            continue
        entry = paths.setdefault(path, {})
        for method in methods:
            if method in ("HEAD", "OPTIONS"):
                continue
            entry[method.lower()] = {"registered": True}
    return {"ok": True, "paths": paths}


app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)


def fail(status: int, error: str, message: str | None = None) -> JSONResponse:
    """统一错误响应：只回显错误码，不回显上游内容、请求头或凭据。"""
    body: dict[str, Any] = {"ok": False, "error": error}
    if message:
        body["message"] = message
    return JSONResponse(content=body, status_code=status)


# 写端点 → 需要的角色。登录/演示端点公开（None），其余按敏感度分级。
# OAuth 回调是 GET，不在此表；扫码创建 state / 管理员登录等都在 /api/auth/ 下公开。
_WRITE_PERSONAL = [
    "/api/jd/analyze", "/api/jd/delete",
    "/api/rag/ingest", "/api/rag/query", "/api/rag/delete",
    "/api/coach/chat", "/api/coach/delete",
    "/api/graph/run", "/api/tasks/dispatch",
    "/api/hybrid/search", "/api/hybrid/compare",
    "/api/vector/search",
    "/api/resume/parse", "/api/resume/parse-text", "/api/resume/withdraw", "/api/resume/delete",
    "/api/me/llm/save", "/api/me/llm/delete", "/api/me/llm/test",
]
_WRITE_ADMIN = [
    "/api/admin/knowledge/ingest", "/api/admin/knowledge/delete",
    "/api/vector/ingest", "/api/vector/delete", "/api/vector/clear-local",
    "/api/vector/guard/check",
]
_ROLE_FOR_WRITE: dict[tuple[str, str], str | None] = {
    **{("POST", p): auth.ROLE_PERSONAL for p in _WRITE_PERSONAL},
    **{("POST", p): auth.ROLE_ADMIN for p in _WRITE_ADMIN},
}


@app.middleware("http")
async def guard(request: Request, call_next):
    # 日志只记录方法与路径，不记录查询串、请求体、请求头或任何凭据
    sys.stderr.write("  %s %s\n" % (request.method, request.url.path))
    t0 = time.perf_counter()

    # ---- 分角色鉴权：集中映射表，避免把校验散落到每个端点 ----
    # 值：None=公开写端点（登录/演示用）；'personal'=登录用户；'admin'=仅管理员。
    need = _ROLE_FOR_WRITE.get((request.method, request.url.path))
    if need and not request.url.path.startswith("/api/auth/"):
        token = request.cookies.get(auth.COOKIE_NAME)
        try:
            auth.authorize(token, need)
        except auth.AuthError as exc:
            r = fail(exc.status, exc.code, exc.message)
            r.headers["X-Content-Type-Options"] = "nosniff"
            r.headers["Cache-Control"] = "no-store"
            return r

    # ---- 页面级强制登录：未登录访问应用页面 → 302 到登录页并带回跳地址 ----
    # 只针对精确的应用页面路径，API / 登录页 / 静态资源 / 指标一律放行。
    if request.method == "GET" and _page_needs_login(request.url.path):
        if not auth.identity(request.cookies.get(auth.COOKIE_NAME)):
            from urllib.parse import quote
            from fastapi.responses import RedirectResponse
            nxt = quote(request.url.path, safe="")
            return RedirectResponse(url="/login.html?next=" + nxt, status_code=302)

    if request.method == "POST":
        # 文件上传端点单独放宽；其余 POST 仍按研究文本上限收紧
        limit = (RESUME_UPLOAD_MAX_BODY if request.url.path == "/api/resume/parse"
                 else LOCAL_VLM_MAX_BODY if request.url.path == "/api/local-vlm/extract"
                 else MAX_BODY)
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length > limit:
            return fail(413, "BAD_BODY_SIZE")
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    m = _metrics()
    if m and hasattr(m, "observe") and request.url.path.startswith(("/api/", "/metrics")):
        m.observe(request.method, request.url.path, response.status_code,
                  time.perf_counter() - t0)
    return response


@app.exception_handler(RequestValidationError)
async def bad_request(_request: Request, _exc: RequestValidationError):
    return fail(400, "BAD_REQUEST")


@app.exception_handler(auth.AuthError)
async def auth_error(_request: Request, exc: auth.AuthError):
    """认证层错误：只回显稳定错误码 + 提示，绝不回显凭据 / 令牌。"""
    return fail(exc.status, exc.code, exc.message)


@app.exception_handler(local_accounts.AccountError)
async def account_error(_request: Request, exc: local_accounts.AccountError):
    """账号层错误：只回显稳定错误码 + 提示，绝不回显账号 / 密码 / 哈希。"""
    return fail(exc.status, exc.code, exc.message)


# 页面级强制登录：仅保护应用页面，放行登录页 / API / 静态资源 / 指标端点。
# 与 AUTH_MODE 解耦：页面是否需要登录由 AUTH_REQUIRE_LOGIN 单独开关控制，
# 便于本地回归（AUTH_MODE=optional）时也能演示强制登录路由；正式部署两者都开。
# 仅保护当前产品页面（工作台）；旧版单页 / /index.html / /legacy/index.html 保留为
# E2E 契约与历史回放，不纳入强制登录，避免破坏历史页面契约。
_PROTECTED_PAGES = {"/workbench", "/workbench.html"}


def _page_needs_login(path: str) -> bool:
    return (secure_config.cfg.flag("AUTH_REQUIRE_LOGIN", True)
            and path in _PROTECTED_PAGES)


# ---------------------------------------------------------------------------
# 认证与会话：微信扫码（真实 OAuth + 演示降级）/ 管理员密码 / 分角色
# 令牌只存 httpOnly cookie；响应体不返回令牌本身，避免被 JS/XSS 读取。
# ---------------------------------------------------------------------------
def _set_session_cookie(response: JSONResponse, token: str) -> None:
    response.set_cookie(
        key=auth.COOKIE_NAME, value=token, httponly=True, samesite="lax",
        max_age=cfg_session_ttl(), path="/", secure=False,  # 本地 http；正式部署应由反代终止 TLS 后置 secure
    )


def cfg_session_ttl() -> int:
    return secure_config.cfg.integer("AUTH_SESSION_TTL", 86400)


@app.get("/api/auth/status")
def auth_status():
    return auth.status()


@app.get("/api/auth/session")
def auth_session(request: Request):
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    page_required = secure_config.cfg.flag("AUTH_REQUIRE_LOGIN", True)
    if not ident:
        return {"ok": True, "logged_in": False, "role": None,
                "page_login_required": page_required}
    sub = ident.get("sub", "")
    return {
        "ok": True, "logged_in": True,
        "role": ident.get("role"), "name": ident.get("name", ""),
        "demo": bool(ident.get("demo")),
        "page_login_required": page_required,
        "has_local_password": sub.startswith("acct:"),
        "provider": ("local" if sub.startswith("acct:") else
                     "github" if sub.startswith("gh") else
                     "wechat" if sub.startswith("wx:") else
                     "admin" if ident.get("role") == auth.ROLE_ADMIN else "demo"),
    }


@app.post("/api/auth/wechat/authorize")
def auth_wechat_authorize():
    wm = auth.wechat_mode()
    state = auth.new_state("real" if wm["real_oauth_ready"] else "demo")
    if wm["real_oauth_ready"]:
        return {"ok": True, "mode": "real", "authorize_url": auth.authorize_url(state), "state": state}
    if not wm["demo_allowed"]:
        return fail(403, "DEMO_DISABLED", "演示扫码已关闭，且未配置微信开放平台凭据")
    # 演示模式：无真实二维码，返回本地确认所需的 state，前端明确标注「演示」
    return {"ok": True, "mode": "demo", "authorize_url": None, "state": state,
            "demo_notice": "未配置微信开放平台凭据，当前为本地演示扫码，非真实微信登录"}


@app.post("/api/auth/wechat/demo/confirm")
def auth_wechat_demo_confirm(req: AuthDemoConfirmRequest):
    ident = auth.demo_confirm(req.state)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=True)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": True,
                              "notice": "演示身份，不代表真实微信用户"})
    _set_session_cookie(r, token)
    return r


@app.post("/api/auth/wechat/callback")
def auth_wechat_callback(req: AuthWechatCallbackRequest):
    ident = auth.exchange_code(req.code, req.state)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=False)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": False})
    _set_session_cookie(r, token)
    return r


@app.post("/api/auth/admin/login")
def auth_admin_login(req: AuthAdminLoginRequest, request: Request):
    host = request.client.host if request.client else ""
    ident = auth.admin_login(req.password, host)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=ident["demo"])
    body: dict[str, Any] = {"ok": True, "logged_in": True, "role": ident["role"],
                            "name": ident["name"], "demo": ident["demo"]}
    if ident["demo"]:
        body["notice"] = "演示凭据登录，正式部署请配置 ADMIN_PASSWORD_HASH"
    r = JSONResponse(content=body)
    _set_session_cookie(r, token)
    return r


@app.get("/api/auth/providers")
def auth_providers():
    """登录页据此渲染可用的登录方式；只返回形态与是否就绪，不含任何密钥。"""
    a = auth.status()
    return {
        "ok": True,
        "providers": {
            "local_accounts": a["local_accounts"],
            "wechat": {"mode": a["wechat"]["mode"],
                       "ready": a["wechat"]["real_oauth_ready"],
                       "demo_allowed": a["wechat"]["demo_allowed"]},
            "github": {"mode": a["github"]["mode"],
                       "ready": a["github"]["real_oauth_ready"],
                       "demo_allowed": a["github"]["demo_allowed"]},
            "admin": {"mode": "password", "demo": a["admin"]["demo_credential"]},
        },
        "page_login_required": a["page_login_required"],
    }


@app.post("/api/auth/register")
def auth_register(req: AuthRegisterRequest):
    ident = local_accounts.register(req.identifier, req.password, req.name)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=False)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": False,
                              "notice": "注册即登录；账号仅用于鉴权，不产出用户画像"})
    _set_session_cookie(r, token)
    return r


@app.post("/api/auth/password/login")
def auth_password_login(req: AuthPasswordLoginRequest):
    ident = local_accounts.login(req.identifier, req.password)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=False)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": False})
    _set_session_cookie(r, token)
    return r


@app.post("/api/auth/password/change")
def auth_password_change(req: AuthPasswordChangeRequest, request: Request):
    # 该端点在 /api/auth/ 下（公开前缀），须在端点内自行强制登录态
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    if not ident:
        raise auth.AuthError("AUTH_REQUIRED", "请先登录后再修改密码", 401)
    result = local_accounts.change_password(ident["sub"], req.old_password, req.new_password)
    return {"ok": True, "changed_at": result["changed_at"]}


@app.post("/api/auth/github/authorize")
def auth_github_authorize():
    gm = auth.github_mode()
    state = auth.new_state("github-real" if gm["real_oauth_ready"] else "github-demo")
    if gm["real_oauth_ready"]:
        return {"ok": True, "mode": "real", "authorize_url": auth.github_authorize_url(state),
                "state": state}
    if not gm["demo_allowed"]:
        return fail(403, "DEMO_DISABLED", "演示授权已关闭，且未配置 GitHub OAuth 凭据")
    return {"ok": True, "mode": "demo", "authorize_url": None, "state": state,
            "demo_notice": "未配置 GitHub OAuth 凭据，当前为本地演示授权，非真实 GitHub 登录"}


@app.post("/api/auth/github/demo/confirm")
def auth_github_demo_confirm(req: AuthGithubDemoConfirmRequest):
    ident = auth.github_demo_confirm(req.state)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=True)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": True,
                              "notice": "GitHub 演示身份，不代表真实 GitHub 用户"})
    _set_session_cookie(r, token)
    return r


@app.post("/api/auth/github/callback")
def auth_github_callback(req: AuthWechatCallbackRequest):
    # 复用 code+state 结构；真实 GitHub 授权码回调由前端读 query 后 POST 到此
    ident = auth.exchange_github_code(req.code, req.state)
    token = auth.issue(ident["role"], ident["sub"], ident["name"], demo=False)
    r = JSONResponse(content={"ok": True, "logged_in": True, "role": ident["role"],
                              "name": ident["name"], "demo": False})
    _set_session_cookie(r, token)
    return r


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    auth.revoke(request.cookies.get(auth.COOKIE_NAME))
    r = JSONResponse(content={"ok": True, "logged_in": False})
    r.delete_cookie(auth.COOKIE_NAME, path="/")
    return r


@app.get("/api/health")
def health():
    ls = llm.status()
    es = embedding.status()
    return {
        "ok": True,
        "phase": 2,
        "provider": "qwen",
        "model": ls["model"],
        "configured": ls["configured"],     # 服务端兜底是否就绪；用户自带 API 另按会话判定
        "mode": ls["mode"],
        "credential_source": ls["credential_source"],
        "llm_credential_scope": "user_configurable_or_server_fallback",
        "user_llm_configurable": True,
        "embedding_scope": "server_fixed_local_bge",
        "embedding_user_selectable": False,
        "framework": "fastapi",
        "schema_validation": True,
        "source_gate": "fail-closed",
        "market_scope": "global_remote / grade D（不代表中国境内招聘市场）",
        "insight_modes": ["skill_reference (ready)", "cn_market (pending_license)"],
        "user_jd_scope": "user_provided / grade C（原文零落盘、可撤回、默认不出本机）",
        "capture_policy": "automated_capture=removed / admin_text_or_screenshot_ocr=allowed / "
                          "pii_redact+job_only / rag_ingest=allowed(qdrant_cloud)",
        "research_mode": "admin_curated_only / no_third_party_capture",
        "rag_scope": "research-only / text-only / PII-redacted / job-info-only / qdrant-cloud / deletable",
        "components": {
            "llm": "qwen/" + ls["mode"],
            "embedding": es["selected_backend"]["public"],
            "qdrant": "configured" if secure_config.secret("QDRANT_URL") else "not-configured",
            "relational": "adapter+sqlite-fallback",
            "cache": "adapter+memory-fallback",
            "graph": "adapter+local-fallback",
            "orchestration": "langgraph-or-compat",
            "celery": "eager-fallback",
            "frontend": "workbench.html (JTBD) + index.html (legacy) + web/ (Next.js)",
        },
        "cloud_user_data_allowed": secure_config.cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"),
    }


@app.get("/api/config")
def config_view():
    """配置状态视图：只有是否配置 / 来源类别 / 形态，绝不含任何密钥值。"""
    view = secure_config.public_view()
    view["credential_boundary"] = {
        "llm": "用户个人空间自带 API（加密存储，按登录身份隔离）；服务端 qwen 仅可选兜底",
        "embedding": "服务器固定本地 BGE 512 维，用户不可选择 / 覆盖",
        "user_llm_configurable": True,
        "embedding_user_selectable": False,
    }
    view["user_llm_providers"] = list(user_llm.PROVIDERS)
    return view


# ---------------------------------------------------------------------------
# 个人空间 · 自带大模型 API（BYOK）
# 按 session sub 隔离；密钥加密落盘、永不下发浏览器；embedding 不在此配置范围。
# ---------------------------------------------------------------------------
def _require_identity(request: Request) -> dict[str, Any]:
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    if not ident:
        raise auth.AuthError("AUTH_REQUIRED", "请先登录，再在个人空间配置自己的大模型 API", 401)
    return ident


@app.get("/api/me/llm")
def my_llm_status(request: Request):
    ident = _require_identity(request)
    return user_llm.public_view(ident["sub"])


@app.get("/api/me/llm/providers")
def my_llm_providers():
    return user_llm.providers_view()


@app.post("/api/me/llm/save")
def my_llm_save(req: UserLLMConfigRequest, request: Request):
    ident = _require_identity(request)
    try:
        return user_llm.save(
            ident["sub"], provider=req.provider, api_key=req.api_key,
            base_url=req.base_url, model=req.model, timeout=req.timeout)
    except user_llm.UserLLMError as exc:
        return fail(exc.status, exc.code, exc.message)


@app.post("/api/me/llm/delete")
def my_llm_delete(request: Request):
    ident = _require_identity(request)
    user_llm.delete(ident["sub"])
    return {"ok": True, "configured": False}


@app.post("/api/me/llm/test")
def my_llm_test(request: Request):
    """用本人配置跑一次最小结构化生成；只回成功与否，不回显上游响应。"""
    ident = _require_identity(request)
    conn = user_llm.get(ident["sub"])
    if not conn:
        return fail(404, "USER_LLM_NOT_CONFIGURED", "尚未配置个人大模型 API")
    try:
        llm.generate("interview_question",
                     {"role": "测试岗位", "stage": "自我介绍", "question": "请用一句话自我介绍。"},
                     connection=conn)
    except llm.LLMUnavailable as exc:
        return fail(502, str(exc), "个人 API 调用未成功；密钥不会被回显")
    except Exception:
        return fail(502, "USER_LLM_TEST_FAILED", "个人 API 调用未成功；密钥不会被回显")
    return {"ok": True, "tested": True}


@app.get("/api/llm/status")
def llm_status():
    return {"ok": True, **llm.status()}


# ---------------------------------------------------------------------------
# 数据源 / 市场 / 洞察（Phase 1 契约完全保留）
# ---------------------------------------------------------------------------
@app.get("/api/sources")
def source_registry():
    return sources.registry_view()


@app.get("/api/market")
def market_insight(major: str = "计算机科学与技术"):
    major = (major or "").strip()[:40] or "计算机科学与技术"
    return {"ok": True, **market.get_market_profile(major)}


@app.get("/api/insight/modes")
def insight_modes():
    return sources.insight_modes()


@app.get("/api/capture/policy")
def capture_policy():
    """只读：截图输入策略（用户/管理员手动截图 + 浏览器本地 OCR）。不执行任何采集。"""
    return sources.capture_policy()


@app.get("/api/research/policy")
def research_policy():
    """只读：自动采集已移除，知识来源只剩管理员文本 / 截图 OCR。"""
    return {
        "ok": True,
        "verdict": "admin_curated_only",
        "automated_capture_executor": "removed",
        "production_capture_unlocked": False,
        "third_party_requests": 0,
        "admin_source": knowledge_ingest.status(),
        "principle": "系统不访问任何第三方页面；知识由管理员主动提供文本或截图（浏览器本地 OCR），"
                     "经脱敏与仅岗位信息过滤后入库。",
        "note": "原受控研究采集执行体已整体删除；本端点只声明当前策略，不执行采集。",
    }


# ---------------------------------------------------------------------------
# 本地研究检索库（C 级 / 稀疏 TF-IDF / 加密片段）
# ---------------------------------------------------------------------------
@app.get("/api/rag/status")
def rag_status():
    return rag_store.status()


@app.post("/api/rag/ingest")
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


@app.post("/api/rag/query")
def rag_query(req: RagQueryRequest):
    try:
        return rag_store.query(req.q, req.top_k)
    except rag_store.InvalidInput as exc:
        return fail(400, str(exc), "检索词长度需在 2–200 字之间")


@app.post("/api/rag/delete")
def rag_delete(req: DeleteRequest):
    if req.id == "__all__":
        return {"ok": True, "deleted_docs": rag_store.delete_all(), "scope": "all"}
    if not rag_store.delete_document(req.id):
        return fail(404, "NOT_FOUND", "文档不存在或已过期删除")
    return {"ok": True, "deleted": req.id}


# ---------------------------------------------------------------------------
# 管理员知识库入库：文本 / 截图 OCR 文本 → 脱敏 → 岗位信息 → 分块 → Qdrant
# （取代原自动爬取链路；服务端只收文本，位图自始不上传）
# ---------------------------------------------------------------------------
@app.get("/api/admin/knowledge/status")
def knowledge_status():
    return knowledge_ingest.status()


@app.post("/api/admin/knowledge/ingest")
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


@app.post("/api/admin/knowledge/delete")
def knowledge_delete_route(req: KnowledgeDeleteRequest):
    return knowledge_ingest.delete(req.ids)


# ---------------------------------------------------------------------------
# C 级用户自带 JD
# ---------------------------------------------------------------------------
@app.get("/api/jd/status")
def jd_status():
    return user_jd.status()


@app.post("/api/jd/analyze")
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


@app.post("/api/jd/delete")
def jd_delete(req: DeleteRequest):
    if not user_jd.delete_session(req.id):
        return fail(404, "NOT_FOUND", "会话不存在或已删除（服务重启也会清空）")
    return {"ok": True, "deleted": req.id, "raw_jd_persist": False}


# ---------------------------------------------------------------------------
# JTBD v2.0 决策工作台：方向 → 核对 → 行动 → 演练 → 看板
# C 级文本只在本次请求内存里处理；不调用 user_jd.analyze 的会话存储、不落盘、不外发。
# ---------------------------------------------------------------------------
@app.get("/api/jtbd/status")
def jtbd_status():
    return jtbd.status()


@app.post("/api/jtbd/compare")
def jtbd_compare(req: JtbdCompareRequest):
    try:
        result = jtbd.compare([i.model_dump() for i in req.jds], req.resume_text)
    except jtbd.InvalidInput as exc:
        return fail(400, "INVALID_INPUT", str(exc))
    sources.log_assessment("jtbd_compare", "user_jd",
                           {"grade": "C", "samples": result["sample_size"]})
    return result


@app.post("/api/jtbd/evidence")
def jtbd_evidence(req: JtbdEvidenceRequest):
    try:
        result = jtbd.evidence(req.jd_text, req.resume_text)
    except jtbd.InvalidInput as exc:
        return fail(400, "INVALID_INPUT", str(exc))
    sources.log_assessment("jtbd_evidence", "user_jd",
                           {"grade": "C", "requirements": result["total"]})
    return result


@app.post("/api/jtbd/diagnose")
def jtbd_diagnose(req: JtbdDiagnoseRequest):
    if not isinstance(req.evidence, dict) or "requirements" not in req.evidence:
        return fail(400, "INVALID_EVIDENCE", "请先完成一次要求—证据对照")
    return jtbd.diagnose(req.evidence, req.resume_text)


@app.post("/api/jtbd/actions")
def jtbd_actions(req: JtbdActionsRequest):
    if not isinstance(req.evidence, dict) or "requirements" not in req.evidence:
        return fail(400, "INVALID_EVIDENCE", "请先完成一次要求—证据对照")
    return jtbd.actions(req.evidence, req.override)


@app.post("/api/jtbd/drill")
def jtbd_drill(req: JtbdDrillRequest):
    try:
        return jtbd.drill(req.requirement, req.project_text, req.depth)
    except jtbd.InvalidInput as exc:
        return fail(400, "INVALID_INPUT", str(exc))


@app.post("/api/jtbd/review")
def jtbd_review(req: JtbdReviewRequest):
    return jtbd.review_answer(req.question, req.answer)


# ---------------------------------------------------------------------------
# 隐私基础设施
# ---------------------------------------------------------------------------
@app.get("/api/privacy/status")
def privacy_status():
    return privacy.status()


@app.post("/api/privacy/store")
def privacy_store(req: StoreRequest):
    try:
        result = privacy.store_record(req.fields, req.meta)
    except ValueError:
        return fail(400, "NO_PII_FIELD", "未包含可加密的 PII 字段")
    return {"ok": True, **result}


@app.get("/api/privacy/record/{rid}")
def privacy_read(rid: str, reveal: bool = False):
    rec = privacy.read_record(rid[:64], reveal)
    if not rec:
        return fail(404, "NOT_FOUND")
    return {"ok": True, "record": rec}


@app.post("/api/privacy/delete")
def privacy_delete(req: DeleteRequest):
    if not privacy.delete_record(req.id):
        return fail(404, "NOT_FOUND")
    return {"ok": True, "deleted": req.id}


@app.get("/api/privacy/audit")
def privacy_audit():
    ok, count, first_bad = privacy.verify_chain()
    return {"ok": True, "entries": count, "chain_ok": ok, "first_bad_seq": first_bad}


# ---------------------------------------------------------------------------
# qwen 语言模型：PII / C 级数据前置拦截 → 结构化生成 → JSON Schema 校验
# ---------------------------------------------------------------------------
@app.post("/api/llm")
def llm_route(req: LLMRequest, request: Request):
    found = privacy.scan_pii(req.payload)
    if found:
        privacy.audit("llm_pii_blocked", detail="拦截字段：" + ",".join(found))
        return fail(403, "PII_BLOCKED", "检测到个人信息，按合规策略不外发至云端模型；本地模型路由尚未配置")
    # 登录用户优先用个人空间自带 API；否则回落到服务端 qwen（可选）。
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    conn = user_llm.get(ident["sub"]) if ident else None
    if conn:
        try:
            user_llm.validate_endpoint(conn.get("base_url", ""))
        except user_llm.UserLLMError:
            conn = None  # 端点被新规则否定，安全回落到服务端链路
    try:
        raw = llm.generate(req.task, req.payload, connection=conn)
    except llm.LLMUnavailable as exc:
        code = str(exc)
        if code in ("PII_BLOCKED", "USER_DATA_BLOCKED"):
            privacy.audit("llm_user_data_blocked", detail=code)
            return fail(403, code, "C 级用户自带数据按默认策略不出本机")
        if conn and code == "USER_LLM_CONFIG_INVALID":
            return fail(400, code, "个人 API 配置不合法，请到个人空间检查")
        if not conn and not llm.qwen_gate()["can_call_chat"]:
            return fail(503, "LLM_NOT_ENABLED",
                        "未配置个人 API 且服务端 qwen 未启用；已自动降级到离线规则引擎")
        return fail(502, code, "模型调用未成功，已切换到离线演示模式")
    try:
        data = llm.validate_output(req.task, raw)
    except ValidationError:
        return fail(502, "SCHEMA_VALIDATION_FAILED", "模型输出未通过结构化校验，已切换到离线演示模式")
    return {"ok": True,
            "provider": (conn.get("provider") if conn else "qwen"),
            "credential_scope": "user" if conn else "server",
            "model": (conn.get("model") if conn else llm.status()["model"]),
            "validated": True, "data": data}


# ---------------------------------------------------------------------------
# Qdrant / BGE 向量链路（只放 D 级公开样本）
# ---------------------------------------------------------------------------
@app.get("/api/embedding/status")
def embedding_status():
    return embedding.status()


@app.get("/api/vector/status")
def vector_status():
    return vector_store.status()


@app.get("/api/vector/probe")
def vector_probe(force: bool = False):
    """只读连通性探测：不写入、不传输任何业务数据。"""
    return vector_store.probe(force=force)


@app.post("/api/vector/ingest")
def vector_ingest(req: VectorIngestRequest):
    if req.scope == "user":
        return fail(403, "CLOUD_USER_DATA_BLOCKED",
                    "C 级用户自带数据默认禁止写入云向量库；请使用本地 /api/rag/ingest")
    if not req.purpose_ack:
        return fail(403, "CONSENT_REQUIRED", "请先确认「仅 D 级公开样本入云」的用途授权")
    try:
        result = vector_store.ingest(
            [it.model_dump() for it in req.items], scope="public", source_id=req.source_id)
    except vector_store.VectorRejected as exc:
        return fail(400, str(exc), "来源未获批准 / 分级不符 / 检出个人信息，已整批拒绝")
    except vector_store.CloudUserDataBlocked:
        return fail(403, "CLOUD_USER_DATA_BLOCKED")
    except Exception:
        return fail(502, "VECTOR_INGEST_FAILED", "向量化入库失败，未写入任何数据")
    sources.log_assessment("vector_public_ingested", req.source_id,
                           {"count": result["ingested"], "backend": result["backend"]})
    return result


@app.post("/api/vector/search")
def vector_search(req: VectorSearchRequest):
    try:
        return vector_store.search(req.q, req.top_k, scope="public")
    except Exception:
        return fail(502, "VECTOR_SEARCH_FAILED", "检索失败，可改用 /api/rag/query 本地索引")


@app.post("/api/vector/delete")
def vector_delete(req: VectorDeleteRequest):
    return vector_store.delete(req.ids)


@app.post("/api/vector/clear-local")
def vector_clear_local():
    return {"ok": True, "deleted": vector_store.clear_local(), "scope": "local-memory-only"}


@app.get("/api/vector/guard")
def vector_outbound_guard_status():
    """只读：Q18 出境写入防护状态。不返回 QDRANT_URL / API key / 任何业务文本。"""
    return vector_store.status()["outbound_guard"]


@app.post("/api/vector/guard/check")
def vector_outbound_guard_check(req: VectorGuardCheckRequest):
    """dry-run：这份 payload 能不能出境？**绝不产生境外写入，也不联网**。

    用于在不新增数据出境的前提下验证集合 / 分级 / 来源 / 字段 / PII / 体积的拦截效果。
    """
    try:
        clearance = vector_store.guard_check(
            [it.model_dump() for it in req.items],
            source_id=req.source_id, scope=req.scope)
    except (vector_store.VectorRejected, vector_store.CloudUserDataBlocked) as exc:
        return {"ok": True, "allowed": False, "reason": str(exc),
                "network_called": False, "data_written": False}
    except outbound_guard.OutboundBlocked as exc:
        return {"ok": True, "allowed": False, "reason": str(exc),
                "network_called": False, "data_written": False}
    return {"ok": True, "allowed": True, "clearance": clearance,
            "network_called": False, "data_written": False,
            "note": "仅完成出站校验，未向量化、未发起网络请求、未写入 Qdrant 或本地索引。"}


# ---------------------------------------------------------------------------
# Phase 2 可选组件：模块未就绪时返回 503，不影响核心服务
# ---------------------------------------------------------------------------
def _need(name: str):
    mod = _optional(name)
    if mod is None:
        return None, fail(503, "MODULE_NOT_READY", "组件 %s 尚未加载" % name)
    return mod, None


@app.get("/api/graph/status")
def graph_status():
    mod, err = _need("orchestration")
    return err or {"ok": True, **mod.status()}


@app.post("/api/graph/run")
def graph_run(req: GraphRunRequest):
    if not req.purpose_ack:
        return fail(403, "CONSENT_REQUIRED", "请先确认用途授权；默认不视为同意")
    mod, err = _need("orchestration")
    if err:
        return err
    try:
        return mod.run(jd_text=req.jd_text, resume_text=req.resume_text,
                       purpose_ack=req.purpose_ack, use_cloud_llm=req.use_cloud_llm)
    except Exception as exc:
        return fail(400, type(exc).__name__, "编排执行失败，已保留输入不落盘")


@app.get("/api/stores/status")
def stores_status():
    mod, err = _need("stores")
    return err or {"ok": True, **mod.status()}


@app.get("/api/tasks/status")
def tasks_status():
    mod, err = _need("celery_app")
    return err or {"ok": True, **mod.status()}


@app.post("/api/tasks/dispatch")
def tasks_dispatch(req: TaskDispatchRequest):
    mod, err = _need("celery_app")
    if err:
        return err
    try:
        return mod.dispatch(req.task_name, req.kwargs)
    except Exception as exc:
        return fail(400, type(exc).__name__, "任务派发失败")


# ---------------------------------------------------------------------------
# Phase 3：A1 本地辅导 / A2 语音策略 / A4 混合检索 / A3 公开仓库只读
# ---------------------------------------------------------------------------
@app.get("/api/coach/status")
def coach_status():
    mod, err = _need("coach")
    return err or {"ok": True, **mod.status()}


@app.post("/api/coach/chat")
def coach_chat(req: CoachChatRequest):
    if not req.purpose_ack:
        return fail(403, "CONSENT_REQUIRED", "请先确认用途授权；默认不视为同意")
    mod, err = _need("coach")
    if err:
        return err
    try:
        return mod.chat(req.session_id, req.message, req.purpose_ack)
    except mod.SessionNotFound:
        return fail(404, "SESSION_EXPIRED_OR_UNKNOWN", "会话已过期或已删除，请重新开始")
    except mod.ConsentRequired:
        return fail(403, "CONSENT_REQUIRED")
    except ValueError as exc:
        return fail(400, type(exc).__name__, str(exc)[:80])
    except Exception as exc:
        return fail(400, type(exc).__name__, "辅导请求未处理，输入未保存")


@app.post("/api/coach/delete")
def coach_delete(req: CoachDeleteRequest):
    mod, err = _need("coach")
    if err:
        return err
    removed = mod.delete(req.session_id)
    privacy.audit("coach_session_deleted", "", "removed=%s" % removed)
    return {"ok": True, "removed": removed, "residual": "none (in-memory only)"}


@app.get("/api/voice/policy")
def voice_policy():
    """A2：Web Speech 的真实边界。服务端不接收音频，但不虚报「音频不出设备」。"""
    return {
        "ok": True,
        "engine": "Web Speech API (browser-side)",
        "asr": "SpeechRecognition / webkitSpeechRecognition",
        "tts": "speechSynthesis",
        "server_audio_upload": False,
        "server_upload_endpoint": None,
        "audio_may_leave_device": True,
        "browser_asr_note": (
            "SpeechRecognition 在 Chrome 等浏览器由浏览器厂商的云端服务完成识别，"
            "麦克风音频可能离开设备；本项目只承诺『本地 BFF 不接收、不保存任何音频』，"
            "不承诺『音频完全不出设备』。需要完全离线 ASR 时应改用 WASM / 本地模型（尚未实现）。"
        ),
        "microphone_permission": "不自动启动；用户每次显式点击并逐次授权，拒绝后降级为文字输入",
        "fallback": "不支持或拒绝权限时禁用语音按钮并保留完整文字流程",
        "note": "识别结果只写入当前页面的输入框，不落盘、不回传服务端（除非用户主动发送文字）。",
    }


@app.get("/api/hybrid/status")
def hybrid_status():
    mod, err = _need("hybrid")
    return err or {"ok": True, **mod.status()}


@app.post("/api/hybrid/search")
def hybrid_search(req: HybridSearchRequest):
    mod, err = _need("hybrid")
    if err:
        return err
    try:
        return mod.search(req.q, req.top_k, req.include_user_local, req.use_dense)
    except mod.InvalidQuery as exc:
        return fail(400, str(exc), "检索词过短，已拒绝")
    except Exception as exc:
        return fail(400, type(exc).__name__, "混合检索失败，已保留输入不落盘")


@app.post("/api/hybrid/compare")
def hybrid_compare(req: HybridCompareRequest):
    mod, err = _need("hybrid")
    if err:
        return err
    try:
        return mod.compare(req.q, req.top_k)
    except mod.InvalidQuery as exc:
        return fail(400, str(exc), "检索词过短，已拒绝")


@app.post("/api/insight/context")
def insight_context(req: InsightContextRequest, request: Request):
    """洞察 RAG：岗位字段 → 检索意图 → 双路召回 → 精排 → D 级脱敏证据。
    仅本次请求内存处理；字段是查询条件，不写库、不落盘、不进用户画像。"""
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    if not ident:
        return fail(401, "LOGIN_REQUIRED", "请先登录后再生成洞察")
    mod, err = _need("hybrid")
    if err:
        return err
    payload = {
        "target_role": req.target_role, "major": req.major, "skills": req.skills,
        "experience": req.experience, "jds": req.jds,
    }
    try:
        result = mod.for_insight(payload, top_k=req.top_k)
    except Exception:
        # 检索失败不阻断洞察：返回空证据，前端仍可用静态统计生成
        result = {"ok": True, "materials": [], "count": 0,
                  "trace": {"retrieval": "检索暂不可用", "rerank": "未执行",
                            "data_policy": mod.DATA_POLICY_NOTE},
                  "disclaimer": "检索暂不可用，改用样本统计；用户字段未入库未落盘。"}
    privacy.audit("insight_context", detail="意图 %d 条 · 证据 %d 条" % (
        len(result.get("trace", {}).get("queries", [])), result.get("count", 0)))
    return result


@app.post("/api/expert/context")
def expert_context(req: ExpertContextRequest, request: Request):
    """所有专家 agent 的统一 RAG 证据入口（登录用户）。

    - agent=insight / coach：岗位专家，检索岗位信息索引；
    - agent=interview：面试专家，岗位信息索引 + 面试话术索引双路各自召回重排。

    岗位字段与用户问题只作检索条件，不写库、不落盘、不进用户画像。
    检索失败只返回空证据，不阻断专家生成（与洞察端点同策略）。
    """
    ident = auth.identity(request.cookies.get(auth.COOKIE_NAME))
    if not ident:
        return fail(401, "LOGIN_REQUIRED", "请先登录后再使用专家解读")
    mod, err = _need("hybrid")
    if err:
        return err
    payload = {
        "target_role": req.target_role, "major": req.major, "skills": req.skills,
        "experience": req.experience, "jds": req.jds,
    }
    try:
        if req.agent == "interview":
            result = mod.for_interview(payload, top_k=req.top_k,
                                       focus=req.focus, stage=req.stage)
            scene = "interview"
        else:
            result = mod.for_expert(payload, top_k=req.top_k, focus=req.focus,
                                    scene=req.agent)
            scene = req.agent
    except Exception:
        result = {"ok": True, "materials": [], "count": 0,
                  "trace": {"scene": req.agent, "retrieval": "检索暂不可用", "rerank": "未执行",
                            "data_policy": mod.DATA_POLICY_NOTE},
                  "disclaimer": "检索暂不可用；用户字段未入库未落盘。"}
    privacy.audit("expert_context", detail="场景 %s · 证据 %d 条" % (
        scene, result.get("count", 0)))
    return result


@app.get("/api/github/status")
def github_status():
    mod, err = _need("github_meta")
    return err or {"ok": True, **mod.status()}


@app.get("/api/github/repo")
def github_repo(owner: str = "", repo: str = ""):
    mod, err = _need("github_meta")
    if err:
        return err
    try:
        return mod.fetch(owner, repo)
    except mod.GithubBlocked as exc:
        return fail(403, "SOURCE_BLOCKED_OR_INVALID", str(exc)[:120])
    except mod.GithubUnavailable as exc:
        return fail(502, str(exc), "公开仓库元数据暂不可用；不影响其他功能")


# ---------------------------------------------------------------------------
# 全项目使用限制条款（2026-11-16 所有者确立）
# ---------------------------------------------------------------------------
@app.get("/api/project/usage")
def project_usage_policy():
    """只读：返回「仅限研究学习 / 禁止商业行为」成文条款与逐项适用说明。"""
    return {
        "ok": True,
        "policy": usage_policy.as_dict(),
        "applies_to_items": {k: usage_policy.applies_to(k) for k in approval_gate.ITEMS},
        "gate_state_changed": False,
        "note": "该条款对 5 项待决事项同理适用（收紧用途），但不改变任何 decision 或闸门状态；"
                "不替代法务书面结论、站点许可与商务合同。",
        "disclaimer": usage_policy.POLICY["disclaimer"],
    }


# ---------------------------------------------------------------------------
# 书面结论回执 / B1 文件解析 / 管理员采集设置（2026-11-16）
# ---------------------------------------------------------------------------
@app.get("/api/approvals/status")
def approvals_status():
    """一次查看 5 项回执与双闸门状态；只读，不提供自我授权入口。"""
    return approval_gate.state()


@app.get("/api/resume/status")
def resume_status():
    return resume_parse.status()


@app.get("/api/resume/consent")
def resume_consent_notice():
    return resume_parse.consent_notice()


@app.post("/api/resume/parse")
async def resume_parse_endpoint(file: UploadFile = File(...), cloud_enrich: bool = False,
                                cloud_ack: bool = False,
                                third_party_notice_ack: bool = False):
    """B1：只返回粗粒度岗位信息；文件与原文不落盘、不留存。"""
    data = await file.read()
    try:
        return resume_parse.parse(file.filename or "", data, cloud_ack,
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


@app.post("/api/resume/parse-text")
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


@app.post("/api/resume/withdraw")
def resume_withdraw(req: ResumeSessionRequest):
    return resume_parse.withdraw(req.session_id)


@app.post("/api/resume/delete")
def resume_delete(req: ResumeSessionRequest):
    return resume_parse.delete(req.session_id)


# ---------------------------------------------------------------------------
# 本地多模态模型（Ollama / Qwen3-VL）：图片与文字都只在本机推理，绝非外发。
# 输出仍复用 job_info 的 PII / 画像词双闸门 + 字段白名单，fail-closed。
# ---------------------------------------------------------------------------
def _coerce_vlm_profile(data: dict[str, Any]) -> dict[str, Any]:
    """把本地 VLM 的语义抽取结果收敛成与 to_coarse_job_profile 一致的岗位字段，
    并做输出 fail-closed：命中 PII / 画像词 / 越界字段整批拒绝。"""
    import hashlib as _hl

    allowed_mime = None
    # 字段白名单：只保留岗位颗粒度字段，杜绝模型夹带身份 / 评分字段
    target = str(data.get("target_role") or "")[:40]
    salary = str(data.get("salary_expectation") or "")[:30]
    major = str(data.get("major") or "")[:40]
    education = str(data.get("education") or "")[:40]
    certs = [str(x)[:40] for x in (data.get("certificates") or []) if isinstance(x, str)][:20]
    honors = [str(x)[:40] for x in (data.get("honors") or []) if isinstance(x, str)][:20]
    skills_raw = [str(x)[:30] for x in (data.get("skills") or []) if isinstance(x, str)][:20]
    exp_raw = [str(x)[:120] for x in (data.get("experience_context") or []) if isinstance(x, str)][:12]
    level = str(data.get("level_hint") or "unspecified")
    if level not in ("junior", "senior", "unspecified"):
        level = "unspecified"

    # 技能方向 = 粗粒度类别 + 模型识别出的具体技术栈原词。
    # 用户要的是「各个技术栈」都保留，不能只留合并后的大类（PyTorch/TensorFlow/CNN
    # 不该被压成一条「机器学习」）。先做词典归类，再把未被类别字面覆盖的具体栈词补进去。
    known: list[dict[str, str]] = []
    seen_cat: set[str] = set()
    covered_words: set[str] = set()   # 已被某个类别字面命中的原词，避免重复
    for w in skills_raw:
        low = w.lower()
        for s in user_jd.SKILLS:
            if s["name"] in seen_cat:
                continue
            if low == s["name"].lower() or any(k in low for k in s["words"]):
                known.append({"name": s["name"], "category": s["cat"], "level": "mentioned"})
                seen_cat.add(s["name"])
                covered_words.add(w)
                break
    # 具体技术栈原词：模型输出的、且不是某个类别名本身的，全部保留为 stack 项。
    # 仅剔除已被类别语义覆盖的泛化词（机器学习/深度学习等），保留真正的技术栈名。
    cat_names_l = {s["name"].lower() for s in user_jd.SKILLS}
    generic_words = {"机器学习", "深度学习", "自然语言处理", "数据分析", "数据处理",
                     "编程", "开发", "工程技术", "算法"}
    seen_stack: set[str] = {s["name"].lower() for s in known}
    stacks: list[dict[str, str]] = []
    for w in skills_raw:
        key = w.lower().strip()
        if (not w or w in covered_words or key in seen_stack or key in cat_names_l
                or w.strip() in generic_words):
            continue
        stacks.append({"name": w, "category": "stack", "level": "mentioned"})
        seen_stack.add(key)
    skill_areas = known + stacks
    names = [s["name"] for s in skill_areas[:12]] or skills_raw[:8]
    summary = ("该简历大致涉及 %s 等技能方向（本地模型抽取的粗粒度岗位信息，"
               "不含个人信息与能力评估）。" % ("、".join(names) if names else "通用方向"))

    context_parts: list[str] = []
    if target:
        context_parts.append("求职意向：" + target)
    if salary:
        context_parts.append("薪资意向（用户自填，仅作约束不预测）：" + salary)
    if major:
        context_parts.append("专业：" + major)
    if education:
        context_parts.append("学历：" + education)
    if exp_raw:
        context_parts.append("项目/实习上下文：" + "；".join(exp_raw[:5]))
    if certs:
        context_parts.append("证书：" + "、".join(certs))
    if honors:
        context_parts.append("荣誉/奖项：" + "、".join(honors))
    if skill_areas:
        context_parts.append("技能方向：" + "、".join(names))
    if level != "unspecified":
        context_parts.append("级别：" + level)
    expert_context = "；".join(context_parts)[:1400]

    profile = {
        "job_title_hint": target,
        "target_role": target,
        "salary_expectation": salary,
        "major": major,
        "education": education,
        "certificates": certs,
        "honors": honors,
        "experience_context": exp_raw,
        "skill_areas": skill_areas[:20],
        "level_hint": level,
        "coarse_summary": summary,
        "expert_context": expert_context,
        "source_lines_kept": 0,
    }
    # fail-closed：任一字段含 PII 或画像语义，整批拒绝
    if privacy.scan_pii(profile) or job_info._PROFILING_WORDS.search(str(profile)):
        raise job_info.JobInfoRejected("OUTPUT_CONTAINS_PERSONAL_OR_PROFILING_DATA")
    profile["policy"] = {
        "pii_extraction": False, "pii_to_store": False,
        "user_profiling_in_this_path": False, "user_profiling_project_wide": True,
        "profiling_path": job_info.POLICY["profiling_path"],
        "raw_text_returned": False, "output_scope": job_info.POLICY["output_scope"],
        "expert_context_scope": "仅本次请求内作为专家解读上下文；不写向量库、不落盘",
        "user_data_vectorized": False,
    }
    return profile


def _local_vlm_unavailable_message(code: str) -> str:
    return {
        "UNREACHABLE": "本机 Ollama 服务未启动或连接中断，请确认服务在运行后重试",
        "TIMEOUT": "本机模型处理超时（GPU 繁忙或内容过长），请稍后重试或改用文本粘贴",
        "ENDPOINT_NOT_LOOPBACK": "本地模型端点被配置为非本机地址，已拒绝以防数据外发",
        "IMAGE_MISSING": "未提供图片数据",
        "IMAGE_MIME_UNSUPPORTED": "图片格式不支持",
        "NOT_JSON": "模型未返回合法结构化结果",
        "SCHEMA_VALIDATION_FAILED": "模型输出未通过结构化校验",
        "UNKNOWN_TASK": "不支持的本地模型任务",
        "LOCAL_VLM_ERROR": "本机模型调用异常，请重试；若持续失败请把下方错误码发给管理员",
    }.get(code, "本地模型调用失败")


@app.get("/api/local-vlm/status")
def local_vlm_status():
    return local_vlm.status()


@app.post("/api/local-vlm/extract")
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
        profile = _coerce_vlm_profile(data)
    except local_vlm.LocalVLMUnavailable as exc:
        code = str(exc)
        privacy.audit("local_vlm_unavailable", detail=code)
        return fail(503, code, _local_vlm_unavailable_message(code))
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


@app.get("/api/phase3/status")
def phase3_status():
    """一次查看 A1/A2/A3/A4 的实际状态，避免把「已接适配器」误读为「已启用」。"""
    coach, hybrid_m, gh = _optional("coach"), _optional("hybrid"), _optional("github_meta")
    return {
        "ok": True,
        "A1_coach": {"ready": coach is not None, **(coach.status() if coach else {})},
        "A2_voice": {"ready": True, "engine": "browser-side Web Speech",
                     "server_audio_upload": False, "audio_may_leave_device": True,
                     "upload_endpoint": None},
        "A3_github": {"ready": gh is not None, **(gh.status() if gh else {})},
        "A4_hybrid": {"ready": hybrid_m is not None, **(hybrid_m.status() if hybrid_m else {})},
    }


@app.get("/metrics", include_in_schema=False)
def metrics_endpoint():
    mod, err = _need("metrics")
    if err:
        return PlainTextResponse("# metrics module not ready\n", status_code=503)
    return PlainTextResponse(mod.render(), media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/api/slo", include_in_schema=False)
def slo_endpoint():
    mod, err = _need("metrics")
    if err:
        return fail(503, "MODULE_NOT_READY")
    return {"ok": True, **mod.slo_view()}


# 只放行页面本身，避免把服务端源码、配置与工作区文件暴露到 HTTP
# 路由约定：
#   /workbench.html, /workbench  -> JTBD v2.0 决策工作台（面向用户的主入口）
#   /, /index.html, /legacy/index.html -> 既有 8 视图单页（E2E 契约 + 历史回放）
# 二者共用同一 BFF 与 API，业务逻辑不重复实现。根路径保持旧页面是为不破坏既有的
# 浏览器 E2E 与首页内容断言；新用户在启动提示 / Next 控制台被引导到工作台。
@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
@app.get("/legacy/index.html", include_in_schema=False)
def index():
    return FileResponse(ROOT / "index.html", media_type="text/html; charset=utf-8")


@app.get("/workbench", include_in_schema=False)
@app.get("/workbench.html", include_in_schema=False)
def workbench():
    return FileResponse(ROOT / "workbench.html", media_type="text/html; charset=utf-8")


@app.get("/login", include_in_schema=False)
@app.get("/login.html", include_in_schema=False)
def login_page():
    return FileResponse(ROOT / "login.html", media_type="text/html; charset=utf-8")


@app.get("/demo-config.js", include_in_schema=False)
def demo_config():
    return FileResponse(ROOT / "demo-config.js", media_type="application/javascript; charset=utf-8")


def main():
    ap = argparse.ArgumentParser(description="职引者 ZHIYIN · FastAPI BFF (Phase 2)")
    ap.add_argument("--port", type=int, default=8848)
    ap.add_argument("--host", default=os.environ.get("ZHIYIN_HOST", "127.0.0.1"),
                    help="监听地址，默认仅本机 127.0.0.1；容器内由 compose 传 0.0.0.0")
    ap.add_argument("--env-file", default=None,
                    help="显式指定凭据文件（优先级最高；文件不存在时会警告并回退）")
    args = ap.parse_args()
    if args.env_file:
        target = Path(args.env_file).expanduser()
        if not target.is_file():
            print("凭据警告   : --env-file 指向的文件不存在，将按默认顺序回退")
        secure_config.cfg.reload(target)
    ls = llm.status()
    es = embedding.status()
    print("职引者 ZHIYIN · FastAPI BFF Phase 2  ->  http://%s:%d" % (args.host, args.port))
    print("语言模型   : qwen 兜底 / %s · %s · %s" %
          (ls["model"], ls["mode"], ls["gate"]["reason"]))
    print("个人 LLM   : 用户个人空间自带 API（加密存储 / 按身份隔离 / 密钥不下发）")
    print("输出校验   : Pydantic JSON Schema（4 个任务）· 校验失败自动降级")
    print("Embedding  : 服务器固定本地 %s · public=%s / user=%s · 用户不可选择" %
          (es["configured_backend"], es["selected_backend"]["public"],
           es["selected_backend"]["user"]))
    print("向量库     : Qdrant %s · 云写入 %s · 不可达自动降级本地内存索引" %
          ("已配置" if secure_config.secret("QDRANT_URL") else "未配置",
           "开启" if secure_config.cfg.flag("QDRANT_ALLOW_WRITE") else "关闭(默认)"))
    print("数据闸门   : fail-closed · /api/sources · /api/insight/modes · 国内市场洞察待授权")
    print("C 级入口   : /api/jd/analyze · 原文零落盘 · PII 先脱敏 · 可撤回 · 默认不出本机")
    print("知识入库   : /api/admin/knowledge/* · 管理员文本 / 截图 OCR 文本 · 脱敏+仅岗位信息 · Qdrant")
    print("研究检索   : /api/rag/* · 仅文本 · 加密片段 · TTL %d 天 · 可删除" %
          rag_store.DEFAULT_RETENTION)
    print("采集状态   : 自动爬取 / 自动截图执行体已移除 · 系统不访问任何第三方页面")
    print("出境防护   : /api/vector/guard · 目标/集合/分级/字段/PII/体积锁定 · 凭据与原文不回显")
    print("凭据状态   : 来源 %s · 密钥仅在本进程内存，不会下发或打印" % ls["credential_source"])
    print("按 Ctrl+C 停止")
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()

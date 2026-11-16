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
5. Qdrant Cloud 只收 D 级公开岗位样本；集合名 zhiyin_public_d；结果强制带
   grade / region / forbidden_claims，不得据此得出国内市场结论。
6. 行业洞察双模式：仅 A 级境内授权数据可解锁 cn_market，当前 pending、零数值。
7. 研究检索库只收文本、仅 research；片段 AES-256-GCM 加密、TTL 30 天、可删除、审计哈希链。
8. 受控研究采集**默认不自动爬取**：设置集中在管理员视图，由手动开关逐次控制；
   开关只记录管理员意图，不发起任何请求。2026-11-16 本轮已按所有者指定接入
   `capture_executor.py` 真实截图执行体，但作用域硬限定为「本地测试页 + 公开声明
   允许爬取的练习沙盒 books.toscrape.com」，招聘网站与真实业务站点在代码层拒绝。
   2026-11-16 所有者已**自担风险放行 B2 的工程决策闸门**（`.privacy/approvals/B2.json`，
   只读、`issuer=项目所有者`、`legal_review=pending`，不冒充法务结论 / 站点许可）。
   「截图不能太快」升级为 `research_capture.PACE_CONTRACT` + `enforce_pace()` 代码级硬契约：
   单域名串行 1 / burst 1 / 每页 ≥ max(15s, 人类阅读静置 12s, Crawl-delay) /
   每日上限 60 / 单批次 10 页 / 批次冷却 300s / 真实 UA / 每页留痕。
   「模拟人类」只指**人类可读的低频节奏**（慢速串行、可核查）；伪装真人身份、
   抖动躲风控、UA / IP 轮换、绕过验证码 / WAF / Cloudflare、登录墙 / 字体反爬一律永久禁止。
   **执行体存在 ≠ 生产采集解锁**：站点书面许可与法务书面结论仍未回、
   注册表仍为 `pending_legal`，故 `effective=false`、生产 `requests_issued=0`、
   `production_capture_unlocked=false`；实际发出的沙盒测试请求另以
   `sandbox_test_requests_total` 如实记录，不把练习沙盒实跑冒充为生产采集。
   同日第三轮按所有者指令接入 `production_capture.py` **生产执行体能力**（九道闸门），
   但目标只由人类线下放置的站点书面许可回执 `.privacy/site_licenses/*.json` 驱动，
   并须法务书面结论 `B2-legal.json` + 注册表 `approved`；两份材料都未回，
   故 `production_capture_unlocked=false`、生产 `requests_issued=0`。
   只读 `GET /api/capture/production/status`；工程侧不提供许可写入 API，不假造许可。
9. **Q18 出境写入防护**（`outbound_guard.py`）：每次请求前锁定 Qdrant Cloud 官方域 /
   集合名 / 分级 D / 已批准来源 / payload 字段白名单 / 逐字段 PII / 体积批量上限；
   凭据与原文不回显，审计只写 digest。只读 `GET /api/vector/guard`，
   不联网不写入的 dry-run `POST /api/vector/guard/check`。
10. **全项目使用限制条款**（2026-11-16 所有者确立，见 usage_policy.py）：
   「本项目仅用于研究与学习，不得用于任何商业行为」。该条款对 B1 / B2 / Q18 / Q19 /
   Q21 / B3 同理适用，收紧目的限定并禁止商业化 / 对外分发；但它**不是合规豁免**、
   不替代法务书面结论 / 站点许可 / 商务合同、**不改变任何回执 decision 或闸门状态**，
   也不解锁生产采集闸门。
11. Phase 2 新增组件全部「可选依赖 + 本地降级」：Qdrant / BGE / LangGraph /
    PostgreSQL / Redis / Neo4j / Celery / Next.js 任一缺失，视图仍可运行。
12. JTBD v2.0 决策工作台（jtbd.py + workbench.html）为面向用户的主入口
    （`/workbench.html`，根路径 `/` 因 E2E 契约保留旧单页）：方向 → 核对 → 行动 →
    演练 → 看板。C 级用户文本只在本次请求内存里处理，零落盘、零外发、不进 RAG / 向量库；
    硬条件单独阻断、无百分比总分、「没提供」一律标未知。看板 / 材料库存浏览器 localStorage。
    旧单页 index.html 保留为 /index.html 与 /legacy/index.html，仅供既有 E2E 契约与历史回放，
    不是并行产品；两套页面共用同一后端 API，不重复实现业务逻辑。
13. 登录与分角色鉴权（auth.py）：个人微信扫码（真实 OAuth / 演示降级）+ 管理员密码登录。
    /login.html 登录页；工作台右上角个人按钮进个人中心 / 管理后台。会话令牌存 httpOnly cookie；
    写端点角色映射集中在 _ROLE_FOR_WRITE，由中间件统一鉴权（off / optional / required）。
    真实微信凭据缺失时明确降级并全程标注「演示 / 非真实微信」，绝不伪装。

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
import auth             # 认证与分角色鉴权（微信 OAuth 真实/演示 / 管理员密码 / 会话）
import capture_executor # B2 真实截图执行体（仅练习沙盒 / 七道闸门 / 严格限速）
import embedding        # BGE / DashScope / hash 统一 embedding 适配层
import job_info         # 仅岗位信息粗粒度抽取（不提个人信息 / 不做用户画像）
import jtbd             # JTBD v2.0 决策工作台（本地规则 / 三态对照 / 行动分流）
import resume_parse     # B1 文件解析（不留存 / 可撤回删除 / 云端逐次单独同意）
import llm              # qwen 语言模型层 + 结构化输出契约
import market           # 公开数据适配层（仅标准库）
import privacy          # PIPL 合规基础设施（AES-256-GCM / 脱敏 / 删除 / 审计链）
import rag_store        # 本地研究检索库（稀疏 TF-IDF / 加密 / TTL / 可删除）
import outbound_guard   # Q18 出境写入防护（目标 / 集合 / 分级 / 字段 / PII / 体积）
import production_capture  # B2 生产执行体（站点书面许可回执驱动 / 九道闸门 / 默认不解锁）
import research_capture # 受控研究采集护栏（限速 / robots / 四重闸门 / 测试沙盒执行体）
import secure_config    # 统一安全配置（凭据仅在内存）
import sources          # 数据源注册 / fail-closed 合规闸门 / 数据分级
import usage_policy     # 全项目「仅限研究学习 / 禁止商业行为」统一条款
import user_jd          # 用户自带 JD（C 级）：确定性抽取 / PII 脱敏 / 原文零落盘
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
    task: Literal["insight", "interview_question", "answer_feedback", "report"]
    payload: dict[str, Any] = Field(default_factory=dict, description="只含演示数据或用户文本，不含凭据")


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


class CaptureToggleRequest(BaseModel):
    """管理员手动采集开关：默认 false，开启需管理员显式确认。"""
    model_config = ConfigDict(extra="forbid")
    enabled: bool = Field(default=False)
    operator_ack: bool = Field(default=False)


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


class CaptureExecutorRunRequest(BaseModel):
    """练习沙盒限速截图：需管理员先逐次手动开启开关，并显式 ack。"""
    model_config = ConfigDict(extra="forbid")
    pages: int = Field(default=3, ge=1, le=3)
    operator_ack: bool = Field(default=False)


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


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(title="职引者 ZHIYIN · BFF", version="2.0", docs_url=None, redoc_url=None)

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
    "/api/resume/parse", "/api/resume/withdraw", "/api/resume/delete",
]
_WRITE_ADMIN = [
    "/api/capture/admin/toggle", "/api/capture/executor/run",
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

    if request.method == "POST":
        # 文件上传端点单独放宽；其余 POST 仍按研究文本上限收紧
        limit = (RESUME_UPLOAD_MAX_BODY if request.url.path == "/api/resume/parse"
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
    if not ident:
        return {"ok": True, "logged_in": False, "role": None}
    return {
        "ok": True, "logged_in": True,
        "role": ident.get("role"), "name": ident.get("name", ""),
        "demo": bool(ident.get("demo")),
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
        "configured": ls["configured"],     # 前端据此决定是否走在线模型
        "mode": ls["mode"],
        "credential_source": ls["credential_source"],
        "framework": "fastapi",
        "schema_validation": True,
        "source_gate": "fail-closed",
        "market_scope": "global_remote / grade D（不代表中国境内招聘市场）",
        "insight_modes": ["skill_reference (ready)", "cn_market (pending_license)"],
        "user_jd_scope": "user_provided / grade C（原文零落盘、可撤回、默认不出本机）",
        "capture_policy": "user_manual=allowed(C) / controlled_research=pending_legal / "
                          "evasion=prohibited / rag_ingest=allowed",
        "research_mode": research_capture.status()["verdict"],
        "rag_scope": "research-only / text-only / encrypted-at-rest / TTL 30d / deletable",
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
    return secure_config.public_view()


@app.get("/api/schema")
def schema():
    """对外暴露真正生效的 JSON Schema，便于前端与测试核对。"""
    return {"ok": True,
            "schemas": {name: model.model_json_schema() for name, model in SCHEMAS.items()}}


@app.get("/api/llm/status")
def llm_status():
    return {"ok": True, **llm.status()}


# ---------------------------------------------------------------------------
# 数据源 / 市场 / 洞察 / 截图策略（Phase 1 契约完全保留）
# ---------------------------------------------------------------------------
@app.get("/api/sources")
def source_registry():
    return sources.registry_view()


@app.get("/api/market")
def market_insight(major: str = "计算机科学与技术"):
    major = (major or "").strip()[:40] or "计算机科学与技术"
    try:
        result = market.get_market_profile(major)
    except Exception:
        return {"ok": False, "error": "PUBLIC_SOURCE_UNAVAILABLE", "fallback": "sample",
                "message": "公开招聘数据源暂不可用，已切换到内置演示数据集"}
    return {"ok": True, **result}


@app.get("/api/insight/modes")
def insight_modes():
    return sources.insight_modes()


@app.get("/api/capture/policy")
def capture_policy():
    return sources.capture_policy()


@app.get("/api/research/policy")
def research_policy():
    return research_capture.research_policy()


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
def llm_route(req: LLMRequest):
    found = privacy.scan_pii(req.payload)
    if found:
        privacy.audit("llm_pii_blocked", detail="拦截字段：" + ",".join(found))
        return fail(403, "PII_BLOCKED", "检测到个人信息，按合规策略不外发至云端模型；本地模型路由尚未配置")
    try:
        raw = llm.generate(req.task, req.payload)
    except llm.LLMUnavailable as exc:
        code = str(exc)
        if code in ("PII_BLOCKED", "USER_DATA_BLOCKED"):
            privacy.audit("llm_user_data_blocked", detail=code)
            return fail(403, code, "C 级用户自带数据按默认策略不出本机")
        if not llm.qwen_gate()["can_call_chat"]:
            return fail(503, "LLM_NOT_ENABLED",
                        "qwen 未启用或 key 形态待确认；已自动降级到离线规则引擎")
        return fail(502, code, "模型调用未成功，已切换到离线演示模式")
    try:
        data = llm.validate_output(req.task, raw)
    except ValidationError:
        return fail(502, "SCHEMA_VALIDATION_FAILED", "模型输出未通过结构化校验，已切换到离线演示模式")
    return {"ok": True, "provider": "qwen", "model": llm.status()["model"],
            "validated": True, "schema": SCHEMAS[req.task].__name__, "data": data}


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


@app.post("/api/resume/withdraw")
def resume_withdraw(req: ResumeSessionRequest):
    return resume_parse.withdraw(req.session_id)


@app.post("/api/resume/delete")
def resume_delete(req: ResumeSessionRequest):
    return resume_parse.delete(req.session_id)


@app.get("/api/capture/pace")
def capture_pace_contract():
    """只读：B2 严格限速硬契约 + 一次准入核算示例。不发起任何第三方请求。"""
    return {
        "ok": True,
        "contract": research_capture.PACE_CONTRACT,
        "example": research_capture.enforce_pace(5),
        "owner_risk_release": research_capture.owner_release_state(),
        "human_scale_pacing": "enforced",
        "evasion_techniques": "forbidden",
        "requests_issued": 0,
        "test_target_allowlist": {
            "default_policy": "local_test_page_plus_verified_sandbox",
            "local_test_page": research_capture.LOCAL_TEST_TARGETS[0],
            "recruitment_sites_forbidden": True,
            "forbidden_keywords": list(research_capture.FORBIDDEN_TARGET_KEYWORDS),
            "extra_allowed_targets": list(research_capture.EXTRA_ALLOWED_TEST_TARGETS),
            "sandbox_sites": {k: {"evidence_url": s["evidence_url"],
                                  "evidence_quote": s["evidence_quote"],
                                  "is_site_written_license": s["is_site_written_license"]}
                              for k, s in research_capture.SANDBOX_SITES.items()},
            "plan_example": research_capture.pace_test_plan(2),
            "sandbox_plan_example": research_capture.pace_test_plan(
                2, research_capture.sandbox_page_urls(2)),
        },
        "executor": capture_executor.status(),
        "production_capture_unlocked": False,
        "recruitment_sites_touched": 0,
        "note": "「模拟人类」只落地为人类可读的低频节奏（慢速串行、可核查留痕）；"
                "伪装身份 / 抖动躲风控 / UA 轮换 / 绕过技术保护措施一律永久禁止。"
                "截图测试只允许本地自建测试页与经核实明确允许爬取的练习沙盒 "
                "books.toscrape.com；招聘网站与任何其他 host 在代码层被拒绝。"
                "本端点只做准入核算，不打开任何页面。",
        "disclaimer": research_capture.ADMIN_POLICY["disclaimer"],
    }


@app.get("/api/capture/test-page", include_in_schema=False)
def capture_local_test_page():
    """本地自建测试页：明确允许爬取的截图测试目标，非招聘网站、无真实个人信息。"""
    return PlainTextResponse(research_capture.LOCAL_TEST_PAGE_HTML,
                             media_type="text/html; charset=utf-8")


@app.get("/api/capture/pace-test/status")
def capture_pace_test_status():
    """只读：最近一次本地限速截图测试报告（由独立脚本产出，不含位图）。"""
    p = ROOT / "tests" / "out" / "capture_pace_report.json"
    if not p.is_file():
        return {"ok": True, "report_present": False,
                "note": "尚未运行本地限速截图测试；执行 python tests/zy_capture_pace_test.py"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"ok": True, "report_present": False, "reason": "REPORT_UNREADABLE"}
    return {"ok": True, "report_present": True, "report": data,
            "note": "报告由本地测试脚本产出；目标仅为本地允许爬取的测试页，第三方请求恒为 0。"}


@app.get("/api/capture/executor/status")
def capture_executor_status():
    """只读：真实执行体策略与最近一次练习沙盒实跑摘要（不含位图 / 页面文本）。"""
    return capture_executor.status()


@app.post("/api/capture/executor/run")
def capture_executor_run(req: CaptureExecutorRunRequest):
    """练习沙盒限速截图：真的会发起请求，因此闸门极严。

    前置：管理员已在 `/api/capture/admin/toggle` 逐次手动开启开关 + 本请求 operator_ack。
    目标由服务端从 sandbox_page_urls() 生成，**不接受客户端传入任意 URL**；
    跑完自动关闭开关（逐次手动语义）。
    """
    if not req.operator_ack:
        return fail(403, "OPERATOR_ACK_REQUIRED", "执行截图需管理员显式确认")
    if not research_capture.admin_status()["toggle"]["enabled"]:
        return fail(403, "MANUAL_TOGGLE_REQUIRED",
                    "请先在管理员页面逐次手动开启采集开关；默认关闭")
    urls = research_capture.sandbox_page_urls(req.pages)
    try:
        report = capture_executor.run(req.pages, operator_ack=True, urls=urls)
    except capture_executor.ExecutorBlocked as exc:
        return fail(403, "EXECUTOR_GATE_CLOSED", str(exc))
    except Exception:
        return fail(502, "EXECUTOR_RUN_FAILED", "练习沙盒截图失败，未产出任何生产数据")
    finally:
        research_capture.set_admin_toggle(False, False)
    return report


@app.get("/api/capture/production/status")
def capture_production_status():
    """只读：B2 生产执行体能力、九道闸门、许可回执与法务结论状态。

    纯本地文件与注册表读取，不联网、不截图、不加载模型、不写入。
    生产执行体「能力已接入」与「当前已解锁」是两个独立状态位。
    """
    return production_capture.status()


@app.get("/api/capture/admin")
def capture_admin_status():
    return research_capture.admin_status()


@app.post("/api/capture/admin/toggle")
def capture_admin_toggle(req: CaptureToggleRequest):
    """管理员手动开关：只记录意图，闸门未齐备时绝不发起任何第三方请求。"""
    if req.enabled and not req.operator_ack:
        return fail(403, "OPERATOR_ACK_REQUIRED", "开启采集需管理员显式确认")
    return research_capture.set_admin_toggle(req.enabled, req.operator_ack)


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
    print("语言模型   : qwen / %s · %s · %s" %
          (ls["model"], ls["mode"], ls["gate"]["reason"]))
    print("输出校验   : Pydantic JSON Schema（4 个任务）· 校验失败自动降级")
    print("Embedding  : %s · public=%s / user=%s" %
          (es["configured_backend"], es["selected_backend"]["public"],
           es["selected_backend"]["user"]))
    print("向量库     : Qdrant %s · 云写入 %s · 不可达自动降级本地内存索引" %
          ("已配置" if secure_config.secret("QDRANT_URL") else "未配置",
           "开启" if secure_config.cfg.flag("QDRANT_ALLOW_WRITE") else "关闭(默认)"))
    print("数据闸门   : fail-closed · /api/sources · /api/insight/modes · 国内市场洞察待授权")
    print("C 级入口   : /api/jd/analyze · 原文零落盘 · PII 先脱敏 · 可撤回 · 默认不出本机")
    print("截图策略   : 手动截图 + 本地 OCR 可用 · 受控研究采集待法务结论 · 规避手段永久禁止")
    print("研究检索   : /api/rag/* · 仅文本 · 加密片段 · TTL %d 天 · 可删除" %
          rag_store.DEFAULT_RETENTION)
    print("研究模式   : /api/research/policy · %s · 执行体仅限练习沙盒（生产采集未解锁）" %
          research_capture.status()["verdict"])
    print("出境防护   : /api/vector/guard · 目标/集合/分级/字段/PII/体积锁定 · 凭据与原文不回显")
    print("凭据状态   : 来源 %s · 密钥仅在本进程内存，不会下发或打印" % ls["credential_source"])
    print("按 Ctrl+C 停止")
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()

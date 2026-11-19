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
12. JTBD v2.0 决策工作台（jtbd.py + workbench.html）为唯一前端页面
    （`/workbench.html`，根路径 `/` 与 `/index.html` 也指向它）：洞察对齐 → 定向学习 →
    模拟面试 → 投递看板。C 级用户文本只在本次请求内存里处理，零落盘、零外发、不进 RAG / 向量库；
    硬条件单独阻断、无百分比总分、「没提供」一律标未知。看板 / 材料库存浏览器 localStorage。
    旧版 8 视图单页已删除；只有一个前端页面与一套后端 API，不重复实现业务逻辑。
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
    from starlette.concurrency import run_in_threadpool
    import uvicorn
except ImportError as exc:  # pragma: no cover
    print("缺少依赖：%s" % exc)
    print("请在项目目录执行： python -m pip install -r requirements.txt")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent

MAX_BODY = 192 * 1024  # 研究文本最长 12000 字，UTF-8 + JSON 转义后留足余量

RESUME_UPLOAD_MAX_BODY = 4 * 1024 * 1024  # 仅 /api/resume/parse 放行 multipart（文件上限 3 MiB + 开销）

LOCAL_VLM_MAX_BODY = 15 * 1024 * 1024     # 本地 VLM 图片/PDF/DOCX base64（原文件 ≤ 10 MiB，编码后留余量）

SCHEMAS = llm.SCHEMAS

Text = Annotated[str, Field(min_length=2, max_length=400)]

LongText = Annotated[str, Field(min_length=10, max_length=900)]

app = FastAPI(title="职引者 ZHIYIN · BFF", version="2.0",
              docs_url=None, redoc_url=None, openapi_url=None)

from routers.common import fail, cfg_session_ttl, _set_session_cookie  # noqa: F401


def _optional(name: str):
    """Phase 2 组件按名称惰性加载；模块缺失时返回 None（guard 的 metrics 采样用）。"""
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def _metrics():
    return _optional("metrics")

# ---- 按域路由（从原单文件拆出，行为一致）----
from routers import auth as _r_auth, jtbd as _r_jtbd, privacy as _r_privacy, resume as _r_resume
from routers import vector as _r_vector, knowledge as _r_knowledge, expert as _r_expert, ops as _r_ops

for _r in (_r_auth, _r_jtbd, _r_privacy, _r_resume, _r_vector, _r_knowledge, _r_expert, _r_ops):
    app.include_router(_r.router)


@app.get("/api/routes", include_in_schema=False)
def route_manifest():
    """只给回归脚本核对路由是否注册，不返回任何 JSON Schema / 字段定义。"""
    paths: dict[str, dict[str, Any]] = {}
    # 新版 Starlette 的 include_router 用惰性 _IncludedRouter 包装，
    # 需展开 original_router.routes 才能看到子路由（旧版 app.routes 直接是 APIRoute）。
    expanded: list[Any] = []
    for route in app.routes:
        orig = getattr(route, "original_router", None)
        expanded.extend(orig.routes if orig is not None else [route])
    for route in expanded:
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

_WRITE_PERSONAL = [
    "/api/jd/analyze", "/api/jd/delete",
    "/api/rag/ingest", "/api/rag/query", "/api/rag/delete",
    "/api/coach/chat", "/api/coach/delete",
    "/api/graph/run", "/api/tasks/dispatch",
    "/api/hybrid/search", "/api/hybrid/compare",
    "/api/vector/search",
    "/api/resume/parse", "/api/resume/parse-text", "/api/resume/withdraw", "/api/resume/delete",
    "/api/me/llm/save", "/api/me/llm/delete", "/api/me/llm/test",
    "/api/privacy/store", "/api/privacy/delete",
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
        # chunked 请求没有 Content-Length，可绕过上面的限额。本项目所有正常客户端
        # 都带 Content-Length，故对 chunked 直接拒绝（不消费流，避免影响下游解析）。
        transfer = (request.headers.get("transfer-encoding") or "").lower()
        if "chunked" in transfer:
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

@app.exception_handler(Exception)
async def unhandled_error(_request: Request, exc: Exception):
    """兜底：任何未捕获异常都返回统一 JSON，避免前端 r.json() 解析 HTML 抛 Unexpected token。

    内部异常细节只写 stderr 供排查，不回显给客户端（不泄露源码 / 路径 / 凭据）。
    """
    sys.stderr.write("  UNHANDLED %s: %s\n" % (type(exc).__name__, exc))
    return fail(500, "INTERNAL_ERROR", "服务内部错误，请稍后重试")

_PROTECTED_PAGES = {"/workbench", "/workbench.html"}

def _page_needs_login(path: str) -> bool:
    return (secure_config.cfg.flag("AUTH_REQUIRE_LOGIN", True)
            and path in _PROTECTED_PAGES)

@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
@app.get("/workbench", include_in_schema=False)
@app.get("/workbench.html", include_in_schema=False)
def workbench():
    return FileResponse(ROOT / "workbench.html", media_type="text/html; charset=utf-8")

@app.get("/login", include_in_schema=False)
@app.get("/login.html", include_in_schema=False)
def login_page():
    return FileResponse(ROOT / "login.html", media_type="text/html; charset=utf-8")

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
    # 持久密钥缺失会静默导致「重启后账号不可读 / PII 密文不可解」，启动时明确告警
    if not secure_config.secret("AUTH_SECRET"):
        print("持久密钥警告: 未设置 AUTH_SECRET —— 账号加密密钥为进程内随机，"
              "服务重启后已注册账号将无法解密；正式部署请在 .env 配置固定强随机值")
    if not secure_config.secret("ZHIYIN_PII_KEY"):
        print("持久密钥警告: 未设置 ZHIYIN_PII_KEY —— PII 保险库密钥为进程内随机，"
              "服务重启后旧密文将无法解密；正式部署请在 .env 配置固定强随机值")
    # 写端点鉴权强度：optional 下未带令牌的请求会被放行，避免部署者误以为已受保护
    if auth.mode() != "required":
        print("鉴权模式警告: AUTH_MODE=%s —— 写端点对匿名请求放行（仅页面级强制登录生效）；"
              "正式部署请设 AUTH_MODE=required" % auth.mode())
    if not secure_config.cfg.flag("AUTH_COOKIE_SECURE", False):
        print("Cookie 提示  : AUTH_COOKIE_SECURE=false —— 会话 cookie 未标记 Secure，"
              "https 正式部署请设为 true")
    print("凭据状态   : 来源 %s · 密钥仅在本进程内存，不会下发或打印" % ls["credential_source"])
    print("按 Ctrl+C 停止")
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="warning")

if __name__ == "__main__":
    main()

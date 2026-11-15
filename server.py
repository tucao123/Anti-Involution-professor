#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · FastAPI BFF（Phase 1）

安全约定：
1. 只使用 DeepSeek 一个模型提供方；配置文件里其他 provider 的行一律跳过，不解析、不载入内存。
2. API Key 只存在于本进程内存中：不写入项目文件、不返回给浏览器、不打印到日志、不回显到错误响应。
3. 前端只与 http://127.0.0.1:<port>/api/* 通信，永远接触不到凭据。
4. 静态文件只放行 index.html；server.py / .env / .workbuddy 等一律不通过 HTTP 暴露。
5. 所有模型输出先经 Pydantic（JSON Schema）校验，校验不通过即降级，绝不把未校验内容渲染给用户。
6. 用户自带 JD（C 级）原文不落盘、不外发云端模型；PII 先脱敏再抽取，须显式用途授权，可随时撤回删除。
7. 行业洞察分双模式；国内市场模式只有存在「已批准的 A 级境内数据源」才开放，未授权时界面显示待授权而非拿 D 级海外样本冒充。
8. 研究检索库只收文本、只用于 research；位图 OCR 在浏览器本地完成后即释放，服务端无图片上传端点；
   片段 AES-256-GCM 加密落盘，默认 30 天 TTL，可按文档删除或整库清空，全程写审计哈希链。
9. 受控研究采集的执行体不存在。「非商用」不是合规豁免：须法务书面结论 + 站点书面许可 +
   书面批准记录 + 注册表状态变更四者齐备；规避技术措施的手段永久禁止。

启动：python server.py            （默认端口 8848）
      python server.py --port 9000
配置：环境变量 ZHIYIN_ENV_FILE 指向 key.env，或放置项目根目录 .env。
      仅识别 deepseek:<key> / DEEPSEEK_API_KEY=<key>，可选 DEEPSEEK_BASE_URL、DEEPSEEK_MODEL。
依赖：python -m pip install -r requirements.txt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any, Literal

import market   # 公开数据适配层（仅标准库）
import privacy  # PIPL 合规基础设施（AES-256-GCM / 脱敏 / 删除 / 审计链）
import sources  # 数据源注册 / fail-closed 合规闸门 / 数据分级
import user_jd  # 用户自带 JD（C 级）：确定性抽取 / PII 脱敏 / 原文零落盘 / 可撤回
import rag_store         # 本地研究检索库：脱敏 → 分块 → 稀疏向量 → 加密落盘 → TTL / 删除
import research_capture  # 受控研究采集护栏：限速 / robots / 双重闸门 / 执行体禁用

try:
    from fastapi import FastAPI, Request
    from fastapi.exceptions import RequestValidationError
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel, ConfigDict, Field, ValidationError
    import uvicorn
except ImportError as exc:  # pragma: no cover
    print("缺少依赖：%s" % exc)
    print("请在项目目录执行： python -m pip install -r requirements.txt")
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parent
DEFAULT_BASE = "https://api.deepseek.com/v1/chat/completions"
DEFAULT_MODEL = "deepseek-chat"
MAX_BODY = 192 * 1024  # 研究文本最长 12000 字，UTF-8 + JSON 转义后留足余量

SYSTEM_PROMPT = """你是「职引者 ZHIYIN」——AI 职业生涯韧性与反内卷管家的语言模型层。
必须遵守以下护栏：
1. 不承诺录用结果、不预测薪资、不替代用户决策。
2. 「被替代风险」仅指 SAFI 在文本任务层面的自动化可行性，不等于岗位整体可被替代；不得制造焦虑，不得对任何人的职业前景下判决。
3. 事实性数值（薪资、增长率、词频、评分、占比）只能来自 payload，绝不自行编造；payload 没有就说未提供。
4. 你只负责叙述性解读、提问与可行动建议；建议必须具体、可执行、不空泛。
5. 只输出一个 JSON 对象，字段必须严格符合 payload 中的 output_schema，不要 markdown 代码块，不要任何解释性前后缀。"""

TASK_KINDS = ("insight", "interview_question", "answer_feedback", "report")

TASKS = {
    "insight": (
        "基于 payload 中的就业形势、指标、蓝海岗位与四象限技能，写一段不超过 180 字的中文解读，"
        "并给出 3 条具体的可行动项。不要重复罗列 payload 里的数字。"
        '输出 JSON：{"reading":"...","focus":["...","...","..."]}'
    ),
    "interview_question": (
        "你是模拟面试官。payload 给出当前环节、语气参数、目标岗位与上一轮回答。"
        "只生成当前环节的下一句提问（中文，一到两句，不超过 90 字），不得越到下一个环节，不得给出答案或评价。"
        '输出 JSON：{"question":"..."}'
    ),
    "answer_feedback": (
        "针对 payload 里的面试回答，给一句不超过 60 字的具体反馈（指出结构、证据或权衡上的问题），"
        "以及一条下一步可执行的改进动作。不要打分，不要复述回答。"
        '输出 JSON：{"feedback":"...","tip":"..."}'
    ),
    "report": (
        "基于 payload 里的逐题得分、能力维度与目标岗位，生成 3 条具体改进建议和 2 条简历优化锚点。"
        "建议要对应最低分维度，锚点要能直接改写简历句子。不要重复 payload 数值。"
        '输出 JSON：{"advice":["...","...","..."],"anchors":["...","..."]}'
    ),
}


# ---------------------------------------------------------------------------
# 结构化输出契约：JSON Schema 由 Pydantic 生成，模型侧与校验侧共用同一份定义
# ---------------------------------------------------------------------------
Text = Annotated[str, Field(min_length=2, max_length=400)]
LongText = Annotated[str, Field(min_length=10, max_length=900)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class InsightReading(StrictModel):
    reading: LongText = Field(description="叙述性解读")
    focus: list[Text] = Field(min_length=1, max_length=5, description="可行动项")


class InterviewQuestion(StrictModel):
    question: Text = Field(description="当前环节的下一句提问")


class AnswerFeedback(StrictModel):
    feedback: Text = Field(description="针对本轮回答的具体反馈")
    tip: Text = Field(description="下一步可执行动作")


class ReportSuggestions(StrictModel):
    advice: list[Text] = Field(min_length=1, max_length=6, description="改进建议")
    anchors: list[Text] = Field(min_length=1, max_length=5, description="简历优化锚点")


SCHEMAS: dict[str, type[StrictModel]] = {
    "insight": InsightReading,
    "interview_question": InterviewQuestion,
    "answer_feedback": AnswerFeedback,
    "report": ReportSuggestions,
}


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


# ---------------------------------------------------------------------------
# 配置读取：只读 deepseek 相关行
# ---------------------------------------------------------------------------
def _candidate_paths():
    custom = os.environ.get("ZHIYIN_ENV_FILE")
    if custom:
        yield Path(custom).expanduser()
    yield ROOT / ".env"
    yield Path.home() / "Desktop" / "青年大学习" / "key.env"


def load_config():
    """只读 deepseek 相关行；其余 provider 的凭据完全跳过。"""
    cfg = {"key": None, "base": DEFAULT_BASE, "model": DEFAULT_MODEL, "found": False}
    for path in _candidate_paths():
        if not path or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.strip()
            # 关键：非 deepseek 行直接跳过，不解析其内容
            if not re.match(r"^(deepseek\s*[:：]|DEEPSEEK_)", line, re.I):
                continue
            m = re.match(r"^deepseek\s*[:：]\s*(\S+)", line, re.I)
            if m and m.group(1).lower() not in ("", "none", "null"):
                cfg["key"] = m.group(1).strip().strip('"').strip("'")
            m = re.match(r"^DEEPSEEK_API_KEY\s*=\s*(\S+)", line, re.I)
            if m:
                cfg["key"] = m.group(1).strip().strip('"').strip("'")
            m = re.match(r"^DEEPSEEK_BASE_URL\s*=\s*(\S+)", line, re.I)
            if m:
                cfg["base"] = m.group(1).strip().strip('"').strip("'")
            m = re.match(r"^DEEPSEEK_MODEL\s*=\s*(\S+)", line, re.I)
            if m:
                cfg["model"] = m.group(1).strip().strip('"').strip("'")
        if cfg["key"]:
            cfg["found"] = True
            break
    return cfg


CFG = load_config()


# ---------------------------------------------------------------------------
# DeepSeek 调用
# ---------------------------------------------------------------------------
def strip_json(text: str) -> dict[str, Any] | None:
    """兼容模型偶尔带上 markdown 围栏或前后缀；解析不出 JSON 就返回 None 交给校验层拒绝。"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start >= 0 and end > start:
        t = t[start:end + 1]
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        return None


def call_deepseek(task: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    if not CFG["key"]:
        raise RuntimeError("DEEPSEEK_NOT_CONFIGURED")
    instruction = TASKS.get(task)
    if not instruction:
        raise RuntimeError("UNKNOWN_TASK")

    # 把同一份 JSON Schema 交给模型，再由 Pydantic 按它校验输出
    user_content = json.dumps(
        {
            "task": task,
            "instructions": instruction,
            "output_schema": SCHEMAS[task].model_json_schema(),
            "payload": payload,
        },
        ensure_ascii=False,
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content + "\n请严格按 output_schema 以 JSON 格式输出。"},
    ]
    base_body = {
        "model": CFG["model"],
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 900,
        "stream": False,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + CFG["key"],
        "Accept": "application/json",
    }

    def send(with_json_mode: bool):
        body = dict(base_body)
        if with_json_mode:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            CFG["base"],
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    try:
        result = send(True)
    except urllib.error.HTTPError as exc:
        # 部分兼容端点不支持 response_format，去掉后重试一次
        if exc.code not in (400, 422):
            raise RuntimeError("UPSTREAM_HTTP_" + str(exc.code))
        result = send(False)

    try:
        content = result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("UPSTREAM_BAD_RESPONSE")
    return strip_json(content)


def validate_output(task: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    """JSON Schema 校验：不合法就抛 ValidationError，由路由降级，绝不返回未校验内容。"""
    return SCHEMAS[task].model_validate(raw or {}).model_dump()


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(
    title="职引者 ZHIYIN · BFF",
    version="1.0",
    docs_url=None,
    redoc_url=None,
)

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


@app.middleware("http")
async def guard(request: Request, call_next):
    # 日志只记录方法与路径，不记录查询串、请求体、请求头或任何凭据
    sys.stderr.write("  %s %s\n" % (request.method, request.url.path))
    if request.method == "POST":
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length > MAX_BODY:
            return fail(413, "BAD_BODY_SIZE")
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def bad_request(_request: Request, _exc: RequestValidationError):
    return fail(400, "BAD_REQUEST")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "provider": "deepseek",
        "model": CFG["model"],
        "configured": bool(CFG["key"]),
        "mode": "live" if CFG["key"] else "offline",
        "framework": "fastapi",
        "schema_validation": True,
        "source_gate": "fail-closed",
        "market_scope": "global_remote / grade D（不代表中国境内招聘市场）",
        "insight_modes": ["skill_reference (ready)", "cn_market (pending_license)"],
        "user_jd_scope": "user_provided / grade C（原文零落盘、可撤回）",
        "capture_policy": "user_manual=allowed(C) / controlled_research=pending_legal / "
                          "evasion=prohibited / rag_ingest=allowed",
        "research_mode": research_capture.status()["verdict"],
        "rag_scope": "research-only / text-only / encrypted-at-rest / TTL 30d / deletable",
    }


@app.get("/api/schema")
def schema():
    """对外暴露真正生效的 JSON Schema，便于前端与测试核对。"""
    return {
        "ok": True,
        "schemas": {name: model.model_json_schema() for name, model in SCHEMAS.items()},
    }


@app.get("/api/sources")
def source_registry():
    """数据源合规状态、数据分级口径，以及可直接转交法务 / BD 的确认清单。"""
    return sources.registry_view()


@app.get("/api/market")
def market_insight(major: str = "计算机科学与技术"):
    """真实公开岗位样本；失败时返回 ok=false，由前端切回内置演示集。"""
    major = (major or "").strip()[:40] or "计算机科学与技术"
    try:
        result = market.get_market_profile(major)
    except Exception:
        return {
            "ok": False,
            "error": "PUBLIC_SOURCE_UNAVAILABLE",
            "fallback": "sample",
            "message": "公开招聘数据源暂不可用，已切换到内置演示数据集",
        }
    return {"ok": True, **result}


@app.get("/api/insight/modes")
def insight_modes():
    """行业洞察双模式就绪状态；由注册表实时推导，不缓存、不手工开关。"""
    return sources.insight_modes()


@app.get("/api/capture/policy")
def capture_policy():
    """截图输入的合规边界：用户手动截图与本地 OCR 入库可用；
    受控研究采集待法务书面结论；规避技术措施永久禁止。"""
    return sources.capture_policy()


@app.get("/api/research/policy")
def research_policy():
    """研究用途的法律地位、准入条件、限速参数与永久禁止项。"""
    return research_capture.research_policy()


@app.get("/api/rag/status")
def rag_status():
    """本地研究检索库状态：文档数、留存、加密、可删除与审计链。"""
    return rag_store.status()


@app.post("/api/rag/ingest")
def rag_ingest(req: RagIngestRequest):
    """文本 → PII 脱敏 → 分块 → 稀疏向量 → 加密落盘。服务端无图片上传端点。"""
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
    """余弦检索；每条命中都带 grade 与 forbidden_claims，避免片段被误当市场结论。"""
    try:
        return rag_store.query(req.q, req.top_k)
    except rag_store.InvalidInput as exc:
        return fail(400, str(exc), "检索词长度需在 2–200 字之间")


@app.post("/api/rag/delete")
def rag_delete(req: DeleteRequest):
    """按文档撤回，或传 "__all__" 清空整个研究索引。"""
    if req.id == "__all__":
        return {"ok": True, "deleted_docs": rag_store.delete_all(), "scope": "all"}
    if not rag_store.delete_document(req.id):
        return fail(404, "NOT_FOUND", "文档不存在或已过期删除")
    return {"ok": True, "deleted": req.id}


@app.get("/api/jd/status")
def jd_status():
    """C 级入口的合规状态：原文零落盘、聚合未启用、授权必填。"""
    return user_jd.status()


@app.post("/api/jd/analyze")
def jd_analyze(req: JdAnalyzeRequest):
    """用户自带 JD 的确定性抽取与能力对齐；不接收文件、不回显原文、不调用模型。"""
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
        # C 级入口同样受 fail-closed 闸门约束
        return fail(403, "SOURCE_NOT_APPROVED", "该数据源未获批准，已停止处理")
    sources.log_assessment("user_jd_served", "user_jd",
                           {"grade": "C", "skills": len(result["skills"])})
    return result


@app.post("/api/jd/delete")
def jd_delete(req: DeleteRequest):
    """撤回并删除本次会话的结构化结果；JD 原文本就未留存。"""
    if not user_jd.delete_session(req.id):
        return fail(404, "NOT_FOUND", "会话不存在或已删除（服务重启也会清空）")
    return {"ok": True, "deleted": req.id, "raw_jd_persist": False}


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


@app.post("/api/llm")
def llm(req: LLMRequest):
    # 合规前置：含 PII 的内容一律不外发到云端模型
    found = privacy.scan_pii(req.payload)
    if found:
        privacy.audit("llm_pii_blocked", detail="拦截字段：" + ",".join(found))
        return fail(403, "PII_BLOCKED", "检测到个人信息，按合规策略不外发至云端模型；本地模型路由尚未配置")
    try:
        raw = call_deepseek(req.task, req.payload)
    except RuntimeError:
        return fail(502, "UPSTREAM_ERROR", "模型调用未成功，已切换到离线演示模式")
    except urllib.error.URLError:
        return fail(502, "UPSTREAM_UNREACHABLE", "网络不可达，已切换到离线演示模式")
    except Exception:
        # 不回显任何上游细节，避免泄露请求头或凭据
        return fail(502, "UPSTREAM_ERROR", "模型调用失败，已切换到离线演示模式")

    try:
        data = validate_output(req.task, raw)
    except ValidationError:
        return fail(502, "SCHEMA_VALIDATION_FAILED", "模型输出未通过结构化校验，已切换到离线演示模式")

    return {
        "ok": True,
        "provider": "deepseek",
        "model": CFG["model"],
        "validated": True,
        "schema": SCHEMAS[req.task].__name__,
        "data": data,
    }


# 只放行页面本身，避免把服务端源码、配置与工作区文件暴露到 HTTP
@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    return FileResponse(ROOT / "index.html", media_type="text/html; charset=utf-8")


def main():
    ap = argparse.ArgumentParser(description="职引者 ZHIYIN · FastAPI BFF")
    ap.add_argument("--port", type=int, default=8848)
    args = ap.parse_args()
    print("职引者 ZHIYIN · FastAPI BFF 已启动  ->  http://127.0.0.1:%d" % args.port)
    print("模型提供方 : deepseek / %s" % CFG["model"])
    print("输出校验   : Pydantic JSON Schema（4 个任务）· 校验失败自动降级")
    print("数据闸门   : fail-closed · /api/sources · /api/insight/modes · 国内市场洞察待授权")
    print("C 级入口   : /api/jd/analyze · 原文零落盘 · PII 先脱敏 · 可撤回 /api/jd/delete")
    print("截图策略   : /api/capture/policy · 手动截图 + 本地 OCR 可用 · 受控研究采集待法务结论 · 规避手段永久禁止")
    print("研究检索   : /api/rag/* · 仅文本 · 脱敏 + 加密片段 · 稀疏 TF-IDF 余弦检索 · TTL %d 天 · 可删除"
          % rag_store.DEFAULT_RETENTION)
    print("研究模式   : /api/research/policy · %s · 执行体禁用（非商用不构成豁免）"
          % research_capture.status()["verdict"])
    print("凭据状态   : %s（密钥仅在本进程内存，不会下发或打印）"
          % ("已配置" if CFG["key"] else "未配置，前端将使用离线演示模式"))
    print("按 Ctrl+C 停止")
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()

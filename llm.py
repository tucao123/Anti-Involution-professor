#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · 语言模型层（qwen / 通义千问，OpenAI 兼容端点）

契约与 Phase 1 完全一致，只是 provider 由 deepseek 换成 qwen：
1. 同一份 JSON Schema 既交给模型，也交给 Pydantic 校验输出；校验不过即降级，
   绝不把未校验内容渲染给用户。
2. 调用前两道闸门：
   - qwen_gate()：key 形态 / 自定义端点 / LLM_ENABLED，任一不满足都不外发；
   - payload 审查：PII 正则 + C 级用户原文字段一律拒绝外发（默认关闭）。
3. 凭据只在服务端进程内存。异常只回错误码，绝不回显上游响应体、请求头或密钥。
4. 未配置 / 未启用时前端自动走离线规则引擎，7 个视图功能不中断。

用户填好 API 后启用步骤见 .env.example：
   QWEN_API_KEY=<DashScope 官方 key>  或  QWEN_BASE_URL=<你的兼容端点>
   LLM_ENABLED=true
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from secure_config import cfg, note_qwen_call, public_view, qwen_gate, secret


class LLMUnavailable(Exception):
    """未配置 / 未启用 / 上游不可达；调用方应降级，不向用户回显细节。"""


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

# C 级用户原文可能不含标准 PII 正则特征，按字段名二次拦截
USER_DATA_FIELDS = frozenset({
    "jd_text", "resume_text", "raw_text", "ocr_text", "screenshot_text",
    "user_text", "cv_text", "original_text",
})


def assert_outbound_safe(payload: Any) -> None:
    """外发前置检查；命中即抛，由路由返回 403 并写审计。"""
    from privacy import scan_pii
    if scan_pii(payload):
        raise LLMUnavailable("PII_BLOCKED")
    if isinstance(payload, dict):
        def keys(obj: Any) -> set[str]:
            out: set[str] = set()
            if isinstance(obj, dict):
                out |= {str(k).lower() for k in obj}
                for v in obj.values():
                    out |= keys(v)
            elif isinstance(obj, list):
                for v in obj:
                    out |= keys(v)
            return out
        if keys(payload) & USER_DATA_FIELDS and not cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"):
            raise LLMUnavailable("USER_DATA_BLOCKED")


def strip_json(text: str) -> dict[str, Any] | None:
    """兼容模型偶尔带 markdown 围栏或前后缀；解析不出 JSON 就返回 None 交给校验层拒绝。"""
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


def generate(task: str, payload: dict[str, Any]) -> dict[str, Any]:
    """返回模型原始 JSON（尚未校验）；失败抛 LLMUnavailable，绝不带上游细节。"""
    gate = qwen_gate()
    if not gate["can_call_chat"]:
        raise LLMUnavailable("LLM_DISABLED_OR_KEY_UNVERIFIED")
    assert_outbound_safe(payload)
    instruction = TASKS.get(task)
    if not instruction:
        raise LLMUnavailable("UNKNOWN_TASK")

    user_content = json.dumps(
        {"task": task, "instructions": instruction,
         "output_schema": SCHEMAS[task].model_json_schema(), "payload": payload},
        ensure_ascii=False,
    )
    body = {
        "model": cfg.get("QWEN_MODEL"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content + "\n请严格按 output_schema 以 JSON 格式输出。"},
        ],
        "temperature": 0.4,
        "max_tokens": 900,
        "stream": False,
    }
    key = secret("QWEN_API_KEY")
    base = cfg.get("QWEN_BASE_URL").rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + (key or ""),
        "Accept": "application/json",
    }

    def send(with_json_mode: bool) -> dict[str, Any]:
        payload_body = dict(body)
        if with_json_mode:
            payload_body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(
            base + "/chat/completions",
            data=json.dumps(payload_body, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST",
        )
        with urllib.request.urlopen(req, timeout=cfg.integer("QWEN_TIMEOUT", 30)) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))

    try:
        try:
            result = send(True)
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 422):
                raise LLMUnavailable("UPSTREAM_HTTP_%d" % exc.code) from None
            result = send(False)  # 部分兼容端点不支持 response_format
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMUnavailable("UPSTREAM_BAD_RESPONSE") from None
        parsed = strip_json(content)
        if parsed is None:
            raise LLMUnavailable("UPSTREAM_NOT_JSON")
        note_qwen_call(True)
        return parsed
    except LLMUnavailable:
        note_qwen_call(False)
        raise
    except urllib.error.URLError as exc:
        note_qwen_call(False)
        raise LLMUnavailable("UPSTREAM_UNREACHABLE") from None
    except Exception as exc:
        note_qwen_call(False)
        raise LLMUnavailable("UPSTREAM_ERROR") from None


def validate_output(task: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    """JSON Schema 校验：不合法就抛 ValidationError，由路由降级。"""
    return SCHEMAS[task].model_validate(raw or {}).model_dump()


def status() -> dict[str, Any]:
    """供 /api/health 与控制台使用；不含任何密钥值。"""
    gate = qwen_gate()
    view = public_view()
    return {
        "provider": "qwen",
        "model": cfg.get("QWEN_MODEL"),
        "configured": gate["can_call_chat"],       # 前端据此决定是否走在线模型
        "mode": "live" if gate["can_call_chat"] else "offline",
        "credential_source": view["file_source"],
        "gate": gate,
        "schema_validation": True,
        "task_kinds": list(TASK_KINDS),
        "user_data_outbound": cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"),
    }

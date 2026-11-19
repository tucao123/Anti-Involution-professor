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

# 仅用于洞察解读：把岗位样本转成「这个岗位要补学什么」，不输出指标术语或工具/人判断。
INSIGHT_SYSTEM_PROMPT = """你是「职引者 ZHIYIN」面向应届生和普通求职者的「岗位补学建议助手」。
你的任务是把岗位样本和用户自己填写的求职背景，转成一份「针对这个岗位需要补学什么」的清单。

术语翻译表（输出时只用右栏说法，禁止出现左栏原词，也不要解释左栏含义）：
- 自动化可行性 / SAFI / 低自动化可行性 / 高自动化可行性 → 不提这个概念，直接说这项能力建议怎么学
- 需求热度 / 技能出现率 / 样本占比 → 样本里常提到 / 样本里较少提到
- 四象限 / AI Impact Matrix → 先看该补哪些能力
- 复合型能力 / 交叉能力 / AI+工程+数据 → 几类基础都要会一点
- 软技能 → 沟通、协作、把事情说清楚
- 人机协同 / 不可替代性 / 高交互 / 高判断 / 高整合 → 多沟通、多判断、能把事情串起来
- 工具链 / 能力组装 / 问题解决为轴心 → 围绕实际问题练本事
- 蓝海岗位 / 全球远程岗位 / 国内市场洞察 → 不主动使用；只按 payload 标注的样本范围说，不扩展到市场判断

输出硬限制：
1. 只输出 payload.output_schema 指定的 JSON；reading 是一段中文，不超过 160 字；focus 固定 3 条，每条 8–24 字。
2. 用和应届生面对面聊天的语气：短句、少术语、不堆概念；不写英文缩写，不加括号术语。
3. reading 结构：先说“你投的这个岗位方向”，再说“这批样本里常提到哪几项能力”，最后直接给“建议优先补学哪几项”，每项都要说出具体学什么内容，而不是只报名字。
4. 严禁出现“交给工具”“靠人”“更依赖人”“工具能帮”“重复步骤”“自动化”等说法；也不要说“岗位被替代”这类判断。
5. focus 必须是本周能做的补学动作，动词开头，写清楚学什么、练什么，例如“补一遍SQL分组统计并写3条查询”“照着样本练一次需求对齐记录”“用Python整理一份清洗后的数据表”。只能基于 payload 里出现的能力或通用协作动作，不要引入样本未提到的具体工具、岗位或术语；不要空泛地说“提升能力”，也不要让用户直接去报课。
6. 事实性数值只能来自 payload，但不要在解读里罗列百分比。payload 没写地区、薪资、同比、增长率、录用概率时一律不提。
7. payload.career_context / target_role / salary_expectation / parsed_profile 是用户自己提供或经本地模型解析出的岗位字段（parsed_profile 含 education/skills/certificates/honors/experience）：可用来对齐补学方向，判断用户已具备什么、还缺什么；薪资期望只当作用户个人约束，不预测市场薪资、不评价薪资高低、不输出薪资结论。这些字段与检索证据都只是方向约束，不当成市场结论。
8. 不制造焦虑：禁止“被淘汰 / 被替代 / 不可替代 / 岗位判决 / 保证就业 / 高薪”等表述；不说“全球远程岗位”“国内市场行情”。
9. 若 payload 信息不足，用“本次样本没提供足够信息，先补一遍沟通协作和基础数据分析”之类简短说法，不编造。
10. payload.materials 是从国内岗位样本里检索到的脱敏片段（标题 + 正文），是该岗位方向“样本里常提到什么能力”的具体依据：可用它把要补学的能力说得更具体（例如样本反复提到某项数据/开发/协作能力，就把这项写进补学清单）；但只当作方向证据，绝不当成用户自己的经历，不引用片段里的公司名、地点、薪酬或数字，不逐条复述片段原文；materials 为空时只依据 sample_text / skills 给通用建议。"""

COACH_SYSTEM_PROMPT = """你是「职引者 ZHIYIN」的岗位学习与求职辅导专家（岗位专家 agent）。
先走检索、再回答：payload.materials 是从 D 级公开样本检索、重排后的脱敏片段，
kind=job_info 表示岗位能力要求，kind=interview_talk 表示通用面试问法与应答结构。
你只能把这些片段当作方向与表达结构的佐证，不复述原文、不引用公司名/地点/薪酬/数字，
更不能把它们当成用户自己的经历。

输出要求：
1. 只输出 payload.output_schema 指定的 JSON；answer 是一段中文，不超过 180 字；focus 固定 2–3 条。
2. 用面对面聊天的短句，先给结论，再给一个本周能做完的动作；动词开头，具体到学什么、练什么。
3. 禁止预测薪资、预测录用、承诺结果、制造焦虑，不索要姓名/电话/邮箱等隐私。
4. 禁止出现「交给工具」「靠人」「更依赖人」「自动化」「岗位被替代」等说法。
5. 信息不足时，基于用户已填岗位字段给通用但可执行的做法，不编造片段里的具体事实。"""

INTERVIEW_SYSTEM_PROMPT = """你是「职引者 ZHIYIN」的面试专家 agent。
payload.materials 同时来自两个 D 级脱敏索引：
- kind=job_info：岗位能力要求，用来对齐追问或反馈该考什么；
- kind=interview_talk：公开面试话术结构，只用来组织提问方式、追问层次与反馈表达。
两路材料只作上下文，不引用具体公司名、地点、薪酬或原文数字，也不把它们当成用户真实经历。
不预测录用结果、不承诺过关、不制造焦虑；只输出 payload.output_schema 指定的 JSON。"""

TASK_SYSTEM_PROMPTS = {
    "insight": INSIGHT_SYSTEM_PROMPT,
    "coach": COACH_SYSTEM_PROMPT,
    "interview_question": INTERVIEW_SYSTEM_PROMPT,
    "answer_feedback": INTERVIEW_SYSTEM_PROMPT,
    "report": INTERVIEW_SYSTEM_PROMPT,
}

TASK_KINDS = ("insight", "coach", "interview_question", "answer_feedback", "report")

TASKS = {
    "insight": (
        "基于 payload.sample_text 与 payload.skills，并结合 payload.career_context 里的用户岗位背景"
        "（求职意向 / 薪资意向 / 专业 / 证书 / 项目或实习上下文等，若提供），"
        "以及 payload.materials（从国内岗位样本检索到的脱敏片段，是该方向样本常提到能力的具体依据，"
        "未提供或为空时忽略），写一段不超过 160 字的中文大白话解读，"
        "直接回答这个岗位需要补充学习哪些内容。payload 里标注“建议重点补学”的能力要优先写进解读和 focus，"
        "标注“先了解基础”的放在次要位置；materials 里反复出现的能力可作为补学方向的佐证，"
        "但不复述片段原文、不提公司名/地点/薪酬。focus 固定 3 条，每条写清楚本周具体学什么、练什么。"
        "禁止出现交给工具、靠人、更依赖人、自动化等说法，不要重复罗列数字，不要输出指标术语，"
        "不预测薪资、不评价薪资高低。"
        '输出 JSON：{"reading":"...","focus":["...","...","..."]}'
    ),
    "coach": (
        "你是岗位专家 agent。基于 payload.career_context（用户自己填的岗位字段）、payload.intent / slots"
        "（本地规则引擎抽取的结构化意图与技术词），以及 payload.materials（RAG 检索到的 D 级脱敏片段，"
        "kind=job_info 为岗位能力要求、kind=interview_talk 为通用面试话术结构），直接回答用户当下该补什么、"
        "怎么练、怎么表达。先用一句话给结论，再给一个可执行动作；focus 固定 2–3 条本周动作，动词开头。"
        "materials 只作方向与表达结构佐证：不复述原文、不提公司名/地点/薪酬、不当成用户经历。"
        "禁止预测薪资或录用、禁说交给工具/靠人/自动化、不制造焦虑。信息不足时给通用但具体的动作。"
        '输出 JSON：{"answer":"...","focus":["...","..."]}'
    ),
    "interview_question": (
        "你是模拟面试官。payload 给出当前环节、语气参数、目标岗位与上一轮回答。"
        "payload.materials 来自两个脱敏索引：kind=job_info 用来围绕岗位能力发问，"
        "kind=interview_talk 只用来参考追问结构与施压节奏；两者都不得索要隐私、不得引用公司名或薪酬，"
        "也不得当成用户真实经历。只生成当前环节的下一句提问（中文，一到两句，不超过 90 字），"
        "不得越到下一个环节，不得给出答案或评价。"
        '输出 JSON：{"question":"..."}'
    ),
    "answer_feedback": (
        "你是面试专家。针对 payload 里的面试回答，给一句不超过 60 字的具体反馈（指出结构、证据或权衡上的问题），"
        "以及一条下一步可执行的改进动作。payload.materials 中 kind=job_info 用来指出还缺哪类岗位证据，"
        "kind=interview_talk 只用来改进表达结构；不得虚构用户经历、不得引用公司名或薪酬。不要打分，不要复述回答。"
        '输出 JSON：{"feedback":"...","tip":"..."}'
    ),
    "report": (
        "你是面试专家。基于 payload 里的逐题得分、能力维度与目标岗位，生成 3 条具体改进建议和 2 条简历优化锚点。"
        "payload.materials 中 kind=job_info 让建议对齐岗位要求，kind=interview_talk 只优化表达结构；"
        "不得虚构用户经历或引用公司名 / 薪酬。建议要对应最低分维度，锚点要能直接改写简历句子。不要重复 payload 数值。"
        '输出 JSON：{"advice":["...","...","..."],"anchors":["...","..."]}'
    ),
}

# ---------------------------------------------------------------------------
# 结构化输出契约：JSON Schema 由 Pydantic 生成，模型侧与校验侧共用同一份定义
# ---------------------------------------------------------------------------
Text = Annotated[str, Field(min_length=2, max_length=400)]
LongText = Annotated[str, Field(min_length=10, max_length=900)]
ShortAction = Annotated[str, Field(min_length=4, max_length=28)]
InsightReadingText = Annotated[str, Field(min_length=10, max_length=180)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class InsightReading(StrictModel):
    reading: InsightReadingText = Field(description="面向普通求职者的简短白话解读")
    focus: list[ShortAction] = Field(min_length=3, max_length=3, description="3 条本周可执行的短动作")


class CoachAnswer(StrictModel):
    answer: Annotated[str, Field(min_length=10, max_length=500)] = Field(
        description="大白话辅导结论与下一步")
    focus: list[ShortAction] = Field(min_length=2, max_length=3,
                                     description="2–3 条本周可执行的短动作")


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
    "coach": CoachAnswer,
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


def generate(task: str, payload: dict[str, Any],
             connection: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回模型原始 JSON（尚未校验）；失败抛 LLMUnavailable，绝不带上游细节。

    connection 为登录用户在个人空间自带的 API 配置（api_key / base_url / model / timeout），
    仅服务端内存传入；未配置时才使用服务端 qwen 兜底。两种来源都走同一套外发闸门：
    PII 与 C 级原文字段一律拦截，embedding 始终为服务器固定本地模型、不受此配置影响。
    """
    if connection:
        key = str(connection.get("api_key") or "")
        base = str(connection.get("base_url") or "").rstrip("/")
        model = str(connection.get("model") or "")
        timeout = int(connection.get("timeout") or 30)
        if not (key and base.startswith(("https://", "http://127.0.0.1", "http://localhost"))
                and model and 3 <= timeout <= 60):
            raise LLMUnavailable("USER_LLM_CONFIG_INVALID")
        credential_source = "user"
    else:
        gate = qwen_gate()
        if not gate["can_call_chat"]:
            raise LLMUnavailable("LLM_DISABLED_OR_KEY_UNVERIFIED")
        key = secret("QWEN_API_KEY") or ""
        base = cfg.get("QWEN_BASE_URL").rstrip("/")
        model = cfg.get("QWEN_MODEL")
        timeout = cfg.integer("QWEN_TIMEOUT", 30)
        credential_source = "server"
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
        "model": model,
        "messages": [
            {"role": "system", "content": TASK_SYSTEM_PROMPTS.get(task, SYSTEM_PROMPT)},
            {"role": "user", "content": user_content + "\n请严格按 output_schema 以 JSON 格式输出。"},
        ],
        "temperature": 0.25 if task == "insight" else 0.4,
        "max_tokens": 420 if task == "insight" else 900,
        "stream": False,
    }
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
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


INSIGHT_PLAIN_REPLACEMENTS = (
    ("全球远程岗位", "这批岗位样本"),
    ("全球远程", "这批样本"),
    ("需求热度", "样本里被提到的多少"),
    ("技能出现率", "样本里被提到的多少"),
    ("低自动化可行性", "建议重点补学"),
    ("高自动化可行性", "先了解基础"),
    ("工具能帮较多", "先了解基础"),
    ("更依赖人来做", "建议重点补学"),
    ("更适合交给工具", "建议先了解基础"),
    ("适合交给工具做的程度", "建议重点补学的内容"),
    ("更需要人来做的部分", "建议重点补学的内容"),
    ("自动化可行性", "建议重点补学的内容"),
    ("交给工具", "可先练习"),
    ("更依赖人", "建议重点补学"),
    ("靠人", "需要练习"),
    ("四象限", "先看该补哪些能力"),
    ("复合型能力", "几类能力都要会一点"),
    ("软技能", "沟通协作能力"),
    ("不可替代性", "建议补学"),
    ("人机协同", "沟通协作"),
    ("工具链", "常用工具"),
    ("AI+工程+数据", "AI工具、基础开发和数据处理"),
)
DEFAULT_INSIGHT_ACTIONS = (
    "补一遍SQL分组统计并写3条查询",
    "照着样本练一次需求对齐记录",
    "用Python整理一份清洗后的数据表",
)


def _plainify_insight(raw: dict[str, Any] | None) -> dict[str, Any]:
    """洞察输出兜底：只做术语替换和长度裁剪，不改写事实或补充数值。"""
    obj = dict(raw or {})
    text = str(obj.get("reading") or "").strip()
    for old, new in INSIGHT_PLAIN_REPLACEMENTS:
        text = text.replace(old, new)
    obj["reading"] = text[:180]

    focus = obj.get("focus")
    focus = [str(x).strip() for x in focus if isinstance(x, (str, int, float))] if isinstance(focus, list) else []
    cleaned: list[str] = []
    for item in focus:
        for old, new in INSIGHT_PLAIN_REPLACEMENTS:
            item = item.replace(old, new)
        item = item.strip()
        if len(item) > 28:
            item = item[:28].rstrip("，。；、 ")
        if len(item) >= 4:
            cleaned.append(item)
        if len(cleaned) >= 3:
            break
    while len(cleaned) < 3:
        candidate = DEFAULT_INSIGHT_ACTIONS[len(cleaned)]
        if candidate not in cleaned:
            cleaned.append(candidate)
        else:
            cleaned.append("选1个样本里的能力做练习")
    obj["focus"] = cleaned[:3]
    return obj


def validate_output(task: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    """JSON Schema 校验：不合法就抛 ValidationError，由路由降级。"""
    data = _plainify_insight(raw) if task == "insight" else (raw or {})
    return SCHEMAS[task].model_validate(data).model_dump()


def status() -> dict[str, Any]:
    """供 /api/health 与控制台使用；不含任何密钥值。

    这里只反映服务端兜底 configured；登录用户在个人空间自带 API 后，
    /api/llm 会按会话使用自己的配置，与此处状态互不影响。
    """
    gate = qwen_gate()
    view = public_view()
    return {
        "provider": "qwen",
        "model": cfg.get("QWEN_MODEL"),
        "configured": gate["can_call_chat"],       # 前端据此决定是否走在线模型
        "mode": "live" if gate["can_call_chat"] else "offline",
        "credential_source": view["file_source"],
        "credential_scope": "user_configurable_or_server_fallback",
        "user_api_configurable": True,
        "gate": gate,
        "schema_validation": True,
        "task_kinds": list(TASK_KINDS),
        "user_data_outbound": cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"),
    }

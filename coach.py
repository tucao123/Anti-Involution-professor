#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 本地多轮对话辅导（ROADMAP A1）

边界（与 C 级数据承诺一致，不因新增对话能力而放宽）：
  1. 会话仅存在于进程内存；进程结束即消失，没有任何文件 / 数据库落盘。
  2. 用户消息原文既不落盘也不保存：只保留意图标签、技能槽位、轮次计数。
     多轮上下文由结构化槽位维持，不是靠缓存对话文本。
  3. 完全本地规则引擎，cloud_called 恒为 False；不调用任何云端模型。
  4. PII 前置脱敏；护栏固定文案，不预测薪资、不承诺录用、不制造焦虑。
  5. 可随时撤回删除整个会话；会话有 TTL 与轮次上限。

仅使用标准库 + 项目内 user_jd，缺任一模块时可被上层 503 降级，不影响主流程。
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any

import user_jd

TTL_SECONDS = 1800
MAX_TURNS = 12
MAX_MESSAGE_CHARS = 600
GUARDRAIL_VERSION = "2026-11-15.1"

_SESSIONS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()

# 顺序敏感：护栏意图必须先于一般意图命中
INTENT_RULES: list[tuple[str, list[str]]] = [
    ("salary_forecast", ["薪资预测", "工资预测", "能拿多少", "月薪多少", "年薪多少",
                          "涨薪多少", "薪资区间", "待遇预测", "给个价"]),
    ("admission_odds", ["录取概率", "录用概率", "能不能过", "会不会录用", "保offer",
                         "包过", "稳进", "一定进", "保证入职", "内推保"]),
    ("withdraw", ["删除会话", "撤回会话", "清空对话", "忘掉我", "清除记录"]),
    ("anxiety", ["内卷", "来不及了", "完蛋", "被淘汰", "没救了", "好焦虑",
                 "35岁危机", "裁员恐慌", "卷不过"]),
    ("skill_gap", ["差距", "短板", "缺什么", "覆盖率", "能力对齐", "还差"]),
    ("interview", ["面试", "追问", "自我介绍", "怎么答", "模拟面", "回答"]),
    ("resume", ["简历", "项目经历", "改写", "锚点", "经历包装"]),
    ("plan", ["计划", "怎么练", "学习路线", "先学", "安排", "从哪开始", "下一步"]),
    ("greet", ["你好", "您好", "hi", "hello", "开始", "在吗"]),
]

SAFE_REPLIES: dict[str, str] = {
    "salary_forecast": (
        "我不能预测薪资数字或涨幅。薪资受地区、行业、公司、岗位与个人谈判等多因素影响，"
        "本项目目前只有 D 级海外远程岗公开样本，没有 A 级境内授权数据，"
        "任何薪资区间都属于无依据推断。建议以官方公开统计、招聘方披露的信息为准。"
    ),
    "admission_odds": (
        "我不能判断或承诺录用概率，也不能给出「保过 / 稳进」这类结论。"
        "面试结果取决于岗位匹配、竞争者、临场表现等大量不可观测因素。"
        "我能做的是帮你把已知技能拆成可练习、可举证的表达。"
    ),
    "anxiety": (
        "先把节奏放慢：这类说法没有数据支撑，本项目也不会用它来推动你做决定。"
        "我们只看两件事——你已经能举证的能力，以及下一周能完成的一小步练习。"
        "不需要和任何人比较，也不需要接受「被淘汰」这类预设。"
    ),
    "withdraw": (
        "可以随时结束：点击「结束并清除」即可删除本次会话（只存在内存中，本来也不落盘）。"
        "清除后我不会保留任何消息、槽位或轮次记录。"
    ),
}

DISCLAIMER = ("本对话由本地规则引擎生成，用于练习与信息整理；"
              "不预测面试结果、不预测薪资、不承诺录用、不制造焦虑，也不替代你的判断。")


class ConsentRequired(RuntimeError):
    """未确认用途授权，拒绝处理，绝不默认同意。"""


class SessionNotFound(RuntimeError):
    """会话不存在或已过期。"""


def _now() -> float:
    return time.time()


def _purge_locked() -> int:
    cutoff = _now() - TTL_SECONDS
    dead = [sid for sid, s in _SESSIONS.items() if s["last"] < cutoff]
    for sid in dead:
        _SESSIONS.pop(sid, None)
    return len(dead)


def _detect_intent(text: str) -> str:
    low = text.lower()
    for intent, words in INTENT_RULES:
        if any(w in low for w in words):
            return intent
    return "general"


def _slots(text: str) -> list[str]:
    low = text.lower()
    found: list[str] = []
    for skill in user_jd.SKILLS:
        if any(w in low for w in skill["words"]):
            found.append(skill["name"])
    return found


def _compose(intent: str, slots: list[str], turn: int, last_intent: str) -> str:
    """确定性回复模板；只依赖槽位与轮次，不依赖任何消息原文。"""
    if intent in SAFE_REPLIES:
        return SAFE_REPLIES[intent]

    anchor = ""
    if slots:
        anchor = "接着我们记下的技能：" + "、".join(slots[:3]) + "。"
    elif turn > 1 and last_intent:
        anchor = "接着上一轮的话题。"

    body: dict[str, str] = {
        "greet": "我们从你手头的一份目标岗位描述或一次练习回答开始就行。"
                 "你可以问技能差距、面试答法、简历锚点或学习安排；"
                 "不需要提供姓名、手机号、学校等个人信息。",
        "skill_gap": "把目标岗位要求拆成「必须 / 加分 / 隐含」三层，"
                     "再逐条对照你能举证的项目证据（做了什么、用什么方法、结果如何衡量）。"
                     "缺证据的条目优先补一个可复现的小实验，而不是先补名词。",
        "interview": "建议用「背景 → 任务 → 动作 → 结果 → 复盘」五段式，"
                     "每段控制在一两句话；把结果与指标放在最后，"
                     "并准备一条你主动收敛范围的决策，便于应对追问。",
        "resume": "每条经历写成一个可核验的锚点：场景、动作、可量化结果。"
                  "避免堆叠形容词；没有指标时写清评测方法与样本量，"
                  "这比编造数字更可靠。",
        "plan": "按「一周一个小闭环」安排：选一个技能点 → 做一个最小可运行样例 → "
                "写 5 条评测问题 → 记录失败案例 → 复盘并改写表达。"
                "下一轮可以带着具体技能名来，我按同一结构继续拆。",
        "general": "我可以陪你做四类练习：技能差距拆解、面试回答结构、简历锚点、"
                   "一周学习安排。直接说你想先做哪一类，或贴一个技能名。"
                   "数值与流程都由确定性规则生成，我不替你下结论。",
    }
    if turn >= MAX_TURNS:
        return anchor + body.get(intent, body["general"]) + (
            " 本次会话已达轮次上限，建议结束并清除后重新开始，避免上下文越攒越长。")
    return anchor + body.get(intent, body["general"])


def chat(session_id: str | None, message: str, purpose_ack: bool) -> dict[str, Any]:
    if not purpose_ack:
        raise ConsentRequired("CONSENT_REQUIRED")
    raw = (message or "").strip()[:MAX_MESSAGE_CHARS]
    if len(raw) < 1:
        raise ValueError("EMPTY_MESSAGE")

    # 原文只在本函数栈内存在：脱敏后仅用于意图识别与槽位抽取，随后丢弃。
    clean, pii_hits, pii_kinds = user_jd.redact(raw)
    intent = _detect_intent(clean)
    slots = _slots(clean)
    del raw, clean

    with _LOCK:
        _purge_locked()
        sid = session_id or ""
        if sid and sid not in _SESSIONS:
            raise SessionNotFound("SESSION_EXPIRED_OR_UNKNOWN")
        if not sid:
            sid = "coach-" + uuid.uuid4().hex[:16]
            _SESSIONS[sid] = {
                "created": _now(), "last": _now(), "turn": 0,
                "slots": [], "last_intent": "", "guardrail_hits": 0,
            }
        s = _SESSIONS[sid]
        s["turn"] += 1
        s["last"] = _now()
        for name in slots:
            if name not in s["slots"]:
                s["slots"].append(name)
        merged_slots = list(s["slots"])
        last_intent = s["last_intent"]
        s["last_intent"] = intent
        if intent in SAFE_REPLIES:
            s["guardrail_hits"] += 1
        turn = s["turn"]
        reply = _compose(intent, merged_slots, turn, last_intent)

    return {
        "ok": True,
        "session_id": sid,
        "turn": turn,
        "intent": intent,
        "guardrail": intent in SAFE_REPLIES,
        "guardrail_version": GUARDRAIL_VERSION,
        "slots": merged_slots,
        "context_kept": bool(merged_slots) or turn > 1,
        "reply": reply,
        "pii_redacted": pii_hits,
        "pii_kinds": pii_kinds,
        "disclaimer": DISCLAIMER,
        "withdraw_url": "/api/coach/delete",
        # 以下三项是对 C 级承诺的机器可断言声明
        "raw_text_persisted": False,
        "raw_text_sent_to_cloud": False,
        "cloud_called": False,
        "storage": "in-memory-only",
        "ttl_seconds": TTL_SECONDS,
    }


def delete(session_id: str) -> bool:
    with _LOCK:
        return _SESSIONS.pop(session_id or "", None) is not None


def status() -> dict[str, Any]:
    with _LOCK:
        expired = _purge_locked()
        return {
            "ok": True,
            "active_sessions": len(_SESSIONS),
            "expired_purged": expired,
            "ttl_seconds": TTL_SECONDS,
            "max_turns": MAX_TURNS,
            "guardrail_version": GUARDRAIL_VERSION,
            "intents": [name for name, _ in INTENT_RULES] + ["general"],
            "protected_topics": sorted(SAFE_REPLIES),
            "storage": "in-memory-only · 原文零落盘 · 槽位化上下文",
            "cloud_called": False,
            "llm_provider": "none (local rule engine)",
            "disclaimer": DISCLAIMER,
        }


def _debug_session_ids() -> list[str]:
    """仅供单元用例断言会话确实被删除，不对外暴露 HTTP。"""
    with _LOCK:
        return list(_SESSIONS)

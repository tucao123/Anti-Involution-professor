#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · JTBD v2.0 决策工作台（本地确定性规则层）

只服务 JTBD v2.0 的最短决策链：方向 → 核对 → 行动 → 演练 → 看板。
边界（与 JTBD §6、项目合规口径一致）：
1. 输入全部是用户自带 C 级文本；只在本次请求内存里处理，不落盘、不外发、不进 RAG、不进向量库。
2. 结论一律限定在「你提供的这几份 JD 内」；不回答行业普遍、薪资、同比、蓝海岗位。
3. 「用户没提供」≠「不会」：一律归为「未知 / 待核实」，并给一句话追问；不编造经历。
4. 学历 / 届别 / 地点等硬条件单独阻断，不折算进任何分数；不输出百分比总分。
5. 每条结论带 evidence_ref（依据哪条要求 / 哪段经历），用户可反驳改写。
6. 建议只重组用户原话；新增内容必须标「待你确认」。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

POLICY_ID = "JTBD-WORKBENCH-2026-11-16"

# 三态 + 未知（JTBD §3.2）：有证据 / 有线索待核实 / 明确缺口 / 未知
STATES = ("evidenced", "probe", "gap", "unknown")
STATE_ZH = {
    "evidenced": "有证据",
    "probe": "待核实",
    "gap": "明确缺口",
    "unknown": "未知",
}

# 硬条件：单独阻断，不折算分数（学历 / 届别 / 地点 / 工作年限 / 证书）
HARD_KEYS = ("学历", "学位", "本科", "硕士", "博士", "大专", "届别", "应届",
             "地点", "城市", "Base", "base", "驻场", "工作年限", "年以上",
             "证书", "CPA", "法考", "教师资格")
HARD_TERMS = tuple(HARD_KEYS)

# 差距四类 → 不同动作（JTBD §3.4）
GAP_KINDS = ("expression", "evidence", "skill", "hard")

# 技能词典：仅用于识别岗位要求与简历证据，不是能力评分
SKILLS = (
    "Python", "Java", "JavaScript", "TypeScript", "Go", "C++", "SQL", "R", "MATLAB",
    "Excel", "PPT", "Word", "数据分析", "数据处理", "数据清洗", "数据可视化", "统计学",
    "机器学习", "深度学习", "NLP", "计算机视觉", "AIGC", "大模型", "Prompt", "LangChain",
    "产品", "运营", "用户研究", "竞品分析", "需求文档", "PRD", "Axure", "Figma", "原型",
    "项目管理", "沟通", "协作", "汇报", "文档", "写作", "英语", "财务", "会计", "审计",
    "市场", "营销", "策划", "设计", "PS", "视频", "剪辑", "前端", "后端", "测试", "运维",
    "Git", "Linux", "Docker", "Spark", "Hadoop", "Tableau", "PowerBI", "BI", "爬虫",
    "供应链", "物流", "人力资源", "招聘", "法务", "合同", "客服", "销售", "咨询",
)

_STOP = re.compile(r"[\s,，。；;、:：/|（）()【】\[\]\-—_]+")


class InvalidInput(ValueError):
    """输入不合规：长度不足 / 没有可辨识的要求行。"""


def _norm(text: str) -> str:
    return (text or "").replace("\r", " ").replace("\n", " ").strip()


def _skill_set(text: str) -> set[str]:
    up = " " + _norm(text) + " "
    found = set()
    for s in SKILLS:
        # 英文词按词边界，中文词直接包含
        if re.fullmatch(r"[A-Za-z0-9+#]+", s):
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(s) + r"(?![A-Za-z0-9])", up, re.I):
                found.add(s)
        elif s in up:
            found.add(s)
    return found


def _split_lines(text: str) -> list[str]:
    """把 JD 拆成要求行：优先按换行 / 序号 / 项目符号切。"""
    raw = (text or "").replace("\r", "\n")
    lines = re.split(r"[\n；;。]+|(?<=[）)])\s*|\s+·\s+|\s+-\s+", raw)
    out, seen = [], set()
    for ln in lines:
        ln = re.sub(r"^\s*(?:[-*•·]|\d+[.、)]|[（(]\d+[）)])\s*", "", ln).strip()
        if 6 <= len(ln) <= 160 and ln not in seen:
            seen.add(ln)
            out.append(ln)
    return out[:40]


def _is_hard(line: str) -> bool:
    return any(t in line for t in HARD_TERMS)


def _digest(text: str) -> str:
    return hashlib.sha256(_norm(text).encode("utf-8")).hexdigest()[:12]


def _scope(note: str = "在你提供的这几份 JD 内") -> dict[str, Any]:
    return {
        "scope": note,
        "grade": "C",
        "representative_of_cn": False,
        "forbidden_claims": ["行业普遍结论", "薪资区间", "需求同比", "蓝海岗位", "录用预测"],
        "disclaimer": "结论只针对你这次粘贴的 JD 文本，不代表任何市场行情；「没写」不等于「不会」。",
    }


def _clause() -> dict[str, Any]:
    """引用全项目统一使用限制，不重复维护条款文案。"""
    try:
        import usage_policy
        return usage_policy.scope_marker()
    except Exception:
        return {"clause_id": "USAGE-RESTRICTION-2026-11-16",
                "clause": "本项目仅用于研究与学习，不得用于任何商业行为"}


# ---------------------------------------------------------------------------
# J1 方向比较：只在用户自带的 5–10 份 JD 内比较
# ---------------------------------------------------------------------------
def compare(jds: list[dict[str, str]], resume_text: str = "") -> dict[str, Any]:
    items = []
    for i, item in enumerate(jds[:10]):
        jd_text = _norm(item.get("text", ""))
        name = _norm(item.get("name", "")) or "JD %d" % (i + 1)
        if len(jd_text) < 12:
            continue
        skills = _skill_set(jd_text)
        lines = _split_lines(jd_text)
        hard = [ln for ln in lines if _is_hard(ln)]
        items.append({"id": "d%d" % (i + 1), "name": name[:60],
                      "skills": sorted(skills), "lines": lines,
                      "hard": hard[:6], "digest": _digest(jd_text)})
    if not items:
        raise InvalidInput("至少需要一份长度 ≥12 字、含岗位要求关键词的 JD 文本")

    freq: dict[str, int] = {}
    for it in items:
        for s in it["skills"]:
            freq[s] = freq.get(s, 0) + 1
    common = sorted([s for s, n in freq.items() if n >= max(2, len(items) // 2)])
    resume = _skill_set(resume_text)
    # 补证据成本最低 = 已有简历证据覆盖最多 + 要求总量适中（不是"最好"，只是"最省力"）
    ranked = []
    for it in items:
        covered = sorted(set(it["skills"]) & resume)
        missing = sorted(set(it["skills"]) - resume)
        cost = len(missing) - len(covered) * 0.5 + len(it["hard"]) * 0.2
        ranked.append({"id": it["id"], "name": it["name"],
                       "covered": covered, "missing": missing,
                       "hard": it["hard"], "cost": round(cost, 2),
                       "digest": it["digest"]})
    ranked.sort(key=lambda x: x["cost"])

    return {
        "ok": True, "policy_id": POLICY_ID,
        **_scope("在你提供的这 %d 份 JD 内" % len(items)),
        "sample_size": len(items),
        "common_requirements": common,
        "directions": [{
            "id": it["id"], "name": it["name"],
            "unique": sorted(set(it["skills"]) - set(common)),
            "lines": it["lines"][:12], "digest": it["digest"]
        } for it in items],
        "lowest_effort": [{
            "rank": i + 1, "id": r["id"], "name": r["name"],
            "reusable": r["covered"], "to_add": r["missing"],
            "hard_check": r["hard"], "effort": r["cost"],
            "basis": "依据：这份 JD 的技能要求 ∩ 你填的经历要点；硬条件另列不折算。"
        } for i, r in enumerate(ranked[:3])],
        "resume_provided": bool(resume_text.strip()),
        "note": "未提供简历要点时，reusable 一律为空，不推断你会什么。",
        "usage_restriction": _clause(),
    }


# ---------------------------------------------------------------------------
# J2 要求—证据对照：三态 + 硬条件阻断 + 可反驳
# ---------------------------------------------------------------------------
def evidence(jd_text: str, resume_text: str = "") -> dict[str, Any]:
    jd_text = _norm(jd_text)
    if len(jd_text) < 20:
        raise InvalidInput("JD 文本长度需 ≥20 字")
    lines = _split_lines(jd_text)
    if not lines:
        raise InvalidInput("未能从 JD 中拆出要求行，请粘贴含「岗位职责 / 任职要求」的原文")
    resume = _norm(resume_text)
    resume_lines = [ln for ln in _split_lines(resume) if len(ln) >= 4]
    rskills = _skill_set(resume)

    rows, blocked = [], []
    for i, ln in enumerate(lines):
        ref = "JD 第 %d 条：%s" % (i + 1, ln[:48])
        skills = sorted(_skill_set(ln))
        hit = sorted(set(skills) & rskills)
        if _is_hard(ln):
            # 硬条件永远单列，不用其他技能抵消
            state = "probe" if any(w in resume for w in ("本科", "硕士", "博士", "应届", "2026", "2027")) else "unknown"
            blocked.append({"ref": ref, "term": ln[:80], "state": state,
                            "question": "你的学历 / 届别 / 地点是否满足这一条？请自行核实。"})
            continue
        if not resume:
            state, ex = "unknown", "未提供简历要点，不判断你会不会。"
        elif hit:
            state, ex = "evidenced", "你填写的经历里出现：%s（仅表示可核对的线索）" % "、".join(hit)
            # 有技能词但简历行里只是一笔带过 → 标待核实
            if not any(all(s in rl for s in hit[:1]) for rl in resume_lines):
                state = "probe"
                ex = "提到过 %s，但没有说明你在其中做了什么、结果如何。" % "、".join(hit)
        elif skills:
            state, ex = "gap", "这条要求涉及 %s，你的经历要点里没有对应表述。" % "、".join(skills)
        else:
            state, ex = "unknown", "这条要求无法用关键词核对，需要你自己判断。"
        rows.append({"id": "r%d" % (i + 1), "requirement": ln[:160], "skills": skills,
                     "state": state, "state_zh": STATE_ZH[state],
                     "evidence_ref": ref,
                     "explanation": ex,
                     "question": None if state == "evidenced" else "你做过与之相关的事吗？说一件具体的。",
                     "override": None, "editable": True})

    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    # JTBD 明确删除百分比总分：不输出 coverage / score
    return {
        "ok": True, "policy_id": POLICY_ID, **_scope("这一份 JD"),
        "requirements": rows,
        "hard_conditions": blocked,
        "hard_blocking": bool(blocked and any(b["state"] != "evidenced" for b in blocked)),
        "counts": counts,
        "total": len(rows) + len(blocked),
        "decision_hint": ("硬条件未核实，先确认再决定" if blocked and any(b["state"] != "evidenced" for b in blocked)
                          else ("有明确缺口，可边投边补" if counts["gap"] else
                                ("有线索待核实，先讲清楚再做决定" if counts["probe"] else
                                 ("证据足够，先投" if counts["evidenced"] else "信息不足，先补充你的经历要点")))),
        "no_score": True,
        "note": "「有证据」只表示可核对的线索，不等于能力评分；每条可点「这个判断不对」改写。",
        "usage_restriction": _clause(),
    }


# ---------------------------------------------------------------------------
# J2 附：简历过筛两栏诊断（JTBD §3.3）
# ---------------------------------------------------------------------------
def diagnose(evidence_result: dict[str, Any], resume_text: str = "") -> dict[str, Any]:
    rows = evidence_result.get("requirements", []) if isinstance(evidence_result, dict) else []
    jd_has_not_written = [{
        "requirement": r["requirement"], "skills": r.get("skills", []),
        "state": r["state"], "evidence_ref": r["evidence_ref"],
        "suggestion": "把你真实做过、但没写进简历的那一件事补一句；不要编。",
    } for r in rows if r["state"] in ("evidenced", "probe", "gap")]
    resume = _norm(resume_text)
    rskills = _skill_set(resume)
    jd_all: set[str] = set()
    for r in rows:
        jd_all.update(r.get("skills", []))
    written_not_needed = [{
        "skill": s, "suggestion": "简历里有，但这几份 JD 没提；可保留但别占主要篇幅，除非你要换方向。",
    } for s in sorted(rskills - jd_all)]
    return {
        "ok": True, "policy_id": POLICY_ID,
        **_scope("这一份 JD 与你的简历要点"),
        "jd_has_you_not_written": jd_has_not_written,
        "you_written_jd_ignores": written_not_needed,
        "note": "左栏是性价比最高的一步：很多问题不是不会，是没写对。所有建议只重组你的原话。",
        "usage_restriction": _clause(),
    }


# ---------------------------------------------------------------------------
# J2 附：行动分流（JTBD §3.4）——允许「先投」和「放弃」
# ---------------------------------------------------------------------------
def actions(evidence_result: dict[str, Any], override: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = evidence_result.get("requirements", []) if isinstance(evidence_result, dict) else []
    hard = evidence_result.get("hard_conditions", []) if isinstance(evidence_result, dict) else []
    override = override or {}
    groups: dict[str, list[str]] = {k: [] for k in GAP_KINDS}
    for r in rows:
        state = override.get(r["id"], r["state"])
        if state not in STATES:
            continue
        if state == "evidenced":
            groups["expression"].append(r["requirement"])
        elif state == "probe":
            groups["expression"].append(r["requirement"])
        elif state == "gap":
            if any(w in r["requirement"] for w in ("沟通", "汇报", "文档", "写作", "表达", "讲", "介绍")):
                groups["expression"].append(r["requirement"])
            elif any(w in r["requirement"] for w in ("实习", "项目", "经历", "作品", "案例")):
                groups["evidence"].append(r["requirement"])
            else:
                groups["skill"].append(r["requirement"])
        else:
            groups["evidence"].append(r["requirement"])
    if hard:
        groups["hard"] = [h["term"] for h in hard]

    actions_out = []
    if groups["hard"]:
        actions_out.append({"kind": "hard", "priority": 1,
                            "title": "先核实硬条件", "items": groups["hard"][:5],
                            "do": "逐条确认学历 / 届别 / 地点；不满足就调整目标或找替代路径。",
                            "allow_stop": True})
    if groups["expression"]:
        actions_out.append({"kind": "expression", "priority": 2,
                            "title": "先改简历 / 练怎么讲", "items": groups["expression"][:5],
                            "do": "把做过但没写的事补一句；再用一句话说清你当时的判断。",
                            "allow_stop": False})
    if groups["evidence"]:
        actions_out.append({"kind": "evidence", "priority": 3,
                            "title": "补一个小证据", "items": groups["evidence"][:5],
                            "do": "做一个能在 1–2 周内完成、可展示结果的小任务，留下可核对的产出。",
                            "allow_stop": False})
    if groups["skill"]:
        actions_out.append({"kind": "skill", "priority": 4,
                            "title": "安排学习", "items": groups["skill"][:5],
                            "do": "只学这几条要求涉及的内容，学完回到对照表重新判一次。",
                            "allow_stop": False})
    gap = sum(len(v) for k, v in groups.items() if k != "hard")
    verdict = ("证据够了，先投" if not gap and not groups["hard"]
               else "先改简历再投；缺口可以边投边补" if not groups["hard"] and gap <= 3
               else "这个方向先放弃或换一个，别把时间耗在硬伤上" if groups["hard"] and gap >= 5
               else "按上面顺序做，做完回到对照表重判")
    return {
        "ok": True, "policy_id": POLICY_ID, **_scope("这一份 JD"),
        "groups": groups, "actions": actions_out,
        "verdict": verdict,
        "allow_apply_now": verdict in ("证据够了，先投", "先改简历再投；缺口可以边投边补"),
        "allow_drop": "先放弃" in verdict,
        "note": "「先投」和「放弃」都是合法结论；不制造「必须补完才能投」的焦虑。",
        "usage_restriction": _clause(),
    }


# ---------------------------------------------------------------------------
# J3 追问演练：针对一个缺口 + 一段真实项目，连续三层
# ---------------------------------------------------------------------------
def drill(requirement: str, project_text: str, depth: int = 3) -> dict[str, Any]:
    requirement = _norm(requirement)[:200]
    project_text = _norm(project_text)[:1200]
    if len(requirement) < 4 or len(project_text) < 10:
        raise InvalidInput("请选一条要求，并写一段你真实做过的项目（≥10 字）")
    # 三层追问：做了什么 → 为什么这么做 → 如果重来怎么改。全部围绕用户原话，不替他编。
    questions = [
        {"layer": 1, "question": "关于「%s」，你在这段项目里具体做了什么？" % requirement[:60],
         "listen_for": "动作 + 结果，不要只讲职责"},
        {"layer": 2, "question": "你为什么选这个做法 / 这个指标？当时有哪些别的选项？",
         "listen_for": "判断依据与取舍，不是做法复述"},
        {"layer": 3, "question": "如果重来一次，你会改哪一步？为什么？",
         "listen_for": "复盘能力；允许说「当时没想到」"},
    ]
    return {
        "ok": True, "policy_id": POLICY_ID, **_scope("这一条要求 + 你的项目"),
        "requirement": requirement, "project_echo": project_text[:200],
        "questions": questions[:max(1, min(3, depth))],
        "score_dimensions": ["表达清楚", "技术判断", "经历证据"],
        "feedback_rule": "反馈必须引用你刚才的原话，指出哪里空、哪里是判断、下次怎么改。",
        "pressure_mode_optional": True,
        "note": "系统不替你生成经历；回答将按你自己的原话逐条核对。",
        "usage_restriction": _clause(),
    }


def review_answer(question: dict[str, Any], answer: str) -> dict[str, Any]:
    """对用户回答做本地规则反馈：引用原话 + 三维度线索，不打分、不编内容。"""
    a = _norm(answer)
    if len(a) < 8:
        return {"ok": True, "too_short": True,
                "feedback": "你只写了几个字。请说一件具体的事：做了什么、为什么、结果如何。",
                "quote": a, "dimensions": {}}
    has_action = bool(re.search(r"我(做|写|负责|设计|分析|搭|改|测|整理|协调|实现|完成)", a))
    has_reason = bool(re.search(r"因为|由于|选择|考虑|权衡|取舍|原因|相比", a))
    has_result = bool(re.search(r"结果|最后|最终|提升|下降|完成|上线|通过|减少|增加|\d+%", a))
    return {
        "ok": True, "too_short": False,
        "quote": a[:160],
        "dimensions": {
            "表达清楚": "已引用你的原话；" + ("有具体动作。" if has_action else "还没说清你具体做了什么。"),
            "技术判断": ("出现了选择 / 取舍的表述。" if has_reason else "缺一句「为什么这么做」，这是第二层追问的重点。"),
            "经历证据": ("提到了结果。" if has_result else "缺一个可核对的结果或产出。"),
        },
        "next": ("第二层：你当时为什么这么选？" if not has_reason else
                 "第三层：如果重来，你会改哪一步？"),
        "no_score": True,
        "note": "只引用并核对你写的原话，不替你补内容、不输出总分。",
    }


def status() -> dict[str, Any]:
    return {
        "ok": True, "policy_id": POLICY_ID,
        "scope": "JTBD v2.0 决策工作台：方向 → 核对 → 行动 → 演练 → 看板",
        "endpoints": ["/api/jtbd/compare", "/api/jtbd/evidence", "/api/jtbd/diagnose",
                      "/api/jtbd/actions", "/api/jtbd/drill", "/api/jtbd/review"],
        "states": list(STATES), "state_zh": STATE_ZH,
        "hard_conditions_separate": True, "no_percentage_score": True,
        "user_data": "C 级用户自带文本：仅本次请求内存处理，零落盘、零外发、零 RAG 入库",
        "board_storage": "投递看板与材料库保存在浏览器 localStorage，由用户自己选择保存/删除",
        "allows_apply_now_and_drop": True,
        "usage_restriction": _clause(),
    }

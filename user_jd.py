#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 用户自带 JD 分析（数据源 C 级，合规路径 C 的境内样本入口）

为什么需要它：
  境内招聘平台无公开 API、爬虫路径在法务结论前被封禁，而「能力对齐 / 定向学习 /
  模拟面试」三个模块本来就不依赖市场行情。让用户主动提供目标岗位 JD，是拿到真实
  境内 JD 且零爬取风险的最短路径。

硬性约束（对应 sources.py 的 user_jd 注册项）：
1. 只处理用户主动提供且明确授权用途的文本；purpose_ack 不为 True 时拒绝处理。
2. 原文不落盘、不外发云端模型。内存会话只保留脱敏后的结构化抽取结果与指纹。
3. PII（手机 / 邮箱 / 身份证 / 姓名标签）先自动脱敏再抽取，并告知脱敏处数；
   对应《网络数据安全管理条例》第 24 条「删除或匿名化」的过程控制。
4. 未经单独同意不得匿名聚合；本版本不提供聚合能力，aggregate_opt_in 恒不生效。
5. 不做任何市场级结论：不给同比、薪资、需求热度、蓝海岗位排序。结论仅对这一份 JD 成立。
6. 数值全部由确定性规则计算；无简历要点时绝不推断覆盖率，不用模型编造分数。
7. 会话可撤回 / 可删除，删除动作写 append-only 合规留痕。

仅使用标准库；由 server.py 作为 /api/jd/* 路由调用。
"""
from __future__ import annotations

import hashlib
import re
import secrets
import threading
from datetime import datetime, timezone
from typing import Any

import sources  # 数据源注册 / fail-closed 准入闸门 / 数据分级

MIN_JD = 40
MAX_JD = 8000
MAX_RESUME = 4000
MAX_EVIDENCE = 46

# 与 privacy.py 一致的 PII 识别口径；额外覆盖 JD 中常见的「联系人：」标签
PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "phone": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "id_card": re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "contact_label": re.compile(r"(联系人|联系邮箱|联系电话|简历投递|投递邮箱)\s*[:：]\s*[^\s,，;；。\n]{1,40}"),
}

# 文档 §3.2 明确引用的三个 O*NET / SAFI 基准值，按技能类目映射，不生成新分数
SAFI_BASELINE = {"programming": 71.8, "mathematics": 73.2, "active_listening": 42.2}

# 中文 JD 技能本体：展示名 → SAFI 类目 → 检索词（中英混排，全部小写匹配）
SKILL_DEFS: list[tuple[str, str, list[str]]] = [
    ("Python", "programming", ["python", "django", "flask", "fastapi"]),
    ("JavaScript / 前端工程", "programming", ["javascript", "typescript", "node.js", "react", "vue", "前端", "小程序", "css"]),
    ("Java / Go / C++", "programming", ["java", "golang", "go语言", "kotlin", "c++", "rust", "spring"]),
    ("SQL 与数据库", "programming", ["sql", "mysql", "postgresql", "postgres", "redis", "mongodb", "数据库"]),
    ("云原生与 DevOps", "programming", ["docker", "kubernetes", "k8s", "ci/cd", "jenkins", "devops", "云原生", "容器化"]),
    ("API 与系统集成", "programming", ["restful", "rest api", "graphql", "微服务", "接口设计", "系统集成", "api 设计"]),
    ("数据管道与采集", "programming", ["etl", "spark", "flink", "数据仓库", "数据管道", "数据清洗", "爬虫", "数据采集"]),
    ("工程规范与测试", "programming", ["git", "单元测试", "代码规范", "code review", "重构", "工程化"]),
    ("统计分析", "mathematics", ["统计分析", "统计学", "假设检验", "回归分析", "spss", "stata"]),
    ("机器学习 / 深度学习", "mathematics", ["机器学习", "深度学习", "machine learning", "deep learning", "神经网络", "pytorch", "tensorflow"]),
    ("大模型应用 / RAG", "mathematics", ["大模型", "llm", "rag", "检索增强", "智能体", "agent", "langchain", "提示词", "prompt"]),
    ("算法与数据结构", "mathematics", ["算法", "数据结构", "复杂度", "leetcode"]),
    ("实验设计与评估", "mathematics", ["a/b", "ab测试", "实验设计", "评测集", "评估指标", "召回率", "准确率", "离线评估"]),
    ("数据可视化 / BI", "mathematics", ["tableau", "power bi", "数据可视化", "报表", "excel", "bi 工具"]),
    ("财务建模与风控", "mathematics", ["财务建模", "估值", "风控", "风险模型", "会计准则", "审计"]),
    ("用户研究与访谈", "active_listening", ["用户研究", "用户访谈", "可用性测试", "用户调研", "用户画像"]),
    ("跨团队沟通协作", "active_listening", ["跨部门", "跨团队", "沟通", "协作", "协同", "stakeholder"]),
    ("产品与需求定义", "active_listening", ["产品经理", "需求文档", "prd", "产品规划", "业务分析", "需求分析"]),
    ("项目管理与推进", "active_listening", ["项目管理", "项目推进", "敏捷", "scrum", "里程碑", "排期"]),
    ("文档与技术写作", "active_listening", ["技术文档", "文档撰写", "文案", "内容运营", "编辑", "写作"]),
    ("方案表达与汇报", "active_listening", ["汇报", "演讲", "提案", "present", "答辩", "表达"]),
    ("客户与业务协作", "active_listening", ["客户", "商务", "销售", "bd", "对接", "客户成功"]),
    ("团队管理与辅导", "active_listening", ["带团队", "团队管理", "辅导", "mentoring", "管理经验"]),
    ("合规与隐私判断", "active_listening", ["合规", "隐私", "pipl", "数据安全", "个人信息", "审计", "风险控制"]),
    ("设计系统与可访问性", "active_listening", ["设计系统", "design token", "可访问性", "wcag", "交互设计", "用户体验", "原型"]),
]

# 三级权重（文档 §4.2：必备 / 加分 / 隐含）
LEVEL_RULES = {
    "required": {"label": "必备 / REQUIRED", "weight": 3, "jd": 90},
    "bonus": {"label": "加分 / PREFERRED", "weight": 2, "jd": 70},
    "implicit": {"label": "隐含 / IMPLICIT", "weight": 1, "jd": 55},
}
REQUIRED_RE = re.compile(r"必须|必需|要求|熟练掌握|精通|熟练|掌握|具备|至少|须有|responsible|required|must|proficient", re.I)
BONUS_RE = re.compile(r"优先|加分|更佳|者优先|了解|有则更|preferred|plus|bonus|nice.to.have", re.I)
_SPLIT_RE = re.compile(r"[\n\r；;。]+|[·•▪▶]|(?<=[.!?])\s+")

_SESSIONS: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()
SKILLS = [
    {"name": name, "cat": cat, "safi": SAFI_BASELINE[cat],
     "words": [w.lower() for w in words]}
    for name, cat, words in SKILL_DEFS
]


class ConsentRequired(RuntimeError):
    """未取得用途授权；必须拒绝处理，不得降级为「默认同意」。"""


class InvalidInput(RuntimeError):
    """输入不合规（长度、格式或含本模块不提供的能力开关）。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(action: str, session_id: str = "", detail: dict[str, Any] | None = None) -> None:
    sources.log_assessment("user_jd_" + action, "user_jd",
                           {"session": session_id, **(detail or {})})


def redact(text: str) -> tuple[str, int, list[str]]:
    """先删除/匿名化 PII，再进入任何分析；返回脱敏文本、处数与命中类别。"""
    hits: list[str] = []
    out = text or ""

    def sub(pattern: re.Pattern[str], name: str, repl: str = "[已脱敏]") -> None:
        nonlocal out
        out, n = pattern.subn(repl, out)
        if n:
            hits.extend([name] * n)

    for key in ("phone", "id_card", "email", "contact_label"):
        sub(PII_PATTERNS[key], key)
    return out, len(hits), sorted(set(hits))


def _lines(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT_RE.split(text or "") if len(s.strip()) >= 4]


def _level(line: str) -> str:
    if BONUS_RE.search(line):
        return "bonus"
    if REQUIRED_RE.search(line):
        return "required"
    return "implicit"


def _title(text: str) -> str:
    for line in _lines(text)[:6]:
        clean = re.sub(r"^(岗位名称|职位名称|职位|岗位|招聘)\s*[:：]\s*", "", line).strip()
        if 2 <= len(clean) <= 28 and not REQUIRED_RE.search(clean):
            return clean
    return "用户提供的目标岗位 JD"


def extract(text: str) -> dict[str, Any]:
    """确定性抽取：句子级命中 → 技能级聚合 → 三级归类。不调用任何模型。"""
    lines = _lines(text)
    found: dict[str, dict[str, Any]] = {}
    for line in lines:
        low = line.lower()
        level = _level(line)
        for skill in SKILLS:
            if not any(w in low for w in skill["words"]):
                continue
            item = found.setdefault(skill["name"], {
                "name": skill["name"], "cat": skill["cat"], "safi": skill["safi"],
                "level": "implicit", "hits": 0, "evidence": "",
                "_evidence_by_level": {},
            })
            item["hits"] += 1
            if level not in item["_evidence_by_level"]:
                # 证据只保留脱敏后的短句，且截断；永不带出 JD 全文
                snippet = re.sub(r"\s+", " ", line).strip()[:MAX_EVIDENCE]
                item["_evidence_by_level"][level] = snippet + ("…" if len(line) > MAX_EVIDENCE else "")
            rank = {"implicit": 0, "bonus": 1, "required": 2}
            if rank[level] > rank[item["level"]]:
                item["level"] = level
    for item in found.values():
        # 证据必须与最终级别来自同一句，避免「隐含句作为必备证据」的口径错配
        by_level = item.pop("_evidence_by_level", {})
        item["evidence"] = by_level.get(item["level"]) or next(iter(by_level.values()), "")
    skills = sorted(found.values(), key=lambda s: (-LEVEL_RULES[s["level"]]["weight"], -s["hits"], s["name"]))
    return {
        "skills": skills,
        "groups": {k: [s["name"] for s in skills if s["level"] == k] for k in LEVEL_RULES},
        "lines": len(lines),
        "title": _title(text),
    }


def align(jd: dict[str, Any], resume_text: str) -> dict[str, Any] | None:
    """只有用户填了简历要点才计算覆盖率；否则返回 None，绝不推断。"""
    if not resume_text or not resume_text.strip():
        return None
    clean, _n, _kinds = redact(resume_text.strip()[:MAX_RESUME])
    r_lines = _lines(clean)
    r_hits: dict[str, int] = {}
    for line in r_lines:
        low = line.lower()
        for skill in SKILLS:
            if any(w in low for w in skill["words"]):
                r_hits[skill["name"]] = r_hits.get(skill["name"], 0) + 1

    dims, gaps = [], {"required": [], "bonus": [], "implicit": []}
    num = den = 0.0
    for s in jd["skills"][:8]:
        rule = LEVEL_RULES[s["level"]]
        j = min(100, rule["jd"] + min(10, 3 * s["hits"]))
        h = r_hits.get(s["name"], 0)
        m = min(95, 55 + 12 * h) if h else 0
        dims.append({"l": s["name"], "j": j, "m": m})
        num += min(m, j) * rule["weight"]
        den += j * rule["weight"]
        if m < j:
            gaps[s["level"]].append([s["name"], m, j])
    if not dims:
        return None
    coverage = round(100 * num / den) if den else 0
    # 证据校准：简历要点里没有可验证数字/结果词时，提示「宣称 vs 证据」风险
    has_evidence = bool(re.search(r"\d|%|提升|优化|负责|落地|上线|复现|评测|交付|故障", clean))
    calibrate = (
        "简历要点中未出现可验证的数字或结果动词，系统按「宣称」处理，不对能力强度做加分。"
        "建议在要点里补上样本量、指标或验收结论，再重新解析一次。"
        if not has_evidence else
        "简历要点含可验证表述。注意：本覆盖率是技能词的确定性匹配结果，不是能力评测分数，"
        "也不能替代面试官对深度的判断。"
    )
    return {
        "available": True,
        "coverage": coverage,
        "dims": dims,
        "gaps": {k: v[:4] for k, v in gaps.items()},
        "calibrate": calibrate,
        "formula": "覆盖率 = Σ min(简历强度, JD 要求强度) × 三级权重 ÷ Σ JD 要求强度 × 三级权重；"
                   "JD 要求强度由级别（必备 90 / 加分 70 / 隐含 55）+ 命中次数确定性映射，"
                   "简历强度由命中次数映射，未命中记 0。全部为规则值，不含模型生成。",
    }


def analyze(jd_text: str, resume_text: str = "", consent: dict[str, Any] | None = None) -> dict[str, Any]:
    """C 级 JD 分析主入口：先过闸门，再过授权，再脱敏，最后确定性抽取。"""
    src = sources.assert_fetchable("user_jd")
    consent = consent or {}
    if consent.get("purpose_ack") is not True:
        _log("consent_missing", detail={})
        raise ConsentRequired("PURPOSE_ACK_REQUIRED")
    raw_jd = (jd_text or "").strip()
    if not (MIN_JD <= len(raw_jd) <= MAX_JD):
        raise InvalidInput("JD_LENGTH_INVALID")
    clean_jd, n_pii, pii_kinds = redact(raw_jd)
    jd = extract(clean_jd)
    if not jd["skills"]:
        raise InvalidInput("NO_SKILL_DETECTED")

    alignment = align(jd, (resume_text or "").strip())
    scope = sources.applicability("C", "CN", jd["lines"])
    scope["disclaimer"] = (
        "结论仅针对这一份由用户提供的 JD，用于个人能力对齐与准备；"
        "不构成任何市场行情、需求热度、薪资、同比或岗位推荐结论，也不代表中国境内招聘市场。"
    )

    sid = secrets.token_hex(8)
    with _LOCK:
        # 内存会话只保留脱敏后的结构化结果与原文指纹，不保存原文、不落盘
        _SESSIONS[sid] = {
            "id": sid,
            "created_at": _now(),
            "fingerprint": hashlib.sha256(clean_jd.encode("utf-8")).hexdigest()[:16],
            "skill_count": len(jd["skills"]),
            "alignment": alignment is not None,
            "grade": "C",
            "raw_persisted": False,
            "consent": {"purpose_ack": True, "aggregate_opt_in": False},
        }
    _log("analyzed", sid, {"skills": len(jd["skills"]), "pii_redacted": n_pii,
                           "alignment": alignment is not None})

    return {
        "ok": True,
        "session": {
            "id": sid,
            "created_at": _SESSIONS[sid]["created_at"],
            "fingerprint": _SESSIONS[sid]["fingerprint"],
            "raw_persisted": False,
            "retention": "原文不落盘、不外发；内存会话仅保留脱敏后的结构化结果，服务重启即清空，也可随时撤回删除",
            "withdraw_url": "/api/jd/delete",
        },
        "grade": "C",
        "region": "CN",
        "representative_of_cn": False,
        "applicability": scope,
        "title": jd["title"],
        "skills": jd["skills"],
        "groups": jd["groups"],
        "lines": jd["lines"],
        "pii": {"redacted": n_pii, "kinds": pii_kinds,
                "note": "手机号 / 邮箱 / 身份证号 / 联系人标签已在抽取前自动脱敏；原文未进入任何存储"},
        "alignment": alignment,
        "alignment_note": (
            "未填写简历要点，因此不计算覆盖率与差距雷达——不推断、不编造分数。"
            "如需对齐，可在左侧补充技能与项目要点后重新解析。"
            if alignment is None else "覆盖率与雷达由确定性规则计算，口径见 formula 字段。"
        ),
        "meta": {
            "source": {"id": "user_jd", "name": src["name"], "grade": "C",
                       "region": "CN", "legal_status": src["legal_status"],
                       "legal_basis": src["legal_basis"], "limits": src["limits"]},
            "aggregation": "本版本不提供匿名聚合能力，未经单独同意不会用于任何统计或模型训练",
            "cloud_model": "JD 原文与证据短句一律不外发至云端模型；如需 AI 建议，只发送技能名、级别与覆盖率等结构化字段",
            "forbidden_claims": scope["forbidden_claims"],
            "boundary": "SAFI 仅衡量文本任务层面的自动化可行性，不等于岗位整体可被替代。",
        },
    }


def delete_session(sid: str) -> bool:
    """撤回并删除本次会话的结构化结果；原文本就未留存。"""
    with _LOCK:
        existed = _SESSIONS.pop((sid or "").strip(), None) is not None
    _log("withdrawn" if existed else "withdraw_not_found", sid, {"deleted": existed})
    return existed


def status() -> dict[str, Any]:
    with _LOCK:
        return {
            "ok": True,
            "source": "user_jd",
            "grade": "C",
            "active_sessions": len(_SESSIONS),
            "raw_jd_persist": False,
            "aggregation_enabled": False,
            "consent_required": ["purpose_ack"],
            "note": "用户自带 JD 仅用于本次能力对齐；不做市场统计、不落盘原文、不外发云端模型。",
        }

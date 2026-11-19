#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · 岗位技能词频（本地确定性，零网络）

设计原则（减法）：
- 项目只做国内样本；词频来自管理员提供的国内岗位样本，启动时已离线聚合成
  data/domestic_market_stats.json。运行时只读这一个本地文件，不请求任何外部 API。
- 因此没有超时、没有多源降级、没有缓存、没有快照趋势——这些都不存在了。
- 只保留确定性统计：语言模型不参与数值；SAFI 横轴只用技术文档 §3.2 的三个 O*NET 基准。
- 数据最小化：本地聚合文件只存技能命中次数/类别统计，不含任何岗位原文或个人信息。

对外契约保持不变：get_market_profile(major) -> {"profile": {...}, "meta": {...}}；
_compute() 仍是纯计算函数（测试用它验证确定性管道），只是数据源换成本地样本行。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import sources

ROOT = Path(__file__).resolve().parent
STATS_FILE = ROOT / "data" / "domestic_market_stats.json"

# 文档 §3.2 明确给出的 O*NET / SAFI 基准值（唯一的横轴数值来源）
SAFI_BASELINE = {
    "programming": 71.8,        # Programming 71.8
    "mathematics": 73.2,        # Mathematics 73.2
    "active_listening": 42.2,   # Active Listening 42.2
}
SAFI_NOTE = ("横轴取值仅来自《技术文档》§3.2 引用的三个公开基准：Programming 71.8 / "
             "Mathematics 73.2 / Active Listening 42.2，按技能所属类目映射，未生成新分数。")

# 技能词表：中文展示名 → O*NET 类目 → 英文检索词
_SKILL_DEFS = [
    ("Python", "programming", ["python"]),
    ("JavaScript / TypeScript", "programming", ["javascript", "typescript", "node.js", "nodejs"]),
    ("Java / Kotlin / Go", "programming", ["java", "kotlin", "golang", "rust"]),
    ("SQL 与数据库", "programming", ["sql", "postgres", "mysql", "database"]),
    ("云原生与 DevOps", "programming", ["docker", "kubernetes", "terraform", "ci/cd", "aws"]),
    ("API 与系统集成", "programming", ["rest api", "graphql", "api integration", "microservice"]),
    ("数据分析与统计", "mathematics", ["analytics", "statistics", "data analysis", "tableau", "power bi", "excel"]),
    ("机器学习 / AI", "mathematics", ["machine learning", "deep learning", "llm", "artificial intelligence", "model training"]),
    ("算法与实验设计", "mathematics", ["algorithm", "a/b test", "experimentation", "optimization"]),
    ("沟通与客户协作", "active_listening", ["communication", "stakeholder", "customer", "client", "collaboration"]),
    ("产品与用户研究", "active_listening", ["product management", "user research", "ux research", "customer success"]),
    ("内容写作与表达", "active_listening", ["content writing", "copywriting", "documentation", "editorial"]),
    ("团队管理与辅导", "active_listening", ["mentoring", "leadership", "team lead", "people management"]),
]


def _patterns(words: list[str]) -> list[re.Pattern[str]]:
    return [re.compile(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", re.I) for w in words]


SKILLS = [
    {"name": name, "cat": cat, "pats": _patterns(words), "safi": SAFI_BASELINE[cat]}
    for name, cat, words in _SKILL_DEFS
]

CATEGORY_RULES = [
    ("数据与算法", ["data scientist", "data engineer", "analytics", "machine learning", "artificial intelligence"]),
    ("工程与开发", ["engineer", "developer", "software", "devops", "backend", "frontend", "programming"]),
    ("设计与体验", ["designer", "ux", "ui design", "visual", "creative"]),
    ("金融与风控", ["finance", "financial", "risk", "accounting", "banking", "fintech"]),
    ("产品与运营", ["product", "operations", "project manager", "growth", "marketing"]),
    ("内容与传播", ["content", "writing", "editor", "copywriting", "communications"]),
]

_COLORS = ["#111", "#6a6a66", "#b3b3ad"]


def _hits(text: str) -> list[int]:
    return [i for i, s in enumerate(SKILLS) if any(p.search(text) for p in s["pats"])]


def _category(job: dict[str, str]) -> str:
    hay = job["text"] + " " + " ".join(job.get("cats") or [])
    for name, words in CATEGORY_RULES:
        if any(w in hay for w in words):
            return name
    return "通用职能"


def _compute(major: str, jobs: list[dict[str, str]], source: dict[str, str]) -> dict[str, Any]:
    """纯计算：从岗位文本行统计词频、类别热度、四象限。无网络、无副作用。

    tests/zy_verify.py 用合成 jobs 验证确定性管道，故保留此入口。
    """
    from collections import defaultdict

    n = len(jobs)
    counts = [0] * len(SKILLS)
    per_job: list[tuple[str, list[int]]] = []
    for job in jobs:
        hit = _hits(job["text"])
        for i in hit:
            counts[i] += 1
        per_job.append((_category(job), hit))

    shares = {SKILLS[i]["name"]: round(100 * counts[i] / n) for i in range(len(SKILLS))}
    peak = max(counts) or 1
    top_idx = max(range(len(SKILLS)), key=lambda i: counts[i])
    top_name = SKILLS[top_idx]["name"]
    top_share = shares[top_name]

    groups: dict[str, list[list[int]]] = defaultdict(list)
    for cat, hit in per_job:
        groups[cat].append(hit)

    rows = []
    for cat, hitlists in groups.items():
        flat = [i for h in hitlists for i in h]
        local_counts: dict[int, int] = defaultdict(int)
        for i in flat:
            local_counts[i] += 1
        low = sum(c for i, c in local_counts.items() if SKILLS[i]["safi"] < 50)
        cats = {SKILLS[i]["cat"] for i in local_counts}
        low_risk = round(100 * low / len(flat)) if flat else 0
        transfer = round(100 * len(cats) / 3) if flat else 0
        heat = round(100 * len(hitlists) / n)
        confidence = min(1.0, len(hitlists) / 10)
        score = round((0.3 * heat + 0.4 * low_risk + 0.3 * transfer) * (0.7 + 0.3 * confidence))
        top = [SKILLS[i]["name"] for i, _ in sorted(local_counts.items(), key=lambda kv: -kv[1])[:3]]
        rows.append({
            "name": cat, "growth": heat, "lowRisk": low_risk, "transfer": transfer,
            "score": score,
            "reason": "来自 %s 的国内岗位样本 %d 条；高频技能：%s。" % (
                source.get("name", "本地样本"), len(hitlists), "、".join(top) if top else "未命中词表技能"),
            "sourceRef": "%s · %d 条" % (source.get("provider", "本地样本"), len(hitlists)),
            "sample": len(hitlists), "basis": "sample_heat",
        })
    rows.sort(key=lambda r: (-r["score"], -r["sample"]))
    rows = rows[:6]

    quad = [
        {"n": SKILLS[i]["name"], "a": SKILLS[i]["safi"],
         "d": round(100 * counts[i] / peak), "raw": shares.get(SKILLS[i]["name"], 0)}
        for i in sorted(range(len(SKILLS)), key=lambda i: -counts[i]) if counts[i] > 0
    ][:10]

    scope = sources.applicability("D", "cn_sample", n)
    summary = (
        "本次技能词频来自项目内置的国内岗位样本（管理员提供、非系统爬取），"
        "共 %d 条岗位、其中 %d 条命中技能词；数值由确定性管道从岗位文本统计，不含模型生成。"
        "本样本仅用于技能词频与补学方向参考，覆盖范围内的方向性参考，"
        "不代表全国就业市场，不用于薪资、同比或岗位推荐结论。" % (n, sum(1 for i in counts if i > 0))
    )
    profile = {
        "name": major, "live": False, "data_grade": scope["grade"],
        "region": scope["region"], "representative_of_cn": scope["representative_of_cn"],
        "applicability": scope, "summary": summary,
        "metrics": [[str(n), "国内岗位样本"],
                    [str(sum(1 for h in per_job if h[1])), "命中技能岗位"],
                    [str(sum(1 for c in counts if c > 0)), "命中技能数"]],
        "jobs": rows, "quad": quad,
        "quadNote": SAFI_NOTE + " 纵轴为相对需求热度（以样本内最高出现率归一化为 100）。"
                    "四象限用于定位技能升级方向，不构成职业判决。",
        "trend": None,
    }
    meta = {
        "source": {"name": source.get("name", "国内岗位样本"), "url": source.get("url", ""),
                   "provider": source.get("provider", "local"), "samples": n,
                   "scope": "项目内置国内岗位样本；仅技能词频与方向性参考；不代表全国市场"},
        "applicability": scope,
        "history": {"points": 0, "ready": False, "needed": 2},
        "note": "本地确定性统计，无网络请求、无外部数据源；不输出薪资、同比或国内市场结论。",
    }
    return {"profile": profile, "meta": meta}


def get_market_profile(major: str = "计算机科学与技术") -> dict[str, Any]:
    """读取项目内聚合统计，组装前端契约。纯本地、确定性、无网络。"""
    major = (major or "").strip()[:40] or "计算机科学与技术"
    with STATS_FILE.open(encoding="utf-8") as f:
        stats = json.load(f)

    counts = stats["counts"]
    peak = max(int(v) for v in counts.values()) or 1
    quad: list[dict[str, Any]] = []
    for i in sorted(range(len(SKILLS)), key=lambda i: -int(counts.get(SKILLS[i]["name"], 0))):
        c = int(counts.get(SKILLS[i]["name"], 0))
        if c <= 0:
            continue
        quad.append({"n": SKILLS[i]["name"], "a": SKILLS[i]["safi"],
                     "d": round(100 * c / peak), "raw": c})
    quad = quad[:10]

    scope = sources.applicability("D", "cn_sample", stats["samples"])
    summary = (
        "本次技能词频来自项目内置的国内岗位样本（管理员提供、非系统爬取），"
        "共 %d 条岗位、其中 %d 条命中技能词；命中技能 %d 项，出现率最高的是「%s」。"
        "数值由确定性管道从岗位文本统计，不含模型生成。"
        "本样本仅用于技能词频与补学方向参考，覆盖范围内的方向性参考，"
        "不代表全国就业市场，不用于薪资、同比或岗位推荐结论。"
        % (stats["samples"], stats["hit_jobs"], stats["hit_skills"],
           quad[0]["n"] if quad else "无")
    )
    profile = {
        "name": major, "live": False, "data_grade": scope["grade"],
        "region": scope["region"], "representative_of_cn": scope["representative_of_cn"],
        "applicability": scope, "summary": summary,
        "metrics": [[str(stats["samples"]), "国内岗位样本"],
                    [str(stats["hit_jobs"]), "命中技能岗位"],
                    [str(stats["hit_skills"]), "命中技能数"]],
        "jobs": stats["jobs"], "quad": quad,
        "quadNote": SAFI_NOTE + " 纵轴为相对需求热度（以样本内最高出现率归一化为 100）。"
                    "四象限用于定位技能升级方向，不构成职业判决。",
        "trend": None,
    }
    meta = {
        "source": {"name": stats["label"], "url": "", "provider": "local_cn_sample",
                   "samples": stats["samples"],
                   "scope": "项目内置国内岗位样本；仅技能词频与方向性参考；不代表全国市场"},
        "applicability": scope,
        "history": {"points": 0, "ready": False, "needed": 2},
        "note": "本地确定性统计，无网络请求、无外部数据源；不输出薪资、同比或国内市场结论。",
    }
    return {"profile": profile, "meta": meta}

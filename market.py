#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 公开招聘数据适配层（文档 §2 路径 C：公开数据 / 合作 API 兜底）

合规边界：
- 只访问无需登录态的公开 API（Jobicy 主路径 / RemoteOK 兜底），不绕过登录墙、不复用浏览器会话、不伪造凭证。
- 不抓取非公开字段；不落盘原始 JD 全文，只保留聚合统计（数据最小化）。
- 每源独立最小请求间隔 + 结果缓存 TTL，避免高频访问。
- 明确标注「仅供学习研究」与非国内市场样本口径。

数值原则（文档 §4.1 / §7）：
- 词频、占比、评分、热度全部由本模块确定性计算，语言模型不参与、不改写。
- SAFI 横轴只使用《技术文档》§3.2 明确引用的三个 O*NET 基准值，按技能类目映射，不 invented 新分数。
- 单次快照无法得到「同比 / 季度趋势」，因此趋势图只基于真实累积的历史快照；不足 2 个点就不画。
"""
from __future__ import annotations

import html
import json
import re
import sources          # 数据源注册与合规闸门（fail-closed）
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SNAP_FILE = ROOT / ".market_snapshots.jsonl"
CACHE_TTL = 900.0          # 15 分钟
MIN_INTERVAL = 5.0         # 同一数据源最小请求间隔（秒）
HTTP_TIMEOUT = 14.0
SAMPLE_LIMIT = 50
UA = "ZhiyinResearch/1.0 (public job data; study only; contact: local)"

JOBICY_API = "https://jobicy.com/api/v2/remote-jobs"
REMOTEOK_API = "https://remoteok.com/api"
JOBICY_DOC = "https://jobicy.com/api/"
REMOTEOK_DOC = "https://remoteok.com/api"

# 文档 §3.2 明确给出的 O*NET / SAFI 基准值（唯一的横轴数值来源）
SAFI_BASELINE = {
    "programming": 71.8,        # Programming 71.8
    "mathematics": 73.2,        # Mathematics 73.2
    "active_listening": 42.2,   # Active Listening 42.2
}
SAFI_NOTE = ("横轴取值仅来自《技术文档》§3.2 引用的三个公开基准：Programming 71.8 / "
             "Mathematics 73.2 / Active Listening 42.2，按技能所属类目映射，未生成新分数。")

# 中文专业 → 公开 API 检索词与相关性词（只影响检索与排序，不产生任何数值）
MAJOR_HINTS: dict[str, dict[str, Any]] = {
    "计算机科学与技术": {"industry": "engineering", "terms": ["engineer", "developer", "software", "backend", "frontend"]},
    "金融学": {"tag": "finance", "terms": ["finance", "financial", "risk", "banking"]},
    "视觉传达设计": {"tag": "design", "terms": ["design", "ux", "visual", "creative"]},
    "汉语言文学": {"tag": "writing", "terms": ["writing", "content", "editor", "copywriting"]},
}
DEFAULT_HINT: dict[str, Any] = {"terms": []}

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
_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_LAST_CALL: dict[str, float] = {}


class MarketError(RuntimeError):
    pass


def _strip_html(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", text or ""))


def _get_json(url: str, source: str) -> Any:
    with _LOCK:
        last = _LAST_CALL.get(source, 0.0)
        wait = MIN_INTERVAL - (time.time() - last)
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "ignore")
        with _LOCK:
            _LAST_CALL[source] = time.time()
        return json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        raise MarketError("PUBLIC_API_UNREACHABLE") from exc


def _fetch_jobicy(hint: dict[str, Any]) -> list[dict[str, str]]:
    sources.assert_fetchable("jobicy")
    params: dict[str, Any] = {"count": SAMPLE_LIMIT}
    if hint.get("tag"):
        params["tag"] = hint["tag"]
    if hint.get("industry"):
        params["industry"] = hint["industry"]
    data = _get_json(JOBICY_API + "?" + urllib.parse.urlencode(params), "jobicy")
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list) or not jobs:
        raise MarketError("EMPTY_SAMPLE")
    out = []
    for j in jobs[:SAMPLE_LIMIT]:
        if not isinstance(j, dict):
            continue
        title = str(j.get("jobTitle") or "")
        cats = [str(c) for c in (j.get("jobIndustry") or [])]
        types = [str(c) for c in (j.get("jobType") or [])]
        text = " ".join([title, str(j.get("jobExcerpt") or ""),
                         _strip_html(str(j.get("jobDescription") or ""))[:1200],
                         " ".join(cats + types)])
        out.append({"title": title, "text": text.lower(), "cats": [c.lower() for c in cats]})
    return out


def _fetch_remoteok() -> list[dict[str, str]]:
    sources.assert_fetchable("remoteok")
    data = _get_json(REMOTEOK_API, "remoteok")
    if not isinstance(data, list):
        raise MarketError("EMPTY_SAMPLE")
    out = []
    for j in data[1:SAMPLE_LIMIT + 1]:   # 第 0 项是 API legal 元信息，不是岗位
        if not isinstance(j, dict) or not j.get("position"):
            continue
        title = str(j.get("position") or "")
        tags = [str(t) for t in (j.get("tags") or [])]
        text = " ".join([title, " ".join(tags),
                         _strip_html(str(j.get("description") or ""))[:1200],
                         str(j.get("location") or "")])
        out.append({"title": title, "text": text.lower(), "cats": [t.lower() for t in tags]})
    if not out:
        raise MarketError("EMPTY_SAMPLE")
    return out


def _fetch_jobs(hint: dict[str, Any]) -> tuple[list[dict[str, str]], dict[str, str]]:
    """主路径 Jobicy，失败自动降级 RemoteOK；两路都失败才返回错误给前端演示兜底。"""
    errors = []
    try:
        jobs = _fetch_jobicy(hint)
        if len(jobs) >= 5:
            return jobs, {"name": "Jobicy Public Job Board API", "url": JOBICY_DOC, "provider": "jobicy"}
        errors.append("JOBICY_TOO_FEW")
    except MarketError as exc:
        errors.append(str(exc))
    try:
        jobs = _fetch_remoteok()
        return jobs, {"name": "RemoteOK Public API（兜底）", "url": REMOTEOK_DOC, "provider": "remoteok"}
    except MarketError as exc:
        errors.append(str(exc))
    raise MarketError("ALL_SOURCES_FAILED:" + ",".join(errors))


def _hits(text: str) -> list[int]:
    return [i for i, s in enumerate(SKILLS) if any(p.search(text) for p in s["pats"])]


def _category(job: dict[str, str]) -> str:
    hay = job["text"] + " " + " ".join(job["cats"])
    for name, words in CATEGORY_RULES:
        if any(w in hay for w in words):
            return name
    return "通用职能"


def _read_snapshots(major: str) -> list[dict[str, Any]]:
    if not SNAP_FILE.is_file():
        return []
    out = []
    try:
        for line in SNAP_FILE.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if obj.get("major") == major:
                out.append(obj)
    except OSError:
        return []
    return out


def _append_snapshot(major: str, shares: dict[str, int], count: int, source: str) -> None:
    """只写聚合统计，不写原始 JD 文本；同一分钟内重复抓取只覆盖一条。"""
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    entry = {"major": major, "stamp": stamp, "minute": now.strftime("%Y%m%d%H%M"),
             "count": count, "source": source, "shares": shares}
    with _LOCK:
        lines = []
        try:
            if SNAP_FILE.is_file():
                lines = SNAP_FILE.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        kept = []
        for ln in lines:
            try:
                obj = json.loads(ln)
                if obj.get("minute") == entry["minute"] and obj.get("major") == major:
                    continue
            except ValueError:
                pass
            kept.append(ln)
        lines = kept
        lines.append(json.dumps(entry, ensure_ascii=False))
        lines = lines[-200:]
        try:
            SNAP_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass


def _build_trend(major: str, shares: dict[str, int]) -> tuple[dict[str, Any] | None, int]:
    """趋势只用真实累积快照；不足 2 个点返回 None，绝不补插值或编造历史。"""
    points = _read_snapshots(major)
    if len(points) < 2:
        return None, len(points)
    points = points[-6:]
    ranked = sorted(SKILLS, key=lambda s: shares.get(s["name"], 0), reverse=True)[:3]
    labels = [p["stamp"].replace(" UTC", "").split(" ")[-1] for p in points]
    lines = []
    for idx, skill in enumerate(ranked):
        lines.append({
            "n": skill["name"],
            "c": _COLORS[idx],
            "v": [int((p.get("shares") or {}).get(skill["name"], 0)) for p in points],
        })
    return {"labels": labels, "lines": lines}, len(points)


def _compute(major: str, jobs: list[dict[str, str]], source: dict[str, str]) -> dict[str, Any]:
    n = len(jobs)
    counts = [0] * len(SKILLS)
    per_job: list[tuple[str, list[int]]] = []
    for job in jobs:
        hit = _hits(job["text"])
        for i in hit:
            counts[i] += 1
        per_job.append((_category(job), hit))

    shares = {SKILLS[i]["name"]: round(100 * counts[i] / n) for i in range(len(SKILLS))}
    total_hits = sum(counts)
    low_hits = sum(counts[i] for i, s in enumerate(SKILLS) if s["safi"] < 50)
    low_ratio = round(100 * low_hits / total_hits) if total_hits else 0
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
            "name": cat,
            "growth": heat,
            "lowRisk": low_risk,
            "transfer": transfer,
            "score": score,
            "reason": f"来自 {source['name']} 公开样本 {len(hitlists)} 条 JD；高频技能：{('、'.join(top) if top else '未命中词表技能')}。",
            "sourceRef": f"{source['provider']} · {len(hitlists)} 条公开样本",
            "sample": len(hitlists),
            "basis": "sample_heat",   # growth 字段的语义是「本次样本占比」，不是同比增速
        })
    rows.sort(key=lambda r: (-r["score"], -r["sample"]))
    rows = rows[:6]

    quad = [
        {"n": SKILLS[i]["name"], "a": SKILLS[i]["safi"],
         "d": round(100 * counts[i] / peak), "raw": shares[SKILLS[i]["name"]]}
        for i in sorted(range(len(SKILLS)), key=lambda i: -counts[i])
        if counts[i] > 0
    ][:10]

    _append_snapshot(major, shares, n, source["provider"])
    trend, points = _build_trend(major, shares)
    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # 口径判定由注册表决定，与「是否实时」无关：D 级海外样本禁止一切国内市场的结论
    scope = sources.applicability("D", "global_remote", n)
    active_src = sources.SOURCES[source["provider"]]
    summary = (
        f"本次从 {source['name']} 取得 {n} 条「全球远程岗位」公开样本（{fetched}），按「{major}」检索口径聚合。"
        f"共命中 {sum(1 for c in counts if c > 0)} 项技能，出现率最高的是「{top_name}」{top_share}%；"
        f"低自动化可行性（SAFI < 50）技能占全部命中的 {low_ratio}%。"
        f"数值由确定性管道从 JD 文本统计，不含模型生成。"
        f"注意：这批样本不代表中国境内招聘市场，不能用于判断国内岗位需求、薪资水平、同比变化或「蓝海岗位」；"
        f"仅可作为技能词频与能力升级方向的方向性参考。"
    )
    profile = {
        "name": major,
        "live": True,
        "data_grade": scope["grade"],
        "region": scope["region"],
        "representative_of_cn": scope["representative_of_cn"],
        "applicability": scope,
        "summary": summary,
        "metrics": [
            [str(n), "公开岗位样本"],
            [str(sum(1 for c in counts if c > 0)), "命中技能数"],
            [f"{low_ratio}%", "低风险技能占比"],
        ],
        "jobs": rows,
        "quad": quad,
        "quadNote": SAFI_NOTE + " 纵轴为相对需求热度（以本次样本最高出现率归一化为 100，原始出现率留档于本地快照）；"
                    "右上「低自动化可行性 × 高需求」是优先投入方向。四象限用于定位技能升级方向，不构成职业判决。",
        "trend": trend,
    }
    meta = {
        "source": {
            "name": source["name"], "url": source["url"], "provider": source["provider"],
            "fetched_at": fetched, "samples": n,
            "scope": "全球远程岗位公开 API 样本（region=global_remote / grade=D）；"
                     "不代表中国境内招聘市场；仅用于技能词频与方向性参考",
            "attribution": active_src["attribution"],
            "limits": active_src["limits"],
        },
        "applicability": scope,
        "history": {"points": points, "ready": trend is not None, "needed": 2},
        "note": "单次快照无法计算同比 / 季度增长；表中「样本热度」是本次样本占比，不是增长率。"
                "综合分 = 0.3×样本热度 + 0.4×低风险占比 + 0.3×可迁移性，样本不足 10 条时按样本量线性折减（最低 0.7）。"
                "禁止由此输出国内就业形势、薪资、同比或蓝海岗位结论。"
                "本源须在界面保留来源署名与可点击的原始链接（followed link，不得 nofollow）。",
    }
    return {"profile": profile, "meta": meta}


def get_market_profile(major: str) -> dict[str, Any]:
    major = (major or "").strip()[:40] or "通用专业"
    hint = MAJOR_HINTS.get(major, DEFAULT_HINT)
    key = major + "|" + str(hint.get("tag")) + "|" + str(hint.get("industry"))
    with _LOCK:
        cached = _CACHE.get(key)
    if cached and time.time() - cached[0] < CACHE_TTL:
        result = cached[1]
        result = dict(result)
        result["meta"] = dict(result["meta"], cached=True)
        return result
    jobs, source = _fetch_jobs(hint)
    result = _compute(major, jobs, source)
    result["meta"] = dict(result["meta"], cached=False)
    with _LOCK:
        _CACHE[key] = (time.time(), result)
    return result

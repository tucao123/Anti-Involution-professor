#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · GitHub 公开仓库只读元数据（ROADMAP A3）

严格限制（工程闸门，不依赖「公开」二字做豁免）：
  - 先在 sources.py 登记并通过 assert_fetchable / enforce_policy 的 fail-closed 准入；
  - 匿名 GET，不带任何 token / 登录态；只请求单个公开仓库的 /repos/{o}/{r} 与 /languages；
  - 不读用户资料、不读私有仓库、不分页批量扫描、不绕限频、不缓存原文落盘；
  - 真实 User-Agent、串行、最小间隔 2 秒、每日 60 次上限；
  - 输出只含语言构成与结构元数据，明确标注「样本不代表开发者能力评分」。

仅使用标准库；网络不可用 / 限频 / 仓库不存在时返回可降级的错误码，不抛栈、不重试风暴。
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

import sources

SOURCE_ID = "github_public_repos"
API_ROOT = "https://api.github.com"
MIN_INTERVAL_S = 2.0
DAILY_LIMIT = 60
TIMEOUT_S = 8.0
UA = "ZhiyinResearch/1.0 (public repo metadata only; no auth; no profiling)"
NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}$")

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {"last": 0.0, "day": "", "count": 0}

DISCLAIMER = ("公开仓库的结构与语言元数据样本，不是能力评分、不是求职结论，"
              "也不代表国内就业市场；不得用于个人画像、排名或重新识别。")


class GithubBlocked(RuntimeError):
    """来源闸门拒绝或输入不合法。"""


class GithubUnavailable(RuntimeError):
    """上游不可达 / 限频 / 仓库不存在；调用方应降级。"""


def _pace() -> dict[str, Any]:
    with _LOCK:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if _STATE["day"] != day:
            _STATE["day"], _STATE["count"] = day, 0
        if _STATE["count"] >= DAILY_LIMIT:
            raise GithubUnavailable("DAILY_LIMIT_REACHED")
        wait = MIN_INTERVAL_S - (time.time() - _STATE["last"])
        if wait > 0:
            time.sleep(min(wait, MIN_INTERVAL_S))
        _STATE["last"] = time.time()
        _STATE["count"] += 1
        return {"day": _STATE["day"], "used": _STATE["count"],
                "daily_limit": DAILY_LIMIT, "min_interval_s": MIN_INTERVAL_S}


def _get(path: str) -> dict[str, Any]:
    req = urllib.request.Request(
        API_ROOT + path,
        headers={"User-Agent": UA, "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8", errors="ignore"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise GithubUnavailable("REPO_NOT_FOUND_OR_PRIVATE") from None
        if exc.code in (403, 429):
            raise GithubUnavailable("UPSTREAM_RATE_LIMITED") from None
        raise GithubUnavailable("UPSTREAM_HTTP_%d" % exc.code) from None
    except Exception as exc:
        raise GithubUnavailable("UPSTREAM_UNREACHABLE") from None


def fetch(owner: str, repo: str) -> dict[str, Any]:
    owner, repo = (owner or "").strip(), (repo or "").strip()
    if not NAME_RE.match(owner) or not NAME_RE.match(repo):
        raise GithubBlocked("INVALID_REPO_PATH")
    if owner.lower() in ("..", ".") or repo.lower() in ("..", "."):
        raise GithubBlocked("INVALID_REPO_PATH")

    # fail-closed：未登记 / 未批准 / 被封禁的来源一律不发请求
    try:
        sources.enforce_policy(SOURCE_ID, path="/repos/%s/%s" % (owner, repo))
    except sources.SourceBlocked as exc:
        raise GithubBlocked(str(exc)) from None
    src = sources.SOURCES[SOURCE_ID]

    pace = _pace()
    meta = _get("/repos/%s/%s" % (owner, repo))
    if meta.get("private") is True:
        raise GithubUnavailable("REPO_NOT_FOUND_OR_PRIVATE")
    langs_raw = _get("/repos/%s/%s/languages" % (owner, repo))
    total = sum(int(v) for v in langs_raw.values() if isinstance(v, (int, float))) or 1
    languages = [
        {"name": name, "bytes": int(bytes_), "share": round(int(bytes_) / total, 4)}
        for name, bytes_ in sorted(langs_raw.items(), key=lambda x: -x[1])[:12]
        if isinstance(bytes_, (int, float))
    ]

    rule = sources.GRADE_RULES.get(src["grade"], sources.GRADE_RULES["D"])
    return {
        "ok": True,
        "source_id": SOURCE_ID,
        "grade": src["grade"],
        "region": src["region"],
        "repo": {
            "full_name": meta.get("full_name"),
            "html_url": meta.get("html_url"),
            "default_branch": meta.get("default_branch"),
            "primary_language": meta.get("language"),
            "languages": languages,
            "size_kb": meta.get("size"),
            "stargazers_count": meta.get("stargazers_count"),
            "forks_count": meta.get("forks_count"),
            "open_issues_count": meta.get("open_issues_count"),
            "license": (meta.get("license") or {}).get("spdx_id"),
            "created_at": meta.get("created_at"),
            "pushed_at": meta.get("pushed_at"),
        },
        "attribution": src["attribution"],
        "forbidden_claims": rule["forbidden"],
        "applicability": "仅公开仓库结构元数据 · 技能词频与方向性参考 · 非国内市场",
        "disclaimer": DISCLAIMER,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "rate_limit": pace,
        # 机器可断言的边界声明
        "auth_used": False,
        "user_profile_read": False,
        "private_repo_access": False,
        "bulk_scan": False,
        "raw_response_persisted": False,
    }


def status() -> dict[str, Any]:
    src = sources.SOURCES.get(SOURCE_ID, {})
    with _LOCK:
        used, day = _STATE["count"], _STATE["day"]
    return {
        "ok": True,
        "source_id": SOURCE_ID,
        "registered": bool(src),
        "legal_status": src.get("legal_status"),
        "grade": src.get("grade"),
        "anonymously_only": True,
        "min_interval_s": MIN_INTERVAL_S,
        "daily_limit": DAILY_LIMIT,
        "used_today": used if day == datetime.now(timezone.utc).strftime("%Y-%m-%d") else 0,
        "endpoints": ["/repos/{owner}/{repo}", "/repos/{owner}/{repo}/languages"],
        "never_read": ["user profile", "private repo", "code content", "issues/comments"],
        "disclaimer": DISCLAIMER,
    }

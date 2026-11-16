#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · B2 真实截图执行体（2026-11-16，测试沙盒专用）

所有者本轮指令：「截图爬取测试网站 https://books.toscrape.com/」「接入 B2 真实执行体」。
本模块是**真的会发起 HTTP 请求并截图**的执行体，但作用域被硬性限定：

    允许：本地自建测试页 / 经核实明确允许爬取的练习沙盒（books.toscrape.com）
    禁止：招聘网站、任何真实业务站点、任何其他 host、任何登录墙 / 反爬绕过

七道准入闸门，缺一即拒绝，且**不发起任何请求**：
  1. B2 所有者自担风险回执有效（.privacy/approvals/B2.json，只读）
  2. 管理员逐次手动开关已开启（默认关闭，跑完自动关闭）
  3. 调用方显式 operator_ack
  4. 全项目「仅研究学习 / 禁止商业行为」条款生效
  5. enforce_pace() 严格限速契约通过（并发 1 / burst 1 / 每页 ≥15s / 每日 60 / 批次 10）
  6. 每个目标都过 is_allowed_test_target() 白名单
  7. 站点 robots.txt best-effort 检查；明确 Disallow 目标路径即拒绝

执行期约束：真实可联络 UA、单域名串行、页间真实 sleep、每页 append-only 留痕、
位图读完字节立即删除（不落盘保留、不上传、不 OCR、不入 RAG、不外发）、
不解析页面文本、不产出任何生产数据。伪装身份 / UA 轮换 / 抖动躲风控 /
WAF 绕过仍永久禁止。

**如实标注**：执行体存在 ≠ 生产采集闸门放开。站点书面许可函与法务书面结论
仍未回，sources.py 中 research_capture.legal_status 仍为 pending_legal，
production_capture_unlocked 恒为 False。练习沙盒的公开声明不是书面许可函。

工程过程控制，不构成法律意见。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import research_capture as rc
import sources
import usage_policy

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "tests" / "out"
REPORT = OUT / "capture_executor_report.json"

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

EXECUTOR_POLICY: dict[str, Any] = {
    "id": "B2-SANDBOX-EXECUTOR-2026-11-16",
    "implemented": True,
    "scope": "test_sandbox_only",
    "production_capture_unlocked": False,
    "allowed_target_kinds": ("local_fixture", "external_scraping_sandbox"),
    "allowed_sandboxes": list(rc.SANDBOX_SITES),
    "recruitment_sites_forbidden": True,
    "real_business_sites_forbidden": True,
    "user_agent": rc.USER_AGENT,
    "ua_rotation": False,
    "ip_rotation": False,
    "identity_spoofing": False,
    "concurrency": 1,
    "burst": 1,
    "per_page_audit_log": True,
    "bitmap_retention": "none（读完字节立即删除）",
    "page_text_parsed": False,
    "ocr_or_rag_ingest": False,
    "production_data_produced": False,
    "default_pages": 3,
    "max_pages_per_run": 3,
    "manual_toggle_required_each_time": True,
    "usage_restriction": usage_policy.scope_marker(),
    "still_blocked_for_production": [
        "站点书面许可 / 合作函未回",
        "法务书面结论未回",
        "sources.py 中 research_capture.legal_status 仍为 pending_legal",
    ],
    "disclaimer": "练习沙盒的公开声明允许爬取不等于站点书面许可函；"
                  "项目所有者自担风险放行不等于法务书面结论。两者仍待回。",
}


class ExecutorBlocked(RuntimeError):
    """准入闸门未通过：不发起任何请求。"""


def find_chrome() -> str | None:
    env = os.environ.get("ZHIYIN_CHROME")
    if env and Path(env).is_file():
        return env
    return next((p for p in CHROME_CANDIDATES if Path(p).is_file()), None)


def _robots_check(url: str) -> dict[str, Any]:
    """best-effort 读取 robots.txt；明确 Disallow 目标路径即拒绝。

    无 robots.txt / 超时 / 4xx 时不视为许可，只如实记录 unavailable，
    继续依赖站点公开声明与白名单硬闸门。
    """
    from urllib.parse import urlparse
    p = urlparse(url)
    robots_url = "%s://%s/robots.txt" % (p.scheme, p.netloc)
    result: dict[str, Any] = {"checked": True, "url": robots_url,
                              "available": False, "disallow_all": False,
                              "path_disallowed": False, "note": ""}
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": rc.USER_AGENT})
        with urllib.request.urlopen(req, timeout=6) as resp:
            body = resp.read(20000).decode("utf-8", "replace")
        result["available"] = True
        rules = [ln.strip() for ln in body.splitlines()
                 if ln.strip().lower().startswith("disallow:")]
        for rule in rules:
            value = rule.split(":", 1)[1].strip()
            if value == "/":
                result["disallow_all"] = True
            elif value and p.path.startswith(value):
                result["path_disallowed"] = True
        result["note"] = "已读取 robots.txt 并核对 Disallow"
    except urllib.error.HTTPError as exc:
        result["note"] = "robots.txt HTTP_%d（未视为许可）" % exc.code
    except Exception as exc:
        result["note"] = "robots.txt 不可读：%s（未视为许可）" % type(exc).__name__
    return result


def readiness(pages: int, operator_ack: bool, urls: list[str]) -> dict[str, Any]:
    """七道准入闸门，**纯校验，不发起任何请求**。"""
    gates: dict[str, bool] = {}
    gates["b2_owner_risk_release"] = bool(rc.owner_release_state()["released"])
    gates["admin_manual_toggle"] = bool(rc.admin_status()["toggle"]["enabled"])
    gates["operator_ack"] = bool(operator_ack)
    gates["research_only_clause"] = bool(usage_policy.as_dict().get("commercial_use") is False)
    try:
        rc.enforce_pace(len(urls))
        gates["strict_pace_contract"] = True
    except rc.PaceViolation:
        gates["strict_pace_contract"] = False
    checks = [rc.is_allowed_test_target(u) for u in urls]
    gates["all_targets_allowlisted"] = bool(urls) and all(c["allowed"] for c in checks)
    gates["chrome_available"] = find_chrome() is not None
    return {
        "ok": all(gates.values()),
        "gates": gates,
        "pages": len(urls),
        "target_checks": checks,
        "production_capture_unlocked": False,
        "requests_issued_by_readiness": 0,
        "executor_policy": EXECUTOR_POLICY["id"],
    }


def run(pages: int = 3, *, operator_ack: bool = False,
        urls: list[str] | None = None, chrome_path: str | None = None,
        robots_check: bool = True) -> dict[str, Any]:
    """对练习沙盒逐页截图。失败也如实写报告，绝不假装成功。

    注意：本函数**真的会发起网络请求**，只在七道闸门全过后才会走到这一步。
    """
    n = max(1, min(int(pages), EXECUTOR_POLICY["max_pages_per_run"]))
    targets = [str(u) for u in urls] if urls else rc.sandbox_page_urls(n)[:n]
    n = len(targets)
    ready = readiness(n, operator_ack, targets)
    if not ready["ok"]:
        missing = [k for k, v in ready["gates"].items() if not v]
        raise ExecutorBlocked("EXECUTOR_GATE_CLOSED:" + ",".join(missing))

    pace = rc.enforce_pace(n)
    interval = pace["interval_s"]
    chrome = chrome_path or find_chrome()
    assert chrome  # readiness 已校验
    OUT.mkdir(parents=True, exist_ok=True)

    robots = _robots_check(targets[0]) if robots_check else {
        "checked": False, "note": "调用方显式跳过 robots 检查"}
    if robots.get("disallow_all") or robots.get("path_disallowed"):
        raise ExecutorBlocked("ROBOTS_DISALLOW")
    # 只要尝试过 robots 请求，就把它计入请求序列；首个页面前由循环统一等待一个完整间隔
    external_requests = 1 if robots.get("checked") else 0

    shots: list[dict[str, Any]] = []
    stamps: list[float] = []
    started = time.monotonic()
    for i, target in enumerate(targets, 1):
        if i > 1 or external_requests:
            time.sleep(interval)  # 真实等待：单域名串行、无并发、无抖动
        shot_path = OUT / ("_sandbox_shot_%d.png" % i)
        cmd = [
            chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", "--disable-dev-shm-usage",
            "--window-size=1200,900", "--virtual-time-budget=3000",
            "--user-agent=" + rc.USER_AGENT,
            "--screenshot=" + str(shot_path), target,
        ]
        err = ""
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=90)
            if proc.returncode != 0:
                err = "CHROME_EXIT_%d" % proc.returncode
        except subprocess.TimeoutExpired:
            err = "CHROME_TIMEOUT"
        size = shot_path.stat().st_size if shot_path.is_file() else 0
        shot_path.unlink(missing_ok=True)  # 位图立即释放
        external_requests += 1
        stamps.append(time.monotonic())
        sources.log_assessment("sandbox_capture_page", rc.SOURCE_ID, {
            "page": i, "host": urllib.parse.urlparse(target).hostname,
            "interval_s": interval, "bytes": size, "bitmap_deleted": True,
            "text_parsed": False, "ocr_or_rag": False, "production": False,
            "user_agent": rc.USER_AGENT, "error": err or None,
        })
        shots.append({
            "page": i, "target": target, "bytes": size,
            "bitmap_deleted": True, "error": err or None,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

    actual = [round(stamps[i] - stamps[i - 1], 2) for i in range(1, len(stamps))]
    ok = (all(s["bytes"] > 0 and not s["error"] for s in shots)
          and all(g + 0.05 >= interval for g in actual))
    report = {
        "ok": ok,
        "executor_id": EXECUTOR_POLICY["id"],
        "executor": "real_headless_screenshot",
        "scope": "test_sandbox_only",
        "pages": n,
        "targets": targets,
        "sandbox_site": "books.toscrape.com",
        "site_evidence": rc.SANDBOX_SITES["books.toscrape.com"]["evidence_quote"],
        "site_evidence_url": rc.SANDBOX_SITES["books.toscrape.com"]["evidence_url"],
        "site_written_license": False,
        "contract_id": pace["contract_id"],
        "interval_s": interval,
        "actual_intervals_s": actual,
        "pace_compliant": bool(all(g + 0.05 >= interval for g in actual)),
        "elapsed_s": round(time.monotonic() - started, 2),
        "user_agent": rc.USER_AGENT,
        "ua_rotation": False, "ip_rotation": False, "identity_spoofing": False,
        "robots": robots,
        "external_sandbox_requests": external_requests,
        "third_party_production_requests": 0,
        "recruitment_sites_touched": 0,
        "production_capture_unlocked": False,
        "bitmap_retention": "none",
        "page_text_parsed": False,
        "ocr_or_rag_ingest": False,
        "production_data_produced": False,
        "shots": shots,
        "usage_restriction": usage_policy.CLAUSE_ZH,
        "note": "真实执行体只对公开声明允许爬取的练习沙盒逐页截图；位图读完字节即删，"
                "不解析文本、不 OCR、不入 RAG、不产出生产数据。招聘网站与任何真实业务站点"
                "在代码层被拒绝。生产采集四重闸门一格未放。",
        "disclaimer": EXECUTOR_POLICY["disclaimer"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def status() -> dict[str, Any]:
    """只读状态：执行体策略 + 最近一次实跑报告摘要（不返回位图 / 页面文本）。"""
    out: dict[str, Any] = {
        "ok": True,
        "policy": EXECUTOR_POLICY,
        "report_present": False,
        "requests_issued": 0,              # 生产采集请求，恒 0
        "production_capture_unlocked": False,
        "recruitment_sites_touched": 0,
        "legal_review": "pending",
    }
    if REPORT.is_file():
        try:
            r = json.loads(REPORT.read_text(encoding="utf-8"))
            out.update({
                "report_present": True,
                "last_run_ok": r.get("ok"),
                "last_run_at": r.get("generated_at"),
                "sandbox_requests_total": r.get("external_sandbox_requests", 0),
                "last_intervals_s": r.get("actual_intervals_s"),
                "last_targets": [str(t) for t in r.get("targets", [])],
                "bitmap_retention": r.get("bitmap_retention"),
                "production_data_produced": r.get("production_data_produced", False),
            })
        except (OSError, ValueError):
            out["report_present"] = False
            out["report_error"] = "REPORT_UNREADABLE"
    return out

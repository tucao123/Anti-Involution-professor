#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 ZHIYIN · B2 生产采集执行体（2026-11-16 接入）

所有者本轮指令：「接入 B2 生产执行体」。本模块把执行体从「仅练习沙盒」
扩展为**具备生产采集能力**的实现，但作用域不是「任何站点」，而是：

    只允许：已取得**站点书面许可函**、且已取得**法务书面结论**、
            且注册表经代码评审改为 approved 的目标。

与 `capture_executor.py`（练习沙盒）的关键区别：沙盒靠「站点公开声明允许爬取」
即可测试；生产必须靠**第三方签发的书面许可**。练习沙盒的公开声明、
项目所有者自担风险放行、`USAGE-RESTRICTION` 条款，三者都不能替代许可函或法务结论。

九道准入闸门，缺一即拒绝且**不发起任何请求**：
  1. B2 所有者自担风险回执有效（.privacy/approvals/B2.json，只读）
  2. 管理员逐次手动开关已开启（默认关闭，跑完自动关闭）
  3. 调用方显式 operator_ack
  4. 全项目「仅研究学习 / 禁止商业行为」条款生效
  5. enforce_pace() 严格限速契约通过
  6. 每个目标都匹配到一份有效的站点书面许可回执
  7. 法务书面结论回执有效（.privacy/approvals/B2-legal.json）
  8. sources.py 注册表 research_capture.legal_status == approved
  9. headless Chrome 可用

许可回执由**人类线下放置**到 `.privacy/site_licenses/*.json`（gitignored，
工程侧只读、不提供写入 API）。任何字段缺失 / 自签发 / 过期 / 格式错误一律
fail-closed。默认目录下没有任何回执，因此生产采集默认不解锁。

执行期约束（与沙盒一致，一格不放宽）：真实可联络 UA、单域名串行、页间真实
sleep ≥15s、robots.txt best-effort、每页 append-only 留痕、位图读完字节立即
删除。伪装身份 / UA 轮换 / IP 轮换 / 随机抖动躲风控 / 绕过验证码 / WAF /
登录墙 / 字体反爬仍**永久禁止**，本模块不提供任何此类能力。

工程过程控制，不构成法律意见。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import capture_executor as sandbox
import research_capture as rc
import sources
import usage_policy

ROOT = Path(__file__).resolve().parent
LICENSE_DIR = ROOT / ".privacy" / "site_licenses"
LEGAL_FILE = ROOT / ".privacy" / "approvals" / "B2-legal.json"
OUT = ROOT / "tests" / "out"
REPORT = OUT / "production_capture_report.json"

POLICY_ID = "B2-PRODUCTION-EXECUTOR-2026-11-16"

# 站点许可函禁止由项目自己签发：这是「第三方许可」与「自担风险决策」的分界线。
SELF_ISSUER_MARKERS = ("项目所有者", "毕设作者", "project owner", "owner", "self")

PRODUCTION_POLICY: dict[str, Any] = {
    "id": POLICY_ID,
    "implemented": True,
    "scope": "licensed_sites_only",
    "license_dir": ".privacy/site_licenses/ (gitignored, human-placed, read-only)",
    "legal_conclusion_file": ".privacy/approvals/B2-legal.json (gitignored, human-placed)",
    "license_writable_api": False,
    "recruitment_sites": "allowed_only_with_site_written_license",
    "unlicensed_targets_forbidden": True,
    "user_agent": rc.USER_AGENT,
    "ua_rotation": False,
    "ip_rotation": False,
    "identity_spoofing": False,
    "evasion_techniques": "forbidden",
    "human_scale_pacing": "enforced",
    "concurrency": 1,
    "burst": 1,
    "per_page_audit_log": True,
    "bitmap_retention": "none（读完字节立即删除）",
    "page_text_parsed": False,
    "ocr_or_rag_ingest": False,
    "production_data_produced": False,
    "max_pages_per_run": 10,
    "manual_toggle_required_each_time": True,
    "usage_restriction": usage_policy.scope_marker(),
    "permanently_forbidden": rc.PERMANENTLY_FORBIDDEN,
    "disclaimer": "站点书面许可函与法务书面结论仍须由第三方签发；"
                  "项目所有者自担风险、练习沙盒公开声明、研究学习条款均不构成豁免。",
}


class ProductionBlocked(RuntimeError):
    """生产准入闸门未通过：不发起任何请求。"""


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def load_site_licenses(directory: Path | None = None) -> dict[str, Any]:
    """只读扫描站点书面许可回执。逐份校验，任何不合格项单独标记、不参与解锁。"""
    d = directory or LICENSE_DIR
    out: dict[str, Any] = {
        "directory": str(d.relative_to(ROOT)) if d.is_relative_to(ROOT) else str(d),
        "directory_present": d.is_dir(),
        "files_seen": 0,
        "valid": [],
        "invalid": [],
    }
    if not d.is_dir():
        out["reason"] = "NO_SITE_LICENSE_DIR"
        return out
    for path in sorted(d.glob("*.json")):
        out["files_seen"] += 1
        name = path.name
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out["invalid"].append({"file": name, "reason": "UNREADABLE_JSON"})
            continue
        if not isinstance(raw, dict):
            out["invalid"].append({"file": name, "reason": "NOT_OBJECT"})
            continue
        reasons: list[str] = []
        issuer = str(raw.get("issuer", "")).strip()
        if not str(raw.get("id", "")).strip():
            reasons.append("ID_MISSING")
        if not str(raw.get("license_ref", "")).strip():
            reasons.append("LICENSE_REF_MISSING")
        if not issuer or not str(raw.get("signed_by", "")).strip():
            reasons.append("ISSUER_OR_SIGNER_MISSING")
        if any(m in issuer.lower() for m in SELF_ISSUER_MARKERS):
            reasons.append("SELF_ISSUED_LICENSE_FORBIDDEN")
        if raw.get("self_issued") is not False:
            reasons.append("SELF_ISSUED_FLAG_MUST_BE_FALSE")
        if raw.get("decision") != "approved":
            reasons.append("DECISION_NOT_APPROVING")
        if raw.get("conditions_met") is not True:
            reasons.append("CONDITIONS_NOT_MET")
        if raw.get("legal_opinion") is not False:
            reasons.append("MUST_NOT_CLAIM_LEGAL_OPINION")
        issued, until = _day(raw.get("issued_at")), _day(raw.get("valid_until"))
        if issued is None or until is None:
            reasons.append("DATE_INVALID")
        elif issued > date.today() or until < date.today():
            reasons.append("EXPIRED_OR_NOT_YET_EFFECTIVE")
        scope = raw.get("scope")
        if not isinstance(scope, dict):
            reasons.append("SCOPE_MISSING")
            scope = {}
        hosts = scope.get("allowed_hosts")
        prefixes = scope.get("allowed_path_prefixes")
        if not isinstance(hosts, list) or not hosts or not all(
                isinstance(h, str) and h.strip() for h in hosts):
            reasons.append("ALLOWED_HOSTS_INVALID")
            hosts = []
        if not isinstance(prefixes, list) or not prefixes or not all(
                isinstance(p, str) and p.startswith("/") for p in prefixes):
            reasons.append("ALLOWED_PATH_PREFIXES_INVALID")
            prefixes = []
        grades = scope.get("data_grade", ["D"])
        if not isinstance(grades, list) or any(str(g).upper() != "D" for g in grades):
            reasons.append("ONLY_GRADE_D_ALLOWED")
        if reasons:
            out["invalid"].append({"file": name, "reason": ";".join(reasons)})
            continue
        out["valid"].append({
            "file": name,
            "license_ref": str(raw["license_ref"]),
            "issuer": issuer,
            "valid_until": str(raw["valid_until"]),
            "hosts": [h.strip().lower() for h in hosts],
            "path_prefixes": list(prefixes),
            "purpose": str(scope.get("purpose", "")),
        })
    out["reason"] = ("NO_VALID_LICENSE" if not out["valid"] else "OK")
    return out


def legal_conclusion_state() -> dict[str, Any]:
    """法务书面结论回执。必须第三方签发、明确 legal_opinion=true / received。"""
    base: dict[str, Any] = {"present": False, "received": False,
                            "legal_review": "pending", "reason": "LEGAL_CONCLUSION_MISSING"}
    if not LEGAL_FILE.is_file():
        return base
    base["present"] = True
    try:
        raw = json.loads(LEGAL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**base, "reason": "LEGAL_CONCLUSION_UNREADABLE"}
    if not isinstance(raw, dict):
        return {**base, "reason": "LEGAL_CONCLUSION_NOT_OBJECT"}
    reasons: list[str] = []
    issuer = str(raw.get("issuer", "")).strip()
    if not str(raw.get("conclusion_ref", "")).strip():
        reasons.append("CONCLUSION_REF_MISSING")
    if not issuer or any(m in issuer.lower() for m in SELF_ISSUER_MARKERS):
        reasons.append("LEGAL_ISSUER_INVALID")
    if raw.get("decision") != "approved":
        reasons.append("DECISION_NOT_APPROVING")
    if raw.get("legal_opinion") is not True or str(raw.get("legal_review", "")) != "received":
        reasons.append("NOT_A_RECEIVED_LEGAL_OPINION")
    until = _day(raw.get("valid_until"))
    if until is None or until < date.today():
        reasons.append("EXPIRED_OR_DATE_INVALID")
    scope = raw.get("scope")
    if not isinstance(scope, dict) or "production_capture" not in str(
            scope.get("items", scope.get("applies_to", ""))):
        reasons.append("SCOPE_MUST_COVER_PRODUCTION_CAPTURE")
    return {
        "present": True,
        "received": not reasons,
        "legal_review": "received" if not reasons else "pending",
        "reason": "OK" if not reasons else ";".join(reasons),
        "conclusion_ref": str(raw.get("conclusion_ref", ""))[:64],
        "issuer": issuer[:64],
    }


def is_allowed_production_target(url: str, licenses: list[dict[str, Any]] | None = None
                                 ) -> dict[str, Any]:
    """生产目标判定：必须被有效站点书面许可的 host + path 前缀覆盖。"""
    pool = licenses if licenses is not None else load_site_licenses()["valid"]
    raw = str(url or "").strip()
    try:
        p = urllib.parse.urlparse(raw)
    except ValueError:
        return {"allowed": False, "reason": "URL_INVALID", "host": ""}
    host = (p.hostname or "").lower()
    if p.scheme.lower() != "https":
        return {"allowed": False, "reason": "HTTPS_REQUIRED", "host": host}
    if p.username or p.password:
        return {"allowed": False, "reason": "USERINFO_FORBIDDEN", "host": host}
    if p.query or p.fragment:
        return {"allowed": False, "reason": "QUERY_OR_FRAGMENT_FORBIDDEN", "host": host}
    if not host:
        return {"allowed": False, "reason": "HOST_MISSING", "host": host}
    for lic in pool:
        if host in lic["hosts"] and any(p.path.startswith(pre) for pre in lic["path_prefixes"]):
            return {"allowed": True, "reason": "OK_SITE_WRITTEN_LICENSE", "host": host,
                    "license_ref": lic["license_ref"], "issuer": lic["issuer"]}
    if any(k in raw.lower() for k in rc.FORBIDDEN_TARGET_KEYWORDS):
        return {"allowed": False, "reason": "RECRUITMENT_SITE_REQUIRES_WRITTEN_LICENSE",
                "host": host}
    return {"allowed": False, "reason": "NO_VALID_SITE_LICENSE_FOR_TARGET", "host": host}


def registry_state() -> dict[str, Any]:
    src = sources.SOURCES.get(rc.SOURCE_ID, {})
    status = str(src.get("legal_status", "unknown"))
    return {"source_id": rc.SOURCE_ID, "legal_status": status,
            "approved": status == "approved",
            "note": "注册表变更须经代码评审，工程侧不提供自我改写入口"}


def production_readiness(urls: list[str], operator_ack: bool,
                         licenses: dict[str, Any] | None = None,
                         legal: dict[str, Any] | None = None) -> dict[str, Any]:
    """九道闸门，**纯校验，不发起任何请求、不加载模型、不写报告**。"""
    lic = licenses if licenses is not None else load_site_licenses()
    law = legal if legal is not None else legal_conclusion_state()
    reg = registry_state()
    targets = [str(u) for u in urls]
    checks = [is_allowed_production_target(u, lic["valid"]) for u in targets]
    gates: dict[str, bool] = {
        "b2_owner_risk_release": bool(rc.owner_release_state()["released"]),
        "admin_manual_toggle": bool(rc.admin_status()["toggle"]["enabled"]),
        "operator_ack": bool(operator_ack),
        "research_only_clause": usage_policy.as_dict().get("commercial_use") is False,
        "site_written_licenses": bool(lic["valid"]) and bool(targets)
                                 and all(c["allowed"] for c in checks),
        "legal_written_conclusion": bool(law["received"]),
        "registry_approved": bool(reg["approved"]),
        "chrome_available": sandbox.find_chrome() is not None,
    }
    try:
        # 空目标列表只用于状态核算，按 1 页评估契约是否可用（不代表要发起请求）
        rc.enforce_pace(max(1, len(targets)))
        gates["strict_pace_contract"] = True
    except rc.PaceViolation:
        gates["strict_pace_contract"] = False
    persistent = ("b2_owner_risk_release", "research_only_clause", "strict_pace_contract",
                  "site_written_licenses", "legal_written_conclusion",
                  "registry_approved", "chrome_available")
    prerequisites = all(gates[k] for k in persistent)
    return {
        "ok": all(gates.values()),
        "policy_id": POLICY_ID,
        "gates": gates,
        "pages": len(targets),
        "target_checks": checks,
        "production_prerequisites_met": prerequisites,
        "production_capture_unlocked": all(gates.values()),
        "site_licenses": {"files_seen": lic["files_seen"],
                          "valid": len(lic["valid"]), "invalid": len(lic["invalid"]),
                          "reason": lic["reason"]},
        "legal_conclusion": {"received": law["received"], "reason": law["reason"]},
        "registry": reg,
        "blocked_by": [k for k, v in gates.items() if not v],
        "requests_issued_by_readiness": 0,
        "disclaimer": PRODUCTION_POLICY["disclaimer"],
    }


def production_run(urls: list[str], *, operator_ack: bool = False,
                   robots_check: bool = True) -> dict[str, Any]:
    """对已获书面许可的目标逐页受控截图。闸门未全过即抛，不发起任何请求。

    注意：本函数**真的会发起网络请求**，只在九道闸门全过后才会走到这一步。
    """
    ready = production_readiness(urls, operator_ack)
    if not ready["ok"]:
        raise ProductionBlocked("PRODUCTION_GATE_CLOSED:" + ",".join(ready["blocked_by"]))
    pace = rc.enforce_pace(len(urls))
    interval = pace["interval_s"]
    chrome = sandbox.find_chrome()
    assert chrome
    OUT.mkdir(parents=True, exist_ok=True)
    robots = sandbox._robots_check(urls[0]) if robots_check else {
        "checked": False, "note": "调用方显式跳过 robots 检查"}
    if robots.get("disallow_all") or robots.get("path_disallowed"):
        raise ProductionBlocked("ROBOTS_DISALLOW")

    external = 1 if robots.get("checked") else 0
    shots: list[dict[str, Any]] = []
    stamps: list[float] = []
    started = time.monotonic()
    for i, target in enumerate(urls, 1):
        if i > 1 or external:
            time.sleep(interval)
        path = OUT / ("_production_shot_%d.png" % i)
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--hide-scrollbars", "--disable-dev-shm-usage",
               "--window-size=1200,900", "--virtual-time-budget=3000",
               "--user-agent=" + rc.USER_AGENT,
               "--screenshot=" + str(path), target]
        err = ""
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=90)
            if proc.returncode != 0:
                err = "CHROME_EXIT_%d" % proc.returncode
        except subprocess.TimeoutExpired:
            err = "CHROME_TIMEOUT"
        size = path.stat().st_size if path.is_file() else 0
        path.unlink(missing_ok=True)
        external += 1
        stamps.append(time.monotonic())
        host = urllib.parse.urlparse(target).hostname or ""
        sources.log_assessment("production_capture_page", rc.SOURCE_ID, {
            "page": i, "host": host, "interval_s": interval, "bytes": size,
            "bitmap_deleted": True, "text_parsed": False, "ocr_or_rag": False,
            "user_agent": rc.USER_AGENT, "error": err or None,
            "site_written_license": True, "legal_conclusion_received": True,
        })
        shots.append({"page": i, "target": target, "host": host, "bytes": size,
                      "bitmap_deleted": True, "error": err or None,
                      "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    actual = [round(stamps[i] - stamps[i - 1], 2) for i in range(1, len(stamps))]
    ok = (all(s["bytes"] > 0 and not s["error"] for s in shots)
          and all(g + 0.05 >= interval for g in actual))
    report = {
        "ok": ok, "policy_id": POLICY_ID, "executor": "real_headless_screenshot",
        "scope": "licensed_sites_only", "pages": len(urls), "targets": list(urls),
        "interval_s": interval, "actual_intervals_s": actual,
        "pace_compliant": bool(all(g + 0.05 >= interval for g in actual)),
        "elapsed_s": round(time.monotonic() - started, 2),
        "user_agent": rc.USER_AGENT, "ua_rotation": False, "ip_rotation": False,
        "identity_spoofing": False, "robots": robots,
        "external_requests": external,
        "unlicensed_targets_touched": 0,
        "bitmap_retention": "none", "page_text_parsed": False,
        "ocr_or_rag_ingest": False, "production_data_produced": False,
        "shots": shots, "usage_restriction": usage_policy.CLAUSE_ZH,
        "note": "生产执行体只对持有效站点书面许可函且法务结论已回的目标逐页受控截图；"
                "位图读完字节即删，不解析文本、不 OCR、不入 RAG。自动入库链路未启用。",
        "disclaimer": PRODUCTION_POLICY["disclaimer"],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def status() -> dict[str, Any]:
    """只读状态：不联网、不截图、不加载模型、不写入。"""
    lic = load_site_licenses()
    law = legal_conclusion_state()
    reg = registry_state()
    ready = production_readiness([], False, lic, law)
    out: dict[str, Any] = {
        "ok": True,
        "policy": PRODUCTION_POLICY,
        "policy_id": POLICY_ID,
        "production_executor_implemented": True,
        "production_prerequisites_met": ready["production_prerequisites_met"],
        "production_capture_unlocked": False,
        "requests_issued": 0,
        "unlicensed_targets_touched": 0,
        "gates_without_toggle": ready["gates"],
        "blocked_by": ready["blocked_by"],
        "site_licenses": {
            "directory": lic["directory"], "directory_present": lic["directory_present"],
            "files_seen": lic["files_seen"], "valid": len(lic["valid"]),
            "invalid": len(lic["invalid"]),
            "valid_licenses": [{"license_ref": v["license_ref"], "issuer": v["issuer"],
                                "hosts": v["hosts"], "valid_until": v["valid_until"]}
                               for v in lic["valid"]],
            "invalid_reasons": lic["invalid"], "reason": lic["reason"],
        },
        "legal_conclusion": law,
        "registry": reg,
        "owner_risk_release": rc.owner_release_state(),
        "usage_restriction": usage_policy.scope_marker(),
        "permanently_forbidden": rc.PERMANENTLY_FORBIDDEN,
        "report_present": REPORT.is_file(),
        "legal_review": "pending" if not law["received"] else "received",
        "note": "生产能力已接入，但站点书面许可函与法务书面结论由第三方签发，"
                "工程侧不提供自我授权入口；当前默认不解锁、不发起任何生产请求。",
        "disclaimer": PRODUCTION_POLICY["disclaimer"],
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if REPORT.is_file():
        try:
            r = json.loads(REPORT.read_text(encoding="utf-8"))
            out.update({"last_run_ok": r.get("ok"), "last_run_at": r.get("generated_at"),
                        "last_run_pages": r.get("pages"),
                        "last_intervals_s": r.get("actual_intervals_s")})
        except (OSError, ValueError):
            out["report_error"] = "REPORT_UNREADABLE"
    return out

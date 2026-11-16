#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B2 生产执行体单元回归：许可回执驱动 / 九道闸门 / fail-closed / 零真实请求。

刻意不接入任何网络：所有许可与法务结论都在临时目录构造，
注册表状态用 mock 临时替换并在 finally 恢复。
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import production_capture as pc
import research_capture as rc
import sources

PASS = 0
FAIL = 0


def check(cond: bool, name: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name)


def license_doc(**over: object) -> dict:
    today = date.today()
    doc = {
        "id": "SITE-LICENSE-TEST",
        "license_ref": "LIC-2026-TEST-001",
        "issuer": "Example Jobs Ltd（站点运营方）",
        "signed_by": "站点法务代表",
        "issued_at": (today - timedelta(days=2)).isoformat(),
        "valid_until": (today + timedelta(days=180)).isoformat(),
        "decision": "approved",
        "self_issued": False,
        "conditions_met": True,
        "legal_opinion": False,
        "scope": {
            "allowed_hosts": ["jobs.example.com"],
            "allowed_path_prefixes": ["/jobs/"],
            "purpose": "research_sample_collection",
            "data_grade": ["D"],
        },
    }
    doc.update(over)
    return doc


def legal_doc(**over: object) -> dict:
    today = date.today()
    doc = {
        "id": "B2-LEGAL-TEST",
        "conclusion_ref": "LEGAL-2026-TEST-001",
        "issuer": "法务部门（书面结论）",
        "issued_at": (today - timedelta(days=1)).isoformat(),
        "valid_until": (today + timedelta(days=365)).isoformat(),
        "decision": "approved",
        "legal_opinion": True,
        "legal_review": "received",
        "scope": {"items": ["production_capture", "B2"]},
    }
    doc.update(over)
    return doc


print("=== B2 生产执行体单元回归 ===")

# ---- 1. 默认状态：能力已接入，但默认不解锁、零请求 ----
s = pc.status()
check(s["production_executor_implemented"] is True, "生产执行体能力已接入")
check(s["production_capture_unlocked"] is False, "默认 production_capture_unlocked=false")
check(s["requests_issued"] == 0 and s["unlicensed_targets_touched"] == 0,
      "默认零生产请求 / 零无许可目标接触")
check(s["legal_review"] == "pending", "法务结论默认 pending")
check(s["registry"]["legal_status"] == "pending_legal", "注册表默认 pending_legal")
check(s["policy"]["license_writable_api"] is False, "工程侧不提供许可写入 API")
check(s["policy"]["evasion_techniques"] == "forbidden", "规避检测手段仍永久禁止")
check(s["policy"]["production_data_produced"] is False, "默认不产出生产数据")

# ---- 2. 无许可时目标一律拒绝 ----
cases = [
    ("https://www.zhipin.com/jobs", "RECRUITMENT_SITE_REQUIRES_WRITTEN_LICENSE"),
    ("https://example.com/jobs/1", "NO_VALID_SITE_LICENSE_FOR_TARGET"),
    ("http://jobs.example.com/jobs/1", "HTTPS_REQUIRED"),
    ("https://jobs.example.com/jobs/1?q=1", "QUERY_OR_FRAGMENT_FORBIDDEN"),
    ("https://u:p@jobs.example.com/jobs/1", "USERINFO_FORBIDDEN"),
]
for url, expected in cases:
    check(pc.is_allowed_production_target(url, [])["reason"] == expected,
          "无许可拒绝：%s" % expected)
check(pc.load_site_licenses()["valid"] == [], "默认许可目录不存在或为空")

# ---- 3. 许可回执逐份校验（临时目录，不碰 .privacy） ----
with tempfile.TemporaryDirectory() as td:
    d = Path(td)
    docs = {
        "valid.json": license_doc(),
        "self_issued.json": license_doc(issuer="项目所有者（毕设作者）"),
        "expired.json": license_doc(valid_until=(date.today() - timedelta(days=1)).isoformat()),
        "pending.json": license_doc(decision="pending"),
        "claims_legal.json": license_doc(legal_opinion=True),
        "grade_a.json": license_doc(scope={
            "allowed_hosts": ["jobs.example.com"], "allowed_path_prefixes": ["/jobs/"],
            "purpose": "x", "data_grade": ["A"]}),
        "no_hosts.json": license_doc(scope={
            "allowed_hosts": [], "allowed_path_prefixes": ["/jobs/"],
            "purpose": "x", "data_grade": ["D"]}),
        "no_ref.json": license_doc(license_ref=""),
        "bad_json.json": None,
    }
    for name, doc in docs.items():
        (d / name).write_text("{broken" if doc is None else
                              json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    loaded = pc.load_site_licenses(d)
    check(loaded["files_seen"] == len(docs), "临时目录全部回执被扫描")
    check(len(loaded["valid"]) == 1 and loaded["valid"][0]["file"] == "valid.json",
          "仅格式完备的一份许可有效")
    reasons = {x["file"]: x["reason"] for x in loaded["invalid"]}
    check("SELF_ISSUED_LICENSE_FORBIDDEN" in reasons["self_issued.json"],
          "自签发许可被拒（站点许可不得由项目自己签）")
    check("EXPIRED_OR_NOT_YET_EFFECTIVE" in reasons["expired.json"], "过期许可被拒")
    check("DECISION_NOT_APPROVING" in reasons["pending.json"], "未批准许可被拒")
    check("MUST_NOT_CLAIM_LEGAL_OPINION" in reasons["claims_legal.json"],
          "站点许可不得冒充法务结论")
    check("ONLY_GRADE_D_ALLOWED" in reasons["grade_a.json"], "非 D 级数据被拒")
    check("ALLOWED_HOSTS_INVALID" in reasons["no_hosts.json"], "空 host 白名单被拒")
    check("LICENSE_REF_MISSING" in reasons["no_ref.json"], "缺许可编号被拒")
    check(reasons["bad_json.json"] == "UNREADABLE_JSON", "损坏 JSON 被拒")

    valid = loaded["valid"]
    check(pc.is_allowed_production_target("https://jobs.example.com/jobs/1", valid)["allowed"],
          "有效许可覆盖 host+path 时允许")
    check(not pc.is_allowed_production_target(
        "https://jobs.example.com/admin/1", valid)["allowed"],
        "有效许可未覆盖的路径仍被拒")
    check(not pc.is_allowed_production_target(
        "https://evil.example.com/jobs/1", valid)["allowed"],
        "有效许可未覆盖的 host 仍被拒")
    recruit_license = [{"license_ref": "LIC-RECRUIT", "issuer": "站点运营方",
                        "hosts": ["www.zhipin.com"], "path_prefixes": ["/jobs/"]}]
    check(pc.is_allowed_production_target(
        "https://www.zhipin.com/jobs/1", recruit_license)["allowed"],
        "招聘站仅在持有站点书面许可时才允许（逻辑校验，不联网）")

    # ---- 4. 法务结论回执 ----
    with tempfile.TemporaryDirectory() as td2:
        legal_path = Path(td2) / "B2-legal.json"
        legal_path.write_text(json.dumps(legal_doc(), ensure_ascii=False), encoding="utf-8")
        with patch.object(pc, "LEGAL_FILE", legal_path):
            law = pc.legal_conclusion_state()
        check(law["received"] is True and law["legal_review"] == "received",
              "有效法务书面结论被识别")
        legal_path.write_text(json.dumps(legal_doc(legal_opinion=False),
                                        ensure_ascii=False), encoding="utf-8")
        with patch.object(pc, "LEGAL_FILE", legal_path):
            check(pc.legal_conclusion_state()["received"] is False,
                  "未声明 legal_opinion 的结论被拒")
        legal_path.write_text(json.dumps(legal_doc(issuer="项目所有者"),
                                        ensure_ascii=False), encoding="utf-8")
        with patch.object(pc, "LEGAL_FILE", legal_path):
            check(pc.legal_conclusion_state()["received"] is False,
                  "所有者自签的「法务结论」被拒")

    # ---- 5. 九道闸门：缺任一即关；齐备才开，且仍不自动发请求 ----
    urls = ["https://jobs.example.com/jobs/1", "https://jobs.example.com/jobs/2"]
    empty_legal = {"present": False, "received": False, "reason": "MISSING"}
    r0 = pc.production_readiness(urls, False, loaded, empty_legal)
    check(r0["production_capture_unlocked"] is False, "缺法务结论时生产闸门关闭")
    check(r0["requests_issued_by_readiness"] == 0, "准入核算零请求")
    check("legal_written_conclusion" in r0["blocked_by"], "缺项如实列出")

    good_legal = {"present": True, "received": True, "legal_review": "received",
                  "reason": "OK"}
    original_status = sources.SOURCES[rc.SOURCE_ID]["legal_status"]
    try:
        sources.SOURCES[rc.SOURCE_ID]["legal_status"] = "approved"
        with patch.object(rc, "admin_status",
                          return_value={"toggle": {"enabled": True}}):
            r1 = pc.production_readiness(urls, True, loaded, good_legal)
        check(r1["gates"]["registry_approved"] is True, "注册表 approved 闸门可通过")
        check(r1["gates"]["site_written_licenses"] is True, "站点许可闸门可通过")
        check(r1["gates"]["legal_written_conclusion"] is True, "法务结论闸门可通过")
        check(r1["production_prerequisites_met"] is True, "持久前提条件齐备")
        check(r1["production_capture_unlocked"] is True,
              "九道闸门齐备时生产执行体解锁（仍需显式调用 run）")
        check(r1["requests_issued_by_readiness"] == 0, "readiness 仍不发起任何请求")

        r2 = pc.production_readiness(urls, False, loaded, good_legal)
        check(r2["production_capture_unlocked"] is False and
              "operator_ack" in r2["blocked_by"], "缺 operator_ack 时闸门关闭")
    finally:
        sources.SOURCES[rc.SOURCE_ID]["legal_status"] = original_status
    check(rc.status()["verdict"] is not None, "注册表状态已恢复，无副作用")

    # ---- 6. 生产 run 在无许可默认状态下必须拒绝 ----
    try:
        pc.production_run(urls, operator_ack=False)
        check(False, "无许可时 production_run 必须抛 ProductionBlocked")
    except pc.ProductionBlocked as exc:
        check("PRODUCTION_GATE_CLOSED" in str(exc),
              "无许可时 production_run 被拒且不发起请求")

print("\n=== B2 生产执行体单元回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

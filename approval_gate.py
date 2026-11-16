#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 书面结论回执闸门（回执清单 §3 / §4 的代码级实现）

设计要点：

1. **只读**。回执文件由人线下放置到 `.privacy/approvals/`（已被 .gitignore 忽略），
   本模块只提供读取与校验，**不提供任何写入 / 生成 / 自我授权接口**。
   工程侧不能因为「代码跑通了」「端点返回 200」就给自己签发批准。
2. **双闸门**。除回执本身，还要看注册表状态 / 配置开关：
   回执有效 + 对应来源分级与开关同时满足，才算 enabled。缺一不可。
3. **区分「工程决策」与「法律结论」**。回执的 `issuer` 如实记录出具方；
   项目所有者（毕设作者）可以决定工程开关，但 `legal_opinion=false` 时
   状态端点与文档必须显著标注「法务书面结论未回」。`legal_opinion` 不参与工程闸门，
   也不允许被当作已批准的证据。
4. **结论不是问题**。台账 §1「需要的一句话结论」栏若只是把问题复述一遍，
   `decision` 只能是 `pending_legal_opinion`，闸门保持 CLOSED。
5. 有条件同意（`approved_with_conditions`）必须 `conditions_met=true`，
   且 `conditions` 逐条在实现中有对应物，否则不启用。

本模块是工程过程控制，**不构成法律意见**。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import usage_policy   # 全项目「仅限研究学习 / 禁止商业行为」统一条款

ROOT = Path(__file__).resolve().parent
APPROVAL_DIR = ROOT / ".privacy" / "approvals"

# 每项期望的授权范围。回执里的 scope 只能比这更窄，不能更宽。
ITEMS: dict[str, dict[str, Any]] = {
    "B1": {
        "title": "简历上传 / PDF·DOCX 解析（仅岗位信息）",
        "expected_scope": {
            "data_grade": ["C"],
            "purpose": "rough_job_profile",
            "file_types": ["pdf", "docx"],
            "retention": "none",
            "withdrawal_and_delete": True,
            # 2026-11-16 用户最终口径：个人画像要做（走既有本地 /api/jd/analyze），
            # 但截图/爬取链路的隐私信息不要入库，文件解析链路只出岗位信息。
            "pii": "excluded",
            "pii_to_store": "excluded",
            "user_profiling": "separate_local_path",
            "fields": ["job_title", "skill_name", "level", "category"],
            "output": "coarse_job_information_only",
        },
        "config_key": None,
        "registry_source": None,
        "action": "开放文件上传与解析端点（仅输出岗位/技能字段）",
    },
    "Q18": {
        "title": "Qdrant Cloud 境外实例写入 D 级公开样本",
        "expected_scope": {
            "data_grade": ["D"],
            "purpose": "public_sample_vector_index",
            "region": "global_remote",
            "sources": ["jobicy", "remoteok"],
        },
        "config_key": "QDRANT_ALLOW_WRITE",
        "registry_source": None,
        "action": "允许把 D 级公开岗位样本及其向量写入云端集合",
    },
    "Q19": {
        "title": "qwen 接收 C 级 JD 派生的结构化字段",
        "expected_scope": {
            "data_grade": ["C"],
            "purpose": "derived_structured_fields_outbound",
            "fields": ["skill_name", "level", "coverage"],
            "pii": "excluded",
        },
        "config_key": "ALLOW_CLOUD_FOR_USER_DATA",
        "registry_source": None,
        "action": "放宽 / 追认结构化字段出境（当前按最小必要范围运行，不新增放宽）",
    },
    "Q21": {
        "title": "GitHub 公开仓库语言 / 结构元数据",
        "expected_scope": {
            "data_grade": ["D"],
            "purpose": "public_repo_metadata",
        },
        "config_key": None,
        "registry_source": "github_public_repos",
        "action": "把法务 / API Terms 结论写入来源登记依据",
    },
    "B3": {
        "title": "A 级境内招聘数据",
        "expected_scope": {
            "data_grade": ["A"],
            "purpose": "cn_market_aggregate",
        },
        "config_key": None,
        "registry_source": "licensed_cn_platform",
        "action": "允许输出境内聚合统计",
    },
}

VALID_DECISIONS = frozenset({
    "approved", "approved_with_conditions", "rejected",
    "pending_legal_opinion", "not_addressed",
})
ENABLED_DECISIONS = frozenset({"approved", "approved_with_conditions"})


class ApprovalRequired(RuntimeError):
    """闸门未通过：任何受控动作都必须在此之前停下。"""


def _parse_day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None


def _load(item_id: str) -> dict[str, Any]:
    """只读。文件不存在 / 损坏 / 非法 JSON 一律视为无回执，fail-closed。"""
    path = APPROVAL_DIR / ("%s.json" % item_id)
    if not path.is_file():
        return {"present": False, "valid": False, "reason": "APPROVAL_FILE_MISSING"}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"present": True, "valid": False, "reason": "APPROVAL_FILE_UNREADABLE"}
    return {"present": True, "raw": raw}


def _subset(actual: Any, expected: Any) -> bool:
    """list 场景：actual 必须是 expected 的子集；标量场景：必须相等。"""
    if isinstance(expected, (list, tuple, set, frozenset)):
        if not isinstance(actual, (list, tuple, set)):
            return False
        return set(map(str, actual)).issubset({str(x) for x in expected})
    return actual == expected


def validate(item_id: str) -> dict[str, Any]:
    """校验单项回执。返回可对外暴露的状态视图（绝不含完整原文）。"""
    meta = ITEMS.get(item_id)
    if not meta:
        return {"id": item_id, "present": False, "valid": False,
                "enabled": False, "reason": "UNKNOWN_ITEM"}
    rec = _load(item_id)
    # 使用限制条款对每一项「同理适用」，但只作标注，不参与闸门判定。
    base = {"id": item_id, "title": meta["title"], "action": meta["action"],
            "present": rec["present"],
            "usage_restriction": usage_policy.scope_marker()}
    if not rec.get("present"):
        return {**base, "valid": False, "enabled": False,
                "reason": rec["reason"], "decision": None, "legal_opinion": False,
                "legal_review": "pending"}
    raw = rec.get("raw")
    if not isinstance(raw, dict):
        return {**base, "valid": False, "enabled": False,
                "reason": "APPROVAL_NOT_OBJECT", "decision": None, "legal_opinion": False,
                "legal_review": "pending"}

    decision = str(raw.get("decision", "")).strip()
    reasons: list[str] = []
    if str(raw.get("id", "")).strip() != item_id:
        reasons.append("ID_MISMATCH")
    if not str(raw.get("approval_ref", "")).strip():
        reasons.append("APPROVAL_REF_MISSING")
    if not str(raw.get("issuer", "")).strip():
        reasons.append("ISSUER_MISSING")
    if decision not in VALID_DECISIONS:
        reasons.append("DECISION_INVALID")
    issued = _parse_day(raw.get("issued_at"))
    until = _parse_day(raw.get("valid_until"))
    today = date.today()
    if issued is None or until is None:
        reasons.append("DATE_INVALID")
    else:
        if issued > today:
            reasons.append("NOT_YET_EFFECTIVE")
        if until < today:
            reasons.append("EXPIRED")
    scope = raw.get("scope")
    if not isinstance(scope, dict):
        reasons.append("SCOPE_MISSING")
    else:
        for key, expected in meta["expected_scope"].items():
            if key not in scope or not _subset(scope[key], expected):
                reasons.append("SCOPE_MISMATCH:" + key)
    if decision == "approved_with_conditions":
        conds = raw.get("conditions")
        if not isinstance(conds, list) or not conds:
            reasons.append("CONDITIONS_NOT_LISTED")
        if raw.get("conditions_met") is not True:
            reasons.append("CONDITIONS_NOT_MET")
    if decision not in ENABLED_DECISIONS:
        reasons.append("DECISION_NOT_APPROVING:%s" % (decision or "empty"))

    valid = not reasons
    return {
        **base,
        "valid": valid,
        "decision": decision,
        "approval_ref": str(raw.get("approval_ref", ""))[:64],
        "issuer": str(raw.get("issuer", ""))[:64],
        "issued_at": str(raw.get("issued_at", ""))[:32],
        "valid_until": str(raw.get("valid_until", ""))[:32],
        "legal_opinion": raw.get("legal_opinion") is True,
        "legal_review": str(raw.get("legal_review", "pending"))[:32],
        "conditions": [str(c)[:120] for c in raw.get("conditions", [])][:8]
                      if isinstance(raw.get("conditions"), list) else [],
        "reason": "OK" if valid else ";".join(reasons),
        "enabled": valid,
    }


def gate(item_id: str) -> dict[str, Any]:
    """双闸门：回执 + 注册表 / 配置开关。"""
    approval = validate(item_id)
    meta = ITEMS[item_id]
    checks: dict[str, Any] = {"approval": approval["enabled"]}

    if meta["config_key"]:
        from secure_config import cfg
        val = cfg.flag(meta["config_key"])
        checks["config:%s" % meta["config_key"]] = val
    if meta["registry_source"]:
        from sources import SOURCES
        src = SOURCES.get(meta["registry_source"], {})
        val = src.get("legal_status") == "approved"
        checks["registry:%s" % meta["registry_source"]] = val

    enabled = approval["enabled"] and all(checks.values())
    return {
        "id": item_id,
        "enabled": enabled,
        "checks": checks,
        "approval_valid": approval["valid"],
        "legal_review": approval["legal_review"],
        "decision": approval["decision"],
        "reason": approval["reason"],
        "usage_restriction": usage_policy.scope_marker(),
        "note": "回执有效 + 注册表/配置双闸门同时满足；工程侧不提供自我授权入口。"
                "「仅限研究学习 / 禁止商业行为」统一适用，但不参与 enabled 判定、不构成豁免。",
    }


def require(item_id: str) -> dict[str, Any]:
    """受控动作调用前强制闸门；不通过即抛，由路由转成 403。"""
    state = gate(item_id)
    if not state["enabled"]:
        raise ApprovalRequired("%s:%s" % (item_id, state["reason"]))
    return state


def is_enabled(item_id: str) -> bool:
    try:
        return gate(item_id)["enabled"]
    except Exception:
        return False


def state() -> dict[str, Any]:
    """一次看全部 5 项，供 /api/approvals/status 与控制台使用。"""
    items = {k: gate(k) for k in ITEMS}
    return {
        "ok": True,
        "items": items,
        "enabled": [k for k, v in items.items() if v["enabled"]],
        "pending": [k for k, v in items.items() if not v["enabled"]],
        "legal_opinion_received": [k for k, v in items.items() if v["legal_review"] == "received"],
        "approval_dir": ".privacy/approvals/ (gitignored, human-placed, read-only)",
        "writable_api": False,
        # 2026-11-16：所有者确立的全项目条款，5 项同理适用（收紧用途，不改闸门）。
        "usage_restriction": usage_policy.as_dict(),
        "uniform_usage_restriction": all(
            items[k].get("usage_restriction", {}).get("commercial_use") is False
            for k in items
        ),
        "disclaimer": "本闸门是工程过程控制，不构成法律意见；项目所有者决策不等于法务书面结论。"
                      "「仅限研究学习 / 禁止商业行为」条款不构成合规豁免，也不改变任何一项的 decision。",
    }

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · B1 简历 / 岗位文件解析（仅岗位信息，2026-11-16 开放）

用户授权与约束（逐条对应回执 B1.json 的 conditions）：

- 允许文件类型：pdf、docx；扩展名 + magic bytes 双重校验，大小上限 3 MiB。
- 不留存：文件只在内存 BytesIO 中解析，**不写任何临时文件、不写 vault、不写 RAG、
  不写审计原文**；原文解析完立即 del；只留一份短 TTL 的粗粒度岗位信息供用户核对。
- 只要岗位信息 / 不要个人信息 / 不做用户画像：统一走 `job_info.to_coarse_job_profile()`，
  输出含求职意向、薪资意向、专业、学历、证书（同义归一去重）、荣誉、技能方向、级别词、
  一句概括与本次专家上下文——这些是用户主动提供的岗位颗粒度有效信息，用于洞察对齐；
  个人信息＝姓名 / 手机 / 邮箱 / 证件号 / 出生地 / 住址 / 性别 / 联系方式等身份标识，
  一律剔除；不含学校、公司、原文、证据句，也不含匹配度 / 覆盖率 / 能力评分 / 薪资预测 /
  录用预测等画像类字段。岗位字段不写向量库、不长期留存；解析结果在前端以可读卡片展示，
  不回显 JSON schema。
- 允许云端解析，但实现为**逐次单独告知同意**：需 `cloud_ack` + `third_party_notice_ack`
  同时为真，且只把已去标识化的粗粒度岗位字段交给云端叙述层，绝不上传原始文件或原文。
  默认关闭；Q19 法务结论若为否定，关掉这个子开关即可，本地解析不受影响。
- 有撤回与删除入口：`withdraw()` / `delete()` 清除会话内派生字段并写审计留痕。

本模块不做任何网络请求（云端叙述层由 llm.py 在显式同意后调用）。
"""
from __future__ import annotations

import hashlib
import io
import re
import threading
import time
import zipfile
from datetime import datetime, timezone
from typing import Any

import approval_gate
import job_info
import llm
import privacy

ALLOWED_EXT = frozenset({"pdf", "docx"})
MAX_BYTES = 3 * 1024 * 1024
SESSION_TTL_S = 300

THIRD_PARTY_NOTICE = (
    "云端解析会把「已去除个人信息后的粗粒度岗位字段」（岗位方向、技能方向、级别词）"
    "发送给第三方大模型服务用于文字润色；不会上传原始文件、原文或任何个人信息。"
    "该境外接收方与数据处理协议尚未取得法务书面结论（Q19），你可以随时撤回并改用本地解析。"
)

_LOCK = threading.RLock()
_SESSIONS: dict[str, dict[str, Any]] = {}


class ResumeRejected(ValueError):
    """文件不合规 / 闸门未通过 / 输出含个人信息，fail-closed 拒绝。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ext(filename: str) -> str:
    return (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""


def _check_magic(ext: str, data: bytes) -> None:
    if ext == "pdf" and not data.startswith(b"%PDF"):
        raise ResumeRejected("FILE_MAGIC_MISMATCH")
    if ext == "docx" and not data.startswith(b"PK\x03\x04"):
        raise ResumeRejected("FILE_MAGIC_MISMATCH")
    if ext == "docx":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if "word/document.xml" not in z.namelist():
                    raise ResumeRejected("NOT_A_DOCX")
        except zipfile.BadZipFile:
            raise ResumeRejected("NOT_A_DOCX")


def _extract_text(ext: str, data: bytes) -> str:
    """纯内存解析：pypdf / zipfile + XML 去标签，不产生任何磁盘文件。"""
    if ext == "pdf":
        try:
            from pypdf import PdfReader  # type: ignore
        except ImportError:
            raise ResumeRejected("PDF_BACKEND_UNAVAILABLE")
        try:
            reader = PdfReader(io.BytesIO(data))
            parts = []
            for page in reader.pages[:40]:
                parts.append(page.extract_text() or "")
            return "\n".join(parts)
        except Exception:
            raise ResumeRejected("PDF_PARSE_FAILED")
    # docx：只取 word/document.xml 的正文文本
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except (zipfile.BadZipFile, KeyError, OSError):
        raise ResumeRejected("DOCX_PARSE_FAILED")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")


def _purge_expired() -> None:
    now = time.time()
    for key in [k for k, v in _SESSIONS.items() if v["expires"] < now]:
        _SESSIONS.pop(key, None)


def parse(filename: str, data: bytes, cloud_ack: bool = False,
          third_party_notice_ack: bool = False, cloud_enrich: bool = False) -> dict[str, Any]:
    """解析文件 → 只返回粗粒度岗位信息。原文与个人信息都不留存、不外发。"""
    gate = approval_gate.gate("B1")
    if not gate["enabled"]:
        raise ResumeRejected("APPROVAL_GATE_CLOSED:" + gate["reason"])

    ext = _ext(filename)
    if ext not in ALLOWED_EXT:
        raise ResumeRejected("FILE_TYPE_NOT_ALLOWED")
    if not data or len(data) > MAX_BYTES:
        raise ResumeRejected("FILE_SIZE_INVALID")
    _check_magic(ext, data)

    digest = hashlib.sha256(data).hexdigest()[:16]  # 只留内容指纹用于审计，不留内容
    raw = _extract_text(ext, data)
    if not raw.strip():
        raise ResumeRejected("NO_TEXT_EXTRACTED")

    try:
        profile = job_info.to_coarse_job_profile(raw)
    except job_info.JobInfoRejected:
        raise ResumeRejected("OUTPUT_CONTAINS_PERSONAL_OR_PROFILING_DATA")
    finally:
        # 不留存：原文与文件字节在离开函数前释放，且从不进入会话表
        raw = ""
        del data

    sid = hashlib.sha256((digest + _now()).encode()).hexdigest()[:20]
    cloud_used = False
    cloud_detail = "未请求云端解析（默认本地）"

    if cloud_enrich:
        if not (cloud_ack and third_party_notice_ack):
            raise ResumeRejected("SEPARATE_CONSENT_REQUIRED")
        # 只发送已去标识化的粗粒度岗位字段；字段名刻意避开 USER_DATA_FIELDS
        outbound = {
            "reading": profile["coarse_summary"],
            "focus": [s["name"] for s in profile["skill_areas"][:5]],
            "career_context": profile.get("expert_context", ""),
        }
        llm.assert_outbound_safe(outbound)
        try:
            llm.generate("insight", outbound)
            cloud_used = True
            cloud_detail = "已按单独同意发送粗粒度岗位字段（不含原文 / 个人信息）"
        except llm.LLMUnavailable as exc:
            cloud_detail = "云端不可用，已降级本地：%s" % str(exc)[:60]
        except Exception:
            cloud_detail = "云端调用失败，已降级本地解析"

    with _LOCK:
        _purge_expired()
        _SESSIONS[sid] = {
            "created": _now(),
            "expires": time.time() + SESSION_TTL_S,
            "profile": profile,            # 仅粗粒度岗位信息
            "raw_text_retained": False,
            "pii_retained": False,
        }

    privacy.audit("resume_parsed_job_only", sid,
                  "sha256=%s ext=%s cloud=%s" % (digest, ext, cloud_used))
    return {
        "ok": True,
        "session_id": sid,
        "job_profile": profile,
        "data_minimization": job_info.POLICY,
        "retention": {"raw_text": "none", "file": "none",
                      "derived_profile_ttl_s": SESSION_TTL_S},
        "cloud_used": cloud_used,
        "cloud_detail": cloud_detail,
        "withdraw_endpoint": "/api/resume/withdraw",
        "delete_endpoint": "/api/resume/delete",
        "profiling_path": "/api/jd/analyze",
        "legal_review": gate["legal_review"],
        "note": "文件解析只提供大致岗位信息、不入库个人信息；如需个人画像请走"
                "用户主动提供 JD / 简历的本地能力对齐链路 /api/jd/analyze。"
                "云端解析依据项目所有者决策，法务书面结论（Q19）仍未回。",
    }


def parse_text(text: str, purpose: str = "resume") -> dict[str, Any]:
    """解析浏览器 OCR / 用户粘贴文本 → 只返回结构化岗位字段。

    与 parse() 共用 job_info 的字段闸门和去重归一；不接收文件、不存原文、
    不写向量库，也不生成用户画像。供截图 OCR 和手动粘贴的「按解析结果刷新」入口使用。
    """
    raw = (text or "")[:20000]
    if not raw.strip():
        raise ResumeRejected("NO_TEXT_EXTRACTED")
    digest = hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:16]
    try:
        profile = job_info.to_coarse_job_profile(raw)
    except job_info.JobInfoRejected:
        raise ResumeRejected("OUTPUT_CONTAINS_PERSONAL_OR_PROFILING_DATA")
    finally:
        raw = ""
    privacy.audit("resume_text_parsed_job_only", digest,
                  "purpose=%s vectorized=false raw_retained=false" % (purpose[:20] or "resume"))
    return {
        "ok": True,
        "session_id": "",
        "job_profile": profile,
        "data_minimization": job_info.POLICY,
        "retention": {"raw_text": "none", "file": "none", "derived_profile_ttl_s": 0},
        "cloud_used": False,
        "cloud_detail": "仅提取结构化岗位字段；未上传原文，未写入向量库",
        "note": "岗位相关字段可在下次生成洞察时作为本次专家解读上下文；"
                "姓名 / 手机 / 邮箱 / 出生地 / 性别等身份信息已剔除。",
    }


def consent_notice() -> dict[str, Any]:
    return {"ok": True, "required_flags": ["cloud_ack", "third_party_notice_ack"],
            "notice": THIRD_PARTY_NOTICE,
            "default": "cloud_enrich=false（本地解析）",
            "legal_review": "pending（Q19 书面结论未回）"}


def withdraw(sid: str) -> dict[str, Any]:
    """撤回：清除本次解析的派生岗位信息并留痕。原文本就未留存。"""
    return _remove(sid, "resume_withdrawn")


def delete(sid: str) -> dict[str, Any]:
    """删除：与撤回同一语义，提供两个显式入口便于前端与合规文案对齐。"""
    return _remove(sid, "resume_deleted")


def _remove(sid: str, action: str) -> dict[str, Any]:
    with _LOCK:
        _purge_expired()
        existed = _SESSIONS.pop((sid or "")[:64], None) is not None
    privacy.audit(action, (sid or "")[:64], "derived_profile_removed=true")
    return {"ok": True, "removed": existed,
            "raw_text_retained": False, "pii_retained": False,
            "note": "原文与文件从未留存；此处清除的是短 TTL 的派生岗位信息。"}


def status() -> dict[str, Any]:
    gate = approval_gate.gate("B1")
    with _LOCK:
        _purge_expired()
        active = len(_SESSIONS)
    return {
        "ok": True,
        "resume_parse_open": gate["enabled"],
        "approval": gate,
        "allowed_file_types": sorted(ALLOWED_EXT),
        "max_bytes": MAX_BYTES,
        "retention": "none（文件与原文仅内存、解析完即释放）",
        "derived_profile_ttl_s": SESSION_TTL_S,
        "active_sessions": active,
        "pii_extraction": job_info.POLICY["pii_extraction"],
        "pii_to_store": False,
        "user_profiling_in_this_path": False,
        "user_profiling_project_wide": True,
        "profiling_path": "/api/jd/analyze",
        "output_scope": job_info.POLICY["output_scope"],
        "cloud_parse": "allowed_with_separate_consent",
        "cloud_sends": "仅去标识化的粗粒度岗位字段，不是原始文件 / 原文",
        "withdraw_endpoint": "/api/resume/withdraw",
        "delete_endpoint": "/api/resume/delete",
        "consent_endpoint": "/api/resume/consent",
        "legal_review": gate["legal_review"],
        "note": "工程开关依据项目所有者 2026-11-16 决策；不等于法务书面结论。",
    }

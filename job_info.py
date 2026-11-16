#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 岗位信息粗粒度抽取（2026-11-16 用户追加约束的实现）

用户原话（2026-11-16，按最终确认的口径）：
  「提取信息里的个人信息不要只要岗位信息即可用作给用户提供大致岗位信息」
  「做个人画像只不过截图爬取中的隐私信息不要入库」

因此约束是**按链路区分**，不是全局禁止画像：

- **截图 / 公开页面 → 本地 RAG 的入库链路**（本模块负责）：只保留岗位信息，
  个人信息整行剔除、**隐私信息不入库**，只用于给用户提供大致岗位信息；
  本链路不产出针对「人」的画像类字段。
- **用户主动提供 JD / 简历 → 本地能力对齐链路**：项目**保留并继续提供个人画像**
  （`user_jd.align` 的覆盖率 / 技能差距、`/api/jd/analyze`），C 级、原文零落盘、
  不外发原文。两条链路完全分离，互不混用。

本模块从需求层面就不解析姓名 / 手机 / 邮箱 / 证件号 / 学校 / 公司 / 地址 /
年龄 / 性别 / 照片 / 联系方式，输出不回显原文、不带证据句，并由
privacy.scan_pii 做 fail-closed 二次校验。
"""
from __future__ import annotations

import re
from typing import Any

import privacy
import user_jd

POLICY: dict[str, Any] = {
    "applies_to": "截图 / 公开页面 → 本地 RAG 的入库链路",
    "pii_extraction": False,
    "pii_to_store": False,
    "raw_text_returned": False,
    "evidence_snippets_returned": False,
    "output_scope": "coarse_job_information_only",
    "purpose": "给用户提供大致岗位信息",
    "user_profiling_in_this_path": False,
    "user_profiling_project_wide": True,
    "profiling_path": "/api/jd/analyze",
    "profiling_note": "个人画像由用户主动提供 JD / 简历的本地能力对齐链路提供"
                      "（user_jd.align / analyze，覆盖率与技能差距），C 级、原文零落盘、"
                      "不外发原文；与截图入库链路分离。",
    "allowed_output_fields": ["job_title_hint", "skill_areas", "level_hint", "coarse_summary"],
    "forbidden_in_this_path": [
        "姓名 / 手机 / 邮箱 / 证件号 / 学校 / 公司 / 地址 / 年龄 / 性别 / 照片（不入库）",
        "本链路不产出能力评分、匹配度 / 覆盖率、技能差距、薪资预测、录用概率",
        "原文、OCR 全文、证据句、行文本、文件内容",
    ],
    "note": "PII 脱敏仅作为防御性兜底，不代表系统曾经提取过个人信息；"
            "岗位信息之外的内容在解析阶段即被丢弃。",
}

# 个人信息语境词：命中即整行丢弃，不做任何形式的回填或占位
_PERSON_CONTEXT = re.compile(
    r"(姓名|手机|电话|邮箱|e-?mail|身份证|证件号|住址|地址|年龄|性别|籍贯|"
    r"婚姻|生日|出生|政治面貌|联系方式|联系人|微信号|qq|薪资期望|期望薪资|"
    r"个人简介|自我评价|教育经历|毕业院校|工作经历|实习经历|"
    r"contact|phone|mobile|applicant|投递|联系人)", re.I)

# 岗位语境词：只有带岗位/职责/技能语境的行才可能保留
_JOB_CONTEXT = re.compile(
    r"(岗位|职位|职责|任职|要求|技能|技术|框架|开发|工程|设计|分析|运维|"
    r"产品|算法|数据|测试|架构|语言|工具|平台|经验|熟悉|掌握|负责|参与|"
    r"招聘|jd|description|requirement|responsib|skill|stack|"
    r"title|job|engineer|develop|position)", re.I)

# 明确的画像 / 评分语义，出现即视为不应输出的内容
_PROFILING_WORDS = re.compile(
    r"(匹配度|覆盖率|能力评分|综合评分|竞争力|录用概率|通过率|薪资预测|"
    r"个人画像|用户画像|能力模型|胜任力|差距分析|排名)", re.I)

MAX_TEXT = 20000
MAX_LINES = 400


class JobInfoRejected(ValueError):
    """输出中仍检出个人信息 / 画像语义，fail-closed 整批拒绝。"""


def _job_lines(text: str) -> list[str]:
    """只保留岗位语境行，个人信息语境行与画像行直接丢弃。"""
    out: list[str] = []
    for line in (text or "").splitlines()[:MAX_LINES]:
        s = line.strip()
        if not s or len(s) > 500:
            continue
        if _PERSON_CONTEXT.search(s) or _PROFILING_WORDS.search(s):
            continue
        if not _JOB_CONTEXT.search(s):
            continue
        # 兜底脱敏：不代表提取过 PII，只是不让残留蒙混过关
        clean = user_jd.redact(s)[0]
        if privacy.scan_pii(clean):
            continue
        out.append(clean)
    return out


def to_coarse_job_profile(text: str) -> dict[str, Any]:
    """把文件 / OCR 文本转成粗粒度岗位信息。原文与个人信息都不进入返回值。"""
    raw = (text or "")[:MAX_TEXT]
    lines = _job_lines(raw)

    # 复用项目已有的确定性技能词典，但只取「技能名 / 类别 / 级别词」，
    # 不取 user_jd.extract() 的 evidence（那是原文句子，可能夹带个人信息）。
    skills: list[dict[str, str]] = []
    seen: set[str] = set()
    joined = " ".join(lines).lower()
    for skill in user_jd.SKILLS:
        if not any(w in joined for w in skill["words"]):
            continue
        if skill["name"] in seen:
            continue
        seen.add(skill["name"])
        skills.append({"name": skill["name"], "category": skill["cat"], "level": "mentioned"})
    skills.sort(key=lambda x: x["name"])

    title_hint = ""
    for line in lines[:20]:
        m = re.search(r"(岗位名称|岗位|职位|招聘|title)\s*(?:名称)?\s*[:：]\s*(.{2,30})", line)
        if m:
            title_hint = m.group(2).strip()[:30]
            break

    level_hint = "unspecified"
    if any(re.search(r"(高级|资深|senior|lead|专家)", x, re.I) for x in lines):
        level_hint = "senior"
    elif any(re.search(r"(初级|实习|junior|应届)", x, re.I) for x in lines):
        level_hint = "junior"

    names = [s["name"] for s in skills[:8]]
    summary = "该岗位大致涉及 %s 等技能方向（粗粒度岗位信息，不含个人信息与能力评估）。" % (
        "、".join(names) if names else "通用技术方向")

    result = {
        "job_title_hint": title_hint,
        "skill_areas": skills[:12],
        "level_hint": level_hint,
        "coarse_summary": summary,
        "source_lines_kept": len(lines),
    }

    # fail-closed：输出里若还有 PII 或画像语义，整批拒绝而不是「尽量抹掉」
    if privacy.scan_pii(result) or _PROFILING_WORDS.search(str(result)):
        raise JobInfoRejected("OUTPUT_CONTAINS_PERSONAL_OR_PROFILING_DATA")
    result["policy"] = {
        "pii_extraction": False,
        "pii_to_store": False,
        "user_profiling_in_this_path": False,
        "user_profiling_project_wide": True,
        "profiling_path": POLICY["profiling_path"],
        "raw_text_returned": False,
        "output_scope": POLICY["output_scope"],
    }
    return result


def job_only_lines(text: str) -> list[str]:
    """截图 / OCR 入库前的硬闸门：只留岗位信息行，个人信息行整行剔除。

    被 rag_store.ingest(origin='local_ocr') 调用，保证「隐私信息不要入库」。
    """
    return _job_lines(text)


def status() -> dict[str, Any]:
    return {"ok": True, **POLICY}

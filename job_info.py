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
    "allowed_output_fields": ["job_title_hint", "target_role", "salary_expectation", "major",
                              "education", "certificates", "honors", "experience_context",
                              "skill_areas", "level_hint", "coarse_summary", "expert_context"],
    "pii_definition": "个人信息＝姓名 / 手机 / 邮箱 / 证件号 / 出生地 / 住址 / 联系方式 / 性别等身份标识；"
                      "求职岗位 / 薪资意向 / 专业 / 学历 / 证书 / 荣誉是用户主动提供的岗位颗粒度信息，"
                      "可读取用于本次洞察对齐与专家解读，但不写入向量库、不产出用户画像",
    "forbidden_in_this_path": [
        "姓名 / 手机 / 邮箱 / 证件号 / 出生地 / 住址 / 联系方式 / 年龄 / 性别 / 照片（不入库）",
        "本链路不产出能力评分、匹配度 / 覆盖率、技能差距、薪资预测、录用概率",
        "原文、OCR 全文、证据句、行文本、文件内容；用户数据不写入 Qdrant / 不生成向量库条目",
    ],
    "note": "PII 脱敏仅作为防御性兜底，不代表系统曾经提取过个人信息；"
            "岗位信息之外的内容在解析阶段即被丢弃。",
}

# 个人信息语境词：命中即整行丢弃，不做任何形式的回填或占位
_PERSON_CONTEXT = re.compile(
    r"(姓名|手机|电话|邮箱|e-?mail|身份证|证件号|住址|地址|年龄|性别|籍贯|出生地|"
    r"婚姻|生日|出生|政治面貌|联系方式|联系人|微信号|qq|薪资期望|期望薪资|"
    r"个人简介|自我评价|毕业院校|"
    r"contact|phone|mobile|applicant|投递|联系人)", re.I)

# 结构化岗位字段的「身份 / 联系方式」排除词（真正需要剔除的个人信息）：
# 姓名 / 手机 / 邮箱 / 证件号 / 出生地 / 住址 / 联系方式 / 性别等是个人信息；
# 求职岗位 / 薪资意向 / 专业 / 学历 / 证书是岗位颗粒度信息，不属于个人信息，应当读取。
_IDENTIFIER_LINE = re.compile(
    r"(姓名|手机|电话|邮箱|e-?mail|身份证|证件号|住址|联系地址|出生地|联系方式|联系人|"
    r"微信号|qq|年龄|性别|籍贯|婚姻|生日|出生年月|政治面貌|"
    r"contact|phone|mobile|applicant|投递邮箱)", re.I)
# 简历里常见「姓名：张三  求职意向：XX」写在同一行：不能整行丢掉岗位字段，
# 只把身份字段连同其值删掉，余下岗位语境继续解析；隐私仍走 redact + scan_pii 双校验。
_IDENTIFIER_FIELD_RE = re.compile(
    r"(?:姓名|手机|电话|邮箱|e-?mail|身份证|证件号|住址|联系地址|出生地|联系方式|联系人|"
    r"微信号|qq|年龄|性别|籍贯|婚姻|生日|出生年月|政治面貌|"
    r"contact|phone|mobile|applicant|投递邮箱)\s*[:：]\s*[^\s，,；;|]{1,40}", re.I)

# 结构化字段的标签与取值（标签后面的一段短文本，不取整行原文）
_MAJOR_RE = re.compile(
    r"(?:所学专业|专业名称|专业方向|专业|major)\s*[:：]?\s*"
    r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9（）()\s·、/]{1,23})", re.I)
_ROLE_RE = re.compile(
    r"(?:求职意向|求职方向|岗位方向|职位意向|工作意向|期望岗位|期望职位|意向岗位|"
    r"应聘岗位|应聘职位|目标岗位|目标职位|目标方向|岗位名称|职位名称|岗位|职位|"
    r"target\s*role|target\s*position|desired\s*position|position\s*applied)\s*[:：]?\s*"
    r"([\u4e00-\u9fa5A-Za-z][\u4e00-\u9fa5A-Za-z0-9（）()+·\s/]{1,29})", re.I)
_SALARY_RE = re.compile(
    r"(?:薪资意向|薪资期望|期望薪资|期望月薪|期望年薪|目标薪资|薪酬期望|待遇期望|薪资要求)\s*[:：]?\s*"
    r"([^\n；;，,]{1,30})", re.I)
_EDU_RE = re.compile(
    r"(?:学历|学位)\s*[:：]?\s*(博士|硕士|研究生|本科|大专|学士|硕士生|本科生)", re.I)
_CERT_GENERIC_RE = re.compile(
    r"([\u4e00-\u9fa5A-Za-z0-9]{2,20})(?:证书|资格证|资格认证|认证)")
_CERT_KNOWN = (
    "计算机等级考试", "软件资格考试", "软考", "PMP", "CPA", "CET-4", "CET-6",
    "英语四级", "英语六级", "大学英语四级", "大学英语六级", "专业八级", "教师资格证",
    "法律职业资格", "雅思", "托福", "ACP", "华为高级应用开发者",
    "HCIA", "HCIP", "HCIE", "红帽认证", "AWS 认证", "阿里云 ACA",
)
# OCR 常把同一证书识别成多种写法；先归一，再去重，避免“英语四级 / CET-4 /
# 大学英语四级证书”同时在专家上下文里出现。
_CERT_ALIASES = {
    "英语四级": "大学英语四级（CET-4）",
    "大学英语四级": "大学英语四级（CET-4）",
    "大学英语四级证书": "大学英语四级（CET-4）",
    "cet4": "大学英语四级（CET-4）",
    "cet-4": "大学英语四级（CET-4）",
    "英语六级": "大学英语六级（CET-6）",
    "大学英语六级": "大学英语六级（CET-6）",
    "大学英语六级证书": "大学英语六级（CET-6）",
    "cet6": "大学英语六级（CET-6）",
    "cet-6": "大学英语六级（CET-6）",
    "专业八级": "英语专业八级（TEM-8）",
    "专八": "英语专业八级（TEM-8）",
    "acp": "ACP 认证",
    "阿里云acp": "ACP 认证",
    "华为高级应用开发者": "华为高级应用开发者认证",
    "华为高级应用开发者证书": "华为高级应用开发者认证",
    "软考中级": "软考中级",
    "软件资格考试中级": "软考中级",
    "pmp": "PMP",
    "cpa": "CPA",
}
_CERT_NOISE = re.compile(r"^(荣誉|证书|资格证|资格|认证|奖项|奖励|获奖|其他|相关|个人)$", re.I)
_BAD_VALUE_PREFIX = re.compile(
    r"^(职责|要求|描述|名称|技能|能力|信息|个人|身份|联系|其他|相关|以上|如下|无|暂无)", re.I)

# 岗位语境词：只有带岗位/职责/技能语境的行才可能保留
_JOB_CONTEXT = re.compile(
    r"(岗位|职位|职责|任职|要求|技能|技术|框架|开发|工程|设计|分析|运维|"
    r"产品|算法|数据|测试|架构|语言|工具|平台|经验|熟悉|掌握|负责|参与|"
    r"招聘|jd|description|requirement|responsib|skill|stack|"
    r"title|job|engineer|develop|position)", re.I)

# 简历解析专用语境：求职意向 / 薪资意向 / 专业 / 学历 / 证书 / 荣誉这些是岗位颗粒度
# 有效信息，不是「姓名 / 邮箱 / 电话 / 出生地 / 性别」类身份信息，读取它们用于洞察对齐。
_RESUME_CONTEXT = re.compile(
    r"(求职意向|目标岗位|意向岗位|应聘岗位|目标职位|薪资意向|薪资期望|期望薪资|"
    r"薪酬|待遇|专业|主修|辅修|学历|学位|本科|硕士|研究生|博士|证书|资格|认证|"
    r"软考|等级考试|获奖|奖励|荣誉|奖项|技能|项目|实习|工作|实践|经历|经验|"
    r"负责|参与|主导|搭建|优化|交付|维护|设计|开发)", re.I)

# 结构化字段里不该出现的值（学校 / 公司名等），命中即丢弃该字段
_ORG_NOISE = re.compile(r"(大学|学院|学校|有限公司|公司|集团|事务所)")

# 项目 / 实习经历上下文：只取关键动作与结果，不回显整段原文。
_EXP_HEADINGS = (
    r"项目经历|项目经验|项目实践|项目描述|项目职责|项目内容|项目工作|在校项目|代表性项目|"
    r"实习经历|实习经验|实习实践|实习工作|工作实习|项目实习|项目[/／]实习|"
    r"实践经历|实践经验|校园经历|在校经历|科研经历|课题经历|社会经历|"
    r"工作经历|工作经验|工作实践|工作内容|主要工作|项目|实习|工作|"
    r"project\s*(?:experience|experiences)|internship\s*(?:experience|experiences)|"
    r"work\s*(?:experience|experiences)|practical\s*experience"
)
_EXP_SECTION_RE = re.compile(r"^(" + _EXP_HEADINGS + r")\s*[:：]?\s*$", re.I)
# 章节标题和内容写在同一行的常见简历写法，例如「项目经验：负责……」
_EXP_INLINE_RE = re.compile(r"^(" + _EXP_HEADINGS + r")\s*[:：]\s*(.+)$", re.I)
_EXP_OTHER_SECTION_RE = re.compile(
    r"^(教育经历|教育背景|教育|技能|技能清单|专业技能|证书|证书资格|荣誉|荣誉奖项|奖项|"
    r"求职意向|求职方向|薪资意向|专业|学历|个人信息|联系方式|自我评价|个人简介)\s*[:：]?\s*$", re.I)
_EXP_LABEL_RE = re.compile(
    r"^(?:项目名称|项目描述|项目职责|工作内容|实习内容|工作职责|主要工作)\s*[:：]\s*(.+)$", re.I)
_EXP_ACTION_RE = re.compile(
    r"(负责|参与|主导|设计|开发|搭建|优化|完成|实现|分析|协调|推动|交付|维护|使用|运用|通过|基于|熟悉|掌握)", re.I)

# 明确的画像 / 评分语义，出现即视为不应输出的内容
_PROFILING_WORDS = re.compile(
    r"(匹配度|覆盖率|能力评分|综合评分|竞争力|录用概率|通过率|薪资预测|"
    r"个人画像|用户画像|能力模型|胜任力|差距分析|排名)", re.I)

MAX_TEXT = 20000
MAX_LINES = 400


class JobInfoRejected(ValueError):
    """输出中仍检出个人信息 / 画像语义，fail-closed 整批拒绝。"""


def _job_lines(text: str, resume_parse: bool = False) -> list[str]:
    """保留岗位语境行；身份 / 联系方式行与画像行直接丢弃。

    resume_parse=True（简历解析链路）时额外保留「专业 / 求职意向 / 学历 / 证书」
    这类岗位颗粒度行——它们不是个人信息，正是洞察对齐需要的有效信息。
    两条链路都会先做 PII 脱敏再校验，隐私信息仍然不入库、不回显。
    """
    out: list[str] = []
    for line in (text or "").splitlines()[:MAX_LINES]:
        s = line.strip()
        if not s or len(s) > 500:
            continue
        if _PROFILING_WORDS.search(s):
            continue
        if resume_parse:
            # 简历解析：同行写了身份字段时，只删掉身份字段本身，保留同一行的岗位字段。
            # 例如「姓名：张三 求职意向：数据分析师」仍应解析出求职意向。
            s = re.sub(r"\s{2,}", " ", _IDENTIFIER_FIELD_RE.sub(" ", s)).strip()
            if not s or _IDENTIFIER_LINE.search(s):
                continue
            if not (_JOB_CONTEXT.search(s) or _RESUME_CONTEXT.search(s)):
                continue
        else:
            # 截图 / 入库链路保持严格：身份 / 联系方式整行剔除，语境词规则不变
            if _IDENTIFIER_LINE.search(s):
                continue
            if _PERSON_CONTEXT.search(s) or not _JOB_CONTEXT.search(s):
                continue
        clean = user_jd.redact(s)[0]
        if privacy.scan_pii(clean):
            continue
        out.append(clean)
    return out


def _clean_value(val: str, limit: int = 30) -> str:
    val = (val or "").strip(" :：,，;；。（）()\t")
    if not val or _BAD_VALUE_PREFIX.match(val) or _ORG_NOISE.search(val):
        return ""
    if privacy.scan_pii(val):
        return ""
    return val[:limit]


def _first_match(pattern: re.Pattern, lines: list[str], limit: int = 30) -> str:
    """从允许的岗位行里取第一个结构化字段值；身份行已被 job_lines 排除。"""
    for line in lines:
        m = pattern.search(line)
        if not m:
            continue
        val = _clean_value(m.group(1) or "", limit)
        if val:
            return val
    return ""


def _all_values(pattern: re.Pattern, lines: list[str], limit: int = 60) -> list[str]:
    out: list[str] = []
    for line in lines:
        for m in pattern.finditer(line):
            val = _clean_value(m.group(1) or "", limit)
            if val and val not in out:
                out.append(val)
    return out


def _canon_cert(value: str) -> str:
    key = re.sub(r"[\s\-_（）()·、]", "", value).lower()
    return _CERT_ALIASES.get(key, value.strip())


def _dedupe_certs(certs: list[str]) -> list[str]:
    out: list[str] = []
    # 长名优先，避免先收了「CET-4」又收「大学英语四级（CET-4）」
    for cert in sorted(certs, key=lambda x: (-len(x), x)):
        canon = _canon_cert(cert)
        if not canon or _CERT_NOISE.match(canon) or len(canon) > 24:
            continue
        if privacy.scan_pii(canon):
            continue
        if any(canon == kept or canon in kept or kept in canon for kept in out):
            continue
        out.append(canon)
    return out[:12]


def _sanitize_experience_line(line: str) -> str:
    """把项目/实习行压成短句：去掉项目符号、标签和机构名，隐私命中即整条丢弃。"""
    s = re.sub(r"^[\-•·\d.、\s]+", "", (line or "").strip())
    m = _EXP_LABEL_RE.match(s)
    if m:
        s = m.group(1)
    # 公司 / 学校名匿名化：保留做了什么、用了什么、结果是什么
    s = re.sub(r"[\u4e00-\u9fa5A-Za-z0-9（）()]{2,24}(?:股份有限公司|有限公司|集团|事务所)", "某单位", s)
    s = re.sub(r"[\u4e00-\u9fa5]{2,12}(?:大学|学院|学校)", "某学校", s)
    s = re.sub(r"\s+", " ", s).strip(" ：:；;，,")
    if len(s) < 6 or len(s) > 120:
        return ""
    if _IDENTIFIER_LINE.search(s) or privacy.scan_pii(s) or _PROFILING_WORDS.search(s):
        return ""
    return s


def _extract_experience_context(lines: list[str]) -> list[str]:
    """从项目 / 实习 / 工作经历段落提取关键上下文短句，不返回整段原文。"""
    out: list[str] = []
    in_section = False

    def consider(raw: str):
        s = (raw or "").strip()
        if not s or len(s) > 240:
            return
        low = s.lower()
        has_skill = any(w.lower() in low for skill in user_jd.SKILLS for w in skill["words"])
        if not (_EXP_LABEL_RE.match(s) or _EXP_ACTION_RE.search(s) or has_skill):
            return
        clean = _sanitize_experience_line(s)
        if not clean:
            return
        dup = next((i for i, kept in enumerate(out) if clean in kept or kept in clean), None)
        if dup is None:
            out.append(clean[:120])
        elif len(clean) > len(out[dup]):
            out[dup] = clean[:120]

    for line in lines:
        s = (line or "").strip()
        # 标题独占一行：进入/退出经历段落
        if _EXP_SECTION_RE.match(s):
            in_section = True
            continue
        if _EXP_OTHER_SECTION_RE.match(s):
            in_section = False
            continue
        # 标题与内容同行：标题本身开启段落，并把同行内容当作经历句处理
        inline = _EXP_INLINE_RE.match(s)
        if inline:
            in_section = True
            consider(inline.group(2))
            continue
        if not in_section:
            continue
        consider(s)
    # 兜底：标题写法未被识别时，仍从整份简历里抓带动作/技能/项目标签的关键句。
    # 只取已通过 PII 过滤的岗位行，不回显原文，也不生成用户画像。
    if not out:
        for line in lines:
            s = (line or "").strip()
            if _EXP_SECTION_RE.match(s) or _EXP_OTHER_SECTION_RE.match(s):
                continue
            if re.match(r"^(求职意向|求职方向|薪资意向|专业|学历|证书|荣誉|技能)", s):
                continue
            consider(s)
    return out[:8]


_DEGREE_WORD_RE = re.compile(r"博士|硕士|研究生|本科|大专|学士|专科", re.I)
_DATE_RANGE_RE = re.compile(
    r"(?:19|20)\d{2}\s*[年./-]\s*\d{1,2}\s*月?\s*[-—~至到]\s*"
    r"(?:(?:19|20)\d{2}\s*[年./-]\s*)?\d{1,2}\s*月?", re.I)
_SCHOOL_NAME_RE = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9（）()]{2,24}(?:大学|学院|学校|校区)", re.I)


def _infer_education_facts(lines: list[str], facts: dict[str, Any]) -> None:
    """从无标签的教育行推断专业 / 学历，例如「某某理工大学 计算机科学与技术（本科） 2023年9月-2027年6月」。

    学校名、日期区间都是身份/背景噪声，只保留专业与学历；已有显式字段时不覆盖。
    """
    for line in lines:
        if facts["major"] and facts["education"]:
            return
        # 只在教育语境行里推断，避免把「求职意向：XX」或「教育经历：」标题当成专业
        if not (_DEGREE_WORD_RE.search(line) or re.search(r"教育|学历|学位", line, re.I)):
            continue
        if re.fullmatch(r"(?:教育经历|教育背景|教育|学历|学位)\s*[:：]?", line.strip(), re.I):
            continue
        raw = _SCHOOL_NAME_RE.sub(" ", line)
        raw = _DATE_RANGE_RE.sub(" ", raw)
        raw = re.sub(r"[\s|/]+", " ", raw).strip(" ：:，,；;（）()")
        if not raw:
            continue
        degree = ""
        m = _DEGREE_WORD_RE.search(raw)
        if m:
            degree = m.group(0)
            if not facts["education"]:
                facts["education"] = degree
            raw = raw[:m.start()] + raw[m.end():]
        raw = re.sub(r"[（）()\[\]【】]", " ", raw)
        raw = re.sub(r"\s+", " ", raw).strip(" ：:，,；;")
        if not facts["major"] and 2 <= len(raw) <= 20 and _clean_value(raw, 20):
            facts["major"] = raw


def _extract_job_facts(lines: list[str]) -> dict[str, Any]:
    """读取求职意向 / 薪资意向 / 专业 / 学历 / 证书 / 荣誉等岗位颗粒度字段。

    只从已过滤的岗位语境行取值，且每个值再过一次 PII 校验；不回显所在整行原文。
    """
    facts: dict[str, Any] = {
        "target_role": _first_match(_ROLE_RE, lines),
        "salary_expectation": _first_match(_SALARY_RE, lines, limit=30),
        "major": _first_match(_MAJOR_RE, lines),
        "education": _first_match(_EDU_RE, lines, limit=12),
        "honors": _all_values(
            re.compile(r"(?:荣誉|奖项|获奖|奖励)\s*[:：]?\s*([^\n；;]{2,40})", re.I),
            lines, limit=40)[:8],
        "experience_context": _extract_experience_context(lines),
        "certificates": [],
    }
    # 无「专业：/学历：」标签但在教育行里写了专业与学位时，补抽一次（学校名已剔除）
    _infer_education_facts(lines, facts)

    certs: list[str] = []
    for line in lines:
        low = line.lower()
        for known in _CERT_KNOWN:
            if known.lower() in low:
                certs.append(known)
        for m in _CERT_GENERIC_RE.finditer(line):
            certs.append(m.group(1) or "")
    facts["certificates"] = _dedupe_certs(certs)
    return facts


def to_coarse_job_profile(text: str) -> dict[str, Any]:
    """把文件 / OCR 文本转成粗粒度岗位信息。原文与个人信息都不进入返回值。"""
    raw = (text or "")[:MAX_TEXT]
    # 简历解析链路：保留专业 / 求职意向 / 证书等岗位颗粒度行（仍先脱敏再校验）。
    lines = _job_lines(raw, resume_parse=True)
    facts = _extract_job_facts(lines)

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

    # 目标岗位优先取显式的求职意向 / 目标岗位，再回退到 JD 的岗位名称
    title_hint = facts["target_role"]
    if not title_hint:
        for line in lines[:20]:
            m = re.search(r"(岗位名称|岗位|职位|招聘|title)\s*(?:名称)?\s*[:：]\s*(.{2,30})", line)
            if m:
                title_hint = m.group(2).strip()[:30]
                break

    # 级别只按岗位语境判断，证书名里的“高级”不能把用户误标成资深
    senior_hint = re.compile(r"(资深|高级工程师|高级开发工程师|高级数据|senior|lead|专家岗)", re.I)
    junior_hint = re.compile(r"(初级|实习|junior|应届)", re.I)
    level_hint = "unspecified"
    if any(senior_hint.search(x) for x in lines):
        level_hint = "senior"
    elif any(junior_hint.search(x) for x in lines):
        level_hint = "junior"

    names = [s["name"] for s in skills[:8]]
    summary = "该岗位大致涉及 %s 等技能方向（粗粒度岗位信息，不含个人信息与能力评估）。" % (
        "、".join(names) if names else "通用技术方向")

    # 这次请求内交给专家 agent 的岗位上下文：只含结构化、已去重、已脱敏的岗位字段。
    # 不写向量库、不落盘、不带 OCR 原文，也不产出用户画像字段。
    context_parts: list[str] = []
    if facts["target_role"]:
        context_parts.append("求职意向：" + facts["target_role"])
    if facts["salary_expectation"]:
        context_parts.append("薪资意向（用户自填）：" + facts["salary_expectation"])
    if facts["major"]:
        context_parts.append("专业：" + facts["major"])
    if facts["education"]:
        context_parts.append("学历：" + facts["education"])
    if facts["experience_context"]:
        context_parts.append("项目/实习上下文：" + "；".join(facts["experience_context"][:5]))
    if facts["certificates"]:
        context_parts.append("证书：" + "、".join(facts["certificates"]))
    if facts["honors"]:
        context_parts.append("荣誉/奖项：" + "、".join(facts["honors"]))
    if names:
        context_parts.append("技能方向：" + "、".join(names))
    if level_hint != "unspecified":
        context_parts.append("级别：" + level_hint)
    expert_context = "；".join(context_parts)[:1400]

    result = {
        "job_title_hint": title_hint,
        "target_role": facts["target_role"],
        "salary_expectation": facts["salary_expectation"],
        "major": facts["major"],
        "education": facts["education"],
        "certificates": facts["certificates"],
        "honors": facts["honors"],
        "experience_context": facts["experience_context"],
        "skill_areas": skills[:12],
        "level_hint": level_hint,
        "coarse_summary": summary,
        "expert_context": expert_context,
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
        "expert_context_scope": "仅本次请求内作为专家解读上下文；不写向量库、不落盘",
        "user_data_vectorized": False,
    }
    return result


def job_only_lines(text: str) -> list[str]:
    """截图 / OCR 入库前的硬闸门：只留岗位信息行，个人信息行整行剔除。

    被 rag_store.ingest(origin='local_ocr') 调用，保证「隐私信息不要入库」。
    """
    return _job_lines(text)


def status() -> dict[str, Any]:
    return {"ok": True, **POLICY}

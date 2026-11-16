#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 受控研究采集护栏（技术文档 §2.6）

2026-11-16 项目所有者确立全项目条款（`usage_policy.py`）：
  **「本项目仅用于研究与学习，不得用于任何商业行为」**。
  本模块是 B2 的落地处，故把原先含糊的「毕设 / 非商用 / 实验」表述改写为该成文条款。
  该条款**收紧**目的限定、禁止商业化与对外分发，但**不是合规豁免**、不替代任何书面结论、
  不改变回执闸门状态，也不解锁自动采集执行体与规避检测手段。其余待决事项同理适用。

核心判断：**「主要用于研究、不商用」不是合规豁免条款，但确实改变了合规论证的方向。**
  - 《个人信息保护法》第 13 条列举的合法性基础中没有笼统的「研究 / 非商用」一项；
    最贴近的是第(六)项「在合理的范围内处理个人自行公开或者其他已经合法公开的个人信息」，
    《民法典》第 999 条、第 1036 条对具备公共利益性质的科学研究亦可作为论证依据。
  - 但不论是否适用，第 5、6、7、17 条的合法正当必要、目的限定、最小范围与告知义务
    一律不免除；《网络数据安全管理条例》第 18 条关于自动化工具「评估对网络服务的影响、
    不得干扰」的义务同样不因非商用而消失。
  - 所以「研究」不是解锁开关，而是**一组更严格的举证条件**：目的限定、去标识化、
    最小留存、不对外分发、不用于个人画像、全程留痕。本模块把这些条件代码化。

关于「控制速度」：限速不是为了躲过风控，而是履行《条例》第 18 条「不得干扰网络服务
正常运行」的过程性证据。因此这里采用真实 UA 标识、单域名串行、Crawl-delay 优先、
每日总量上限与可核查的节奏估算；**不提供** UA 轮换、隐身、验证码或 Cloudflare 绕过。

2026-11-16 追加（所有者「自担风险直接放行 B2，但截图不能太快、必须严格控制速度」）：
  - 放行的是**工程决策闸门**，由 `.privacy/approvals/B2.json` 只读记录，
    `issuer=项目所有者`、`risk_bearing=project_owner`、`legal_review=pending`，
    **不冒充法务书面结论或站点书面许可**（两者仍未回）。
  - 「严格控制速度」升级为代码级硬契约 `PACE_CONTRACT` + `enforce_pace()`：
    单域名串行 1 / burst 1 / 每页间隔 ≥ max(15s, 人类阅读静置 12s, Crawl-delay) /
    每日上限 60 / 单批次 10 页 / 批次冷却 300s / 真实 UA / 每页留痕。
  - 「模拟人类」只按唯一合规含义落地为 **human-scale pacing（人类可读的低频节奏）**：
    一次一页、慢速、可核查。**不是**伪装真人身份、按「刚好不被发现」调参、随机抖动
    躲风控、UA / IP 轮换、隐身或绕过验证码 / WAF；这些仍在 `PERMANENTLY_FORBIDDEN`，
    所有者自担风险也不解除。

截图的数据生命周期：位图 → 浏览器本地 OCR → 位图立即从内存释放 → 用户核对文本
→ PII 脱敏 → 加密片段与稀疏向量入本地 RAG → TTL 到期 / 一键删除。
服务端自始没有图片上传端点，也不保留原始截图。

执行动作保持 fail-closed：**执行体存在 ≠ 生产采集解锁**。本轮按所有者指定接入了
`capture_executor.py` 真实截图执行体，但作用域被硬限定为「本地自建测试页 + 公开声明
允许爬取的练习沙盒 books.toscrape.com」；招聘网站、真实业务站点与任何其他 host
在代码层拒绝。生产采集仍须站点书面许可 + 法务书面结论 + 注册表 approved，
故 `effective=false`、生产 `requests_issued` 恒为 0；实跑的沙盒请求另以
`sandbox_test_requests_total` 如实记录。本模块自身仍只产出「采集计划与限速核算」，
不打开任何页面。

2026-11-16「截图测试只用允许爬取的测试网页、不要去招聘网站」：
  - `is_allowed_test_target()` 只允许：本地测试页 `TEST_PAGE_PATH`、显式精确白名单、
    以及 `SANDBOX_SITES` 中经核实的练习沙盒公开分页路径；
    招聘站点关键词 / 非白名单 host / 非白名单路径 / query 注入一律代码层拒绝。
  - `tests/zy_capture_pace_test.py`：本地自建测试页限速自测（零第三方请求）。
  - `tests/zy_capture_executor_test.py`：真实执行体对 books.toscrape.com 限速截图，
    先过七道闸门 + robots best-effort + 页间真实等待 + 每页留痕 + 位图即弃，
    不解析文本、不 OCR、不入 RAG、不产出生产数据。
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import sources
import usage_policy   # 全项目「仅限研究学习 / 禁止商业行为」统一条款

ROOT = Path(__file__).resolve().parent
APPROVAL_FILE = ROOT / ".privacy" / "research_approval.json"

SOURCE_ID = "research_capture"
USER_AGENT = "ZhiyinResearch/1.0 (+local-study; contact: project-owner)"

# 条款化后的主张：原先「毕设 / 非商用 / 实验」的含糊表述已改写为成文条款。
# 注意 claim 变明确了，但 is_exemption 仍为 False —— 措辞收紧，不等于解锁。
LEGAL_POSITION: dict[str, Any] = {
    "claim": usage_policy.CLAUSE_ZH,
    "clause_id": usage_policy.CLAUSE_ID,
    "usage_restriction": usage_policy.scope_marker(),
    "is_exemption": False,
    "why_not": [
        "《个人信息保护法》第 13 条的合法性基础采封闭列举，其中没有笼统的「研究」或「非商用」项；"
        "处理个人信息仍须具备同意或其他法定基础。",
        "《网络数据安全管理条例》第 18 条对自动化工具的要求是「评估对网络服务的影响、不得干扰」，"
        "判断对象是访问行为本身，与是否盈利无关。",
        "2025 修订《反不正当竞争法》数据专款下，是否构成实质性替代也不以「收费」为唯一标准。",
        "把 OCR 结果向量化并长期留存，属于《个人信息保护法》第 4 条的「存储、使用、加工」，"
        "反而扩大了处理范围，比一次性浏览需要更充分的合法性基础。",
    ],
    "possible_basis": [
        "《个人信息保护法》第 13 条第(六)项：在合理范围内处理个人自行公开或者其他已经合法公开的个人信息",
        "《民法典》第 999 条、第 1036 条：为公共利益实施的行为中对个人信息的合理使用"
        "（需具备公共利益性质并经利益衡量）",
    ],
    "conditions_attached": [
        "目的限定于研究检索，不得转用于商业产品、个人画像、信用评估或重新识别",
        "最小必要：只保留脱敏后的文本片段与向量，不留原始截图、不留页面 HTML",
        "去标识化：PII 前置脱敏；向量不等同于匿名化，故文本加密落盘并配 TTL 与删除权",
        "如实告知并保留处理记录；对外提供研究结果时须再次去标识化",
        "遵守《网络数据安全管理条例》第 18、24 条与站点条款、robots.txt、Crawl-delay",
    ],
    "disclaimer": "以上为工程侧整理的法规理解，不构成法律意见；是否适用须由法务书面确认。",
}

# 研究模式准入条件（全部满足才可能解锁，且仍需注册表状态变更）
ADMISSION_CONDITIONS = [
    "法务书面结论：明确本次研究采集的合法性基础、允许的具体域名 / 路径 / 字段与「合理范围」边界",
    "站点书面使用许可或合作函；robots.txt 允许目标路径，Crawl-delay / Request-rate 已记录并服从",
    "sources.py 注册表中 research_capture.legal_status 由 pending_legal 改为 approved（需代码评审）",
    ".privacy/research_approval.json 存在且含法务结论编号 approval_ref、批准人、范围与有效期",
    "限速参数经评估确认：单域名并发 = 1、间隔 ≥ Crawl-delay、每日总量设上限、真实 UA 可联络",
    "采集范围锁定无需登录、公开可见页面；不涉及登录墙、风控、验证码与字体反爬",
    "数据去向锁定：本地 OCR → 位图即弃 → PII 脱敏 → 加密片段入本地 RAG → TTL → 一键删除",
    "不对外分发原始片段或向量；研究结果对外提供前再次去标识化",
]

# 无论研究用途如何，这些一律不做
PERMANENTLY_FORBIDDEN = [
    "复用或伪造登录态、抓取登录墙内内容",
    "为破解字体反爬 / 私用区码位映射而截图或做字形还原",
    "绕过验证码、风控、Cloudflare 或任何技术保护措施",
    "UA / IP 轮换、隐身模式、伪装真人身份、随机抖动躲风控等**规避检测式**人类行为模拟"
    "（注意：PACE_CONTRACT 的『人类可读低频节奏』只指慢速串行，不属于本条，且不得用于规避检测）",
    "把限速参数按『刚好不被发现』调参，或以『避免封禁』作为限速目的",
    "定时 / 巡航式无人值守批量截图，且无每日总量上限",
    "把截图 OCR 结果聚合成需求热度、同比、薪资或蓝海岗位排序",
    "把位图发送到服务端、云端模型或第三方 OCR 服务",
]

# 限速默认值：不是「刚好不被发现」，而是明显低于影响站点正常服务的量级
RATE_DEFAULTS: dict[str, Any] = {
    "concurrency_per_host": 1,
    "min_interval_s": 10.0,
    "burst": 1,
    "daily_cap": 120,
    "robots_crawl_delay_takes_precedence": True,
    "user_agent": USER_AGENT,
    "ua_rotation": False,
    "jitter": "仅用于错峰，不用于规避检测；不得缩短 Crawl-delay",
}

# 所有者自担风险放行 B2 的只读记录（人线下放置，代码不提供写入 / 自我授权接口）
OWNER_RELEASE_FILE = ROOT / ".privacy" / "approvals" / "B2.json"

# ---------------------------------------------------------------------------
# B2 严格限速硬契约（2026-11-16，所有者「自担风险放行」的附加条件）
#
# 用户原话：「截图不能太快必须严格控制速度模拟人类」。
# 「模拟人类」按唯一合规的含义落地为 **human-scale pacing（人类可读的低频节奏）**：
# 一次一页、单域名串行、每页静置到明显长于机器请求、全程留痕可核查。它对应
# 《网络数据安全管理条例》第 18 条「不干扰网络服务正常运行」的过程控制。
#
# 明确**不是**：伪装真人身份、按「刚好不被发现」调参、随机抖动躲风控、UA / IP
# 轮换、隐身、验证码 / WAF / Cloudflare 绕过 —— 这些落在 PERMANENTLY_FORBIDDEN，
# 所有者自担风险也不解除。
# ---------------------------------------------------------------------------
PACE_CONTRACT: dict[str, Any] = {
    "id": "PACE-CONTRACT-2026-11-16",
    "definition": "人类可读的低频节奏：一次一页、单域名串行、间隔明显长于机器请求、每页留痕可核查",
    "not_definition": "不是伪装身份躲避反爬 / 封禁，不是 UA / IP 轮换、隐身或随机抖动规避检测",
    "purpose": "履行《网络数据安全管理条例》第 18 条『不干扰网络服务正常运行』；不是规避检测",
    "concurrency_per_host": 1,
    "burst": 1,
    "min_interval_s": 15.0,
    "human_reading_dwell_s": 12.0,
    "crawl_delay_takes_precedence": True,
    "daily_cap": 60,
    "max_pages_per_batch": 10,
    "batch_cooldown_s": 300,
    "user_agent": USER_AGENT,
    "ua_rotation": False,
    "ip_rotation": False,
    "identity_spoofing": False,
    "randomized_jitter_for_evasion": False,
    "captcha_or_waf_bypass": False,
    "login_wall_or_font_anti_scrape": False,
    "per_page_audit_log": True,
    "manual_toggle_required_each_time": True,
    "auto_capture_default": False,
    "enforced_in_code": True,
    "human_scale_pacing": "enforced",
    "evasion_techniques": "forbidden",
}


class PaceViolation(RuntimeError):
    """限速契约被违反：任何采集动作都必须在此之前停下。"""


def effective_interval_s(crawl_delay_s: float = 0.0) -> float:
    """单页最小间隔 = max(硬性下限, 人类阅读静置, 站点 Crawl-delay)。只会更慢。"""
    return max(PACE_CONTRACT["min_interval_s"],
               PACE_CONTRACT["human_reading_dwell_s"],
               float(crawl_delay_s or 0.0))


def enforce_pace(pages: int, crawl_delay_s: float = 0.0, *,
                 concurrency: int = 1, burst: int = 1,
                 ua_rotation: bool = False, ip_rotation: bool = False,
                 evasion: bool = False) -> dict[str, Any]:
    """任何采集动作前的硬闸门：违反任一约束即抛，不返回采集计划。

    这是「严格控制速度」的代码级强制，不只是文案；也明确拒绝规避检测类参数。
    """
    reasons: list[str] = []
    n = max(0, int(pages))
    if n < 1:
        reasons.append("NO_PAGES")
    if concurrency != 1:
        reasons.append("CONCURRENCY_MUST_BE_1")
    if burst != 1:
        reasons.append("BURST_MUST_BE_1")
    if ua_rotation or ip_rotation or evasion:
        reasons.append("EVASION_TECHNIQUE_FORBIDDEN")
    if n > PACE_CONTRACT["max_pages_per_batch"]:
        reasons.append("BATCH_TOO_LARGE:%d>%d" % (n, PACE_CONTRACT["max_pages_per_batch"]))
    if n > PACE_CONTRACT["daily_cap"]:
        reasons.append("OVER_DAILY_CAP")
    interval = effective_interval_s(crawl_delay_s)
    if interval < PACE_CONTRACT["min_interval_s"]:
        reasons.append("INTERVAL_TOO_SHORT")
    if reasons:
        raise PaceViolation("PACE_CONTRACT_VIOLATED:" + ";".join(reasons))
    limiter = RateLimiter(interval, 0.0, PACE_CONTRACT["daily_cap"], 1)
    return {
        "ok": True,
        "contract_id": PACE_CONTRACT["id"],
        "pages": n,
        "interval_s": interval,
        "seconds_if_single_batch": round(n * interval, 1),
        "batch_cooldown_s": PACE_CONTRACT["batch_cooldown_s"],
        "daily_cap": PACE_CONTRACT["daily_cap"],
        "example_pace": limiter.estimate(n),
        "human_scale_pacing": "enforced",
        "evasion_techniques": "forbidden",
        "requests_issued": 0,
        "note": "本函数只做准入核算，不打开任何第三方页面；未来接入执行体也须先过此闸门。",
    }


def owner_release_state() -> dict[str, Any]:
    """只读：项目所有者自担风险放行 B2 的工程决策记录。

    刻意不写成法务 / 站点许可：`legal_opinion` 必须为 false，
    `is_legal_or_site_license` 恒为 false。缺文件 / 损坏 / 字段不全一律 fail-closed。
    """
    base = {"present": False, "released": False, "is_legal_or_site_license": False}
    if not OWNER_RELEASE_FILE.is_file():
        return {**base, "reason": "OWNER_RELEASE_MISSING"}
    try:
        data = json.loads(OWNER_RELEASE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**base, "present": True, "reason": "OWNER_RELEASE_UNREADABLE"}
    if not isinstance(data, dict):
        return {**base, "present": True, "reason": "OWNER_RELEASE_NOT_OBJECT"}
    reasons: list[str] = []
    if str(data.get("id", "")).strip() != "B2":
        reasons.append("ID_MISMATCH")
    if not str(data.get("approval_ref", "")).strip():
        reasons.append("APPROVAL_REF_MISSING")
    if not str(data.get("issuer", "")).strip():
        reasons.append("ISSUER_MISSING")
    if data.get("decision") not in ("approved", "approved_with_conditions"):
        reasons.append("DECISION_NOT_APPROVING")
    if data.get("risk_bearing") != "project_owner":
        reasons.append("RISK_BEARING_MISSING")
    if data.get("conditions_met") is not True:
        reasons.append("CONDITIONS_NOT_MET")
    if data.get("legal_opinion") is not False:
        reasons.append("MUST_NOT_CLAIM_LEGAL_OPINION")
    try:
        if date.fromisoformat(str(data.get("valid_until", ""))[:10]) < date.today():
            reasons.append("EXPIRED")
    except (ValueError, TypeError):
        reasons.append("DATE_INVALID")
    released = not reasons
    return {
        "present": True,
        "released": released,
        "reason": "OK" if released else ";".join(reasons),
        "approval_ref": str(data.get("approval_ref", ""))[:64],
        "issuer": str(data.get("issuer", ""))[:64],
        "decision": str(data.get("decision", ""))[:32],
        "risk_bearing": str(data.get("risk_bearing", ""))[:32],
        "legal_opinion": False,
        "legal_review": str(data.get("legal_review", "pending"))[:32],
        "is_legal_or_site_license": False,
        "conditions": [str(c)[:120] for c in data.get("conditions", [])][:8]
                      if isinstance(data.get("conditions"), list) else [],
        "note": "项目所有者自担风险的工程决策记录，不是法务书面结论或站点书面许可；"
                "站点许可与法务结论仍未回，风险由项目所有者承担。",
    }


# ---------------------------------------------------------------------------
# 截图测试目标白名单（2026-11-16 用户要求）
#   「截图测试拿允许爬取的测试网页测试，不要去招聘网站」
#   本轮追加：「截图爬取测试网站 https://books.toscrape.com/」
#
# 规则：**只允许两类目标** —— 本机自建测试页，或经核实「明确允许爬取」的
# 外部练习沙盒。任何招聘 / 真实站点关键词、任何其他 host、任何非白名单路径
# 一律拒绝；「不去招聘网站」是代码级硬约束，不是口头约定。
#
# books.toscrape.com 是 Zyte / Scrapinghub 提供的公开爬虫练习沙盒：
#   页面标题含 "Sandbox"，页面自述用于 "scraping purposes"，并明确说明
#   "Prices and ratings here were randomly assigned and have no real meaning"；
#   内容为虚构书目 + 随机价格 / 评分，无真实个人信息，也不是招聘网站。
#   核实日期 2026-11-16。证据见 SANDBOX_SITES["books.toscrape.com"]。
# 站点公开声明允许练习爬取，**不等于站点书面许可函或法务书面结论**；
# legal_review=pending 与 real_site_capture 闸门不放宽，如实标注。
# ---------------------------------------------------------------------------
TEST_PAGE_PATH = "/api/capture/test-page"
LOCAL_TEST_TARGETS: tuple[str, ...] = (
    "http://127.0.0.1:8848" + TEST_PAGE_PATH,
    "http://localhost:8848" + TEST_PAGE_PATH,
)
FORBIDDEN_TARGET_KEYWORDS: tuple[str, ...] = (
    "zhipin", "boss", "lagou", "liepin", "51job", "zhaopin", "jobicy", "remoteok",
    "indeed", "linkedin", "glassdoor", "monster", "naukri", "workday",
    "greenhouse", "lever.co", "recruit", "careers", "hiring", "岗位", "招聘",
)

SANDBOX_SITES: dict[str, dict[str, Any]] = {
    "books.toscrape.com": {
        "kind": "external_scraping_sandbox",
        "evidence_url": "https://books.toscrape.com/",
        "evidence_quote": "Books to Scrape - Sandbox；页面自述用于 scraping purposes，"
                          "Prices and ratings here were randomly assigned and have no real meaning",
        "verified_at": "2026-11-16",
        "scheme": "https",
        "allowed_path_prefixes": ("/", "/index.html", "/catalogue/"),
        "allow_query": False,
        "has_real_personal_data": False,
        "is_recruitment_site": False,
        "is_site_written_license": False,   # 公开声明 ≠ 书面许可函（如实）
        "crawl_delay_s": 0.0,               # 未提供 robots Crawl-delay；仍按 ≥15s 契约
        "note": "只对练习沙盒分页截图，不解析入库、不做 OCR、不入 RAG、不产出生产数据",
    },
}

EXTRA_ALLOWED_TEST_TARGETS: tuple[str, ...] = (
    "https://books.toscrape.com/",
    "https://books.toscrape.com/index.html",
    "https://books.toscrape.com/catalogue/page-2.html",
    "https://books.toscrape.com/catalogue/page-3.html",
)


def sandbox_page_urls(pages: int) -> list[str]:
    """生成练习沙盒的分页目标（仅用于准入核算，不发起请求）。"""
    n = max(1, min(int(pages), PACE_CONTRACT["max_pages_per_batch"]))
    urls = ["https://books.toscrape.com/"]
    for i in range(2, n + 1):
        urls.append("https://books.toscrape.com/catalogue/page-%d.html" % i)
    return urls

LOCAL_TEST_PAGE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>职引者 · 本地限速截图测试页（允许爬取）</title>
<style>body{font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;margin:0;
background:#f5f5f2;color:#22252a}main{max-width:720px;margin:0 auto;padding:40px 28px}
h1{font-size:22px;letter-spacing:.5px} .tag{display:inline-block;background:#d8f0c8;
padding:4px 10px;border-radius:3px;font-size:12px;margin-bottom:18px}
.card{background:#fff;border:1px solid #d9d9d2;border-radius:6px;padding:18px 20px;margin:14px 0;
font-size:14px;line-height:1.8}small{color:#5b6875}</style></head>
<body><main><div class="tag">LOCAL TEST PAGE · 允许爬取 · 非招聘网站</div>
<h1>本地限速截图测试页 / PACE TEST FIXTURE</h1>
<p>本页由本项目自带、仅供「严格限速截图链路」的本地自测使用，<b>不涉及任何第三方站点、
不含真实个人信息、不是招聘网站</b>。页面自述允许被本项目抓取与截图。</p>
<div class="card"><b>样本岗位 A</b><br>岗位方向：数据工程实习（虚构）<br>
技能方向：Python / SQL / 数据管道<br>级别词：初级</div>
<div class="card"><b>样本岗位 B</b><br>岗位方向：前端开发实习（虚构）<br>
技能方向：TypeScript / React / 可访问性<br>级别词：初级</div>
<div class="card"><b>样本岗位 C</b><br>岗位方向：产品研究助理（虚构）<br>
技能方向：用户研究 / 文档整理<br>级别词：入门</div>
<p><small>以上全部为虚构演示文本，无姓名、电话、邮箱、学校、公司等个人信息。
截图仅用于核验访问节奏（串行、间隔、留痕），位图用完即弃、不上传、不入 RAG。</small></p>
</main></body></html>"""


def is_allowed_test_target(url: str) -> dict[str, Any]:
    """只读判定：能否作为截图测试目标。

    只允许：① 本地自建测试页；② 显式精确白名单；③ 经核实的练习沙盒 host
    下的公开分页路径。招聘关键词命中、任何其他 host、任何 query / fragment
    一律在代码层拒绝。
    """
    raw = str(url or "").strip()
    try:
        parsed = urllib.parse.urlparse(raw)
    except ValueError:
        return {"allowed": False, "reason": "URL_INVALID", "host": ""}
    host = (parsed.hostname or "").lower()
    scheme = (parsed.scheme or "").lower()
    low = raw.lower()
    if any(k in low for k in FORBIDDEN_TARGET_KEYWORDS):
        return {"allowed": False, "reason": "RECRUITMENT_OR_REAL_SITE_FORBIDDEN",
                "host": host}
    if scheme not in ("http", "https"):
        return {"allowed": False, "reason": "SCHEME_NOT_ALLOWED", "host": host}
    if raw in EXTRA_ALLOWED_TEST_TARGETS:
        return {"allowed": True, "reason": "OK_EXPLICIT_TEST_ALLOWLIST", "host": host,
                "sandbox": host in SANDBOX_SITES,
                "note": "显式加入的允许爬取测试页，仍须服从限速契约与逐次手动开关"}
    site = SANDBOX_SITES.get(host)
    if site:
        if scheme != site["scheme"]:
            return {"allowed": False, "reason": "SANDBOX_SCHEME_MISMATCH", "host": host}
        if (parsed.query or parsed.fragment) and not site["allow_query"]:
            return {"allowed": False, "reason": "SANDBOX_QUERY_FORBIDDEN", "host": host}
        if not any(parsed.path.startswith(p) for p in site["allowed_path_prefixes"]):
            return {"allowed": False, "reason": "SANDBOX_PATH_NOT_ALLOWED", "host": host}
        return {"allowed": True, "reason": "OK_SCRAPING_SANDBOX", "host": host,
                "sandbox": True, "evidence_url": site["evidence_url"],
                "evidence_quote": site["evidence_quote"],
                "note": "公开声明允许爬取的练习沙盒；仍受 ≥15s 限速契约、真实 UA、"
                        "逐次手动开关与位图即弃约束。不是站点书面许可函。"}
    if host not in ("127.0.0.1", "localhost", "::1"):
        return {"allowed": False, "reason": "NON_LOCAL_HOST_FORBIDDEN", "host": host}
    if parsed.query or parsed.fragment:
        return {"allowed": False, "reason": "LOCAL_QUERY_FORBIDDEN", "host": host}
    if parsed.path.rstrip("/") != TEST_PAGE_PATH.rstrip("/"):
        return {"allowed": False, "reason": "PATH_NOT_IN_TEST_ALLOWLIST", "host": host}
    return {"allowed": True, "reason": "OK", "host": host, "sandbox": False,
            "note": "本地自建测试页，项目自有、明确允许爬取；不涉及任何第三方或招聘网站"}


def pace_test_plan(pages: int = 2, urls: list[str] | None = None) -> dict[str, Any]:
    """限速截图测试的准入核算：白名单 + 硬契约 + 目标清单。**不发起任何请求**。

    不传 urls 时只用本地自建测试页；传入时逐条校验，供练习沙盒执行体使用。
    """
    if urls:
        targets = [str(u) for u in urls]
        n = len(targets)
        pace = enforce_pace(n)
    else:
        pace = enforce_pace(pages)
        n = max(0, int(pages))
        targets = [LOCAL_TEST_TARGETS[0] for _ in range(n)]
    checks = [is_allowed_test_target(t) for t in targets]
    return {
        "ok": True,
        "contract_id": PACE_CONTRACT["id"],
        "pages": n,
        "interval_s": pace["interval_s"],
        "seconds_if_single_batch": pace["seconds_if_single_batch"],
        "targets": targets,
        "target_checks": checks,
        "all_targets_allowed": all(c["allowed"] for c in checks),
        "third_party_requests": 0,
        "recruitment_sites": 0,
        "test_page_path": TEST_PAGE_PATH,
        "sandbox_sites": {k: {"evidence_url": s["evidence_url"],
                              "evidence_quote": s["evidence_quote"],
                              "is_site_written_license": s["is_site_written_license"]}
                          for k, s in SANDBOX_SITES.items()},
        "extra_allowed_targets": list(EXTRA_ALLOWED_TEST_TARGETS),
        "forbidden_keywords": list(FORBIDDEN_TARGET_KEYWORDS),
        "executor": "test_sandbox_headless_screenshot_only",
        "note": "只允许本地自建测试页与公开声明允许爬取的练习沙盒；招聘网站与任何其他 "
                "host 在代码层被拒绝。本函数只做准入核算，实际截图由独立测试脚本执行。",
    }


class CaptureDisabled(RuntimeError):
    """执行闸门：研究采集未获书面批准前，任何截图动作都拒绝。"""


class RateLimiter:
    """单域名串行令牌桶 + 每日总量上限。只做准入核算，不发起网络请求。"""

    def __init__(self, min_interval_s: float = 10.0, crawl_delay_s: float = 0.0,
                 daily_cap: int = 120, burst: int = 1):
        self.interval = max(float(min_interval_s), float(crawl_delay_s or 0), 1.0)
        self.rate = 1.0 / self.interval
        self.burst = max(1, int(burst))
        self.daily_cap = max(1, int(daily_cap))
        self._tokens = float(self.burst)
        self._last = time.monotonic()
        self._day = date.today()
        self._used = 0
        self._lock = threading.Lock()

    def acquire(self) -> dict[str, Any]:
        with self._lock:
            today = date.today()
            if today != self._day:
                self._day, self._used = today, 0
            if self._used >= self.daily_cap:
                return {"allowed": False, "reason": "DAILY_CAP_REACHED",
                        "wait_s": 0.0, "used_today": self._used,
                        "daily_cap": self.daily_cap}
            now = time.monotonic()
            self._tokens = min(float(self.burst),
                               self._tokens + (now - self._last) * self.rate)
            self._last = now
            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.rate
                return {"allowed": False, "reason": "RATE_LIMIT_WAIT",
                        "wait_s": round(wait, 2), "used_today": self._used,
                        "daily_cap": self.daily_cap}
            self._tokens -= 1.0
            self._used += 1
            return {"allowed": True, "reason": "OK", "wait_s": 0.0,
                    "used_today": self._used, "daily_cap": self.daily_cap,
                    "interval_s": self.interval}

    def estimate(self, pages: int) -> dict[str, Any]:
        n = max(0, int(pages))
        remaining = max(0, self.daily_cap - self._used)
        days = math.ceil(n / max(1, self.daily_cap))
        return {
            "pages": n,
            "interval_s": self.interval,
            "seconds_if_single_session": round(n * self.interval, 1),
            "daily_cap": self.daily_cap,
            "remaining_today": remaining,
            "days_required": days,
            "note": "按单域名串行、每页一次请求估算；实际须再服从 robots.txt 的 Crawl-delay。",
        }


def _approval_record() -> dict[str, Any]:
    """批准记录由法务 / 合规负责人线下放置；本模块只读取，不提供写入接口。"""
    if not APPROVAL_FILE.is_file():
        return {"present": False}
    try:
        data = json.loads(APPROVAL_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"present": True, "valid": False}
    if not isinstance(data, dict) or not str(data.get("approval_ref", "")).strip():
        return {"present": True, "valid": False}
    return {
        "present": True,
        "valid": True,
        "approval_ref": str(data.get("approval_ref"))[:64],
        "approved_by": str(data.get("approved_by", ""))[:64],
        "scope": str(data.get("scope", ""))[:160],
        "valid_until": str(data.get("valid_until", ""))[:32],
    }


def gate_state() -> dict[str, Any]:
    src = sources.SOURCES.get(SOURCE_ID, {})
    approval = _approval_record()
    status = src.get("legal_status", "unregistered")
    ready = status == "approved" and approval.get("valid") is True
    return {
        "source_id": SOURCE_ID,
        "legal_status": status,
        "written_approval": approval,
        "ready": ready,
        "gate": "fail-closed",
        "note": "两项闸门（注册表 approved + 书面批准记录）必须同时满足；工程侧不提供自我授权入口。",
    }


def research_policy() -> dict[str, Any]:
    """研究用途采集策略；verdict 由闸门状态实时推导，不缓存、不手工开关。"""
    gate = gate_state()
    verdict = "ready" if gate["ready"] else "pending_legal"
    limiter = RateLimiter(RATE_DEFAULTS["min_interval_s"], 0.0,
                          RATE_DEFAULTS["daily_cap"], RATE_DEFAULTS["burst"])
    return {
        "ok": True,
        "verdict": verdict,
        "gate": gate,
        "legal_position": LEGAL_POSITION,
        "admission_conditions": ADMISSION_CONDITIONS,
        "rate_control": {**RATE_DEFAULTS,
                         "purpose": "履行《条例》第 18 条『不干扰网络服务』的过程控制，不是规避检测",
                         "example_pace": limiter.estimate(100)},
        "screenshot_lifecycle": [
            "用户 / 研究者在自己有权查看的页面上手动或按计划打开页面",
            "位图仅存在于内存，交由浏览器本地 OCR 得到文本",
            "位图立即释放；不上传、不落盘、不外发第三方 OCR",
            "OCR 文本由人核对，写入可编辑文本框后才提交",
            "服务端只收文本：PII 脱敏 → 分块 → 稀疏向量 → 加密片段入本地 RAG",
            "TTL 到期自动删除；亦可按文档删除或整库清空，全程写审计哈希链",
        ],
        "permanently_forbidden": PERMANENTLY_FORBIDDEN,
        "unlock_requirements": ADMISSION_CONDITIONS,
        "usage_restriction": usage_policy.scope_marker(),
        "auto_capture_executor": "test_sandbox_only",
        "executor_scope": "仅本地测试页 / 公开声明允许爬取的练习沙盒；生产采集未解锁",
        "production_capture_unlocked": False,
        "owner_risk_release": owner_release_state(),
        "active_pace_contract": PACE_CONTRACT,
        "strict_pace_example": enforce_pace(5),
        "human_scale_pacing": "enforced",
        "evasion_techniques": "forbidden",
        "admin_control": {
            "placement": ADMIN_POLICY["placement"],
            "auto_capture_default": False,
            "toggle_endpoint": "/api/capture/admin/toggle",
            "status_endpoint": "/api/capture/admin",
            "requires_manual_toggle": True,
        },
        "disclaimer": "本表是工程过程控制，不构成法律意见；项目所有者自担风险的工程放行"
                      "不等于法务书面结论或站点书面许可，两者仍未回。",
    }


def plan_capture(paths: list[str]) -> dict[str, Any]:
    """只生成采集计划与限速核算；不打开任何第三方页面。

    会先过 sources.enforce_policy —— research_capture 当前为 pending_legal，
    因此正常调用必然抛 SourceBlocked，这正是 fail-closed 的表现。
    """
    policy = sources.enforce_policy(SOURCE_ID, (paths or ["/"])[0])
    limiter = RateLimiter(policy.get("crawl_delay_s") or RATE_DEFAULTS["min_interval_s"],
                          0.0, RATE_DEFAULTS["daily_cap"], RATE_DEFAULTS["burst"])
    clean_paths = [str(p)[:200] for p in (paths or [])[:RATE_DEFAULTS["daily_cap"]]]
    plan = {
        "ok": True,
        "source_id": SOURCE_ID,
        "user_agent": USER_AGENT,
        "paths": clean_paths,
        "policy": policy,
        "pace": limiter.estimate(len(clean_paths)),
        "executor": "planning_only（本模块不执行页面访问；沙盒执行体见 capture_executor.py）",
    }
    sources.log_assessment("research_capture_planned", SOURCE_ID,
                           {"paths": len(clean_paths), "crawl_delay": policy.get("crawl_delay_s")})
    return plan


def capture_screenshot(url: str = "", operator_ack: bool = False) -> dict[str, Any]:
    """B2 真实执行体的单页入口（2026-11-16 本轮接入）。

    仅当 url 是**白名单练习沙盒 / 本地测试页**且 B2 回执、逐次手动开关、
    operator_ack、限速契约全部齐备时，委托 capture_executor 真实截图；
    其余一切情况（空 URL、招聘网站、真实业务站点、闸门未开）一律拒绝，
    且拒绝路径不发起任何请求。

    **生产采集闸门不放宽**：站点书面许可 / 法务书面结论未回，
    sources.py 中 legal_status 仍为 pending_legal。
    """
    check = is_allowed_test_target(url)
    if not check["allowed"]:
        sources.log_assessment("research_capture_denied", SOURCE_ID,
                               {"reason": check["reason"], "url_host_only": str(url)[:40]})
        raise CaptureDisabled("CAPTURE_TARGET_BLOCKED:" + check["reason"])
    import capture_executor  # 延迟导入，避免与本模块形成循环依赖
    if not capture_executor.readiness(1, operator_ack, [url])["ok"]:
        sources.log_assessment("research_capture_denied", SOURCE_ID,
                               {"reason": "executor_gate_closed", "url_host_only": str(url)[:40]})
        raise CaptureDisabled(
            "EXECUTOR_GATE_CLOSED: 需 B2 所有者回执 + 管理员逐次手动开关 + "
            "operator_ack + 限速契约 + Chrome 可用；默认关闭，不发起任何请求。")
    return capture_executor.run(1, operator_ack=operator_ack, urls=[url])


def _executor_snapshot() -> dict[str, Any]:
    try:
        import capture_executor  # 延迟导入，避免循环依赖
        return capture_executor.status()
    except Exception:
        return {"ok": False, "reason": "EXECUTOR_MODULE_UNAVAILABLE"}


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "source_id": SOURCE_ID,
        "verdict": research_policy()["verdict"],
        "gate": gate_state(),
        "rate_defaults": RATE_DEFAULTS,
        "active_pace_contract": PACE_CONTRACT,
        "owner_risk_release": owner_release_state(),
        "human_scale_pacing": "enforced",
        "evasion_techniques": "forbidden",
        "usage_restriction": usage_policy.scope_marker(),
        "executor": "test_sandbox_only",
        "production_capture_unlocked": False,
        "executor_status": _executor_snapshot(),
        "image_upload_endpoint": False,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

# ---------------------------------------------------------------------------
# 管理员手动开关（2026-11-16 用户要求）
#
# 用户要求：「爬取设置相关页面设置到管理员页面中，不要默认自动爬取，
#            手动开关控制爬取」。本段据此实现：
#   - 设置集中到独立管理员视图；默认 auto_capture=false；
#   - 开关只记录管理员意图，逐次手动、关闭即无待执行任务；
#   - 即便管理员打开开关，生产采集四重闸门仍然生效；执行体只对练习沙盒可用，
#     **不会因此发起任何真实业务站点 / 招聘网站的网页请求**。
#   - 「模拟正常人类访问节奏避免封禁」不作为限速目的；限速只用于履行
#     《网络数据安全管理条例》第 18 条「不干扰网络服务正常运行」。
#   - 2026-11-16 追加：所有者确立全项目条款「本项目仅用于研究与学习，
#     不得用于任何商业行为」（usage_policy.py），替代原先含糊的「毕设 / 非商用 / 实验」。
#     条款收紧目的限定与商业化禁止，但**不是合规豁免**。
#   - 2026-11-16 再追加（本轮）：所有者明确「自担风险直接放行 B2，但截图不能太快、
#     必须严格控制速度模拟人类」。处理：
#       * 放行**工程决策闸门**，由 B2.json 只读记录，如实标注非法律结论 / 非站点许可；
#       * 「严格控制速度」升级为 PACE_CONTRACT + enforce_pace() 代码级硬契约；
#       * 「模拟人类」只落地为慢速串行的**人类可读低频节奏**，
#         规避检测式拟人化（伪装身份 / 抖动躲风控 / UA 轮换 / 绕过 WAF）仍永久禁止；
#       * **默认关闭 + 逐次手动开关不变**（所有者前一轮明确要求）；
#       * 本轮再追加：所有者指定「截图爬取测试网站 books.toscrape.com」并要求
#         「接入 B2 真实执行体」。已接入 capture_executor.py，但作用域被硬限定为
#         练习沙盒；生产采集 `effective` 仍为 false、`requests_issued`（生产请求）
#         恒为 0，实跑的沙盒请求另以 sandbox_requests_total 如实记录。
# ---------------------------------------------------------------------------
ADMIN_POLICY: dict[str, Any] = {
    "placement": "独立管理员视图（不在用户主流程自动出现）",
    "auto_capture_default": False,
    "control": "逐次手动开关；关闭后不保留任何待执行采集任务",
    "switch_effect": "只记录管理员意图，不发起任何第三方请求",
    "rate_control_purpose": "履行《网络数据安全管理条例》第 18 条『不干扰网络服务正常运行』，"
                            "不是规避检测或躲避封禁；本轮升级为 PACE_CONTRACT 代码级硬契约",
    "pace_contract": PACE_CONTRACT,
    "human_scale_pacing": "enforced",
    # 注意：这个字段特指「规避检测式拟人化」（伪装真人 / 抖动躲风控 / UA 轮换），
    # 与上面「人类可读低频节奏」是两件事；所有者自担风险也不解除本项禁止。
    "human_pace_simulation": "forbidden",
    "owner_risk_release": "项目所有者自担风险放行 B2 工程决策闸门（B2.json 只读记录）；"
                          "legal_review=pending，不是法务书面结论或站点书面许可",
    "still_blocked_by": [
        "站点书面使用许可 / 合作函未回（练习沙盒的公开声明不是书面许可函）",
        "法务书面结论未回",
        "sources.py 注册表中 research_capture.legal_status 仍为 pending_legal",
        "生产采集执行体未解锁：capture_executor.scope=test_sandbox_only、"
        "production_capture_unlocked=false",
    ],
    "executor": {
        "implemented": True,
        "scope": "test_sandbox_only",
        "allowed": "本地自建测试页 + 公开声明允许爬取的练习沙盒 books.toscrape.com",
        "production_capture_unlocked": False,
        "entry": "capture_executor.run() / research_capture.capture_screenshot(url, operator_ack)",
        "endpoints": ["/api/capture/executor/status", "/api/capture/executor/run"],
    },
    "usage_restriction": usage_policy.as_dict(),
    "commercial_use_allowed": False,
    "noncommercial_exemption": False,
    "why_not_exemption": [
        "《个人信息保护法》第 13 条未列『研究 / 非商用』为独立合法性基础",
        "《网络数据安全管理条例》第 18 条判断对象是访问行为，与是否盈利无关",
        "把页面内容 OCR 后向量化并留存属于『存储、使用、加工』，处理范围更大",
        "成文条款『仅限研究学习 / 禁止商业行为』收紧用途，但不改变上述判断，"
        "也不替代法务书面结论与站点书面许可",
    ],
    "compliant_alternative": {
        "path": "用户手动截图 → 浏览器本地 OCR → 只保留岗位信息（隐私信息整行剔除、不入库）"
                " → 本地稀疏向量 → 加密片段入本地 RAG → TTL / 一键删除",
        "endpoints": ["/api/rag/ingest (origin=local_ocr)", "/api/rag/query",
                      "/api/rag/delete"],
        "server_side_fetcher": False,
    },
    "uniform_application": "本条款对 B1 / Q18 / Q19 / Q21 / B3 同理适用"
                           "（见 approval_gate.state().usage_restriction 与 5 份回执顶层字段）："
                           "逐项收紧适用范围，但不改变任何 decision 或闸门状态。"
                           "B2 本轮由所有者自担风险放行的是工程决策闸门、严格限速契约，"
                           "以及**仅限练习沙盒**的真实截图执行体；本地 OCR → 岗位信息 → "
                           "本地 RAG → AI 链路继续开放。生产采集、真实业务站点与"
                           "规避检测手段仍未解锁。",
    "disclaimer": "本模块是工程过程控制，不构成法律意见；项目所有者自担风险的工程放行"
                  "不等于法务书面结论或站点书面许可，两者仍未回，风险由项目所有者承担。",
}

_ADMIN_LOCK = threading.Lock()
_ADMIN_STATE: dict[str, Any] = {
    "enabled": False,
    "updated_at": None,
    "last_action": "none",
    "requests_issued": 0,
}


def _admin_gates() -> dict[str, bool]:
    g = gate_state()
    with _ADMIN_LOCK:
        manual = bool(_ADMIN_STATE["enabled"])
    try:
        enforce_pace(1)
        pace_ok = True
    except PaceViolation:
        pace_ok = False
    return {
        "registry_approved": g["legal_status"] == "approved",
        "written_approval_record": g["written_approval"].get("valid") is True,
        "site_written_license": False,       # 站点书面许可 / 合作函未回（如实）
        "legal_written_conclusion": False,   # 法务书面结论未回（如实）
        "owner_risk_release": owner_release_state()["released"],  # 本轮新增：所有者自担风险放行
        "strict_pace_contract": pace_ok,     # 本轮新增：限速硬契约生效
        "admin_manual_toggle": manual,
        "capture_executor_implemented": True,   # 本轮接入真实执行体（仅练习沙盒）
        "production_capture_unlocked": False,  # 生产采集永久需站点许可 + 法务结论
    }


def admin_status() -> dict[str, Any]:
    """管理员面板只读状态：默认关闭、四重闸门、限速口径、合规替代链路。"""
    with _ADMIN_LOCK:
        snap = dict(_ADMIN_STATE)
    gates = _admin_gates()
    limiter = RateLimiter(RATE_DEFAULTS["min_interval_s"], 0.0,
                          RATE_DEFAULTS["daily_cap"], RATE_DEFAULTS["burst"])
    return {
        "ok": True,
        "auto_capture_default": False,
        "toggle": snap,
        "gates": gates,
        "effective": all(gates.values()),
        "requests_issued": 0,
        "policy": ADMIN_POLICY,
        "rate_control": {**RATE_DEFAULTS, "example_pace": limiter.estimate(20)},
        "permanently_forbidden": PERMANENTLY_FORBIDDEN,
        "auto_capture_executor": "test_sandbox_only",
        "executor": _executor_snapshot(),
        "executor_scope": "仅本地测试页 / 公开声明允许爬取的练习沙盒 books.toscrape.com",
        "production_capture_unlocked": False,
        "sandbox_test_requests_total": _executor_snapshot().get("sandbox_requests_total", 0),
        "usage_restriction": usage_policy.scope_marker(),
        "owner_risk_release": owner_release_state(),
        "decision_gate_released": bool(gates["owner_risk_release"]
                                       and gates["strict_pace_contract"]
                                       and gates["admin_manual_toggle"]),
        "active_pace_contract": PACE_CONTRACT,
        "strict_pace_example": enforce_pace(5),
        "human_scale_pacing": "enforced",
        "evasion_techniques": "forbidden",
        "blocked_by": ADMIN_POLICY["still_blocked_by"],
        "note": "项目所有者已自担风险放行 B2 的**工程决策闸门**与严格限速契约，本轮并按其"
                "指定接入真实截图执行体；但执行体作用域被硬限定为本地测试页与公开声明允许"
                "爬取的练习沙盒 books.toscrape.com。站点书面许可函、法务书面结论仍未回，"
                "注册表仍为 pending_legal，因此生产采集 effective=false、"
                "requests_issued（生产请求）恒为 0、production_capture_unlocked=false；"
                "实际发出的沙盒测试请求另以 sandbox_test_requests_total 如实记录。",
        "disclaimer": ADMIN_POLICY["disclaimer"],
    }


def set_admin_toggle(enabled: bool, operator_ack: bool = False) -> dict[str, Any]:
    """逐次手动开关。只记录意图；绝不在此函数内发起网络请求。"""
    with _ADMIN_LOCK:
        _ADMIN_STATE["enabled"] = bool(enabled)
        _ADMIN_STATE["last_action"] = "enabled" if enabled else "disabled"
        _ADMIN_STATE["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _ADMIN_STATE["requests_issued"] = 0
        snap = dict(_ADMIN_STATE)
    gates = _admin_gates()
    return {
        "ok": True,
        "toggle": snap,
        "operator_ack": bool(operator_ack),
        "gates": gates,
        "effective": all(gates.values()),
        "requests_issued": 0,
        "usage_restriction": usage_policy.scope_marker(),
        "owner_risk_release": owner_release_state(),
        "decision_gate_released": bool(gates["owner_risk_release"]
                                       and gates["strict_pace_contract"]
                                       and gates["admin_manual_toggle"]),
        "active_pace_contract": PACE_CONTRACT,
        "strict_pace_example": enforce_pace(5) if enabled else enforce_pace(1),
        "human_scale_pacing": "enforced",
        "evasion_techniques": "forbidden",
        "auto_capture_executor": "test_sandbox_only",
        "executor": _executor_snapshot(),
        "production_capture_unlocked": False,
        "blocked_by": ADMIN_POLICY["still_blocked_by"],
        "reason": "项目所有者已自担风险放行 B2 工程决策闸门，严格限速硬契约生效，"
                  "并接入了仅限练习沙盒的真实截图执行体；但站点书面许可、法务书面结论"
                  "仍未回，注册表仍为 pending_legal，生产采集未解锁。开关本身只记录"
                  "管理员意图，不发起任何请求。",
        "note": "开启开关后可在 /api/capture/executor/run 对练习沙盒执行限速截图；"
                "如需对真实业务站点实际采集，仍须先取得站点书面许可与法务书面结论，"
                "并经代码评审把 research_capture.legal_status 改为 approved。"
                "届时仍需逐次手动开启，默认仍不自动爬取。"
                "「仅限研究学习 / 禁止商业行为」条款收紧用途，但不构成解锁条件。",
        "disclaimer": ADMIN_POLICY["disclaimer"],
    }

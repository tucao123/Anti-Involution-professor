#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 数据源注册与合规闸门（技术文档 §2.3 红线代码化）

设计原则：
1. fail-closed：任何数据源的 legal_status 不是 "approved" 时，代码层面拒绝发起采集，
   法务未回复期间也能安全开发，不会有人误开爬虫。
2. 口径分级：数据能不能回答「国内市场」问题由 grade 决定，而不是由「是否实时」决定。
   D 级（海外远程岗样本）只允许技能词频与方向性参考，禁止同比 / 薪资 / 蓝海岗位结论。
3. 可核查：每次 robots 评估与采集准入都写 append-only 记录，作为
   《网络数据安全管理条例》第 18 条「评估对网络服务的影响」的过程性证据。
4. 本模块只做工程控制，不构成法律意见；所有 "pending" 状态须由法务书面确认后改为 approved。

仅使用标准库，可被 market.py 独立调用。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import robotparser

ROOT = Path(__file__).resolve().parent
LOG_FILE = ROOT / ".privacy" / "compliance.jsonl"
ROBOTS_TIMEOUT = 8.0

# 数据代表性等级（决定「能说什么」，与「数据是否新鲜」无关）
GRADE_RULES: dict[str, dict[str, Any]] = {
    "A": {
        "label": "授权境内全量/大样本",
        "allowed": ["国内就业形势", "需求同比与趋势", "薪资区间", "蓝海岗位排序", "技能词频", "SAFI 象限"],
        "forbidden": [],
        "representative_of_cn": True,
    },
    "B": {
        "label": "境内公开/公共就业样本",
        "allowed": ["样本覆盖范围内的技能词频", "方向性参考", "SAFI 象限"],
        "forbidden": ["全国市场同比", "薪资推断", "平台间横向比较"],
        "representative_of_cn": False,
    },
    "C": {
        "label": "用户自带 JD",
        "allowed": ["该 JD 与简历的能力对齐", "该 JD 的技能抽取"],
        "forbidden": ["任何市场级结论", "岗位推荐排序"],
        "representative_of_cn": False,
    },
    "D": {
        "label": "海外远程岗 / 演示样本",
        "allowed": ["技能词频", "方向性参考", "SAFI 象限"],
        "forbidden": ["国内就业形势", "需求同比与增长", "薪资区间", "蓝海岗位推荐", "被替代风险结论"],
        "representative_of_cn": False,
    },
}

# 数据源注册表。新增任何来源都必须先在这里登记，否则 assert_fetchable 直接拒绝。
SOURCES: dict[str, dict[str, Any]] = {
    "jobicy": {
        "name": "Jobicy Public Remote Jobs API",
        "url": "https://jobicy.com/api/",
        "kind": "public_api",
        "region": "global_remote",
        "grade": "D",
        "legal_status": "approved",
        "legal_basis": "官方公开 API：免密钥、允许商业使用与聚合统计；须保留来源与原始岗位链接",
        "robots_required": False,
        "crawl_delay_s": 3600,
        "retention_days": 90,
        "raw_jd_persist": False,
        "attribution": {"required": True, "text": "Jobicy", "url": "https://jobicy.com/api/", "nofollow": False},
        "limits": "仅返回最近 7 天岗位；单次快照无法计算同比",
    },
    "remoteok": {
        "name": "RemoteOK Public API",
        "url": "https://remoteok.com/api",
        "kind": "public_api",
        "region": "global_remote",
        "grade": "D",
        "legal_status": "approved",
        "legal_basis": "官方公开 API：条款随响应第 0 项返回，要求 followed 回链并注明来源，禁止使用其 logo",
        "robots_required": False,
        "crawl_delay_s": 3600,
        "retention_days": 90,
        "raw_jd_persist": False,
        "attribution": {"required": True, "text": "Remote OK", "url": "https://remoteok.com", "nofollow": False},
        "limits": "仅最新约 100 条岗位；无搜索与分页",
    },
    "mohrss_public": {
        "name": "中国公共招聘网 / 就业在线（公共就业服务）",
        "url": "http://job.mohrss.gov.cn",
        "kind": "public_web",
        "region": "CN",
        "grade": "B",
        "legal_status": "pending_legal",
        "legal_basis": "人社部公开平台，免费向社会开放；但『公开』不等于允许自动化批量采集，须先取得书面使用许可或合作函",
        "robots_required": True,
        "crawl_delay_s": 10,
        "retention_days": 90,
        "raw_jd_persist": False,
        "attribution": {"required": True, "text": "中国公共招聘网", "url": "http://job.mohrss.gov.cn", "nofollow": False},
        "limits": "未获书面许可前禁止任何自动化访问",
    },
    "user_jd": {
        "name": "用户自行提供的目标岗位 JD",
        "url": "",
        "kind": "user_provided",
        "region": "CN",
        "grade": "C",
        "legal_status": "approved",
        "legal_basis": "用户主动提供且授权用于本次分析；仅做技能抽取与能力对齐，不做市场级统计",
        "robots_required": False,
        "crawl_delay_s": 0,
        "retention_days": 90,
        "raw_jd_persist": False,
        "attribution": {"required": False, "text": "", "url": "", "nofollow": False},
        "limits": "需明示用途、可撤回、可删除；未经单独同意不得匿名聚合",
    },
    "licensed_cn_platform": {
        "name": "境内招聘平台商务授权 API / 数据集",
        "url": "",
        "kind": "licensed_api",
        "region": "CN",
        "grade": "A",
        "legal_status": "pending_contract",
        "legal_basis": "须签署数据使用协议；拿到的是合同授权范围内的岗位数据，不等于全平台市场数据",
        "robots_required": False,
        "crawl_delay_s": 0,
        "retention_days": 90,
        "raw_jd_persist": False,
        "attribution": {"required": True, "text": "按合同约定署名", "url": "", "nofollow": False},
        "limits": "合同生效且字段范围、留存、二次分发条款确认后方可启用",
    },
    "domestic_crawler": {
        "name": "境内平台页面采集（技术文档 §2.1 路径 A / B）",
        "url": "",
        "kind": "crawler",
        "region": "CN",
        "grade": "B",
        "legal_status": "blocked",
        "legal_basis": "§2.1 的隐身 Playwright / Cloudflare 绕过 / CDP 登录态复用与 §2.3 红线冲突；"
                       "《网络数据安全管理条例》第 18、39 条与 2025 修订《反不正当竞争法》数据专款下风险高",
        "robots_required": True,
        "crawl_delay_s": 0,
        "retention_days": 90,
        "raw_jd_persist": False,
        "attribution": {"required": True, "text": "", "url": "", "nofollow": False},
        "limits": "法务书面批准并按授权重写方案前，永久禁止实施",
    },
    "user_screenshot": {
        "name": "用户手动截图并主动上传（浏览器本地 OCR）",
        "url": "",
        "kind": "user_provided",
        "region": "CN",
        "grade": "C",
        "legal_status": "approved",
        "legal_basis": "用户对自己有权查看的内容手动截图并主动提供；系统不访问任何第三方页面，"
                       "图片不出浏览器，OCR 结果由用户核对后再走 C 级 user_jd 通道",
        "robots_required": False,
        "crawl_delay_s": 0,
        "retention_days": 0,
        "raw_jd_persist": False,
        "attribution": {"required": False, "text": "", "url": "", "nofollow": False},
        "limits": "仅限单份 JD / 本人简历；不做聚合、不产生市场级结论；图片仅在内存且用完即释放",
    },
    "auto_screenshot": {
        "name": "系统自动打开第三方页面并截图（含定时/批量截图、登录态截图）",
        "url": "",
        "kind": "automated_capture",
        "region": "CN",
        "grade": "B",
        "legal_status": "blocked",
        "legal_basis": "截图只是采集的呈现形式，不改变自动化访问第三方页面的法律性质："
                       "仍受《网络数据安全管理条例》第 18 条与站点条款约束；"
                       "若为绕过字体反爬 / 登录校验而截图，还可能触及第 39 条『避开技术措施』",
        "robots_required": True,
        "crawl_delay_s": 0,
        "retention_days": 0,
        "raw_jd_persist": False,
        "attribution": {"required": True, "text": "", "url": "", "nofollow": False},
        "limits": "永久禁止实施；法务书面批准并按授权重写的也只是『授权 API』路径，不是自动截图",
    },
    "research_capture": {
        "name": "受控研究采集（书面许可 + robots + 限速，仅公开可见页面）",
        "url": "",
        "kind": "research_capture",
        "region": "CN",
        "grade": "B",
        "legal_status": "pending_legal",
        "legal_basis": "『主要用于研究、不商用』不是 PIPL 第 13 条的豁免项；可争取的基础是第 13 条第(六)项"
                       "『合理范围内处理已合法公开的个人信息』及《民法典》第 999 / 1036 条，"
                       "但均附带目的限定、最小必要、去标识化与不对外分发等条件，须法务书面确认",
        "robots_required": True,
        "crawl_delay_s": 10,
        "retention_days": 30,
        "raw_jd_persist": False,
        "attribution": {"required": True, "text": "按书面许可要求署名", "url": "", "nofollow": False},
        "limits": "需法务书面结论 + 站点书面许可 + 书面批准记录 + 注册表状态变更四者齐备；"
                  "执行体当前不存在。禁止登录态、字体反爬、验证码与 UA 轮换；位图 OCR 后立即丢弃",
    },
    "github_public_repos": {
        "name": "GitHub 公开仓库 REST 元数据（匿名只读）",
        "url": "https://api.github.com",
        "kind": "public_api",
        "region": "global_public",
        "grade": "D",
        "legal_status": "approved",
        "legal_basis": "官方公开 REST API 的仓库级结构与语言元数据；匿名只读、不带 token、"
                       "不读取用户资料/私有仓库/代码内容、不绕登录态与限频、保留出处回链；"
                       "工程侧按 Jobicy / RemoteOK 同类公开 API 口径登记，法务 Q18-Q21 书面结论待回，"
                       "期间仅做单仓库实时查询、结果不落盘、不用于个人画像",
        "robots_required": False,
        "crawl_delay_s": 2,
        "retention_days": 0,
        "raw_jd_persist": False,
        "attribution": {"required": True, "text": "GitHub REST API",
                        "url": "https://docs.github.com/rest", "nofollow": False},
        "limits": "匿名限频 60 次/小时；工程侧再限串行 ≥2 秒、每日 60 次；"
                  "禁止批量扫描、用户画像、能力评分与国内市场结论",
    },
    "demo": {
        "name": "内置演示数据集",
        "url": "",
        "kind": "sample",
        "region": "n/a",
        "grade": "D",
        "legal_status": "approved",
        "legal_basis": "本地结构化样例，非抓取数据",
        "robots_required": False,
        "crawl_delay_s": 0,
        "retention_days": 0,
        "raw_jd_persist": False,
        "attribution": {"required": False, "text": "", "url": "", "nofollow": False},
        "limits": "仅用于展示交互闭环",
    },
}

# 交给法务的书面确认清单（可直接作为邮件/工单正文）
LEGAL_QUESTIONS = [
    "目标站点 robots.txt 的 Disallow / Crawl-delay / Request-rate 是否允许按本系统 UA 与路径采集？是否需要书面许可？",
    "站点服务条款与隐私政策是否禁止自动化访问、批量下载、商业性再利用或衍生数据产品？",
    "岗位 JD 集合是否构成平台『合法持有的数据』（2025 修订《反不正当竞争法》第 13 条第 3 款）？聚合统计后展示是否构成实质性替代？",
    "仅访问公开可见、无需登录的页面，是否仍可能触及《网络数据安全管理条例》第 18 条『干扰网络服务正常运行』？频率/并发上限如何界定？",
    "以下手段是否一律禁止：CDP 登录态复用、字体反爬破解、IP/UA 轮换、验证码与风控绕过、Cloudflare 绕过（技术文档 §2.1 路径 A/B）？",
    "采集中不可避免带出的 HR 姓名/电话/邮箱，如何按《条例》第 24 条即时删除或匿名化？留存期限如何定？",
    "原始 HTML / JD 全文是否允许留存？建议只保留聚合统计与来源指纹，90 天是否过长？",
    "向云厂商或 LLM 服务商提供数据，是否需按《条例》第 12 条签署书面协议、监督接收方并保存记录 3 年？",
    "Jobicy / RemoteOK 的 followed 回链与署名义务是否已在产品界面履行？数据境外存储与出境评估是否适用？",
    "JD 文本送入云端模型做叙述性解读，是否满足《条例》第 19 条输入数据安全管理与留痕要求？现有 PII 拦截是否足够？",
    "用户自行上传 JD：授权文本、用途范围、匿名聚合是否需要单独同意？撤回与删除链路是否完整？",
    "中国公共招聘网等政府平台『免费向社会开放』是否覆盖自动化采集与商业产品使用？是否需数据使用许可或合作函？",
    "把自动抓取 HTML/DOM 改成『自动打开页面并截图 + OCR』，是否仍构成《条例》第 18 条意义上的自动化采集？"
    "在平台已部署字体反爬/风控的情形下，用截图取得渲染后文字是否会被认定为第 39 条的『避开技术措施』？",
    "截图比纯文本更容易带出头像、二维码、HR 姓名与页面水印。若允许『用户手动截图上传』，"
    "在浏览器本地 OCR、图片不上传不落盘的前提下，是否仍需单独告知并取得同意？OCR 结果的留存与撤回如何界定？",
    "『主要用于研究、不商用』是否构成个人信息处理的合法性基础？PIPL 第 13 条第(六)项"
    "『合理范围内处理已合法公开的个人信息』与《民法典》第 999 / 1036 条能否适用于本项目？"
    "如可，『合理范围』的目的、字段、留存与对外提供边界应如何书面界定？",
    "把 OCR 文本向量化并存入本地 RAG 检索库，是否属于 PIPL 第 4 条的『存储、使用、加工』？"
    "向量存在反演风险、不等同于匿名化，文本加密 + 默认 30 天 TTL + 单文档删除 / 整库清空是否满足"
    "第 5、6、17 条的最小必要与告知义务？留存期应如何定？",
    "受控研究采集（站点书面许可、robots 允许、单域名并发 1、间隔 ≥ Crawl-delay、每日上限、真实可联络 UA、"
    "仅公开可见页面）能否满足《网络数据安全管理条例》第 18 条『评估影响、不得干扰』？"
    "哪些证据 / 留痕足以证明已履行该义务？",
    "Q21（工程侧新增，待书面确认）：匿名读取 GitHub 公开仓库的语言构成 / 结构元数据，"
    "用于技能方向性参考，是否属于『合理范围内处理已合法公开的信息』？"
    "匿名 REST 限频、不读用户资料、不落盘、不做能力评分与画像的约束是否足够？"
    "如需书面许可或 API Terms 逐条确认，请指出应由 BD 还是法务出具。",
]

# 交给 BD / 采购的授权要点
BD_REQUIREMENTS = [
    "数据范围：JD 字段清单、地域、行业、时间窗；历史深度至少 6 个季度，否则无法计算同比/趋势",
    "授权性质：API / 离线数据集 / 联合研究；明确是否允许聚合统计、对外展示、二次分发与长期留存",
    "主体资质：营业执照、ICP、人力资源服务许可证（若涉招聘中介服务）",
    "合同必备条款：处理目的/方式/范围、安全义务、我方监督权、记录保存 3 年、数据来源合法性担保与不侵权条款",
    "技术条款：QPS 与配额、字段字典、更新频率、SLA、沙箱与试点配额",
    "合规凭证：平台侧个人信息授权链条证明、数据来源合法性声明、是否允许境内存储与云端模型处理",
    "试点：NDA + 最小可用样本 + 明确验收口径，先验证代表性再谈全量",
    "认知对齐：企业侧 ATS / 开放平台接口通常只覆盖本企业有权访问的数据，不等于全平台市场数据",
]


class SourceBlocked(RuntimeError):
    """数据源未获批准时抛出；调用方必须降级，不得绕过。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log_assessment(action: str, source_id: str, detail: dict[str, Any]) -> None:
    """append-only 合规留痕；不记录原始 JD、凭据或个人信息。"""
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": _now(), "action": action, "source": source_id,
                 "detail": {k: str(v)[:300] for k, v in detail.items()}}
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def assert_fetchable(source_id: str) -> dict[str, Any]:
    """fail-closed 准入闸门：未登记 / 未批准 / 已封禁的来源一律拒绝。"""
    src = SOURCES.get(source_id)
    if not src:
        log_assessment("source_unregistered", source_id, {})
        raise SourceBlocked("UNREGISTERED_SOURCE:" + source_id)
    if src["legal_status"] != "approved":
        log_assessment("source_blocked", source_id, {"status": src["legal_status"]})
        raise SourceBlocked("SOURCE_NOT_APPROVED:" + source_id + ":" + src["legal_status"])
    log_assessment("source_allowed", source_id, {"grade": src["grade"], "region": src["region"]})
    return src


def inspect_robots(base_url: str, path: str = "/", ua: str = "*") -> dict[str, Any]:
    """读取并解析 robots.txt；结果用于准入判断与留痕，不缓存为永久许可。"""
    robots_url = urllib.parse.urljoin(base_url, "/robots.txt")
    record: dict[str, Any] = {"robots_url": robots_url, "fetched_at": _now(), "path": path, "ua": ua}
    try:
        req = urllib.request.Request(robots_url, headers={"User-Agent": "ZhiyinResearch/1.0"})
        with urllib.request.urlopen(req, timeout=ROBOTS_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "ignore")
        rp = robotparser.RobotFileParser()
        rp.parse(text.splitlines())
        record.update({
            "reachable": True,
            "can_fetch": bool(rp.can_fetch(ua, path)),
            "crawl_delay": rp.crawl_delay(ua),
            "request_rate": str(rp.request_rate(ua)) if rp.request_rate(ua) else None,
            "bytes": len(text),
        })
    except Exception as exc:
        # 取不到 robots.txt 时按「不允许」处理，不默认放行
        record.update({"reachable": False, "can_fetch": False, "error": type(exc).__name__})
    log_assessment("robots_checked", base_url, record)
    return record


def enforce_policy(source_id: str, path: str = "/") -> dict[str, Any]:
    """完整准入：先查批准状态，再（若需要）检查 robots 与 Crawl-delay。"""
    src = assert_fetchable(source_id)
    policy: dict[str, Any] = {"source": source_id, "grade": src["grade"], "region": src["region"],
                              "crawl_delay_s": src["crawl_delay_s"], "robots": None}
    if src["robots_required"]:
        robots = inspect_robots(src["url"], path)
        policy["robots"] = robots
        if not robots.get("can_fetch"):
            raise SourceBlocked("ROBOTS_DISALLOW:" + source_id)
        declared = float(robots.get("crawl_delay") or 0)
        policy["crawl_delay_s"] = max(src["crawl_delay_s"], declared)
    return policy


def applicability(grade: str, region: str, samples: int) -> dict[str, Any]:
    """返回本次数据『能回答什么、不能回答什么』，供后端与前端共用。"""
    rule = GRADE_RULES.get(grade, GRADE_RULES["D"])
    return {
        "grade": grade,
        "grade_label": rule["label"],
        "region": region,
        "representative_of_cn": rule["representative_of_cn"] and region == "CN",
        "samples": samples,
        "allowed_claims": rule["allowed"],
        "forbidden_claims": rule["forbidden"],
        "disclaimer": (
            "本样本为全球远程岗位，不代表中国境内招聘市场；不得据此判断国内岗位需求、薪资或同比变化。"
            if region == "global_remote" else
            "结论仅在样本覆盖范围内成立，不得外推为全国市场数据。"
        ),
    }


# 截图采集的合规认定。核心结论：截图是「采集的呈现形式」，不是「合规豁免」。
# 判定依据只看两件事：谁发起的页面访问、访问的是谁有权查看的内容。
CAPTURE_POLICY: dict[str, Any] = {
    "principle": "把 DOM 抓取换成像素抓取，不改变自动化访问第三方页面的法律性质。"
                 "决定是否合规的是『谁访问、访问了什么、后续怎么处理』，不是『用 HTML 还是用 PNG』。",
    "scenarios": [
        {
            "id": "user_manual_screenshot",
            "label": "用户自己手动截图、主动上传本人有权查看的内容",
            "source_id": "user_screenshot",
            "grade": "C",
            "requirements": [
                "系统不发起任何第三方页面访问，不提供自动截图 / 定时截图 / 批量截图能力",
                "图片只在本页内存中处理：不上传、不落盘、不写入 localStorage / IndexedDB，用完即释放",
                "OCR 结果先落到用户可见、可编辑的文本框，由用户核对后再提交，避免错字直接变成结论",
                "提交时只发送文本，仍走 user_jd 的 C 级闸门：显式授权、PII 先脱敏、原文零落盘、可撤回",
                "结论仅对这一份 JD / 这一份简历成立，不聚合、不产生任何市场级数值",
            ],
            "note": "这是唯一被允许的截图路径，本质是 C 级『用户自带数据』换了一种输入形态。",
        },
        {
            "id": "automated_third_party_screenshot",
            "label": "受控研究采集：书面许可 + robots 允许 + 限速后的自动截图",
            "source_id": "research_capture",
            "grade": "B",
            "verdict_hint": "pending_legal",
            "requirements": [
                "『主要用于研究、不商用』不是豁免；须先明确 PIPL 第 13 条第(六)项的合法性基础与『合理范围』",
                "站点书面使用许可 + 法务书面结论 + 书面批准记录 + 注册表状态变更，四项齐备",
                "robots.txt 允许目标路径，服从 Crawl-delay；单域名并发 1、每日总量上限、真实可联络 UA",
                "只截无需登录、公开可见页面；不涉及登录态、风控、验证码与字体反爬",
                "位图 OCR 后立即从内存释放；只保留脱敏文本，配 TTL、删除权与审计留痕",
            ],
            "note": "相比无控制的自动截图，这条路径把『控速』从规避检测改造成履行《条例》第 18 条"
                    "『不得干扰网络服务』的过程控制，法律上有可争取空间——但仍属自动化访问，"
                    "默认 pending_legal：工程侧不提供执行体，拿到书面结论后也建议优先改走授权 API。"
                    "截图比纯文本更容易带出头像、二维码与 HR 联系方式，正则脱敏对像素无效。",
        },
        {
            "id": "automated_evasion_screenshot",
            "label": "复用登录态 / 为绕过字体反爬与风控而做的自动截图",
            "source_id": "auto_screenshot",
            "grade": "B",
            "note": "字体反爬把私用区码位渲染成视觉汉字，截图 + OCR 技术上确实读得出来——"
                    "但正因如此，它从『自动化采集』升级为『规避技术保护措施』，风险等级不降反升，"
                    "可能触及《条例》第 39 条。技术可行不等于法律可行。",
        },
        {
            "id": "rag_ingest_from_local_ocr",
            "label": "本地 OCR 文本 → 脱敏 → 向量化 → 本地研究检索库",
            "source_id": "user_screenshot",
            "grade": "C",
            "requirements": [
                "只收文本；服务端无图片上传端点，位图自始不离开浏览器内存",
                "入库前过 fail-closed 闸门，来源须已批准；用途只能为 research 且显式授权",
                "PII 前置脱敏；片段文本 AES-256-GCM 加密落盘，向量单独存放",
                "向量不等同于匿名化：默认 30 天 TTL、单文档删除、整库清空、全程审计哈希链",
                "检索结果强制携带 grade / forbidden_claims；不对外分发、不外发云端模型",
            ],
            "note": "这是本次新增的向量检索链路。它把处理范围从『一次性浏览』扩大到『存储 + 加工』"
                    "（PIPL 第 4 条），因此比只看一次截图需要更严格的合法性基础与留存控制，"
                    "不能因为『研究 / 不商用』就省略 TTL 与删除权。",
        },
    ],
    "forbidden_capabilities": [
        "在取得站点书面许可与法务书面结论前，启用任何定时 / 批量 / 巡航式截图任务",
        "服务端或浏览器自动打开第三方招聘页面并截图（执行体仅限练习沙盒，招聘站在代码层拒绝）",
        "复用或伪造登录态后截图登录墙内内容、破解字体反爬、绕过验证码与风控",
        "把截图 OCR 结果聚合成需求热度、同比、薪资或蓝海岗位排序",
        "把截图原图发送到服务端、云端模型或任何第三方 OCR 服务",
        "把『向量化』当作匿名化，因而省略文本加密、TTL、删除权或审计留痕",
    ],
    "research_use": {
        # 2026-11-16 全项目条款化：字面量与 usage_policy.CLAUSE_ZH 保持一致
        # （由 tests/zy_approval_test.py 断言核对），本模块保持仅依赖标准库。
        "claim": "本项目仅用于研究与学习，不得用于任何商业行为",
        "clause_id": "USAGE-RESTRICTION-2026-11-16",
        "is_exemption": False,
        "why": "PIPL 第 13 条采封闭列举，没有笼统的『研究 / 非商用』项；"
               "《条例》第 18 条评价的是访问行为本身，与是否盈利无关。",
        "possible_basis": "PIPL 第 13 条第(六)项『合理范围内处理已合法公开的个人信息』；"
                          "《民法典》第 999 / 1036 条对具备公共利益性质的科学研究",
        "conditions": ["目的限定", "最小必要", "PII 前置脱敏与文本加密",
                       "默认 30 天 TTL 与删除权", "不对外分发、不用于个人画像或重新识别",
                       "robots / Crawl-delay / 单域名并发 1 / 每日上限 / 真实 UA", "全程审计留痕"],
        "note": "研究用途不是解锁开关，而是一组更严格的举证条件；详见 research_capture.LEGAL_POSITION。",
    },
    "legal_basis": [
        "《网络数据安全管理条例》第 18 条：自动化工具须评估对网络服务的影响，不得非法侵入、干扰",
        "《网络数据安全管理条例》第 24 条：自动化采集带出的非必要个人信息须删除或匿名化",
        "《网络数据安全管理条例》第 39 条：不得提供用于避开技术措施的工具",
        "2025 修订《反不正当竞争法》数据专款；站点服务条款、robots.txt 与 Crawl-delay",
        "《个人信息保护法》第 4 条（存储 / 使用 / 加工）、第 5、6、7、17 条（最小必要、目的限定、告知）"
        "、第 13 条第(六)项（合理范围内处理已合法公开的信息）、第 15 条（撤回同意）",
    ],
    "disclaimer": "本表是工程过程控制，不构成法律意见；边界须以法务书面结论为准。",
}


def _capture_verdict(status: str, permanent: bool = False) -> str:
    if permanent or status == "blocked":
        return "prohibited"
    if status == "approved":
        return "allowed"
    return "pending_legal"


def capture_policy() -> dict[str, Any]:
    """截图输入策略；verdict 由注册表状态实时推导，不缓存、不手工开关。"""
    assert_fetchable("user_screenshot")  # 走一次 fail-closed 闸门并留痕
    user = SOURCES["user_screenshot"]
    auto = SOURCES["auto_screenshot"]
    verdicts = {
        "user_manual_screenshot": _capture_verdict(user["legal_status"]),
        "automated_third_party_screenshot": _capture_verdict(
            SOURCES["research_capture"]["legal_status"]),
        "automated_evasion_screenshot": _capture_verdict(auto["legal_status"], permanent=True),
        "rag_ingest_from_local_ocr": _capture_verdict(user["legal_status"]),
    }
    return {
        "ok": True,
        "gate": "fail-closed",
        "principle": CAPTURE_POLICY["principle"],
        "scenarios": [{**s, "verdict": verdicts[s["id"]],
                       "legal_status": SOURCES[s["source_id"]]["legal_status"]}
                      for s in CAPTURE_POLICY["scenarios"]],
        "forbidden_capabilities": CAPTURE_POLICY["forbidden_capabilities"],
        "legal_basis": CAPTURE_POLICY["legal_basis"],
        "research_use": CAPTURE_POLICY["research_use"],
        "ocr": {
            "engine": "Tesseract.js（浏览器本地 WASM / 按需加载）",
            "image_upload": False,
            "image_persisted": False,
            "raw_text_target": "#jdInput / #jdResume（用户可编辑，核对后再提交）",
            "note": "OCR 会误识别，尤其截图含自定义字体、水印或密集表格时；结果必须由用户核对，"
                    "且提交前仍会按 user_jd.redact 对手机号、邮箱、身份证号与联系人标签脱敏。",
            "rag": {
                "store": "本地 .rag/index.json（纯标准库稀疏 TF-IDF + 余弦检索）",
                "input": "仅文本，无图片上传端点",
                "plaintext_at_rest": False,
                "retention_default_days": 30,
                "delete": "按文档删除 / 整库清空 / TTL 自动过期",
                "note": "向量不等同于匿名化，因此片段 AES-256-GCM 加密落盘并配删除权与审计哈希链；"
                        "未配置持久密钥时服务重启即按不可恢复数据清空。",
            },
        },
        "disclaimer": CAPTURE_POLICY["disclaimer"],
    }


def insight_modes() -> dict[str, Any]:
    """行业洞察双模式就绪状态。

    关键：国内市场模式的 ready 只能由「已获批准且 grade=A 的境内数据源」推导，
    绝不能因为存在 D 级海外样本就视为可用——这正是「拿海外样本冒充国内市场」的代码级防线。
    """
    approved = {k: v for k, v in SOURCES.items() if v["legal_status"] == "approved"}

    d_sources = [
        {"id": k, "name": v["name"], "grade": v["grade"], "region": v["region"]}
        for k, v in approved.items() if v["kind"] == "public_api" and v["grade"] == "D"
    ]
    a_sources = [
        {"id": k, "name": v["name"], "grade": v["grade"], "region": v["region"]}
        for k, v in approved.items() if v["grade"] == "A" and v["region"] == "CN"
    ]
    blockers = [
        {"id": k, "name": v["name"], "grade": v["grade"], "legal_status": v["legal_status"],
         "reason": v["limits"]}
        for k, v in SOURCES.items()
        if v["region"] == "CN" and v["kind"] in ("licensed_api", "public_web", "crawler")
        and v["legal_status"] != "approved"
    ]
    cn_ready = bool(a_sources)
    return {
        "ok": True,
        "gate": "fail-closed",
        "modes": [
            {
                "id": "skill_reference",
                "label": "技能词频参考",
                "subtitle": "全球远程岗公开样本 / 内置演示集",
                "grade": "D",
                "ready": True,
                "sources": d_sources or [{"id": "demo", "name": "内置演示数据集", "grade": "D", "region": "n/a"}],
                "allowed_claims": GRADE_RULES["D"]["allowed"],
                "forbidden_claims": GRADE_RULES["D"]["forbidden"],
                "note": "只谈技能词频与学习方向；不代表中国境内招聘市场，不输出同比、薪资或蓝海岗位结论。",
            },
            {
                "id": "cn_market",
                "label": "国内市场洞察",
                "subtitle": "需 A 级商务授权数据 / 书面许可",
                "grade": "A",
                "ready": cn_ready,
                "sources": a_sources,
                "blockers": blockers,
                "allowed_claims": GRADE_RULES["A"]["allowed"] if cn_ready else [],
                "forbidden_claims": GRADE_RULES["A"]["forbidden"] if cn_ready else
                    ["当前禁止输出任何国内市场数值、趋势、排序或推荐"],
                "status_label": "已授权" if cn_ready else "待授权",
                "note": ("已接入 A 级境内授权数据，可按合同范围输出国内市场结论。"
                         if cn_ready else
                         "尚无 grade=A 且 legal_status=approved 的境内数据源，本模式不提供任何数值与图表；"
                         "不得以 D 级海外样本或演示集替代。"),
                "unblock_requirements": [] if cn_ready else BD_REQUIREMENTS[:4],
            },
        ],
        "note": "本表由 sources.py 注册表实时推导，是工程控制而非法律意见；状态变更须以法务书面结论与合同为准。",
    }


def registry_view() -> dict[str, Any]:
    """给 /api/sources 与合规页面使用；不暴露任何凭据或内部路径。"""
    return {
        "ok": True,
        "gate": "fail-closed",
        "note": "legal_status != approved 的来源在代码层拒绝采集；本表是工程控制，不替代法务意见。",
        "grades": GRADE_RULES,
        "sources": [
            {"id": k, **{f: v[f] for f in ("name", "url", "kind", "region", "grade",
                                            "legal_status", "legal_basis", "limits")}}
            for k, v in SOURCES.items()
        ],
        "legal_checklist": LEGAL_QUESTIONS,
        "bd_checklist": BD_REQUIREMENTS,
    }

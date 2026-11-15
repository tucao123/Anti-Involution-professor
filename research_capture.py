#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 受控研究采集护栏（技术文档 §2.6）

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

截图的数据生命周期：位图 → 浏览器本地 OCR → 位图立即从内存释放 → 用户核对文本
→ PII 脱敏 → 加密片段与稀疏向量入本地 RAG → TTL 到期 / 一键删除。
服务端自始没有图片上传端点，也不保留原始截图。

执行动作保持 fail-closed：本模块只产出「采集计划与限速核算」，不打开任何第三方页面。
"""
from __future__ import annotations

import json
import math
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import sources

ROOT = Path(__file__).resolve().parent
APPROVAL_FILE = ROOT / ".privacy" / "research_approval.json"

SOURCE_ID = "research_capture"
USER_AGENT = "ZhiyinResearch/1.0 (+local-study; contact: project-owner)"

# 「非商用」为什么不是开关，以及可以争取的法律基础
LEGAL_POSITION: dict[str, Any] = {
    "claim": "主要用于研究、不商用",
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
    "UA / IP 轮换、隐身模式、人类行为模拟等规避检测手段",
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
        "auto_capture_executor": "disabled",
        "disclaimer": "本表是工程过程控制，不构成法律意见；是否可实施以法务书面结论与站点书面许可为准。",
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
        "executor": "disabled（本模块不执行页面访问）",
    }
    sources.log_assessment("research_capture_planned", SOURCE_ID,
                           {"paths": len(clean_paths), "crawl_delay": policy.get("crawl_delay_s")})
    return plan


def capture_screenshot(url: str = "") -> None:
    """执行闸门：无论参数如何，未取得双重批准前一律拒绝。

    刻意不实现渲染 / 截图逻辑：提供「自动截图执行体」本身会与
    《条例》第 39 条及 §2.5 红线冲突。拿到书面结论后应优先改走授权 API，
    而不是启用这个入口。
    """
    sources.log_assessment("research_capture_denied", SOURCE_ID,
                           {"reason": "executor_disabled", "url_host_only": str(url)[:40]})
    raise CaptureDisabled(
        "AUTO_CAPTURE_DISABLED: 研究采集需同时满足「注册表 approved」与「法务书面批准记录」；"
        "当前执行体不存在。请改走授权 API 或用户手动截图 / 本地 OCR 的 C 级通道。"
    )


def status() -> dict[str, Any]:
    return {
        "ok": True,
        "source_id": SOURCE_ID,
        "verdict": research_policy()["verdict"],
        "gate": gate_state(),
        "rate_defaults": RATE_DEFAULTS,
        "executor": "disabled",
        "image_upload_endpoint": False,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

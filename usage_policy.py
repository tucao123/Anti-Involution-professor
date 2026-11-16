#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
职引者 ZHIYIN · 项目性质与使用限制条款（2026-11-16 项目所有者确立）

所有者原话：「本项目只允许研究学习，不能用于商业行为」。
本模块把这句话从口头定位固化为**全项目统一适用的成文条款**，
由 research_capture / approval_gate / 回执台账 / 各状态端点 / 文档 / 界面共同引用，
避免在 B1、B2、Q18、Q19、Q21、B3 与各数据链路里各写一套、口径漂移。

性质判断（重要，别读反）：

  - 这条声明是**收紧**：把处理目的限定在毕业设计与学习研究，并明确禁止商业化、
    对外经营、转售分发、把产出用于商业决策。它让合规论证更严格，不是更松。
  - 它**不是合规豁免**。《个人信息保护法》第 13 条采封闭列举，没有笼统的
    「研究 / 非商用」项；《网络数据安全管理条例》第 18 条评价的是自动化访问
    行为本身，与是否盈利无关；第三方服务条款也不因非商用而自动失效。
  - 它**不替代**任何法务书面结论、站点书面许可、商务合同、数据出境评估或
    单独告知同意，也**不改变** `.privacy/approvals/` 中任何一项的 decision
    与双闸门状态。
  - 它**不解锁** B2 的自动采集执行体、规避检测或「模拟正常人类访问节奏」。
    能否绕过技术措施与是否盈利是两个独立问题：非商用不等于可以绕。

因此「其他问题同理都解决」在工程侧的落地含义是：
    **同一条使用限制对每一项都适用，且逐项写进回执、状态端点、文档与界面**；
而不是「用这条声明把每一项待决结论都判为通过」。后者需要对应出具方的书面结论，
工程侧不能自我签发。

本模块是工程过程控制，不构成法律意见。
"""
from __future__ import annotations

from typing import Any

CLAUSE_ID = "USAGE-RESTRICTION-2026-11-16"
CLAUSE_ZH = "本项目仅用于研究与学习，不得用于任何商业行为"
CLAUSE_EN = "Research and study only; no commercial use."

POLICY: dict[str, Any] = {
    "clause_id": CLAUSE_ID,
    "clause": CLAUSE_ZH,
    "clause_en": CLAUSE_EN,
    "declared_by": "项目所有者（毕设作者）",
    "declared_at": "2026-11-16",
    "scope": "全项目统一适用：B1 / B2 / Q18 / Q19 / Q21 / B3 及所有数据链路与前端界面",
    "purpose": "graduation_project_research_and_study",
    "commercial_use": False,
    "commercial_use_allowed": False,
    "is_legal_exemption": False,
    "changes_gate_state": False,
    "applies_to": [
        "B1", "B2", "Q18", "Q19", "Q21", "B3",
        "resume_parse", "rag", "capture", "vector", "llm", "github_meta",
    ],
    "prohibited": [
        "商业化部署、对外提供付费 / 经营性服务、以本项目提供 SaaS 或多租户服务",
        "转售、出租、对外分发岗位样本 / 向量 / 片段 / 研究结果或任何派生数据集",
        "把项目产出用于招聘筛选、广告投放、信用评估、人员定价等商业决策",
        "以「研究学习 / 非商用」为由豁免合法性基础、告知同意、数据出境评估、"
        "站点条款、robots.txt 或商务合同授权",
        "以本声明为由启用自动采集执行体、模拟人类访问节奏、UA / IP 轮换、"
        "绕过验证码 / 风控 / 技术保护措施",
        "把截图 / 采集链路的隐私信息用于个人画像（画像只由用户主动提供材料的"
        "本地 /api/jd/analyze 链路提供，且不对外商业化）",
    ],
    "still_required": [
        "法务书面结论：Q18 数据出境评估、Q19 合法性基础 / 告知同意 / DPA、"
        "Q21 条款核对、B1 产品侧告知文案",
        "站点书面使用许可 / 合作函，以及 robots.txt、Crawl-delay 的逐项服从",
        "B3 生效的商务合同编号与授权范围（地域 / 期限 / 字段）",
        "回执文件 + 注册表 / 配置双闸门逐项独立启用，工程侧无自我授权入口",
        "目的限定、最小必要、去标识化、TTL、撤回删除、审计哈希链留痕",
    ],
    "legal_effect": "收紧目的限定与使用范围；不产生豁免，不改变任何回执 decision 或闸门状态。",
    "disclaimer": "本条款是项目所有者确立的工程过程控制，不构成法律意见；"
                  "最终边界以法务书面结论、站点许可与合同为准。",
}


def as_dict() -> dict[str, Any]:
    """返回可对外暴露的条款视图（只读副本）。"""
    return dict(POLICY)


def clause() -> str:
    return CLAUSE_ZH


def applies_to(item: str) -> bool:
    """任一待决事项 / 数据链路是否受本条款约束。默认全项目适用。"""
    return str(item).strip() in POLICY["applies_to"]


def scope_marker() -> dict[str, Any]:
    """回执台账与状态端点共用的轻量标记。"""
    return {
        "clause_id": CLAUSE_ID,
        "clause": CLAUSE_ZH,
        "commercial_use": False,
        "is_legal_exemption": False,
        "changes_gate_state": False,
    }

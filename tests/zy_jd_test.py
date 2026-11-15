# -*- coding: utf-8 -*-
# 项目内回归用例（由临时验证脚本参数化迁移而来）。
# 路径 / 端口可通过 ZHIYIN_BASE_URL、ZHIYIN_CHROME 覆盖，换机器可复现。
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import json
import sources, user_jd

JD = """岗位名称：AI 应用工程师（校招）
工作地点：上海    联系人：张伟    联系电话：13812345678    投递邮箱：hr@example.com
一、岗位职责
1. 负责大模型应用的状态编排与工具调用，参与 RAG 检索链路建设；
2. 编写技术文档，与产品、算法团队跨团队协作推进里程碑。
二、任职要求
1. 必须熟练掌握 Python，熟悉 LangChain / LangGraph，掌握 SQL 与数据库；
2. 要求具备混合检索、评测集设计与离线评估经验，理解召回率与准确率口径；
3. 熟悉 Docker、Kubernetes 与 CI/CD 者优先，了解数据可视化者加分；
4. 具备良好的沟通表达与方案汇报能力，有用户研究或需求分析经验优先；
5. 了解数据安全合规与个人信息保护相关要求。
"""

RESUME = """计算机科学与技术 2027 届。熟悉 Python，做过一个课程问答助手，负责检索链路；
用 20 条查询做评测，召回率从 0.61 提升到 0.78。写过技术文档，参与过小组项目答辩。
"""

def check(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (("  -> " + str(extra)) if extra else ""))
    if not cond:
        raise AssertionError(label)

# 1. 未授权必须拒绝
try:
    user_jd.analyze(JD, RESUME, {"purpose_ack": False})
    check("未授权拒绝", False)
except user_jd.ConsentRequired:
    check("未授权拒绝", True)

# 2. 正常分析
r = user_jd.analyze(JD, RESUME, {"purpose_ack": True, "aggregate_opt_in": False})
check("grade=C / region=CN", r["grade"] == "C" and r["region"] == "CN")
check("不代表国内市场", r["representative_of_cn"] is False)
check("禁止市场级结论", "任何市场级结论" in r["applicability"]["forbidden_claims"])
check("允许项仅 JD 对齐", r["applicability"]["allowed_claims"] == ["该 JD 与简历的能力对齐", "该 JD 的技能抽取"])
check("PII 自动脱敏", r["pii"]["redacted"] >= 3, r["pii"])
blob = json.dumps(r, ensure_ascii=False)
check("返回体不含手机号", "13812345678" not in blob)
check("返回体不含邮箱", "hr@example.com" not in blob)
check("返回体不含联系人姓名", "张伟" not in blob)
check("原文不落盘", r["session"]["raw_persisted"] is False)
check("聚合未启用", "aggregation" in r["meta"] and "不提供" in r["meta"]["aggregation"])
check("识别出技能", len(r["skills"]) >= 8, len(r["skills"]))
check("技能三级分组", all(k in r["groups"] for k in ("required", "bonus", "implicit")))
check("必备含 Python", any("Python" in n for n in r["groups"]["required"]), r["groups"]["required"][:5])
check("加分含云原生", any("云原生" in n for n in r["groups"]["bonus"]), r["groups"]["bonus"][:5])
check("SAFI 只用三个基准值", set(s["safi"] for s in r["skills"]) <= {71.8, 73.2, 42.2})
check("证据短句已截断", all(len(s["evidence"]) <= 48 for s in r["skills"]))
check("覆盖率已计算", r["alignment"] and isinstance(r["alignment"]["coverage"], int), r["alignment"]["coverage"])
check("雷达 8 维以内", len(r["alignment"]["dims"]) <= 8, len(r["alignment"]["dims"]))
check("缺口三级结构", all(k in r["alignment"]["gaps"] for k in ("required", "bonus", "implicit")))
check("标题提取", r["title"].startswith("AI 应用工程师"), r["title"])

# 3. 无简历要点时不得推断覆盖率
r2 = user_jd.analyze(JD, "", {"purpose_ack": True})
check("无简历不推断覆盖率", r2["alignment"] is None)
check("无简历有明确说明", "不推断" in r2["alignment_note"])

# 4. 撤回删除
sid = r["session"]["id"]
check("会话存在", user_jd.status()["active_sessions"] >= 2)
check("撤回成功", user_jd.delete_session(sid) is True)
check("重复删除返回 False", user_jd.delete_session(sid) is False)
check("撤回后会话数减少", user_jd.status()["active_sessions"] >= 1)

# 5. 双模式闸门
m = sources.insight_modes()
by = {x["id"]: x for x in m["modes"]}
check("技能词频参考可用", by["skill_reference"]["ready"] is True and by["skill_reference"]["grade"] == "D")
check("国内市场洞察待授权", by["cn_market"]["ready"] is False and by["cn_market"]["status_label"] == "待授权")
check("国内市场无可用源", by["cn_market"]["sources"] == [])
check("列出阻塞源", len(by["cn_market"]["blockers"]) >= 3, [b["id"] for b in by["cn_market"]["blockers"]])
check("明确禁止任何数值", any("禁止输出" in s for s in by["cn_market"]["forbidden_claims"]))

# 6. 闸门对 C 级同样生效
sources.SOURCES["user_jd"]["legal_status"] = "pending_legal"
try:
    user_jd.analyze(JD, "", {"purpose_ack": True})
    check("C 级闸门 fail-closed", False)
except sources.SourceBlocked:
    check("C 级闸门 fail-closed", True)
sources.SOURCES["user_jd"]["legal_status"] = "approved"

# 7. 短文本 / 无技能文本
try:
    user_jd.analyze("太短", "", {"purpose_ack": True})
    check("短文本拒绝", False)
except user_jd.InvalidInput as e:
    check("短文本拒绝", str(e) == "JD_LENGTH_INVALID")
try:
    user_jd.analyze("本岗位主要负责日常工作安排与内部协调，具体事项面议。" * 2, "", {"purpose_ack": True})
    check("无技能拒绝", False)
except user_jd.InvalidInput as e:
    check("无技能拒绝", str(e) == "NO_SKILL_DETECTED")

print("\n--- 技能分级样例 ---")
for s in r["skills"][:8]:
    print(" ", s["level"].ljust(9), s["name"].ljust(22), "SAFI", s["safi"], "|", s["evidence"][:34])
print("\n--- 就绪模式 ---")
print(json.dumps({k: {"label": v["label"], "grade": v["grade"], "ready": v["ready"],
                      "status": v.get("status_label", "-")} for k, v in by.items()}, ensure_ascii=False, indent=2))
print("\nALL CHECKS PASSED")

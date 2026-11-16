#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""JTBD v2.0 决策工作台单元测试：三态 / 硬条件 / 行动分流 / 可反驳 / 不编造。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import jtbd  # noqa: E402

PASS = 0
FAIL = 0


def check(cond, label):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("  FAIL:", label)


JD = ("岗位职责：负责数据分析与数据可视化。任职要求：本科及以上学历，"
      "熟悉 Python 和 SQL，有数据分析项目经验，具备良好的沟通汇报能力。")

print("=== JTBD 决策工作台单元测试 ===")

# 政策与边界
s = jtbd.status()
check(s["ok"] and s["policy_id"] == jtbd.POLICY_ID, "status 策略标识")
check(s["hard_conditions_separate"] and s["no_percentage_score"], "硬条件独立 / 无百分比")
check(s["allows_apply_now_and_drop"], "允许先投与放弃")
check(len(s["states"]) == 4, "四态齐全")
check("localStorage" in s["board_storage"], "看板本地存储说明")

# 三态对照
e = jtbd.evidence(JD, "计算机本科，做过 Python 数据分析项目，用 SQL 取数，做过小组汇报。")
check(e["ok"] and e["total"] > 0, "evidence 有结果")
check(all(r["state"] in jtbd.STATES for r in e["requirements"]), "所有行落在四态内")
check(all("evidence_ref" in r and r["editable"] for r in e["requirements"]), "每行可反驳有依据")
check(e["hard_blocking"] is True, "学历未核实 → 硬条件阻断")
check(e["no_score"] is True, "无总分字段")
check("不代表任何市场行情" in e["disclaimer"], "口径免责声明")

# 没提供 ≠ 不会
e0 = jtbd.evidence(JD, "")
check(e0["counts"]["unknown"] > 0, "未提供经历 → 归未知")
check(any("没写" in r["explanation"] or "未提供" in r["explanation"] for r in e0["requirements"]),
      "明确说明未提供不等于不会")
check(e0["decision_hint"].find("信息不足") >= 0 or e0["decision_hint"].find("硬条件") >= 0,
      "信息不足时给追问提示")

# 硬条件不被技能抵消
e_hard = jtbd.evidence(JD, "精通 Python SQL 数据分析，但没有学历信息")
check(len(e_hard["hard_conditions"]) >= 1, "硬条件单列")
check(e_hard["hard_blocking"], "技能再多也不抵消硬条件")

# 方向比较
c = jtbd.compare([
    {"name": "数据岗", "text": "要求：Python SQL 数据分析 沟通 本科"},
    {"name": "产品岗", "text": "要求：产品 原型 需求文档 沟通 本科"},
], "Python SQL 数据分析")
check(c["ok"] and c["sample_size"] == 2, "compare 样本数")
check("沟通" in c["common_requirements"], "共同要求识别")
check(len(c["lowest_effort"]) >= 1, "给出最省力方向")
check(all("basis" in d for d in c["lowest_effort"]), "排序有依据说明")
check(c["representative_of_cn"] is False, "不代表国内市场")
check("JD" in c["scope"] and "内" in c["scope"], "结论限定在样本内")

# 行动分流
a = jtbd.actions(e)
check(a["ok"] and "verdict" in a, "actions 有结论")
check(all(k in a["groups"] for k in ("expression", "evidence", "skill", "hard")), "四类分流齐全")
check(a["allow_apply_now"] or a["verdict"], "可投判定有结论")

# 简历诊断两栏
g = jtbd.diagnose(e, "计算机本科，做过 Python 项目。也擅长 PPT 和视频剪辑。")
check("jd_has_you_not_written" in g and "you_written_jd_ignores" in g, "两栏诊断齐全")
check(all("suggestion" in x for x in g["jd_has_you_not_written"]), "左栏有建议")
check(all("suggestion" in x for x in g["you_written_jd_ignores"]), "右栏有建议")
check(all("编" not in x["suggestion"] or "不要编" in x["suggestion"] for x in g["jd_has_you_not_written"]),
      "建议不鼓励编造")

# 追问演练
d = jtbd.drill("用数据支撑业务判断", "我用 Python 做过销售数据分析项目，按区域汇总订单做了可视化。")
check(d["ok"] and len(d["questions"]) == 3, "三层追问")
check([q["layer"] for q in d["questions"]] == [1, 2, 3], "追问层级递进")
check(len(d["score_dimensions"]) == 3 and d["pressure_mode_optional"], "三维 + 压力可选")

# 反馈引用原话、不打分
rv = jtbd.review_answer(d["questions"][0], "我做了数据清洗，因为原始有很多缺失值，最后准确率提升了5%")
check(rv["ok"] and rv["quote"], "反馈引用原话")
check(len(rv["dimensions"]) == 3 and rv["no_score"], "三维度无总分")
check("第二层" in rv["next"] or "第三层" in rv["next"], "给出下一步")
short = jtbd.review_answer(d["questions"][0], "做了")
check(short["too_short"], "过短回答被拦截")

# 输入校验
for fn, args in [(jtbd.evidence, ("短",)), (jtbd.drill, ("要求", "太短"))]:
    try:
        fn(*args)
        check(False, "非法输入应被拒绝")
    except jtbd.InvalidInput:
        check(True, "非法输入被拒绝")

print("=== 汇总 === PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

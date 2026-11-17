# -*- coding: utf-8 -*-
"""知识入库扩展回归：CSV 来源 / 面试素材 / job_only 开关（本次文档 7 模块改造新增）。

在 server 进程外直接调用 knowledge_ingest 的本地确定性加工部分，不触发真实云写入。
覆盖：
  - ORIGINS 扩展（admin_csv / interview_material）合法；
  - job_only=False 时非岗位文本（STAR 案例）能完整保留并切片，仍强制 PII 脱敏；
  - job_only=True（默认）仍保持仅岗位信息过滤；
  - 标题 PII 脱敏不变；
  - prepare 结果携带 job_only 标记。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import knowledge_ingest as kb

PASS = FAIL = 0


def check(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", label)
    else:
        FAIL += 1
        print("  FAIL", label, extra)


CSV_TEXT = (
    "岗位名称,岗位职责,任职要求\n"
    "AI 应用工程师,负责大模型应用与 RAG 检索链路建设,熟悉 Python 与向量数据库\n"
    "数据分析师,负责业务数据取数与可视化,掌握 SQL 与 Tableau"
)
INTERVIEW_MAT = (
    "面试 STAR 案例：我在一个推荐系统重构项目里负责召回优化，"
    "用 A/B 测试验证召回率提升，最终点击率提高了百分之十二。"
    "追问时我说明了为什么先做召回优化而不是排序模型，以及当时考虑过的两种替代方案。"
)
NON_JOB = "今天天气很好，我们一起去公园散步然后吃了午饭，心情愉快地聊了很多有趣的话题。" * 2

print("=== 知识入库扩展回归（CSV / 面试素材 / job_only）===")
check("admin_csv 在合法来源内", "admin_csv" in kb.ORIGINS)
check("interview_material 在合法来源内", "interview_material" in kb.ORIGINS)

# CSV 来源：默认 job_only=True，岗位行被保留
r = kb.prepare(CSV_TEXT, "admin_csv", title="CSV 岗位样本", purpose_ack=True)
check("CSV 来源入库加工通过", len(r["chunks"]) >= 1, str(r.get("chunks")))
check("CSV 默认 job_only=True", r["job_only"] is True)
check("prepare 携带 job_only 标记", "job_only" in r)

# 面试素材：job_only=False 时非岗位文本也能保留
r2 = kb.prepare(INTERVIEW_MAT, "interview_material", title="STAR 案例",
                purpose_ack=True, job_only=False)
check("面试素材 job_only=False 通过", len(r2["chunks"]) >= 1, str(r2.get("chunks")))
check("面试素材标记 job_only=False", r2["job_only"] is False)
check("面试素材文本被完整保留", "召回优化" in r2["text"], r2["text"][:80])

# 同一段非岗位文本：默认 job_only=True 会被岗位过滤拒绝（NO_JOB_INFO / NO_CHUNK）
try:
    kb.prepare(NON_JOB, "admin_text", purpose_ack=True)
    default_ok = True
except kb.KnowledgeRejected as e:
    default_ok = False
    default_code = str(e)
check("纯非岗位文本默认模式被拒", not default_ok, default_code if not default_ok else "意外通过")
# job_only=False 则可保留
r3 = kb.prepare(NON_JOB, "interview_material", purpose_ack=True, job_only=False)
check("纯非岗位文本 job_only=False 通过", len(r3["chunks"]) >= 1)

# 面试素材仍强制 PII 脱敏：手机号不得残留
PII_MAT = INTERVIEW_MAT + " 联系方式 13812345678。"
r4 = kb.prepare(PII_MAT, "interview_material", purpose_ack=True, job_only=False)
check("面试素材 PII 被脱敏", "13812345678" not in r4["text"], r4["text"][-80:])
check("面试素材脱敏有计数", r4["pii_redacted"] >= 1, str(r4["pii_redacted"]))

# 标题 PII 脱敏在两种模式下都生效
r5 = kb.prepare(CSV_TEXT, "admin_csv", title="联系人：张伟 13812345678 的样本",
                purpose_ack=True, job_only=False)
check("CSV 标题 PII 被脱敏", "13812345678" not in r5["title"], r5["title"])

# 未授权一律拒绝（两模式一致）
for origin, jo in (("admin_csv", True), ("interview_material", False)):
    try:
        kb.prepare(CSV_TEXT, origin, purpose_ack=False, job_only=jo)
        ok = True
    except kb.ConsentRequired:
        ok = False
    check("未授权被拒 " + origin, not ok)

print(f"\n=== 汇总：PASS {PASS}  FAIL {FAIL} ===")
sys.exit(1 if FAIL else 0)

# -*- coding: utf-8 -*-
# 项目内回归用例（由临时验证脚本参数化迁移而来）。
# 路径 / 端口可通过 ZHIYIN_BASE_URL、ZHIYIN_CHROME 覆盖，换机器可复现。
import os
import json
import urllib.request
import urllib.error

BASE = os.environ.get("ZHIYIN_BASE_URL", "http://127.0.0.1:8848").rstrip("/")
JD = """岗位名称：AI 应用工程师（校招）
联系人：张伟 联系电话：13812345678 投递邮箱：hr@example.com
一、岗位职责
1. 负责大模型应用的状态编排与工具调用，参与 RAG 检索链路建设；
2. 编写技术文档，与产品、算法团队跨团队协作推进里程碑。
二、任职要求
1. 必须熟练掌握 Python，熟悉 LangChain / LangGraph，掌握 SQL 与数据库；
2. 要求具备混合检索、评测集设计与离线评估经验，理解召回率与准确率口径；
3. 熟悉 Docker、Kubernetes 与 CI/CD 者优先，了解数据可视化者加分；
4. 具备良好的沟通表达与方案汇报能力，有用户研究或需求分析经验优先；
5. 了解数据安全合规与个人信息保护相关要求。"""
RESUME = "熟悉 Python；做过课程问答助手，负责检索链路；用 20 条查询做评测，召回率从 0.61 提升到 0.78；写过技术文档。"


def call(path, payload=None):
    url = BASE + path
    if payload is None:
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def check(label, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + label + (("  -> " + str(extra)) if extra else ""))
    if not cond:
        raise AssertionError(label)


s, h = call("/api/health")
check("health 200", s == 200 and h["ok"] is True)
check("闸门状态", h["source_gate"] == "fail-closed", h["insight_modes"])
check("C 级范围声明", "grade C" in h["user_jd_scope"])

s, m = call("/api/insight/modes")
by = {x["id"]: x for x in m["modes"]}
check("双模式接口", s == 200 and len(by) == 2)
check("国内市场待授权", by["cn_market"]["ready"] is False and by["cn_market"]["status_label"] == "待授权")
check("无可用 A 级源", by["cn_market"]["sources"] == [])
check("技能词频可用", by["skill_reference"]["ready"] is True)

s, reg = call("/api/sources")
check("数据源注册表", s == 200 and len(reg["sources"]) == 10)
check("法务清单 17 问", len(reg["legal_checklist"]) == 17)
check("BD 8 项要点", len(reg["bd_checklist"]) == 8)
check("爬虫路径封禁", any(x["id"] == "domestic_crawler" and x["legal_status"] == "blocked" for x in reg["sources"]))

s, st = call("/api/jd/status")
check("C 级状态", s == 200 and st["raw_jd_persist"] is False and st["aggregation_enabled"] is False)

s, no_consent = call("/api/jd/analyze", {"jd_text": JD, "resume_text": RESUME, "purpose_ack": False})
check("未授权 403", s == 403 and no_consent["error"] == "CONSENT_REQUIRED", (s, no_consent))

s, ok = call("/api/jd/analyze", {"jd_text": JD, "resume_text": RESUME, "purpose_ack": True, "aggregate_opt_in": False})
check("正常分析 200", s == 200 and ok["ok"] is True, (s, list(ok)[:8]))
check("C 级分级", ok["grade"] == "C" and ok["representative_of_cn"] is False)
check("原文零落盘", ok["session"]["raw_persisted"] is False)
check("已脱敏", ok["pii"]["redacted"] >= 3, ok["pii"])
blob = json.dumps(ok, ensure_ascii=False)
check("响应不含手机号", "13812345678" not in blob)
check("响应不含邮箱", "hr@example.com" not in blob)
check("响应不含姓名", "张伟" not in blob)
check("技能三级分组", all(k in ok["groups"] for k in ("required", "bonus", "implicit")), ok["groups"])
check("覆盖率存在", isinstance(ok["alignment"]["coverage"], int), ok["alignment"]["coverage"])
check("会话 id", len(ok["session"]["id"]) == 16, ok["session"]["id"])

s, d = call("/api/jd/delete", {"id": ok["session"]["id"]})
check("撤回 200", s == 200 and d["deleted"] == ok["session"]["id"])
s, d2 = call("/api/jd/delete", {"id": ok["session"]["id"]})
check("重复撤回 404", s == 404)

s, bad = call("/api/jd/analyze", {"jd_text": "太短", "purpose_ack": True})
check("短文本 400", s == 400 and bad["error"] == "JD_LENGTH_INVALID")

s, noskill = call("/api/jd/analyze", {"jd_text": "本岗位主要负责日常工作安排与内部协调，具体事项面议。" * 2,
                                      "purpose_ack": True})
check("无技能 400", s == 400 and noskill["error"] == "NO_SKILL_DETECTED", (s, noskill))

s, no_resume = call("/api/jd/analyze", {"jd_text": JD, "purpose_ack": True})
check("无简历不推断覆盖率", s == 200 and no_resume["alignment"] is None)

# 静态文件白名单
for path in ("/server.py", "/.env", "/user_jd.py", "/sources.py"):
    try:
        with urllib.request.urlopen(BASE + path, timeout=5) as r:
            check("源码不暴露 " + path, False, r.status)
    except urllib.error.HTTPError:
        check("源码不暴露 " + path, True)

print("\nALL HTTP CHECKS PASSED")

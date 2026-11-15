# -*- coding: utf-8 -*-
# 项目内回归用例（由临时验证脚本参数化迁移而来）。
# 路径 / 端口可通过 ZHIYIN_BASE_URL、ZHIYIN_CHROME 覆盖，换机器可复现。
import os
# -*- coding: utf-8 -*-
"""截图合规闸门与 C 级链路的真实 HTTP 验证。"""
import json
import time
import urllib.error
import urllib.request

BASE = os.environ.get("ZHIYIN_BASE_URL", "http://127.0.0.1:8848").rstrip("/")
PASS = 0
FAIL = 0


def check(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   " + label)
    else:
        FAIL += 1
        print("  FAIL " + label + ("  " + str(extra) if extra else ""))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=8) as r:
        return r.status, json.loads(r.read())


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# 等待服务就绪
for _ in range(30):
    try:
        get("/api/health")
        break
    except Exception:
        time.sleep(0.3)

print("=== /api/health ===")
s, h = get("/api/health")
check(s == 200 and h["ok"], "health 200")
check("capture_policy" in h, "health 暴露截图策略字段")
check("prohibited" in h.get("capture_policy", ""), "自动截图被标为 prohibited")

print("=== /api/capture/policy ===")
s, p = get("/api/capture/policy")
check(s == 200 and p["ok"], "policy 200")
check(p["gate"] == "fail-closed", "gate = fail-closed")
sc = {x["id"]: x for x in p["scenarios"]}
check(len(sc) == 4, "四种情形齐全", len(sc))
check(sc["user_manual_screenshot"]["verdict"] == "allowed", "用户手动截图 allowed")
check(sc["user_manual_screenshot"]["grade"] == "C", "用户手动截图 grade C")
check(len(sc["user_manual_screenshot"].get("requirements", [])) >= 5, "手动截图有完整约束")
check(sc["automated_third_party_screenshot"]["verdict"] == "pending_legal",
      "受控研究采集 pending_legal")
check(len(sc["automated_third_party_screenshot"].get("requirements", [])) >= 5,
      "受控研究采集列明准入条件")
check(sc["automated_evasion_screenshot"]["verdict"] == "prohibited", "绕反爬截图 prohibited")
check(sc["rag_ingest_from_local_ocr"]["verdict"] == "allowed", "本地 OCR 文本入库 allowed")
check(p["research_use"]["is_exemption"] is False, "研究 / 非商用不是豁免")
check(p["ocr"]["rag"]["plaintext_at_rest"] is False, "RAG 片段不明文落盘")
check("第 39 条" in sc["automated_evasion_screenshot"]["note"], "明确规避技术措施风险")
check(p["ocr"]["image_upload"] is False, "图片不上传")
check(p["ocr"]["image_persisted"] is False, "图片不落盘")
check("Tesseract" in p["ocr"]["engine"], "OCR 引擎标注为浏览器本地")
check(len(p["forbidden_capabilities"]) >= 5, "禁止能力清单齐全")
joined = json.dumps(p, ensure_ascii=False)
check("自动" in joined and "豁免" not in p["principle"], "未宣称截图可豁免")
check("不构成法律意见" in p["disclaimer"], "带非法律意见声明")

print("=== /api/sources ===")
s, reg = get("/api/sources")
src = {x["id"]: x for x in reg["sources"]}
check("user_screenshot" in src and src["user_screenshot"]["legal_status"] == "approved",
      "user_screenshot 已登记且 approved")
check(src["user_screenshot"]["grade"] == "C", "user_screenshot 为 C 级")
check("auto_screenshot" in src and src["auto_screenshot"]["legal_status"] == "blocked",
      "auto_screenshot 已封禁")
check(src["auto_screenshot"]["kind"] == "automated_capture", "auto_screenshot 类型为自动化采集")
check(len(reg["legal_checklist"]) == 17, "法务清单同步为 17 问", len(reg["legal_checklist"]))
check("research_capture" in src and src["research_capture"]["legal_status"] == "pending_legal",
      "受控研究采集源已登记且待法务结论")

print("=== /api/insight/modes（不得被截图源误解锁）===")
s, m = get("/api/insight/modes")
modes = {x["id"]: x for x in m["modes"]}
check(modes["cn_market"]["ready"] is False, "国内市场洞察仍待授权")
check(modes["skill_reference"]["ready"] is True, "技能词频参考仍可用")
check(all(b["id"] != "auto_screenshot" for b in modes["cn_market"]["blockers"]),
      "永久封禁的自动截图没有被表述为可解锁阻塞项")

print("=== 服务端拒绝图片输入 ===")
s, r = post("/api/jd/analyze", {
    "jd_text": "岗位名称：AI 应用工程师。必须熟练掌握 Python，熟悉 Docker 与 SQL 与数据库，具备跨团队协作与方案汇报能力，了解数据可视化者加分。",
    "purpose_ack": True, "image": "data:image/png;base64,AAAA",
})
check(s in (400, 422) and not r.get("ok"),
      "带 image 字段被 JSON Schema 拒绝（extra=forbid）", (s, r))
s, r = post("/api/jd/analyze", {"jd_text": "x" * 60, "purpose_ack": False})
check(s == 403, "未授权仍返回 403", s)

JD = ("岗位名称：AI 应用工程师（校招）\n"
      "联系人：张伟 联系电话：13812345678 投递邮箱：hr@example.com\n"
      "一、岗位职责：负责大模型应用的状态编排与工具调用，参与 RAG 检索链路建设；编写技术文档，与产品、算法团队跨团队协作推进里程碑。\n"
      "二、任职要求：必须熟练掌握 Python，熟悉 LangChain，掌握 SQL 与数据库；要求具备评测集设计与离线评估经验；"
      "熟悉 Docker、Kubernetes 者优先；具备良好的沟通表达与方案汇报能力；了解数据安全合规要求。")
s, r = post("/api/jd/analyze", {"jd_text": JD, "purpose_ack": True, "aggregate_opt_in": False})
check(s == 200 and r["ok"], "正常 C 级分析 200", s)
check(r["grade"] == "C" and r["representative_of_cn"] is False, "仍为 C 级、不代表国内市场")
check(len(r["skills"]) >= 8, "技能抽取有效", len(r["skills"]))
check(r["session"]["raw_persisted"] is False, "原文零落盘")
check("13812345678" not in json.dumps(r, ensure_ascii=False), "响应体不含手机号")
check("hr@example.com" not in json.dumps(r, ensure_ascii=False), "响应体不含邮箱")
sid = r["session"]["id"]
s, d = post("/api/jd/delete", {"id": sid})
check(s == 200 and d["ok"], "撤回删除 200")

print("=== 路由表 ===")
s, spec = get("/openapi.json")
paths = spec.get("paths", {})
check("/api/capture/policy" in paths, "新接口已注册")
check(all(not ("/capture" in p and "post" in methods) for p, methods in paths.items()
          if p != "/api/capture/policy"),
      "不存在任何截图上传 POST 端点")

print("\n%s  CAP HTTP CHECKS PASSED: %d / %d" % ("OK" if FAIL == 0 else "FAILED", PASS, PASS + FAIL))
if FAIL:
    raise SystemExit(1)

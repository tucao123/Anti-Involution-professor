# -*- coding: utf-8 -*-
# 项目内回归用例（由临时验证脚本参数化迁移而来）。
# 路径 / 端口可通过 ZHIYIN_BASE_URL、ZHIYIN_CHROME 覆盖，换机器可复现。
# -*- coding: utf-8 -*-
"""本地研究检索库：单元级验证（在 server 进程外直接调用模块）。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rag_store as rag
import research_capture as rc
import sources
import privacy

PASS = 0
FAIL = 0


def check(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", label)
    else:
        FAIL += 1
        print("  FAIL", label, extra)


JD = (
    "岗位名称：AI 应用工程师（校招）\n"
    "联系人：张伟　联系电话：13812345678　投递邮箱：hr@example.com\n"
    "岗位职责：必须熟练掌握 Python，负责大模型应用与 RAG 检索增强链路的设计与落地；"
    "要求熟悉 LangChain、向量数据库与混合检索，能够独立完成评测集构建与召回率优化。\n"
    "任职要求：熟悉 Docker 与 Kubernetes 者优先；具备跨团队协作、方案汇报与技术文档撰写能力；"
    "了解 Prompt 工程与智能体编排，有离线评估与 A/B 实验经验者加分。\n"
    "加分项：熟悉用户研究与可用性测试，能写清晰的需求文档与 PRD。"
)

print("=== 1. 授权 / 闸门 / 图片拦截 ===")
for kwargs, label, exc in [
    ({"purpose_ack": False}, "未授权被拒", rag.ConsentRequired),
    ({"purpose_ack": True, "purpose": "product"}, "非 research 用途被拒", rag.InvalidInput),
    ({"purpose_ack": True, "source_id": "auto_screenshot"}, "封禁源被拒", sources.SourceBlocked),
    ({"purpose_ack": True, "source_id": "research_capture"}, "待法务源被拒", sources.SourceBlocked),
    ({"purpose_ack": True, "source_id": "licensed_cn_platform"}, "待合同源被拒", sources.SourceBlocked),
    ({"purpose_ack": True, "source_id": "not_registered"}, "未登记源被拒", sources.SourceBlocked),
]:
    args = {"text": JD, "source_id": "user_screenshot", "title": "测试 JD",
            "origin": "local_ocr"}
    args.update(kwargs)
    try:
        rag.ingest(**args)
        check(False, label, "应抛异常但成功了")
    except exc:
        check(True, label)
    except Exception as e:
        check(False, label, "异常类型错误: " + type(e).__name__)

try:
    rag.ingest(text="data:image/png;base64,iVBORw0KGgo" * 30, source_id="user_screenshot",
               purpose_ack=True, origin="local_ocr")
    check(False, "base64 图片被拒", "应抛异常")
except rag.InvalidInput as e:
    check(str(e) == "IMAGE_PAYLOAD_REJECTED", "base64 图片被拒", str(e))

print("=== 1b. 来源与文本形态一致性 ===")
for src, origin in [("jobicy", "pasted_text"), ("remoteok", "local_ocr"),
                    ("user_screenshot", "public_api"), ("demo", "public_api")]:
    try:
        rag.ingest(text=JD, source_id=src, purpose_ack=True, origin=origin)
        check(False, "错配被拒: %s / %s" % (src, origin), "应抛 ORIGIN_MISMATCH")
    except rag.InvalidInput as e:
        check(str(e) == "ORIGIN_MISMATCH", "错配被拒: %s / %s" % (src, origin), str(e))
try:
    rag.ingest(text=JD, source_id="auto_screenshot", purpose_ack=True, origin="public_api")
    check(False, "封禁源优先于口径校验", "应抛 SourceBlocked")
except sources.SourceBlocked:
    check(True, "封禁源优先于口径校验")

print("=== 2. 入库与脱敏 ===")
res = rag.ingest(text=JD, source_id="user_screenshot", title="AI 应用工程师 JD",
                 purpose_ack=True, origin="local_ocr", retain_days=30)
doc = res["doc"]
check(res["ok"] and doc["chunk_count"] >= 1, "入库成功并切出片段", doc["chunk_count"])
check(doc["pii_redacted"] >= 3, "PII 命中并脱敏", doc["pii_redacted"])
check(set(doc["pii_kinds"]) >= {"phone", "email", "contact_label"}, "脱敏类别完整", doc["pii_kinds"])
check(doc["grade"] == "C" and doc["source_id"] == "user_screenshot", "分级随来源注入")
check(doc["retain_days"] == 30, "默认留存 30 天")
check(res["encrypted_at_rest"] and res["image_uploaded"] is False, "加密落盘 / 无图片上传")

raw = (ROOT / ".rag" / "index.json").read_text(encoding="utf-8")
for secret in ("13812345678", "hr@example.com", "张伟", "LangChain", "RAG 检索增强"):
    check(secret not in raw, "落盘不含明文: " + secret[:16])

print("=== 3. 检索与口径 ===")
q = rag.query("RAG 检索增强 召回率 评测", top_k=5)
check(q["ok"] and len(q["hits"]) >= 1, "检索有命中", len(q["hits"]))
check(q["hits"][0]["score"] > 0, "余弦分数为正", q["hits"][0]["score"] if q["hits"] else None)
check(all(h["grade"] == "C" for h in q["hits"]), "每条命中带 grade")
check(all(h["forbidden_claims"] for h in q["hits"]), "每条命中带 forbidden_claims")
check(q["scope"]["representative_of_cn"] is False, "口径不代表国内市场")
check("任何市场级结论" in q["scope"]["forbidden_claims"], "禁止市场级结论透传")
check("LangChain" in q["hits"][0]["text"] or "Python" in q["hits"][0]["text"],
      "命中片段含技能原文", q["hits"][0]["text"][:60] if q["hits"] else "")

q2 = rag.query("Docker Kubernetes 容器化")
check(any("Docker" in h["text"] for h in q2["hits"]), "另一组技能词也能召回")

print("=== 4. 第二份文档 / D 级来源 / 删除 ===")
res2 = rag.ingest(text="远程岗位样本：要求熟悉 React 与 TypeScript，了解 Figma 与设计系统，"
                       "具备跨团队沟通协作能力，熟悉用户访谈与可用性测试流程。",
                  source_id="jobicy", title="海外远程岗样本", purpose_ack=True,
                  origin="public_api", retain_days=7)
check(res2["doc"]["grade"] == "D" and res2["doc"]["retain_days"] == 7, "D 级来源 + 自定义留存")
q3 = rag.query("React TypeScript 设计系统")
check(any(h["grade"] == "D" for h in q3["hits"]), "D 级片段可检索")
check(set(q3["scope"]["grades"]) <= {"C", "D"}, "混合分级汇总正确", q3["scope"]["grades"])
check("国内就业形势" in q3["scope"]["forbidden_claims"], "D 级禁止结论生效")

did = doc["id"]
check(rag.delete_document(did), "按文档删除成功")
check(not rag.delete_document(did), "重复删除返回 False")
check(all(h["doc_id"] != did for h in rag.query("Python")["hits"]), "删除后不再召回")
check(rag.delete_all() == 1, "整库清空返回剩余文档数")
check(rag.status()["chunks"] == 0, "清空后片段数为 0")

print("=== 5. 研究采集护栏 ===")
pol = rc.research_policy()
check(pol["verdict"] == "pending_legal", "研究模式待法务结论")
check(pol["gate"]["ready"] is False, "双重闸门未满足")
check(pol["legal_position"]["is_exemption"] is False, "非商用不是豁免")
check(len(pol["admission_conditions"]) >= 8, "准入条件齐备", len(pol["admission_conditions"]))
check(pol["auto_capture_executor"] == "test_sandbox_only"
      and pol["production_capture_unlocked"] is False,
      "执行体仅限练习沙盒，生产采集未解锁")
check(pol["rate_control"]["concurrency_per_host"] == 1, "单域名并发 1")
check(pol["rate_control"]["ua_rotation"] is False, "无 UA 轮换")
check(pol["rate_control"]["example_pace"]["days_required"] >= 1, "节奏可估算")
try:
    rc.plan_capture(["/jobs"])
    check(False, "采集计划被闸门拒绝", "应抛 SourceBlocked")
except sources.SourceBlocked:
    check(True, "采集计划被闸门拒绝")
try:
    rc.capture_screenshot("https://example.com/job")
    check(False, "截图执行体拒绝", "应抛 CaptureDisabled")
except rc.CaptureDisabled:
    check(True, "截图执行体拒绝")

lim = rc.RateLimiter(min_interval_s=10, crawl_delay_s=30, daily_cap=2, burst=1)
check(lim.interval == 30, "Crawl-delay 优先于默认间隔", lim.interval)
check(lim.acquire()["allowed"] is True, "首个令牌放行")
check(lim.acquire()["reason"] == "RATE_LIMIT_WAIT", "第二个请求需等待")
check(lim.estimate(5)["days_required"] == 3, "每日上限决定跨天数", lim.estimate(5))

print("=== 6. 策略一致性 ===")
p = sources.capture_policy()
verdicts = {s["id"]: s["verdict"] for s in p["scenarios"]}
check(verdicts["automated_third_party_screenshot"] == "pending_legal", "受控研究采集=待法务")
check(verdicts["automated_evasion_screenshot"] == "prohibited", "规避手段=永久禁止")
check(verdicts["rag_ingest_from_local_ocr"] == "allowed", "本地 OCR 入库=允许")
check(p["research_use"]["is_exemption"] is False, "策略层否定豁免")
check(len(sources.SOURCES) == 11 and len(sources.LEGAL_QUESTIONS) == 18,
      "注册表 11 源 / 法务 18 问")
check(sources.insight_modes()["modes"][1]["ready"] is False, "国内市场模式仍待授权")
ok_audit, n_audit, bad = privacy.verify_chain()
check(ok_audit, "审计哈希链完整", "%d 条 / first_bad=%s" % (n_audit, bad))

print("\n%s: %d passed, %d failed" % ("ALL RAG CHECKS PASSED" if FAIL == 0 else "RAG CHECKS FAILED", PASS, FAIL))
sys.exit(1 if FAIL else 0)

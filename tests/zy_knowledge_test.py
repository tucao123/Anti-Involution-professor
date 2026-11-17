# -*- coding: utf-8 -*-
"""管理员知识入库链路单元回归（在 server 进程外直接调用 knowledge_ingest）。

覆盖：来源注册 / 用途授权 / 来源形态 / 图片拦截 / PII 脱敏 / 仅岗位信息 /
切片 / 标题脱敏 / status 口径。不触发真实 Qdrant 写入（ingest 的写云部分由
HTTP 回归以 fail-closed 方式验证，本文件只测本地确定性加工）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import knowledge_ingest as kb
import sources

PASS = FAIL = 0


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

print("=== 1. 来源注册与闸门 ===")
check(sources.SOURCES["admin_curated"]["legal_status"] == "approved", "管理员来源已批准")
check(sources.SOURCES["admin_curated"]["grade"] == "D", "管理员来源为 D 级")
sources.assert_fetchable("admin_curated")
check(True, "fail-closed 来源校验通过")

print("=== 2. 授权 / 形态 / 图片拦截 ===")
try:
    kb.prepare(JD, "admin_text", purpose_ack=False)
    check(False, "未授权被拒")
except kb.ConsentRequired as e:
    check(str(e) == "RESEARCH_PURPOSE_ACK_REQUIRED", "未授权被拒", str(e))

for origin, label in [("public_api", "非管理员来源形态被拒"), ("auto_screenshot", "自动截图形态被拒")]:
    try:
        kb.prepare(JD, origin, purpose_ack=True)
        check(False, label)
    except kb.KnowledgeRejected as e:
        check(str(e) == "ORIGIN_INVALID", label, str(e))

for text, label in [
    ("data:image/png;base64," + "aGVsbG8" * 80, "data URI 图片被拒"),
    ("岗位职责要求熟悉 Python 与向量数据库。base64," + "A" * 420, "base64 块被拒"),
]:
    try:
        kb.prepare(text, "admin_ocr", purpose_ack=True)
        check(False, label)
    except kb.KnowledgeRejected as e:
        check(str(e) == "IMAGE_PAYLOAD_REJECTED", label, str(e))

for text, label in [("短", "过短文本被拒"), ("x" * (kb.MAX_TEXT + 1), "过长文本被拒")]:
    try:
        kb.prepare(text, "admin_text", purpose_ack=True)
        check(False, label)
    except kb.KnowledgeRejected as e:
        check(str(e) == "TEXT_LENGTH_INVALID", label, str(e))

print("=== 3. 文本 / OCR 两种来源的脱敏与岗位过滤 ===")
for origin in ("admin_text", "admin_ocr"):
    r = kb.prepare(JD, origin, title="测试岗位", purpose_ack=True)
    check(r["origin"] == origin, origin + " 来源标注正确")
    check(len(r["chunks"]) >= 1, origin + " 切出片段", len(r["chunks"]))
    check(r["pii_redacted"] >= 3, origin + " PII 命中并脱敏", r["pii_redacted"])
    check(set(r["pii_kinds"]) >= {"phone", "email", "contact_label"},
          origin + " 脱敏类别完整", r["pii_kinds"])
    check("13812345678" not in r["text"] and "hr@example.com" not in r["text"]
          and "张伟" not in r["text"], origin + " 正文无隐私信息")
    check("Python" in r["text"] and "LangChain" in r["text"], origin + " 岗位信息保留")
    check(all("联系人" not in line for line in r["text"].splitlines()),
          origin + " 个人信息行整行剔除")

print("=== 4. 标题脱敏 / 非岗位文本拒绝 ===")
r = kb.prepare(JD, "admin_text", title="联系人：张伟 13812345678 的岗位", purpose_ack=True)
check("13812345678" not in r["title"] and "张伟" not in r["title"], "标题中的 PII 被脱敏", r["title"])
try:
    kb.prepare("今天天气很好，适合出去散步。联系方式 13800138000。" * 3,
               "admin_text", purpose_ack=True)
    check(False, "纯非岗位文本被拒")
except kb.KnowledgeRejected as e:
    check(str(e) in ("NO_JOB_INFO", "NO_CHUNK"), "纯非岗位文本被拒", str(e))

print("=== 5. 只读 status 口径 ===")
st = kb.status()
check(st["source_id"] == "admin_curated", "status 来源正确")
check(st["image_upload_endpoint"] is False, "无图片上传端点")
check("browser-local" in st["ocr_location"], "OCR 在浏览器本地", st["ocr_location"])
check(st["pii_to_store"] is False and st["job_only"] is True, "隐私不入库 / 仅岗位信息")
check(st["grade"] == "D", "D 级口径")
check("PII redaction" in st["pipeline"] and "chunk" in st["pipeline"]
      and "Qdrant cloud RAG" in st["pipeline"], "处理链完整", st["pipeline"])

print("\n%s: %d passed, %d failed" % ("ALL KNOWLEDGE CHECKS PASSED" if FAIL == 0 else "KNOWLEDGE CHECKS FAILED", PASS, FAIL))
sys.exit(1 if FAIL else 0)

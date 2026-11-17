# -*- coding: utf-8 -*-
"""2026-11-16 单元回归：书面回执双闸门 / B1 文件解析 / 管理员手动开关 /
截图链路隐私信息不入库 / 个人画像与岗位信息按链路分离。

不依赖 HTTP 服务，不发起任何网络请求。
"""
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import approval_gate
import job_info
import rag_store
import resume_parse
import secure_config as sc
import sources
import vector_store

PASS = FAIL = 0


def check(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", label)
    else:
        FAIL += 1
        print("  FAIL", label, extra)


def minimal_pdf(lines):
    # 每行一个独立文本块，pypdf 会把它们抽取成换行分隔的多行文本
    parts = []
    for i, line in enumerate(lines):
        esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        parts.append("BT /F1 12 Tf 72 %d Td (%s) Tj ET" % (720 - 16 * i, esc))
    content = "\n".join(parts)
    objs = [
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        "4 0 obj\n<< /Length %d >>\nstream\n%s\nendstream\nendobj\n" % (len(content), content),
        "5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    out = "%PDF-1.4\n"
    offs = []
    for o in objs:
        offs.append(len(out.encode("latin-1")))
        out += o
    xref = len(out.encode("latin-1"))
    out += "xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offs:
        out += "%010d 00000 n \n" % off
    out += "trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out.encode("latin-1")


def minimal_docx(lines):
    paras = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % x for x in lines)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s</w:body></w:document>' % paras)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
    return buf.getvalue()


JD_LINES = [
    "岗位名称：AI 应用工程师（校招）",
    "联系人：张伟 联系电话：13812345678 投递邮箱：hr@example.com",
    "岗位职责：必须熟练掌握 Python，负责大模型应用与 RAG 检索增强链路的设计与落地",
    "任职要求：熟悉 LangChain、向量数据库与混合检索，独立完成评测集构建",
]

print("=== 1. 书面结论回执闸门 ===")
st = approval_gate.state()
check(set(st["enabled"]) == {"B1", "Q18"}, "仅 B1 / Q18 由项目所有者决策启用", st["enabled"])
check(set(st["pending"]) == {"Q19", "Q21", "B3"}, "Q19 / Q21 / B3 保持 CLOSED", st["pending"])
check(st["writable_api"] is False, "闸门不提供自我授权写入接口")
check(st["legal_opinion_received"] == [], "没有任何一项被虚报为法务结论")
b1 = approval_gate.validate("B1")
check(b1["decision"] == "approved_with_conditions" and b1["legal_review"] == "pending",
      "B1 是有条件同意且法务结论待回", b1["decision"])
check(b1["legal_opinion"] is False, "项目所有者决策不等于法务意见")
check(approval_gate.gate("Q18")["legal_review"] == "pending", "Q18 已运行但法务结论待回")
check(sc.cfg.flag("QDRANT_ALLOW_WRITE") is True, "Q18 配置开关按回执开启")
check(vector_store.cloud_writes_allowed() is True, "Q18 双闸门（回执 + 配置）同时满足")
check(sc.cfg.flag("ALLOW_CLOUD_FOR_USER_DATA") is False, "C 级用户数据仍禁止出本机")

# 负向：移走回执，云写入必须立刻关闭
q18 = ROOT / ".privacy" / "approvals" / "Q18.json"
bak = ROOT / ".privacy" / "approvals" / "Q18.json.testbak"
shutil.move(q18, bak)
try:
    check(vector_store.cloud_writes_allowed() is False, "回执缺失时云写入立即被拒（即使开关为 true）")
    check(approval_gate.gate("Q18")["reason"] == "APPROVAL_FILE_MISSING", "缺失原因如实标注")
finally:
    shutil.move(bak, q18)
check(vector_store.cloud_writes_allowed() is True, "回执恢复后闸门重新通过")

print("=== 2. 只保留岗位信息 / 不做画像（链路分离）===")
pol = job_info.status()
check(pol["pii_extraction"] is False and pol["pii_to_store"] is False,
      "本链路不提取个人信息、隐私信息不入库")
check(pol["user_profiling_in_this_path"] is False, "截图入库链路不产出个人画像")
check(pol["user_profiling_project_wide"] is True and pol["profiling_path"] == "/api/jd/analyze",
      "项目整体保留本地个人画像链路")
r = job_info.to_coarse_job_profile("\n".join(JD_LINES))
check("张伟" not in str(r) and "13812345678" not in str(r) and "hr@example.com" not in str(r),
      "粗粒度岗位信息不含任何个人信息")
check("覆盖率" not in str(r) and "匹配度" not in str(r), "本链路不输出画像类字段")
check(len(r["skill_areas"]) >= 1 and "Python" in str(r), "保留岗位技能方向")
lines = job_info.job_only_lines("\n".join(JD_LINES))
check(all("联系人" not in x and "13812345678" not in x for x in lines),
      "个人信息语境行整行剔除", lines)
check(sources.SOURCES["auto_screenshot"]["legal_status"] == "blocked", "自动截图来源仍永久封禁")

print("=== 3. 截图 / local_ocr 入库：隐私信息不入库 ===")
JD = "\n".join(JD_LINES)
doc_id = None
try:
    res = rag_store.ingest(text=JD, source_id="user_screenshot", title="截图测试",
                           purpose_ack=True, origin="local_ocr", retain_days=30)
    d = res["doc"]
    doc_id = d["id"]
    check(d["job_only"] is True and d["pii_lines_excluded"] is True, "入库标记仅岗位信息")
    check(d["pii_redacted"] >= 1, "个人信息检出并排除的举证保留")
    q = rag_store.query("Python 与向量检索", 3)
    stored = " ".join(h.get("text", "") for h in q.get("hits", []))
    check("13812345678" not in stored and "hr@example.com" not in stored and "张伟" not in stored,
          "索引片段中不含任何隐私信息")
    check("Python" in stored, "岗位信息仍可检索")
finally:
    if doc_id:
        rag_store.delete_document(doc_id)
check(rag_store.status()["screenshot_pii_to_store"] is False, "状态如实声明隐私信息不入库")

print("=== 4. B1 文件解析（不留存 / 可撤回删除）===")
check(resume_parse.status()["resume_parse_open"] is True, "B1 按回执开放")
check(resume_parse.status()["pii_to_store"] is False, "B1 不入库个人信息")
check(resume_parse.status()["profiling_path"] == "/api/jd/analyze", "个人画像指向既有本地链路")
docx_bytes = minimal_docx(JD_LINES)
out = resume_parse.parse("岗位.docx", docx_bytes)
p = out["job_profile"]
check(out["ok"] and out["session_id"], "docx 解析成功")
check("张伟" not in str(p) and "13812345678" not in str(p), "docx 解析输出不含个人信息")
check(out["retention"]["raw_text"] == "none" and out["retention"]["file"] == "none",
      "文件与原文均不留存")
check(out["cloud_used"] is False, "默认走本地解析、不调用云端")
check("覆盖率" not in str(p), "文件解析链路不产出画像字段")
sid = out["session_id"]
check(resume_parse.withdraw(sid)["removed"] is True, "撤回入口有效")
check(resume_parse.delete(sid)["removed"] is False, "删除幂等（已撤回）")

pdf_bytes = minimal_pdf([
    "Job title: Backend Engineer",
    "Required skills: Python RAG vector search LangGraph",
    "Contact: John 13800138000 hr@example.com",
])
out2 = resume_parse.parse("岗位.pdf", pdf_bytes)
check(out2["ok"] and "Python" in str(out2["job_profile"]), "pdf 解析成功并仅保留岗位信息")
check("13800138000" not in str(out2["job_profile"]), "pdf 中的隐私信息未进入输出")
resume_parse.delete(out2["session_id"])

for name, data, code in [
    ("bad.txt", b"plain text", "FILE_TYPE_NOT_ALLOWED"),
    ("bad.pdf", b"not a pdf at all", "FILE_MAGIC_MISMATCH"),
    ("empty.docx", b"PK\x03\x04" + b"0" * 40, "NOT_A_DOCX"),
]:
    try:
        resume_parse.parse(name, data)
        check(False, "越权文件被拒: " + name)
    except resume_parse.ResumeRejected as e:
        check(str(e) == code, "越权文件被拒: " + name, str(e))

try:
    resume_parse.parse("岗位.docx", docx_bytes, cloud_enrich=True)
    check(False, "云端解析需单独同意")
except resume_parse.ResumeRejected as e:
    check(str(e) == "SEPARATE_CONSENT_REQUIRED", "云端解析需逐次单独告知同意", str(e))

notice = resume_parse.consent_notice()
check("third_party_notice_ack" in notice["required_flags"] and "Q19" in notice["notice"],
      "第三方告知文案明示接收方与待回结论")

print("=== 5. 自动爬取已移除 / 管理员知识来源 ===")
import knowledge_ingest
st = knowledge_ingest.status()
check(st["image_upload_endpoint"] is False, "服务端无图片上传端点")
check("browser-local" in st["ocr_location"], "OCR 在浏览器本地")
check(st["job_only"] is True and st["pii_to_store"] is False, "仅岗位信息 / 隐私不入库")
check(st["source_id"] == "admin_curated", "知识来源为管理员主动提供")
check(sources.SOURCES["auto_screenshot"]["legal_status"] == "blocked", "自动截图仍永久封禁")
check(sources.CAPTURE_POLICY["research_use"]["is_exemption"] is False, "毕设 / 非商用不构成合规豁免")
check(len(sources.CAPTURE_POLICY["forbidden_capabilities"]) >= 5, "禁止能力清单仍在")

print("=== 6. 全项目使用限制条款（USAGE-RESTRICTION-2026-11-16）===")
import usage_policy
up = usage_policy.as_dict()
check(usage_policy.CLAUSE_ZH == "本项目仅用于研究与学习，不得用于任何商业行为", "条款原文")
check(up["commercial_use"] is False and up["commercial_use_allowed"] is False, "禁止商业行为")
check(up["is_legal_exemption"] is False, "条款不构成合规豁免")
check(up["changes_gate_state"] is False, "条款不改变闸门状态")
check(all(usage_policy.applies_to(k) for k in ("B1", "B2", "Q18", "Q19", "Q21", "B3")),
      "B2 与 5 项待决事项同理适用")
st = approval_gate.state()
check(st["uniform_usage_restriction"] is True, "回执台账 5/5 统一适用")
check(st["enabled"] == ["B1", "Q18"] and st["pending"] == ["Q19", "Q21", "B3"],
      "条款未改变任何一项启用状态", (st["enabled"], st["pending"]))
check(all(v["usage_restriction"]["commercial_use"] is False for v in st["items"].values()),
      "每项 gate 都带条款标记")
expected_decisions = {
    "B1": "approved_with_conditions", "Q18": "approved",
    "Q19": "pending_legal_opinion", "Q21": "pending_legal_opinion", "B3": "not_addressed",
}
for item, dec in expected_decisions.items():
    f = ROOT / ".privacy" / "approvals" / (item + ".json")
    rec = json.loads(f.read_text(encoding="utf-8"))
    ur = rec.get("usage_restriction", {})
    check(ur.get("commercial_use") is False and ur.get("changes_gate_state") is False
          and ur.get("is_legal_exemption") is False, item + " 回执顶层写入条款")
    check(rec.get("decision") == dec, item + " 的 decision 未被条款改写", rec.get("decision"))
check(sources.CAPTURE_POLICY["research_use"]["claim"] == usage_policy.CLAUSE_ZH
      and sources.CAPTURE_POLICY["research_use"]["clause_id"] == usage_policy.CLAUSE_ID,
      "sources 研究口径与统一条款一致")
check(sources.CAPTURE_POLICY["research_use"]["is_exemption"] is False,
      "条款化后仍非合规豁免")
check(approval_gate.state()["uniform_usage_restriction"] is True,
      "回执台账对全部事项统一适用使用限制条款")

print("=== 7. Q18 出境写入防护（爬取执行体已移除，防护闸门保留）===")
import outbound_guard
for bad_url in ("https://evil.example.com", "http://abc.cloud.qdrant.io"):
    try:
        outbound_guard.validate_endpoint(bad_url)
        check(False, "出站端点拒绝：" + bad_url)
    except outbound_guard.OutboundBlocked:
        check(True, "出站端点拒绝：" + bad_url)
check(outbound_guard.status()["api_key_never_returned"] is True,
      "防护状态不返回凭据")
check("admin_curated" in outbound_guard.allowed_source_ids(),
      "管理员来源在出站白名单内", outbound_guard.allowed_source_ids())

print("=== 8. 管理员知识入库（文本 / 截图 OCR 文本 → 岗位信息 → Qdrant）===")
import knowledge_ingest
prep = knowledge_ingest.prepare("\n".join(JD_LINES), "admin_ocr",
                                title="测试岗位", purpose_ack=True)
check(prep["origin"] == "admin_ocr", "OCR 来源标注正确")
check(len(prep["chunks"]) >= 1 and prep["lines_kept"] >= 1, "切出岗位片段")
check("13812345678" not in prep["text"] and "hr@example.com" not in prep["text"],
      "知识片段不含隐私信息")
check("Python" in prep["text"], "岗位技能信息保留")
try:
    knowledge_ingest.prepare(JD, "admin_text", purpose_ack=False)
    check(False, "未授权入库被拒")
except knowledge_ingest.ConsentRequired:
    check(True, "未授权入库被拒")
try:
    knowledge_ingest.prepare("data:image/png;base64," + "A" * 420, "admin_ocr",
                             purpose_ack=True)
    check(False, "图片 payload 被拒")
except knowledge_ingest.KnowledgeRejected as e:
    check(str(e) == "IMAGE_PAYLOAD_REJECTED", "图片 payload 被拒", str(e))

print("\n=== 回执 / B1 / 管理员 单元回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

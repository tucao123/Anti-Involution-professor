# -*- coding: utf-8 -*-
"""2026-11-16 单元回归：书面回执双闸门 / B1 文件解析 / 管理员手动开关 /
截图链路隐私信息不入库 / 个人画像与岗位信息按链路分离。

不依赖 HTTP 服务，不发起任何网络请求。
"""
import io
import json
import shutil
import sys
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import approval_gate
import job_info
import rag_store
import research_capture
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

print("=== 5. 管理员手动开关（默认不自动爬取）===")
a = research_capture.admin_status()
check(a["auto_capture_default"] is False and a["toggle"]["enabled"] is False, "默认关闭")
check(a["effective"] is False and a["requests_issued"] == 0, "默认不产生任何第三方请求")
check(a["gates"]["capture_executor_implemented"] is True
      and a["gates"]["production_capture_unlocked"] is False,
      "执行体已接入但仅限练习沙盒，生产采集未解锁")
check(a["policy"]["noncommercial_exemption"] is False, "毕设 / 非商用不构成合规豁免")
check(a["policy"]["human_pace_simulation"] == "forbidden", "拒绝以模拟人类节奏规避封禁")
t = research_capture.set_admin_toggle(True, True)
check(t["toggle"]["enabled"] is True and t["effective"] is False,
      "手动开启只记录意图，闸门未齐备仍不执行")
check(t["requests_issued"] == 0, "开关开启后累计请求仍为 0")
research_capture.set_admin_toggle(False, False)
check(research_capture.admin_status()["toggle"]["enabled"] is False, "可随时关闭并复位")
rp = research_capture.research_policy()
check(rp["admin_control"]["requires_manual_toggle"] is True, "研究策略同步声明手动开关")

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
adm = research_capture.admin_status()
check(adm["policy"]["usage_restriction"]["clause_id"] == usage_policy.CLAUSE_ID,
      "管理员策略引用统一条款")
check(adm["policy"]["commercial_use_allowed"] is False
      and adm["policy"]["noncommercial_exemption"] is False
      and adm["policy"]["human_pace_simulation"] == "forbidden",
      "禁止商用 / 非豁免 / 拒绝拟人化规避三项不变")
check(adm["usage_restriction"]["changes_gate_state"] is False, "管理员状态带条款标记")
check("自担风险放行" in adm["policy"]["uniform_application"]
      and "严格限速契约" in adm["policy"]["uniform_application"],
      "B2 放行范围与严格限速契约被明确写入策略")
check(sources.CAPTURE_POLICY["research_use"]["claim"] == usage_policy.CLAUSE_ZH
      and sources.CAPTURE_POLICY["research_use"]["clause_id"] == usage_policy.CLAUSE_ID,
      "sources 研究口径与统一条款一致")
rp2 = research_capture.research_policy()
check(rp2["legal_position"]["clause_id"] == usage_policy.CLAUSE_ID, "B2 主张已条款化")
check(rp2["legal_position"]["is_exemption"] is False, "条款化后仍非合规豁免")
try:
    research_capture.capture_screenshot("/")
    denied = False
except research_capture.CaptureDisabled:
    denied = True
check(denied, "自动采集执行体仍禁用，条款不构成解锁")

print("=== 7. B2 自担风险放行 + 严格限速硬契约 ===")
rel = research_capture.owner_release_state()
check(rel["released"] is True and rel["reason"] == "OK", "B2 决策放行回执校验通过")
check(rel["issuer"] == "项目所有者（毕设作者）" and rel["risk_bearing"] == "project_owner",
      "出具方与风险承担方如实标注")
check(rel["legal_opinion"] is False and rel["legal_review"] == "pending"
      and rel["is_legal_or_site_license"] is False,
      "不冒充法务书面结论或站点书面许可")
b2rec = json.loads((ROOT / ".privacy" / "approvals" / "B2.json").read_text(encoding="utf-8"))
check(b2rec["scope"]["auto_capture_default"] is False
      and b2rec["scope"]["manual_toggle_each_time"] is True,
      "默认不自动爬取、逐次手动开关写进回执条件")
pc = research_capture.PACE_CONTRACT
check(pc["concurrency_per_host"] == 1 and pc["burst"] == 1, "单域名串行 / 无突发")
check(pc["min_interval_s"] >= 15.0 and pc["human_reading_dwell_s"] >= 12.0,
      "每页慢速间隔 + 人类阅读静置")
check(research_capture.effective_interval_s(30.0) == 30.0, "Crawl-delay 优先只会更慢")
check(pc["daily_cap"] <= 60 and pc["max_pages_per_batch"] <= 10
      and pc["batch_cooldown_s"] >= 300, "每日 / 单批次 / 冷却三层上限")
check(pc["ua_rotation"] is False and pc["ip_rotation"] is False
      and pc["identity_spoofing"] is False and pc["randomized_jitter_for_evasion"] is False
      and pc["captcha_or_waf_bypass"] is False, "规避检测类参数全部禁用")
check(pc["human_scale_pacing"] == "enforced" and pc["evasion_techniques"] == "forbidden",
      "「模拟人类」= 人类可读低频节奏，规避式拟人化禁止")
p5 = research_capture.enforce_pace(5)
check(p5["ok"] is True and p5["interval_s"] >= 15.0 and p5["requests_issued"] == 0,
      "正常批次通过准入核算且不发请求")
for kw, label in (({"concurrency": 4}, "并发 > 1"), ({"burst": 3}, "burst > 1"),
                  ({"ua_rotation": True}, "UA 轮换"), ({"ip_rotation": True}, "IP 轮换"),
                  ({"evasion": True}, "规避参数")):
    try:
        research_capture.enforce_pace(5, **kw)
        ok = False
    except research_capture.PaceViolation:
        ok = True
    check(ok, "硬闸门拒绝：" + label)
try:
    research_capture.enforce_pace(11)
    ok = False
except research_capture.PaceViolation:
    ok = True
check(ok, "硬闸门拒绝：超出单批次 10 页")
adm3 = research_capture.admin_status()
check(adm3["gates"]["owner_risk_release"] is True
      and adm3["gates"]["strict_pace_contract"] is True, "管理员闸门显示放行与限速契约生效")
check(adm3["gates"]["site_written_license"] is False
      and adm3["gates"]["legal_written_conclusion"] is False,
      "站点许可 / 法务结论仍如实标注未回")
check(adm3["effective"] is False and adm3["requests_issued"] == 0
      and adm3["production_capture_unlocked"] is False,
      "执行体存在不等于生产采集解锁：effective=false / 生产请求为零")
check(adm3["gates"]["capture_executor_implemented"] is True
      and adm3["auto_capture_executor"] == "test_sandbox_only",
      "执行体作用域被硬限定为练习沙盒")
check(adm3["decision_gate_released"] is False, "未手动开启时决策闸门未释放")
check(len(adm3["blocked_by"]) >= 4, "剩余阻塞项逐条列出")
t3 = research_capture.set_admin_toggle(True, True)
check(t3["decision_gate_released"] is True, "逐次手动开启后决策闸门释放")
check(t3["effective"] is False and t3["requests_issued"] == 0,
      "开启后仍不执行、仍零请求")
research_capture.set_admin_toggle(False, False)
check(adm3["policy"]["human_pace_simulation"] == "forbidden"
      and adm3["human_scale_pacing"] == "enforced", "两类「拟人化」概念严格区分")
check(any("规避检测" in x for x in research_capture.PERMANENTLY_FORBIDDEN),
      "规避检测式拟人化仍在永久禁止清单")

print("=== 8. 截图测试目标白名单（只用允许爬取的测试页 / 不去招聘网站）===")
local_ok = research_capture.is_allowed_test_target(research_capture.LOCAL_TEST_TARGETS[0])
check(local_ok["allowed"] is True and local_ok["reason"] == "OK", "本地自建测试页允许爬取")
check(research_capture.is_allowed_test_target(research_capture.LOCAL_TEST_TARGETS[1])["allowed"] is True,
      "localhost 别名同样允许")
for bad in ("https://www.zhipin.com/job_detail/", "https://www.lagou.com/wn/jobs",
            "https://www.liepin.com/zhaopin/", "https://jobicy.com/api/v2/remote-jobs",
            "https://remoteok.com/", "https://www.indeed.com/jobs"):
    check(research_capture.is_allowed_test_target(bad)["allowed"] is False,
          "招聘网站被代码层拒绝：" + urllib.parse.urlparse(bad).hostname)
check(research_capture.is_allowed_test_target("https://example.com/")["allowed"] is False,
      "非本地 host 即使非招聘站也拒绝（默认白名单）")
check(research_capture.is_allowed_test_target("http://127.0.0.1:8848/index.html")["allowed"] is False,
      "本地非白名单路径拒绝")
check(research_capture.is_allowed_test_target("file:///etc/passwd")["allowed"] is False,
      "非 HTTP(S) 目标拒绝")
check("PACE TEST FIXTURE" in research_capture.LOCAL_TEST_PAGE_HTML
      and "虚构" in research_capture.LOCAL_TEST_PAGE_HTML,
      "测试页自述允许爬取且声明为虚构演示内容")
tp = research_capture.pace_test_plan(3)
check(tp["all_targets_allowed"] is True and tp["pages"] == 3, "3 页准入核算全部目标合法")
check(tp["third_party_requests"] == 0 and tp["recruitment_sites"] == 0
      and tp["interval_s"] >= 15.0, "零第三方请求 / 零招聘站 / 慢速间隔")
try:
    research_capture.pace_test_plan(11)
    ok = False
except research_capture.PaceViolation:
    ok = True
check(ok, "超出单批次上限的测试计划被拒绝")

print("=== 9. 练习沙盒白名单 / 真实执行体 / Q18 出境防护 ===")
import capture_executor
import outbound_guard
sites = research_capture.SANDBOX_SITES
check("books.toscrape.com" in sites
      and "scraping purposes" in sites["books.toscrape.com"]["evidence_quote"],
      "练习沙盒带公开声明证据")
check(sites["books.toscrape.com"]["is_site_written_license"] is False,
      "公开声明如实标注不是站点书面许可函")
for good in ("https://books.toscrape.com/", "https://books.toscrape.com/index.html",
             "https://books.toscrape.com/catalogue/page-2.html"):
    check(research_capture.is_allowed_test_target(good)["allowed"] is True,
          "沙盒分页允许：" + good)
for bad, why in (("https://books.toscrape.com/?q=1", "SANDBOX_QUERY_FORBIDDEN"),
                 ("http://books.toscrape.com/", "SANDBOX_SCHEME_MISMATCH"),
                 ("https://www.zhipin.com/", "RECRUITMENT_OR_REAL_SITE_FORBIDDEN"),
                 ("https://example.com/", "NON_LOCAL_HOST_FORBIDDEN")):
    r = research_capture.is_allowed_test_target(bad)
    check(r["allowed"] is False and r["reason"] == why, "目标硬拒绝：" + why)
sp = research_capture.pace_test_plan(3, research_capture.sandbox_page_urls(3))
check(sp["all_targets_allowed"] is True and sp["interval_s"] >= 15.0,
      "沙盒 3 页准入核算通过且间隔符合契约")
check(capture_executor.EXECUTOR_POLICY["scope"] == "test_sandbox_only"
      and capture_executor.EXECUTOR_POLICY["production_capture_unlocked"] is False,
      "执行体策略限定沙盒")
urls = research_capture.sandbox_page_urls(2)
check(capture_executor.readiness(2, False, urls)["ok"] is False,
      "默认闸门关闭：未确认不执行")
check(capture_executor.readiness(2, True, ["https://www.zhipin.com/"])["ok"] is False,
      "招聘站目标导致执行体闸门关闭")
check(capture_executor.status()["production_capture_unlocked"] is False,
      "执行体状态明确生产采集未解锁")
for bad_url in ("https://evil.example.com", "http://abc.cloud.qdrant.io"):
    try:
        outbound_guard.validate_endpoint(bad_url)
        check(False, "出站端点拒绝：" + bad_url)
    except outbound_guard.OutboundBlocked:
        check(True, "出站端点拒绝：" + bad_url)
check(outbound_guard.status()["api_key_never_returned"] is True,
      "防护状态不返回凭据")

print("\n=== 回执 / B1 / 管理员 单元回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

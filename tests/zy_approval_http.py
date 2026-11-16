# -*- coding: utf-8 -*-
"""2026-11-16 新增端点的真实 HTTP 回归：回执台账 / B1 上传解析 /
管理员手动开关 / Q18 状态 / 截图链路隐私不入库。

不触发真实境外写入（云写入由人工验收单独执行）。
"""
import io
import json
import os
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = os.environ.get("ZHIYIN_BASE_URL", "http://127.0.0.1:8848").rstrip("/")
PASS = FAIL = 0


def check(cond, label, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", label)
    else:
        FAIL += 1
        print("  FAIL", label, extra)


def req(path, method="GET", body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(r, timeout=25) as x:
            return x.status, json.loads(x.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {}
    except Exception as e:
        return 0, {"error": type(e).__name__}


def req_upload(path, filename, content, query=""):
    boundary = "----ZhiyinApprovalTest"
    head = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n" % (boundary, filename)).encode()
    body = head + content + ("\r\n--%s--\r\n" % boundary).encode()
    r = urllib.request.Request(BASE + path + query, data=body, method="POST",
                               headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.status, json.loads(x.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace") or "{}")
        except ValueError:
            return e.code, {}
    except Exception as e:
        return 0, {"error": type(e).__name__}


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
    "岗位职责：必须熟练掌握 Python，负责大模型应用与 RAG 检索增强链路设计",
    "任职要求：熟悉 LangChain、向量数据库与混合检索，独立完成评测集构建",
]

print("=== 1. 回执台账端点 ===")
s, d = req("/api/approvals/status")
check(s == 200 and d.get("ok"), "回执状态端点 200")
check(set(d.get("enabled", [])) == {"B1", "Q18"}, "仅 B1 / Q18 启用", d.get("enabled"))
check(set(d.get("pending", [])) == {"Q19", "Q21", "B3"}, "三项保持 CLOSED")
check(d.get("writable_api") is False, "无自我授权写入接口")
check(d.get("legal_opinion_received") == [], "未虚报任何法务结论")
check(all(v.get("legal_review") == "pending" for v in d.get("items", {}).values()),
      "所有项如实标注法务结论待回")

print("=== 2. Q18 双闸门状态 ===")
s, v = req("/api/vector/status")
check(s == 200 and v.get("cloud_writes_enabled") is True, "云写入按回执开启")
check("legal_review" in str(v.get("q18_approval", {})), "状态含回执闸门明细")
check("待回" in v.get("cloud_write_gate", ""), "状态显著标注法务结论待回")
check(v.get("user_data_to_cloud") is False, "C 级用户数据仍不入云")

print("=== 3. 管理员手动开关 ===")
s, a = req("/api/capture/admin")
check(s == 200 and a.get("auto_capture_default") is False, "默认不自动爬取")
check(a.get("toggle", {}).get("enabled") is False, "开关默认关闭")
check(a.get("effective") is False and a.get("requests_issued") == 0, "默认零请求")
check(a.get("policy", {}).get("noncommercial_exemption") is False, "非商用不构成豁免")
s, t = req("/api/capture/admin/toggle", "POST", {"enabled": True})
check(s == 403 and t.get("error") == "OPERATOR_ACK_REQUIRED", "开启需管理员显式确认")
s, t = req("/api/capture/admin/toggle", "POST", {"enabled": True, "operator_ack": True})
check(s == 200 and t.get("effective") is False and t.get("requests_issued") == 0,
      "手动开启仍不发起任何第三方请求")
req("/api/capture/admin/toggle", "POST", {"enabled": False, "operator_ack": False})
s, a2 = req("/api/capture/admin")
check(a2.get("toggle", {}).get("enabled") is False, "开关可复位关闭")

print("=== 4. B1 文件解析（不留存 / 可撤回删除）===")
s, r = req("/api/resume/status")
check(s == 200 and r.get("resume_parse_open") is True, "B1 状态端点显示已开放")
check(r.get("pii_to_store") is False, "状态声明个人信息不入库")
check(r.get("profiling_path") == "/api/jd/analyze", "个人画像指向本地链路")
s, c = req("/api/resume/consent")
check(s == 200 and "Q19" in c.get("notice", ""), "第三方告知文案可读")

docx = minimal_docx(JD_LINES)
s, out = req_upload("/api/resume/parse", "岗位.docx", docx)
check(s == 200 and out.get("ok"), "docx 上传解析 200", (s, out.get("error")))
p = out.get("job_profile", {})
check("张伟" not in str(p) and "13812345678" not in str(p), "返回不含个人信息")
check("覆盖率" not in str(p) and "匹配度" not in str(p), "返回不含画像字段")
check(out.get("retention", {}).get("raw_text") == "none", "原文不留存")
check(out.get("cloud_used") is False, "默认本地解析")
sid = out.get("session_id", "")
check(bool(sid), "返回会话 id 供撤回")
s, w = req("/api/resume/withdraw", "POST", {"session_id": sid})
check(s == 200 and w.get("removed") is True, "撤回端点有效")
s, dl = req("/api/resume/delete", "POST", {"session_id": sid})
check(s == 200 and dl.get("ok"), "删除端点有效")

s, bad = req_upload("/api/resume/parse", "bad.txt", b"plain text")
check(s == 400 and bad.get("error") == "FILE_TYPE_NOT_ALLOWED", "非 pdf/docx 被拒", (s, bad))
s, bad = req_upload("/api/resume/parse", "bad.pdf", b"not a pdf")
check(s == 400 and bad.get("error") == "FILE_MAGIC_MISMATCH", "文件头不符被拒", (s, bad))
s, bad = req_upload("/api/resume/parse", "岗位.docx", docx,
                    "?cloud_enrich=true")
check(s == 403 and bad.get("error") == "SEPARATE_CONSENT_REQUIRED",
      "云端解析需单独同意", (s, bad))

print("=== 5. 截图链路隐私信息不入库 ===")
s, j = req("/api/rag/ingest", "POST", {
    "text": "\n".join(JD_LINES), "source_id": "user_screenshot",
    "title": "HTTP 截图测试", "purpose_ack": True, "retain_days": 30,
    "origin": "local_ocr"})
check(s == 200 and j.get("ok"), "截图 OCR 文本入库 200", (s, j.get("error")))
d = j.get("doc", {})
check(d.get("job_only") is True and d.get("pii_lines_excluded") is True,
      "入库标记仅岗位信息 / 隐私行剔除")
check(d.get("pii_redacted", 0) >= 1, "个人信息检出并排除的举证保留")
if d.get("id"):
    req("/api/rag/delete", "POST", {"id": d["id"]})
s, rst = req("/api/rag/status")
check(rst.get("screenshot_pii_to_store") is False and rst.get("local_ocr_job_only") is True,
      "RAG 状态如实声明隐私不入库")

print("=== 6. 隐私状态同步 ===")
s, ps = req("/api/privacy/status")
check(s == 200 and ps.get("resume_parse_open") is True, "privacy 状态同步 B1 闸门")
check(ps.get("screenshot_pii_to_store") is False, "privacy 状态同步截图边界")
check(ps.get("pii_ingest_open") is False, "PII 入库入口仍关闭")

print("=== 7. 全项目使用限制条款（只读端点）===")
s, u = req("/api/project/usage")
check(s == 200 and u.get("ok") is True, "GET /api/project/usage 返回 200", (s, u.get("error")))
pol = u.get("policy", {})
check(pol.get("commercial_use") is False and pol.get("is_legal_exemption") is False,
      "条款：禁止商业行为 / 不是合规豁免")
check(u.get("gate_state_changed") is False, "端点明示未改变闸门状态")
items = u.get("applies_to_items", {})
check(all(items.get(k) is True for k in ("B1", "Q18", "Q19", "Q21", "B3")),
      "5 项待决事项同理适用", items)
s, apr = req("/api/approvals/status")
check(apr.get("uniform_usage_restriction") is True, "回执状态 5/5 统一适用")
check(apr.get("enabled") == ["B1", "Q18"] and apr.get("pending") == ["Q19", "Q21", "B3"],
      "启用状态未因条款改变", (apr.get("enabled"), apr.get("pending")))
s, cap = req("/api/capture/admin")
check(cap.get("usage_restriction", {}).get("changes_gate_state") is False,
      "管理员状态带条款标记")
check(cap.get("policy", {}).get("human_pace_simulation") == "forbidden",
      "拟人化规避采集仍禁止")

print("=== 8. B2 自担风险放行 + 严格限速（只读端点）===")
s, pace = req("/api/capture/pace")
check(s == 200 and pace.get("ok") is True, "GET /api/capture/pace 返回 200", (s, pace.get("error")))
ct = pace.get("contract", {})
check(ct.get("concurrency_per_host") == 1 and ct.get("burst") == 1
      and ct.get("min_interval_s", 0) >= 15 and ct.get("daily_cap", 999) <= 60,
      "契约：串行 / 慢速 / 每日上限")
check(ct.get("ua_rotation") is False and ct.get("randomized_jitter_for_evasion") is False
      and ct.get("captcha_or_waf_bypass") is False, "契约：规避检测手段禁用")
check(pace.get("human_scale_pacing") == "enforced"
      and pace.get("evasion_techniques") == "forbidden", "两类拟人化概念区分")
ex = pace.get("example", {})
check(ex.get("interval_s", 0) >= 15 and pace.get("requests_issued") == 0,
      "5 页准入核算慢速且不发请求", (ex.get("interval_s"), pace.get("requests_issued")))
rel = pace.get("owner_risk_release", {})
check(rel.get("released") is True and rel.get("is_legal_or_site_license") is False
      and rel.get("legal_review") == "pending", "放行回执如实标注非法律结论 / 非站点许可")
s, cap2 = req("/api/capture/admin")
check(cap2.get("gates", {}).get("owner_risk_release") is True
      and cap2.get("gates", {}).get("strict_pace_contract") is True,
      "管理员闸门显示放行与限速契约生效")
check(cap2.get("gates", {}).get("site_written_license") is False
      and cap2.get("gates", {}).get("legal_written_conclusion") is False,
      "站点许可 / 法务结论仍未回")
check(cap2.get("effective") is False and cap2.get("requests_issued") == 0
      and cap2.get("production_capture_unlocked") is False,
      "执行体存在不等于生产采集解锁：effective=false / 生产请求为零")
check(cap2.get("gates", {}).get("capture_executor_implemented") is True
      and cap2.get("auto_capture_executor") == "test_sandbox_only",
      "执行体作用域被硬限定为练习沙盒")
check(cap2.get("decision_gate_released") is False, "未手动开启时决策闸门未释放")
check(len(cap2.get("blocked_by", [])) >= 4, "剩余阻塞项逐条列出")
s, tog = req("/api/capture/admin/toggle", "POST", {"enabled": True, "operator_ack": True})
check(s == 200 and tog.get("decision_gate_released") is True, "逐次手动开启后决策闸门释放")
check(tog.get("effective") is False and tog.get("requests_issued") == 0,
      "开启后仍不执行、仍零请求")
req("/api/capture/admin/toggle", "POST", {"enabled": False, "operator_ack": False})
check(cap2.get("policy", {}).get("human_pace_simulation") == "forbidden",
      "规避检测式拟人化仍禁止")

print("=== 9. 截图测试目标白名单（只用允许爬取的测试页 / 不去招聘网站）===")
try:
    with urllib.request.urlopen(BASE + "/api/capture/test-page", timeout=10) as x:
        page_code, test_html = x.status, x.read().decode("utf-8", "replace")
except Exception:
    page_code, test_html = 0, ""
check(page_code == 200 and "PACE TEST FIXTURE" in test_html and "虚构" in test_html,
      "本地自建测试页返回且自述允许爬取 / 虚构内容", page_code)
s, pace2 = req("/api/capture/pace")
al = pace2.get("test_target_allowlist", {})
check(al.get("default_policy") == "local_test_page_plus_verified_sandbox"
      and al.get("recruitment_sites_forbidden") is True, "白名单为本地测试页 + 已核实练习沙盒")
check("https://books.toscrape.com/" in al.get("extra_allowed_targets", []),
      "所有者指定的练习沙盒已显式列入白名单")
check("books.toscrape.com" in al.get("sandbox_sites", {})
      and al["sandbox_sites"]["books.toscrape.com"].get("is_site_written_license") is False,
      "练习沙盒附带证据且如实标注不是书面许可函")
check(pace2.get("production_capture_unlocked") is False, "生产采集未解锁")
pe = al.get("plan_example", {})
check(pe.get("all_targets_allowed") is True and pe.get("third_party_requests") == 0
      and pe.get("recruitment_sites") == 0, "准入核算：零第三方 / 零招聘站")
check(any("zhipin" in k or "lagou" in k for k in al.get("forbidden_keywords", [])),
      "招聘站点关键词硬拦截清单可读")
s, st = req("/api/capture/pace-test/status")
check(s == 200 and st.get("ok") is True, "本地限速测试报告状态端点 200")
if st.get("report_present"):
    rp = st.get("report", {})
    check(rp.get("third_party_requests") == 0 and rp.get("recruitment_sites_touched") == 0
          and rp.get("bitmap_retention") == "none",
          "已有测试报告如实标注零第三方请求 / 位图无留存")

print("=== 10. Q18 出境写入防护 / B2 真实执行体端点 ===")
s, gd = req("/api/vector/guard")
check(s == 200 and gd.get("guard_id") == "QDRANT-OUTBOUND-GUARD-2026-11-16",
      "出境防护状态端点 200")
check(gd.get("api_key_never_returned") is True and gd.get("audit_content") == "digest_only",
      "防护状态不返回凭据、审计只写摘要")
check("D" in gd.get("allowed_grades", []) and "user_jd" not in gd.get("allowed_source_ids", []),
      "分级 D / 已批准来源白名单可读")
check(gd.get("endpoint", {}).get("ok") is True, "当前 Qdrant 端点通过目标锁定")
s, dry = req("/api/vector/guard/check", "POST",
             {"items": [{"id": "a", "text": "Data engineer remote Python SQL", "title": "DE"}],
              "source_id": "jobicy", "scope": "public"})
check(s == 200 and dry.get("allowed") is True and dry.get("network_called") is False
      and dry.get("data_written") is False,
      "dry-run 干净样本通过，且不联网不写入")
s, dry2 = req("/api/vector/guard/check", "POST",
              {"items": [{"id": "a", "text": "张三 13800138000 a@b.com", "title": "x"}],
               "source_id": "jobicy", "scope": "public"})
check(s == 200 and dry2.get("allowed") is False and dry2.get("data_written") is False,
      "dry-run 检出个人信息即拒绝，且不写入")
s, exs = req("/api/capture/executor/status")
check(s == 200 and exs.get("policy", {}).get("scope") == "test_sandbox_only"
      and exs.get("production_capture_unlocked") is False,
      "执行体状态端点：仅沙盒、生产采集未解锁")
check(exs.get("requests_issued") == 0, "生产采集请求恒为 0")
s, noack = req("/api/capture/executor/run", "POST", {"pages": 1, "operator_ack": False})
check(s == 403, "未确认时执行体端点拒绝（403）")
s, notoggle = req("/api/capture/executor/run", "POST", {"pages": 1, "operator_ack": True})
check(s == 403, "未逐次手动开启开关时执行体端点拒绝（403）")

print("\n=== 回执 / B1 / 管理员 HTTP 回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

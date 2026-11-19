# -*- coding: utf-8 -*-
"""新增端点的真实 HTTP 回归：回执台账 / B1 上传解析 / Q18 状态 /
管理员知识入库 / 截图链路隐私不入库 / Q18 出境防护 / BGE 恢复。

自动爬取 / 截图执行体已整体移除，本套件不再断言任何 /api/capture/* 写端点。
不触发真实境外写入（云写入由人工验收单独执行；知识入库仅验证校验路径）。
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

print("=== 3. 管理员知识入库（自动爬取已移除，来源只由管理员提供）===")
s, kst = req("/api/admin/knowledge/status")
check(s == 200 and kst.get("ok"), "知识入库状态端点 200")
check(kst.get("source_id") == "admin_curated" and kst.get("grade") == "D", "管理员来源 D 级")
check(kst.get("image_upload_endpoint") is False, "服务端无图片上传端点")
check("browser-local" in kst.get("ocr_location", ""), "OCR 在浏览器本地")
check(kst.get("pii_to_store") is False and kst.get("job_only") is True, "隐私不入库 / 仅岗位信息")
check(kst.get("cloud_writes_enabled") is True, "写入指向 Qdrant 云")
# 校验路径：未授权 → 403；图片 payload → 400；均不产生写入
s, noack = req("/api/admin/knowledge/ingest", "POST",
               {"text": "\n".join(JD_LINES), "origin": "admin_text", "purpose_ack": False})
check(s == 403 and noack.get("error") == "CONSENT_REQUIRED", "未授权入库 403", (s, noack))
s, img = req("/api/admin/knowledge/ingest", "POST",
             {"text": "data:image/png;base64," + "A" * 420,
              "origin": "admin_ocr", "purpose_ack": True})
check(s == 400 and img.get("error") == "IMAGE_PAYLOAD_REJECTED", "图片 payload 被拒 400", (s, img))
s, badorigin = req("/api/admin/knowledge/ingest", "POST",
                   {"text": "\n".join(JD_LINES) * 2, "origin": "public_api", "purpose_ack": True})
check(s in (400, 422), "非法来源形态被 Schema/逻辑拒绝", (s, badorigin))
# 路由表确认：capture 下无 POST，知识入库端点已注册
s, spec = req("/api/routes")
paths = spec.get("paths", {})
check("/api/admin/knowledge/ingest" in paths and "/api/admin/knowledge/delete" in paths,
      "知识入库 / 删除端点已注册")
check(not any("/capture" in p and "post" in m for p, m in paths.items()),
      "capture 命名空间无 POST 写端点")

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
s, bad = req_upload("/api/resume/parse", "岗位.docx", docx, "?cloud_enrich=true")
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
s, cap = req("/api/research/policy")
check(cap.get("verdict") == "admin_curated_only", "研究策略=仅管理员提供")
check(cap.get("third_party_requests") == 0, "零第三方请求")

print("=== 8. Q18 出境写入防护 / BGE 恢复 ===")
s, gd = req("/api/vector/guard")
check(s == 200 and gd.get("guard_id") == "QDRANT-OUTBOUND-GUARD-2026-11-16",
      "出境防护状态端点 200")
check(gd.get("api_key_never_returned") is True and gd.get("audit_content") == "digest_only",
      "防护状态不返回凭据、审计只写摘要")
check("D" in gd.get("allowed_grades", []) and "admin_curated" in gd.get("allowed_source_ids", [])
      and "user_jd" not in gd.get("allowed_source_ids", []),
      "分级 D / 管理员来源在白名单 / 用户数据不在白名单")
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

s, ast = req("/api/approvals/status")
q18 = (ast.get("items") or {}).get("Q18", {}) if s == 200 else {}
oc = q18.get("owner_confirmation") or {}
check(oc.get("present") is True and "不回滚" in str(oc.get("decision", "")),
      "Q18 所有者「维持允许、不回滚」确认可观测")
check(oc.get("legal_review") == "pending" and q18.get("legal_review") == "pending",
      "Q18 确认如实标注 legal_review=pending，不冒充法务结论")
s, emb = req("/api/embedding/status")
check(s == 200 and emb.get("bge_available") is True
      and (emb.get("selected_backend") or {}).get("user") == "bge"
      and "bge_env" in str(emb.get("bge_runtime", "")),
      "BGE 已恢复：独立依赖目录 + user 侧本地语义向量")
check(emb.get("semantic", {}).get("bge") is True
      and emb.get("user_data_stays_local") is True,
      "BGE 恢复后用户数据仍不出本机")
check(str(emb.get("bge_model_source", "")).startswith("local_hub_snapshot"),
      "BGE 按官方 modules.json 加载（非 mean pooling 重建）")
check((emb.get("selected_backend") or {}).get("public") == "bge"
      and "512" in str(emb.get("dimension_policy", ""))
      and emb.get("cloud_embedding_used") is False,
      "public / user 统一走服务器固定本地 BGE 512 维，不再使用 embedding-v3")

print("\n=== 回执 / B1 / 管理员 HTTP 回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

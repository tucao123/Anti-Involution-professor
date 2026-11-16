# -*- coding: utf-8 -*-
"""Phase 2 新增端点的真实 HTTP 回归（目标服务由 ZHIYIN_BASE_URL 指定）。"""
import json
import os
import urllib.error
import urllib.request

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
        with urllib.request.urlopen(r, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw
    except Exception as e:
        return 0, type(e).__name__


print("=== 1. 健康与配置 ===")
s, h = req("/api/health")
check(s == 200 and h.get("phase") == 2 and h.get("provider") == "qwen", "health 为 Phase 2 / qwen")
check("gate" not in h and h.get("configured") in (True, False), "health 不回显凭据细节")
check(h.get("cloud_user_data_allowed") is False, "C 级数据上云默认关闭")

s, c = req("/api/config")
raw = json.dumps(c, ensure_ascii=False)
check(s == 200 and c.get("sensitive_values_never_returned") is True, "config 视图可用")
_qurl = (c.get("keys") or {}).get("QDRANT_URL") or {}
check("host-redacted" in str(_qurl.get("shape", "")) and "value" not in _qurl
      and "sk-" not in raw, "config 不泄露实例主机名与 key")

print("=== 2. 状态端点全部可用 ===")
for path in ("/api/llm/status", "/api/embedding/status", "/api/vector/status",
             "/api/vector/probe", "/api/graph/status", "/api/stores/status",
             "/api/tasks/status", "/api/slo", "/api/schema"):
    s, j = req(path)
    check(s == 200, "GET %s" % path, str(j)[:100])

s, m = req("/metrics")
check(s == 200 and isinstance(m, str) and "zhiyin_" in m, "GET /metrics 文本指标")

print("=== 3. /api/llm 契约（启用时结构化输出 / 未启用时降级）===")
s, j = req("/api/llm", "POST", {"task": "insight", "payload": {"reading": "x", "focus": ["a"]}})
check((s == 200 and j.get("validated") is True and j.get("provider") == "qwen")
      or s in (502, 503), "qwen 链路按当前开关如实返回，不返回未校验内容", str(j)[:120])

print("=== 4. 云向量库闸门 ===")
s, j = req("/api/vector/ingest", "POST", {
    "items": [{"id": "http-a", "text": "RAG 检索增强与向量数据库岗位"}],
    "source_id": "jobicy", "scope": "public"})
check(s == 403 and j.get("error") == "CONSENT_REQUIRED", "未确认用途授权即拒绝")
s, j = req("/api/vector/ingest", "POST", {
    "items": [{"id": "http-b", "text": "用户自带简历内容"}],
    "scope": "user", "purpose_ack": True})
check(s == 403 and j.get("error") == "CLOUD_USER_DATA_BLOCKED", "user scope 在路由层拒绝")
s, j = req("/api/vector/ingest", "POST", {
    "items": [{"id": "http-c", "title": "Remote Python Engineer",
               "text": "负责 RAG 检索增强 pipeline，使用 Python 与向量数据库构建岗位匹配服务。"}],
    "source_id": "jobicy", "scope": "public", "purpose_ack": True})
check(s == 200 and j.get("ok") and j.get("grade") == "D", "D 级公开样本入库")
s, q = req("/api/vector/search", "POST", {"q": "向量检索 RAG", "top_k": 3})
check(s == 200 and q.get("count", 0) >= 1 and q["hits"][0]["score"] > 0, "检索命中且分数非零")
check(all(x["grade"] == "D" and x["forbidden_claims"] for x in q["hits"]), "命中带分级与禁止结论")

print("=== 5. 编排与任务 ===")
jd = "岗位职责：负责 RAG 检索增强系统开发，使用 Python 与向量数据库。任职要求：精通 Python，熟悉 SQL 与 FastAPI。"
s, j = req("/api/graph/run", "POST", {"jd_text": jd, "purpose_ack": False})
check(s == 403, "编排未授权即拒绝")
s, j = req("/api/graph/run", "POST", {"jd_text": jd, "purpose_ack": True})
check(s == 200 and j.get("ok") is True and j.get("cloud_called") is False, "编排本地分支成功")
check(j.get("raw_text_persisted") is False and j.get("raw_text_sent_to_cloud") is False,
      "编排声明原文零落盘、未外发")
check(bool(j.get("session_id")) and bool(j.get("withdraw_url")), "编排结果带可撤回入口")
check(bool(j.get("engine")), "编排如实标注实际引擎（langgraph / compat-stdlib）")
s, j = req("/api/tasks/dispatch", "POST", {"task_name": "verify_audit_chain", "kwargs": {}})
check(s == 200 and j.get("ok") is True, "任务派发成功")

print("=== 6. Phase 1 契约回归 ===")
for path in ("/api/sources", "/api/insight/modes", "/api/capture/policy",
             "/api/research/policy", "/api/rag/status", "/api/jd/status",
             "/api/privacy/status", "/"):
    s, _ = req(path)
    check(s == 200, "GET %s 仍可用" % path)

print("\n=== Phase 2 HTTP 回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
raise SystemExit(1 if FAIL else 0)

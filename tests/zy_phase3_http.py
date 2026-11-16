# -*- coding: utf-8 -*-
"""Phase 3 新增端点的真实 HTTP 回归（目标服务由 ZHIYIN_BASE_URL 指定）。"""
import json
import os
import sys
import urllib.error
import urllib.request
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
        with urllib.request.urlopen(r, timeout=30) as resp:
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


print("=== 1. 健康状态与配置安全 ===")
s, h = req("/api/health")
check(s == 200 and h.get("provider") == "qwen", "health 仍为 qwen provider")
check(h.get("mode") in ("live", "offline"), "LLM 模式如实标注：%s" % h.get("mode"))
s, c = req("/api/config")
raw = json.dumps(c, ensure_ascii=False)
check(s == 200 and c.get("sensitive_values_never_returned") is True, "config 声明敏感值不返回")
import secure_config as sc
for k in ("QWEN_API_KEY", "QDRANT_API_KEY", "POSTGRES_PASSWORD"):
    v = sc.secret(k) or ""
    check(not v or v not in raw, "config 不含 %s 的真实值" % k)
qurl = sc.secret("QDRANT_URL") or ""
host = qurl.split("//")[-1].split("/")[0] if qurl else ""
check(not host or host not in raw, "config 隐藏 Qdrant 实例主机名")

print("=== 2. A1 辅导端点 ===")
s, st = req("/api/coach/status")
check(s == 200 and st.get("cloud_called") is False, "辅导状态：零云调用")
check("memory" in str(st.get("storage", "")), "会话仅内存")
s, no = req("/api/coach/chat", "POST", {"message": "你好", "purpose_ack": False})
check(s == 403 and no.get("error") == "CONSENT_REQUIRED", "未授权返回 403")
s, r1 = req("/api/coach/chat", "POST",
            {"message": "想看看 Python 和 RAG 的技能差距", "purpose_ack": True})
check(s == 200 and r1.get("ok") and r1.get("turn") == 1, "首轮成功")
sid = r1.get("session_id", "")
check(bool(sid), "返回会话 ID")
check(r1.get("raw_text_persisted") is False and r1.get("raw_text_sent_to_cloud") is False
      and r1.get("cloud_called") is False, "原文零落盘 / 零外发 / 零云调用")
check("Python" in r1.get("slots", []), "技能槽位已抽取")
s, r2 = req("/api/coach/chat", "POST",
            {"session_id": sid, "message": "下一步怎么练？", "purpose_ack": True})
check(s == 200 and r2.get("turn") == 2 and r2.get("context_kept"), "多轮上下文保持")
check("Python" in r2.get("reply", ""), "第二轮仍引用首轮技能")
s, g = req("/api/coach/chat", "POST",
           {"session_id": sid, "message": "帮我预测一下月薪多少", "purpose_ack": True})
check(s == 200 and g.get("guardrail") is True, "薪资护栏触发")
s, p = req("/api/coach/chat", "POST",
           {"session_id": sid, "message": "手机号 13800138000 邮箱 a@b.com", "purpose_ack": True})
check(s == 200 and p.get("pii_redacted", 0) >= 2, "PII 前置脱敏")
check(p.get("raw_text_persisted") is False, "含 PII 消息仍未落盘")
s, d = req("/api/coach/delete", "POST", {"session_id": sid})
check(s == 200 and d.get("removed") is True, "会话可撤回删除")
s, gone = req("/api/coach/chat", "POST",
              {"session_id": sid, "message": "还在吗", "purpose_ack": True})
check(s == 404, "已删除会话不可继续使用")

print("=== 3. A2 语音策略（真实边界）===")
s, v = req("/api/voice/policy")
check(s == 200 and v.get("server_audio_upload") is False, "服务端零音频上传")
check(v.get("server_upload_endpoint") is None, "无音频上传端点")
check(v.get("audio_may_leave_device") is True, "不虚报「音频不出设备」")
check("云端服务" in v.get("browser_asr_note", ""), "浏览器 ASR 云端实现已告知")

print("=== 4. A4 混合检索端点 ===")
s, hs = req("/api/hybrid/status")
check(s == 200 and "Rank Fusion" in hs.get("algorithm", "") and hs.get("rrf_k") == 60,
      "算法为 RRF 且 k=60")
check("C 级" in hs.get("user_data_policy", ""), "C 级边界已声明")
s, hr = req("/api/hybrid/search", "POST", {"q": "RAG 向量检索与技能图谱", "top_k": 5})
check(s == 200 and hr.get("ok"), "混合检索 200")
check(hr.get("comparison", {}).get("rrf_k") == 60, "RRF k=60")
check(hr.get("scope", {}).get("representative_of_cn") is False, "未外推国内市场")
check(hr.get("query_pii_redacted") == 0, "查询已脱敏")
s, bad = req("/api/hybrid/search", "POST", {"q": "x", "top_k": 1})
check(s in (400, 422), "短查询由请求校验前置拒绝（BFF 统一返回 400）")
s, cmp = req("/api/hybrid/compare", "POST", {"q": "RAG 向量检索", "top_k": 3})
check(s == 200 and isinstance(cmp.get("metrics"), dict), "三路对比返回可核查指标")
check(isinstance(cmp.get("sparse_only"), dict) and isinstance(cmp.get("dense_only"), dict),
      "对比含稀疏 / 稠密单路数据")

print("=== 5. A3 公开仓库端点 ===")
s, gs = req("/api/github/status")
check(s == 200 and gs.get("registered") is True, "来源已登记")
check(gs.get("grade") == "D" and gs.get("anonymously_only") is True, "D 级匿名只读")
s, blocked = req("/api/github/repo?owner=../evil&repo=x")
check(s == 403, "非法路径被拒绝，未发起上游请求")
s, reg = req("/api/sources")
check(s == 200 and any(x.get("id") == "github_public_repos"
                       for x in reg.get("sources", [])), "注册表对外可见")
check(any("Q21" in q for q in reg.get("legal_checklist", [])), "法务 Q21 对外可见")

print("=== 6. 汇总与既有契约 ===")
s, p3 = req("/api/phase3/status")
check(s == 200 and p3.get("ok"), "Phase 3 汇总可用")
for key in ("A1_coach", "A2_voice", "A3_github", "A4_hybrid"):
    check(p3.get(key, {}).get("ready") is True, "%s ready" % key)
check(p3["A2_voice"].get("server_audio_upload") is False, "汇总里语音边界一致")
s, es = req("/api/embedding/status")
check(s == 200 and "bge_autoload" in es, "embedding 状态含 BGE 自动加载开关")
s, ls = req("/api/llm/status")
check(s == 200 and ls.get("provider") == "qwen", "llm 状态仍为 qwen")
check(ls.get("gate", {}).get("endpoint_trustworthy") is True, "端点可信判定生效")
for path in ("/", "/api/sources", "/api/rag/status", "/api/jd/status", "/api/privacy/status"):
    s, _ = req(path)
    check(s == 200, "既有端点 %s 仍可用" % path)
s, page = req("/")
check(isinstance(page, str) and 'id="p3Wrap"' in page, "页面已含 Phase 3 面板")

print("\n=== Phase 3 HTTP 回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
raise SystemExit(1 if FAIL else 0)

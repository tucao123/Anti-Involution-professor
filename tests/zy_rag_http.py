# -*- coding: utf-8 -*-
# 项目内回归用例（由临时验证脚本参数化迁移而来）。
# 路径 / 端口可通过 ZHIYIN_BASE_URL、ZHIYIN_CHROME 覆盖，换机器可复现。
import os
# -*- coding: utf-8 -*-
"""通过真实 HTTP 验证研究策略、RAG 接口与既有链路回归。"""
import json
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("ZHIYIN_BASE_URL", "http://127.0.0.1:8848").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
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
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


JD = ("岗位名称：AI 应用工程师（校招）\n联系人：张伟　电话：13812345678　邮箱：hr@example.com\n"
      "岗位职责：必须熟练掌握 Python，负责大模型应用与 RAG 检索增强链路设计；"
      "要求熟悉 LangChain、向量数据库与混合检索，能独立完成评测集构建与召回率优化。\n"
      "任职要求：熟悉 Docker 与 Kubernetes 者优先；具备跨团队协作、方案汇报与技术文档撰写能力。\n"
      "加分项：了解 Prompt 工程与智能体编排，有 A/B 实验与离线评估经验。")

print("=== 1. health / 策略接口 ===")
s, h = req("/api/health")
check(s == 200 and h["ok"], "health 200")
check(h["research_mode"] == "pending_legal", "研究模式待法务结论", h.get("research_mode"))
check("rag_ingest=allowed" in h["capture_policy"], "截图判定含 RAG 入库", h.get("capture_policy"))
check("research-only" in h["rag_scope"], "RAG 口径标注", h.get("rag_scope"))

s, p = req("/api/research/policy")
check(s == 200 and p["verdict"] == "pending_legal", "研究策略 verdict")
check(p["legal_position"]["is_exemption"] is False, "非商用不是豁免")
check(p["gate"]["ready"] is False and p["auto_capture_executor"] == "disabled", "双闸门 + 执行体禁用")
check(p["rate_control"]["concurrency_per_host"] == 1 and p["rate_control"]["ua_rotation"] is False,
      "限速参数：并发 1 / 无 UA 轮换")
check(len(p["admission_conditions"]) >= 8 and len(p["permanently_forbidden"]) >= 6,
      "准入条件与永久禁止项齐备")

s, cp = req("/api/capture/policy")
v = {x["id"]: x["verdict"] for x in cp["scenarios"]}
check(len(v) == 4, "截图判定 4 种情形", len(v))
check(v["automated_third_party_screenshot"] == "pending_legal", "受控研究采集待法务")
check(v["automated_evasion_screenshot"] == "prohibited", "规避手段永久禁止")
check(v["rag_ingest_from_local_ocr"] == "allowed", "本地 OCR 入库允许")
check(cp["research_use"]["is_exemption"] is False, "策略层否定豁免")
check(cp["ocr"]["rag"]["plaintext_at_rest"] is False, "片段不明文落盘")

s, reg = req("/api/sources")
check(s == 200 and len(reg["sources"]) == 10, "注册表 10 源", len(reg["sources"]))
check(len(reg["legal_checklist"]) == 17, "法务清单 17 问", len(reg["legal_checklist"]))
rc = next(x for x in reg["sources"] if x["id"] == "research_capture")
check(rc["legal_status"] == "pending_legal" and rc["grade"] == "B", "研究源状态/分级")

s, modes = req("/api/insight/modes")
check(modes["modes"][1]["ready"] is False, "国内市场洞察仍待授权")

print("=== 2. 入库准入 ===")
s, j = req("/api/rag/ingest", "POST", {"text": JD, "source_id": "user_screenshot",
                                       "purpose_ack": False})
check(s == 403 and j.get("error") == "CONSENT_REQUIRED", "未授权 403", (s, j))
s, j = req("/api/rag/ingest", "POST", {"text": JD, "source_id": "user_screenshot",
                                       "purpose_ack": True, "purpose": "product"})
check(s in (400, 422), "非 research 用途被 Schema 拒绝", s)
s, j = req("/api/rag/ingest", "POST", {"text": JD, "source_id": "auto_screenshot",
                                       "purpose_ack": True})
check(s == 403 and j.get("error") == "SOURCE_NOT_APPROVED", "封禁源 403", (s, j))
s, j = req("/api/rag/ingest", "POST", {"text": JD, "source_id": "research_capture",
                                       "purpose_ack": True})
check(s == 403, "待法务源 403", (s, j))
s, j = req("/api/rag/ingest", "POST", {"text": JD, "source_id": "user_screenshot",
                                       "purpose_ack": True, "image": "abc"})
check(s in (400, 422), "带 image 字段被 extra=forbid 拒绝", s)
s, j = req("/api/rag/ingest", "POST", {"text": "data:image/png;base64," + "aGVsbG8" * 80,
                                       "source_id": "user_screenshot", "purpose_ack": True})
check(s == 400 and j.get("error") == "IMAGE_PAYLOAD_REJECTED", "base64 图片被拒", (s, j))
s, j = req("/api/rag/ingest", "POST", {"text": JD, "source_id": "jobicy",
                                       "purpose_ack": True, "origin": "pasted_text"})
check(s == 400 and j.get("error") == "ORIGIN_MISMATCH",
      "用户文本不能标成公开 API 样本", (s, j))
s, j = req("/api/rag/ingest", "POST", {"text": JD, "source_id": "auto_screenshot",
                                       "purpose_ack": True, "origin": "public_api"})
check(s == 403 and j.get("error") == "SOURCE_NOT_APPROVED",
      "封禁源优先于口径校验", (s, j))

print("=== 3. 正常入库 / 检索 / 删除 ===")
s, j = req("/api/rag/ingest", "POST", {"text": JD, "source_id": "user_screenshot",
                                       "title": "AI 应用工程师 JD", "purpose_ack": True,
                                       "retain_days": 30, "origin": "local_ocr"})
check(s == 200 and j["ok"], "入库 200", (s, j.get("error")))
d = j.get("doc", {})
check(d.get("chunk_count", 0) >= 1, "切出片段", d.get("chunk_count"))
check(d.get("pii_redacted", 0) >= 3, "PII 脱敏", d.get("pii_redacted"))
check(j.get("image_uploaded") is False and j.get("encrypted_at_rest") is True, "无图片上传 / 加密落盘")
did = d.get("id", "")

raw = (ROOT / ".rag" / "index.json").read_text(encoding="utf-8")
for secret in ("13812345678", "hr@example.com", "张伟", "LangChain"):
    check(secret not in raw, "索引文件不含明文: " + secret[:14])

s, q = req("/api/rag/query", "POST", {"q": "RAG 检索增强 召回率", "top_k": 5})
check(s == 200 and len(q["hits"]) >= 1, "检索有命中", len(q.get("hits", [])))
check(all(x["grade"] == "C" and x["forbidden_claims"] for x in q["hits"]), "命中带分级与禁止结论")
check(q["scope"]["representative_of_cn"] is False, "口径不代表国内市场")
check("LangChain" in q["hits"][0]["text"] or "RAG" in q["hits"][0]["text"],
      "命中含技能原文", q["hits"][0]["text"][:50] if q["hits"] else "")
check("13812345678" not in json.dumps(q, ensure_ascii=False), "响应不含手机号")

s, q2 = req("/api/rag/query", "POST", {"q": "Docker Kubernetes 容器化"})
check(s == 200 and q2["hits"], "第二组技能词也能召回")

s, st = req("/api/rag/status")
check(s == 200 and st["docs"] == 1 and st["chunks"] >= 1, "状态文档/片段数正确", (st["docs"], st["chunks"]))
check(st["image_upload"] is False and st["cloud_model"] is False and st["external_vector_db"] is False,
      "无图片 / 无云端 / 无外部向量库")
check(st["audit"]["chain_ok"] is True, "审计哈希链完整")

s, j = req("/api/rag/delete", "POST", {"id": did})
check(s == 200 and j["ok"], "按文档删除 200")
s, j = req("/api/rag/delete", "POST", {"id": did})
check(s == 404, "重复删除 404", s)
s, j = req("/api/rag/delete", "POST", {"id": "__all__"})
check(s == 200 and j["scope"] == "all", "整库清空 200")
s, st = req("/api/rag/status")
check(st["docs"] == 0 and st["chunks"] == 0, "清空后为 0")

print("=== 4. 既有链路回归 ===")
s, j = req("/api/jd/analyze", "POST", {"jd_text": JD, "purpose_ack": True})
check(s == 200 and j["ok"] and j["grade"] == "C", "JD 解析回归", (s, j.get("error")))
s, j = req("/api/privacy/status")
check(s == 200 and j["audit"]["chain_ok"], "隐私状态与审计链回归")
s, j = req("/api/privacy/audit")
check(s == 200 and j["chain_ok"], "审计接口回归")

print("\n%s: %d passed, %d failed" % ("ALL RAG HTTP CHECKS PASSED" if FAIL == 0 else "RAG HTTP FAILED", PASS, FAIL))
raise SystemExit(1 if FAIL else 0)

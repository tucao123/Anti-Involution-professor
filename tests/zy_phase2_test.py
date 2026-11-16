# -*- coding: utf-8 -*-
"""Phase 2 组件单元回归：配置安全 / embedding / 向量库 / 编排 / 三库 / Celery / 指标。

在 server 进程外直接调用模块，不依赖 HTTP 服务与外部服务；所有外部依赖缺失时
应自动降级而非报错，这正是本用例要验证的核心契约。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import celery_app
import embedding
import llm
import metrics
import orchestration
import secure_config as sc
import stores
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


JD = """岗位职责：
1. 负责 RAG 检索增强系统设计与开发，使用 Python 与向量数据库；
2. 熟悉 LangGraph 编排，具备 PostgreSQL 与 Redis 使用经验。
任职要求：精通 Python，熟练掌握 SQL 与 FastAPI。"""

print("=== 1. 安全配置：绝不泄露凭据 ===")
view = sc.public_view()
raw = json.dumps(view, ensure_ascii=False)
check(view["sensitive_values_never_returned"] is True, "声明敏感值永不返回")
# 真实主机名只在进程内存中取值比对，绝不把实例标识硬编码进测试源码
_real_host = (sc.secret("QDRANT_URL") or "").split("//")[-1].split("/")[0]
check((not _real_host or _real_host not in raw), "配置视图不含真实实例主机名")
check("sk-" not in raw and "postgresqlpassword" not in raw, "配置视图不含任何 key 片段或标签值")
check("host-redacted" in view["keys"]["QDRANT_URL"]["shape"], "URL 形态已隐藏主机")
check(view["cloud_user_data_allowed"] is False, "C 级数据上云默认关闭")
check(view["keys"]["QDRANT_API_KEY"]["configured"] is True or view["file_source"] == "none",
      "Qdrant API key 已从外部凭据文件读到内存")

print("=== 2. qwen 语言模型闸门 ===")
g = sc.qwen_gate()
check(g["can_call_chat"] == (g["endpoint_trustworthy"] and g["llm_enabled"]), "chat 闸门逻辑一致")
check(llm.status()["provider"] == "qwen", "provider 已切换为 qwen")
check(len(llm.status()["task_kinds"]) == 4, "4 个结构化任务契约保留")
try:
    llm.assert_outbound_safe({"jd_text": "用户自带岗位描述"})
    check(False, "C 级原文字段应被外发拦截")
except llm.LLMUnavailable as exc:
    check(str(exc) == "USER_DATA_BLOCKED", "C 级原文字段外发被拒绝")

print("=== 3. Embedding 与数据边界 ===")
e = embedding.embed(["RAG 检索增强", "BGE 语义向量与技能图谱"])
check(len(e["vectors"]) == 2 and e["dim"] == len(e["vectors"][0]), "向量条数与维度一致")
check(e["backend"] in ("bge", "dashscope-text-embedding-v3", "local-hash-ngram"), "后端如实标注")
u = embedding.embed(["用户自带的岗位描述"], scope="user")
check(u["cloud_used"] is False, "用户数据 embedding 不出本机")
check(embedding.status()["user_data_stays_local"] is True, "状态声明用户数据留本地")

print("=== 4. Qdrant 适配器与合规闸门 ===")
r = vector_store.ingest([
    {"id": "phase2-a", "title": "Remote Python Engineer",
     "text": "负责 RAG 检索增强 pipeline，使用 Python 与向量数据库构建岗位匹配服务。"},
    {"id": "phase2-b", "title": "Remote Data Analyst",
     "text": "使用 SQL 与 Python 做数据分析，构建可视化看板与技能图谱。"},
], scope="public", source_id="jobicy")
check(r["ok"] and r["ingested"] == 2 and r["grade"] == "D", "D 级公开样本入库")
s = vector_store.search("向量检索 RAG", 2)
check(s["count"] >= 1 and s["hits"][0]["score"] > 0, "检索排序有效（非全零向量）")
check(all(h["grade"] == "D" and h["forbidden_claims"] for h in s["hits"]), "命中强制带分级与禁止结论")
try:
    vector_store.ingest([{"id": "u", "text": "用户自带简历内容"}], scope="user")
    check(False, "用户数据上云应被拒绝")
except vector_store.CloudUserDataBlocked:
    check(True, "C 级用户数据上云被拒绝")
try:
    vector_store.ingest([{"id": "p", "text": "联系电话 13800138000 负责 Python 开发"}],
                        scope="public", source_id="jobicy")
    check(False, "含 PII 的文本应整批拒绝")
except vector_store.VectorRejected as exc:
    check(str(exc) == "PII_RESIDUAL", "PII 命中即整批拒绝")
try:
    vector_store.ingest([{"id": "x", "text": "未知来源的岗位描述"}],
                        scope="public", source_id="unknown_source")
    check(False, "未登记来源应被闸门拒绝")
except vector_store.VectorRejected as exc:
    check(str(exc) == "SOURCE_NOT_APPROVED", "未批准来源被 fail-closed 拒绝")
check(vector_store.delete(["phase2-a"])["ok"], "按 ID 删除可用")

print("=== 5. LangGraph 编排 ===")
o = orchestration.run(JD, purpose_ack=True)
check(o.get("ok") is True, "编排返回 ok=true")
check(o["raw_text_removed_after_extract"] is True, "原文在 extract 后移出 state")
check(o["cloud_called"] is False, "默认分支不调用云端模型")
check(len(o["trace"]) >= 5 and o["trace"][-1]["node"] == "finish", "状态图 trace 完整")
check(o["grade"] == "C" and o["forbidden_claims"], "结果带 C 级与禁止结论")
o2 = orchestration.run(JD, purpose_ack=False)
check(o2["ok"] is False and o2["error"] == "CONSENT_REQUIRED", "未授权即拒绝")

print("=== 6. 三库适配器 ===")
st = stores.status()
check(all(k in st for k in ("postgresql", "redis", "neo4j")), "三库状态齐全")
check(set(st["degraded"]) <= {"postgresql", "redis", "neo4j"}, "降级列表可观测")
check(stores.cache.set("resume_raw", {"x": 1}) is False, "缓存拒绝 C 级数据键")
check(stores.cache.set("zhiyin:market:测试", {"ok": True}, 30) is True, "公开聚合快照可缓存")
check(stores.relational.record_event("phase2_test", grade="D"), "关系库可写审计事件")
check("PII" in stores.graph.status()["data_policy"], "图库声明不写 PII")

print("=== 7. Celery 任务 ===")
cs = celery_app.status()
check(cs["ok"] and len(cs["registered_tasks"]) >= 5, "任务注册表齐全")
d = celery_app.dispatch("verify_audit_chain", {})
check(d["ok"] and d["state"] == "SUCCESS", "eager 派发成功")
try:
    celery_app.dispatch("not_whitelisted", {})
    check(False, "未知任务应被拒绝")
except celery_app.UnknownTask:
    check(True, "任务白名单生效")

print("=== 8. 指标与 SLO ===")
metrics.observe("GET", "/api/health", 200, 0.02)
text = metrics.render()
check("zhiyin_http_requests_total" in text and "zhiyin_component_up" in text, "Prometheus 文本含关键指标")
check("13800138000" not in text and "sk-" not in text and "api-key" not in text.lower(),
      "指标不含用户文本或凭据")
so = metrics.slo_view()
check(len(so["slo"]["objectives"]) == 4, "4 项 SLO 目标")
check(so["slo"]["objectives"][3]["target"] == 1.0, "合规闸门零错误预算")

print("\n=== Phase 2 单元回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

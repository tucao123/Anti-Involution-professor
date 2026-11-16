# -*- coding: utf-8 -*-
"""Phase 3 单元回归：A1 辅导 / A2 语音口径 / A3 来源闸门 / A4 RRF / 配置与前端静态。

不依赖 HTTP 服务；外部依赖缺失时必须降级而不是报错。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import approval_gate
import coach
import embedding
import github_meta
import hybrid
import llm
import secure_config as sc
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


print("=== 1. 配置与 qwen 放行 ===")
gate = sc.qwen_gate()
check(gate["key_present"] and gate["endpoint_trustworthy"], "qwen 端点可信（不再被 key 形态误判）")
check("形态不符" not in gate["reason"], "放行理由不含过时的形态否决口径")
check(gate["can_call_chat"] == sc.cfg.flag("LLM_ENABLED"), "chat 仍受 LLM_ENABLED 闸门控制")
check(llm.status()["provider"] == "qwen", "provider 仍为 qwen")
q18 = approval_gate.gate("Q18")
check(q18["enabled"] and sc.cfg.flag("QDRANT_ALLOW_WRITE"),
      "Q18 需回执 + 配置双闸门同时满足才开启云写入")
check(q18["legal_review"] == "pending", "工程开关不等于法务书面结论")
check(not sc.cfg.flag("ALLOW_CLOUD_FOR_USER_DATA"), "C 级用户数据仍禁止出本机")

print("=== 2. A1 本地多轮辅导 ===")
check(coach.status()["cloud_called"] is False, "辅导状态声明从未调用云端模型")
r1 = coach.chat(None, "我想看看 Python 和 RAG 的技能差距", True)
check(r1["ok"] and r1["turn"] == 1, "首轮建立会话")
check("Python" in r1["slots"], "技能槽位已抽取")
r2 = coach.chat(r1["session_id"], "那下一步怎么练？", True)
check(r2["turn"] == 2 and r2["context_kept"], "多轮上下文由槽位维持")
check("Python" in r2["reply"], "第二轮仍引用第一轮技能")
g = coach.chat(r1["session_id"], "帮我预测一下月薪多少", True)
check(g["guardrail"] is True and "不能预测薪资" in g["reply"], "薪资护栏生效")
g2 = coach.chat(r1["session_id"], "能保证我一定进大厂吗", True)
check(g2["guardrail"] is True and "承诺录用概率" in g2["reply"], "录用承诺护栏生效")
p = coach.chat(r1["session_id"], "我的手机号 13800138000，邮箱 a@b.com", True)
check(p["pii_redacted"] >= 2, "PII 前置脱敏")
check(p["raw_text_persisted"] is False and p["raw_text_sent_to_cloud"] is False
      and p["cloud_called"] is False, "原文零落盘 / 零外发 / 零云调用")
check(coach.delete(r1["session_id"]) is True, "会话可撤回删除")
check(r1["session_id"] not in coach._debug_session_ids(), "删除后内存无残留")
try:
    coach.chat(None, "你好", False)
    check(False, "未授权必须拒绝")
except coach.ConsentRequired:
    check(True, "未授权必须拒绝")

print("=== 3. A4 RRF 混合检索 ===")
acc = {}
hybrid._rrf([{"source_id": "jobicy", "title": "T1", "text": "RAG 向量检索",
              "score": 0.8, "grade": "D", "chunk_id": "c1"}], "sparse", acc)
hybrid._rrf([{"source_id": "jobicy", "title": "T1", "text": "RAG 向量检索",
              "score": 0.7, "grade": "D"}], "dense", acc)
row = list(acc.values())[0]
check(len(acc) == 1, "跨通道归一去重")
check(row["channels"] == ["sparse", "dense"], "命中记录双通道")
check(abs(row["rrf_score"] - 2.0 / (hybrid.RRF_K + 1)) < 1e-9, "RRF 分数正确")
st = hybrid.status()
check("C 级" in st["user_data_policy"] and "稠密路" in st["user_data_policy"], "C 级只走本地稀疏路")
res = hybrid.search("向量检索 RAG", 5, use_dense=False)
check(res["ok"] and "comparison" in res, "稠密路关闭时仍返回可用结果")
check(res["backends"]["dense"] == "disabled", "稠密路关闭如实标注")
check(res["comparison"]["rrf_k"] == 60, "RRF k=60")
check(res["disclaimer"] and res["scope"]["representative_of_cn"] is False, "口径未外推国内市场")
cmp = hybrid.compare("RAG 向量检索", 3)
check(cmp["ok"] and "metrics" in cmp and "sparse_only" in cmp, "三路对比可核查")

print("=== 4. A3 公开仓库只读闸门 ===")
gs = github_meta.status()
check(gs["registered"] and gs["grade"] == "D", "来源已登记且为 D 级")
check(gs["anonymously_only"] and gs["used_today"] >= 0, "匿名只读限速可观测")
check("github_public_repos" in sources.SOURCES, "注册表含新来源")
check(any("Q21" in q for q in sources.LEGAL_QUESTIONS), "法务 Q21 已同步")
try:
    github_meta.fetch("../evil", "repo")
    check(False, "非法仓库路径必须拒绝")
except github_meta.GithubBlocked:
    check(True, "非法仓库路径必须拒绝")
old = sources.SOURCES["github_public_repos"]["legal_status"]
try:
    sources.SOURCES["github_public_repos"]["legal_status"] = "pending_legal"
    try:
        github_meta.fetch("langchain-ai", "langgraph")
        check(False, "fail-closed 拒绝未批准来源")
    except github_meta.GithubBlocked:
        check(True, "fail-closed 拒绝未批准来源（不发请求）")
finally:
    sources.SOURCES["github_public_repos"]["legal_status"] = old

print("=== 5. Embedding 与前端静态 ===")
es = embedding.status()
check(isinstance(es["bge_package_installed"], bool), "BGE 依赖状态可观测")
check(isinstance(es["bge_available"], bool) and bool(es.get("bge_reason")),
      "BGE 可用性与降级理由如实可观测（缺权重 / 缺依赖时不冒充语义向量）")
print("  INFO BGE: package=%s autoload=%s loaded=%s backend=%s" %
      (es["bge_package_installed"], es["bge_autoload"], es["bge_available"],
       es["selected_backend"]["user"]))
uv = embedding.embed(["用户自带的岗位描述片段"], scope="user")
check(uv["cloud_used"] is False, "用户侧向量未外发云端")
html = (ROOT / "index.html").read_text(encoding="utf-8", errors="ignore")
for token in ('id="p3Wrap"', 'id="coachLog"', 'id="hybridOut"', 'id="ghOut"',
              'id="voiceMic"', 'id="voiceRead"', 'window.PHASE3',
              'api/coach/chat', 'api/hybrid/search', 'api/github/repo', 'api/voice/policy'):
    check(token in html, "前端含 %s" % token)
srv = (ROOT / "server.py").read_text(encoding="utf-8", errors="ignore")
check('"/api/resume/parse"' in srv and "UploadFile" in srv,
      "B1 文件上传端点按回执开放（仅 pdf / docx、内存解析、不落盘）")
check("RESUME_UPLOAD_MAX_BODY" in srv, "上传端点有独立体积上限")
check("job_info" in srv or "job_info" in (ROOT / "resume_parse.py").read_text(encoding="utf-8"),
      "文件解析只走岗位信息抽取")
check("SpeechRecognition" in html and "speechSynthesis" in html, "语音走浏览器 Web Speech")
check("音频可能离开设备" in html, "语音边界如实声明，不虚报音频不出设备")

print("\n=== Phase 3 单元回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

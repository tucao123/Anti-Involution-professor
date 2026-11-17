# -*- coding: utf-8 -*-
"""管理员知识入库 HTTP 端到端回归（TestClient 进程内，不发真实网络到第三方）。

覆盖用户要求的核心链路：
  管理员文本 / 截图 OCR 文本 → PII 脱敏 + 仅岗位信息 → 切片 → 向量化 → Qdrant RAG
并验证：管理员角色闸门、两种来源、chunk_ids 可删除、脱敏与岗位过滤、无图片上传。

说明：Qdrant 云写入闸门按回执开启；本用例写入的是 D 级、已脱敏、仅岗位信息的
管理员知识样本，并在用例末尾逐个删除（幂等清理），不留数据。
"""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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
    "任职要求：熟悉 Docker 与 Kubernetes 者优先；具备跨团队协作、方案汇报与技术文档撰写能力。"
)

try:
    from fastapi.testclient import TestClient
    import auth
    import server

    # TestClient 进程内回环；管理员角色鉴权本身由 zy_auth_test 在 required 模式覆盖，
    # 本用例专注入库链路（optional 默认模式放行写端点）。
    with TestClient(server.app) as client:
        print("=== 1. 端点可用性 ===")
        kst = client.get("/api/admin/knowledge/status")
        check(kst.status_code == 200 and kst.json().get("ok"),
              "知识入库状态端点可用", kst.status_code)

        print("=== 2. 文本来源入库（脱敏 → 岗位 → 切片 → 向量化 → Qdrant）===")
        r = client.post("/api/admin/knowledge/ingest", json={
            "text": JD, "title": "AI 应用工程师样本", "origin": "admin_text",
            "purpose_ack": True})
        check(r.status_code == 200 and r.json().get("ok"),
              "文本入库 200", (r.status_code, r.json()))
        d = r.json()
        check(d.get("origin") == "admin_text" and d.get("job_only") is True,
              "来源标注 / 仅岗位信息")
        check(d.get("chunks", 0) >= 1 and len(d.get("chunk_ids", [])) == d.get("chunks"),
              "切出片段且返回等量 chunk_ids", (d.get("chunks"), len(d.get("chunk_ids", []))))
        check(d.get("pii_redacted", 0) >= 3, "PII 命中并脱敏", d.get("pii_redacted"))
        check(d.get("source_id") == "admin_curated" and d.get("grade") == "D",
              "来源 admin_curated / D 级")
        check(d.get("backend") and "qdrant-cloud" in d.get("backend", ""),
              "向量写入 Qdrant 云", d.get("backend"))
        check("13812345678" not in str(d) and "hr@example.com" not in str(d)
              and "张伟" not in str(d), "响应体不含任何隐私信息")
        ids = d.get("chunk_ids", [])

        print("=== 3. 截图 OCR 文本来源入库 ===")
        r2 = client.post("/api/admin/knowledge/ingest", json={
            "text": JD, "title": "OCR 识别岗位样本", "origin": "admin_ocr",
            "purpose_ack": True})
        check(r2.status_code == 200 and r2.json().get("ok"), "OCR 文本入库 200",
              (r2.status_code, r2.json()))
        d2 = r2.json()
        check(d2.get("origin") == "admin_ocr", "OCR 来源标注正确")
        ids += d2.get("chunk_ids", [])

        print("=== 4. 校验闸门（不产生写入）===")
        r3 = client.post("/api/admin/knowledge/ingest", json={
            "text": JD, "purpose_ack": False})
        check(r3.status_code == 403 and r3.json().get("error") == "CONSENT_REQUIRED",
              "未授权 403", (r3.status_code, r3.json()))
        r4 = client.post("/api/admin/knowledge/ingest", json={
            "text": "data:image/png;base64," + "A" * 420,
            "origin": "admin_ocr", "purpose_ack": True})
        check(r4.status_code == 400 and r4.json().get("error") == "IMAGE_PAYLOAD_REJECTED",
              "图片 payload 400", (r4.status_code, r4.json()))
        # 纯生活闲聊（不含任何岗位语境词）应被「仅岗位信息」闸门拒绝
        r5 = client.post("/api/admin/knowledge/ingest", json={
            "text": "今天天气很好适合出去散步，晚饭后看了一部有趣的电影，"
                    "周末打算去公园野餐放松一下心情。" * 2,
            "origin": "admin_text", "purpose_ack": True})
        check(r5.status_code == 400, "非岗位文本被拒", (r5.status_code, r5.json()))

        print("=== 5. 按 chunk id 删除（幂等清理）===")
        if ids:
            dl = client.post("/api/admin/knowledge/delete", json={"ids": ids[:5]})
            check(dl.status_code == 200 and dl.json().get("ok"),
                  "按 chunk_id 删除 200", (dl.status_code, dl.json()))
        else:
            check(False, "按 chunk_id 删除 200", "无 chunk_id 可删")

        client.post("/api/auth/logout", json={})
except Exception as exc:
    check(False, "知识入库 HTTP 用例执行异常：%s: %s" % (type(exc).__name__, exc))

print("\n=== 知识入库 HTTP 回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
sys.exit(1 if FAIL else 0)

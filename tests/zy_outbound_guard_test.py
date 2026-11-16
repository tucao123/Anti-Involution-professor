#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Q18 出境写入防护回归（纯校验，**不联网、不产生任何境外写入**）。

覆盖：端点目标锁定 / 集合锁定 / 分级与来源锁定 / 字段白名单 / PII /
体积与批量上限 / 凭据不回显 / dry-run 不写入。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import outbound_guard as g
import vector_store as v

PASS = 0
FAIL = 0


def check(cond: bool, label: str, extra: object = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        print("  FAIL", label, extra)


def blocked(fn, label: str, expect: str | None = None) -> None:
    try:
        fn()
        check(False, label, "未被拦截")
    except g.OutboundBlocked as e:
        check(expect is None or str(e) == expect, label, str(e))
    except Exception as e:  # dry-run 会把部分前置闸门转成别的异常类型
        check(expect is None or expect in str(e), label, type(e).__name__ + ":" + str(e))


def main() -> int:
    print("=== Q18 出境写入防护回归（无网络、无写入）===")

    # 1) 端点目标锁定：防止配置被改到任意主机后把数据发过去
    check(g.validate_endpoint("https://abc.cloud.qdrant.io")["ok"] is True,
          "官方 Qdrant Cloud 域放行")
    for url, code in (
        ("http://abc.cloud.qdrant.io", "ENDPOINT_NOT_HTTPS"),
        ("https://evil.example.com", "ENDPOINT_HOST_NOT_ALLOWED"),
        ("https://u:p@abc.cloud.qdrant.io", "ENDPOINT_CONTAINS_CREDENTIALS"),
        ("https://abc.cloud.qdrant.io/x", "ENDPOINT_PATH_NOT_ALLOWED"),
        ("https://abc.cloud.qdrant.io/?k=secret", "ENDPOINT_CONTAINS_QUERY"),
        ("", "ENDPOINT_NOT_CONFIGURED"),
    ):
        blocked(lambda u=url: g.validate_endpoint(u), "端点拒绝 " + code, code)

    # 2) 集合锁定
    check(g.validate_collection("zhiyin_public_d")["ok"] is True, "D 级集合放行")
    for name in ("leak_bucket", "zhiyin_private", "zhiyin_public_d;drop", "../other"):
        blocked(lambda n=name: g.validate_collection(n), "集合拒绝 " + name,
                "COLLECTION_NOT_ALLOWED")

    # 3) 来源 / 分级锁定
    ids = g.allowed_source_ids()
    check("jobicy" in ids and "remoteok" in ids, "D 级已批准来源在白名单", ids)
    check("user_jd" not in ids and "licensed_cn_platform" not in ids
          and "research_capture" not in ids, "C/A/B 级来源不在白名单", ids)

    clean = [{"id": "a", "text": "Data engineer remote Python SQL", "title": "DE"}]
    ok = v.guard_check(clean)
    check(ok["ok"] is True and ok["network_called"] is False
          and ok["data_written"] is False, "干净 D 级样本通过且不联网不写入")
    check(ok["pii_scan"].startswith("passed"), "逐条 PII 扫描已执行")

    # 4) 分级 / 来源 / scope 伪装
    blocked(lambda: g.validate_outbound(
        [{"id": "x", "vector": [0.0], "payload": {
            "text": "clean", "title": "x", "grade": "C", "region": "CN",
            "source_id": "jobicy", "scope": "public"}}],
        collection="zhiyin_public_d", scope="public", source_id="jobicy", grade="C"),
        "伪造 C 级被拒", "GRADE_NOT_ALLOWED")
    blocked(lambda: v.guard_check(clean, source_id="licensed_cn_platform"),
            "A 级境内来源被拒")
    blocked(lambda: v.guard_check(clean, scope="user"), "user scope 被拒")
    blocked(lambda: g.validate_outbound(
        [{"id": "x", "vector": [0.0], "payload": {
            "text": "clean", "title": "x", "grade": "D", "region": "global_remote",
            "source_id": "evil", "scope": "public"}}],
        collection="zhiyin_public_d", scope="public", source_id="evil", grade="D"),
        "未登记来源被拒", "SOURCE_NOT_ALLOWED")

    # 5) payload 字段白名单 / 缺键 / 混入内部字段
    base_payload = {
        "text": "clean text", "title": "x", "grade": "D", "region": "global_remote",
        "source_id": "jobicy", "scope": "public",
    }
    blocked(lambda: g.validate_outbound(
        [{"id": "x", "vector": [0.0],
          "payload": {**base_payload, "user_email": "a@b.com", "raw_jd": "原文"}}],
        collection="zhiyin_public_d", scope="public", source_id="jobicy", grade="D"),
        "多余字段被拒", "PAYLOAD_KEY_NOT_ALLOWED")
    blocked(lambda: g.validate_outbound(
        [{"id": "x", "vector": [0.0],
          "payload": {k: val for k, val in base_payload.items() if k != "grade"}}],
        collection="zhiyin_public_d", scope="public", source_id="jobicy", grade="D"),
        "缺必需字段被拒", "PAYLOAD_KEY_MISSING")
    blocked(lambda: g.validate_outbound(
        [{"id": "x", "vector": [0.0],
          "payload": {**base_payload, "grade": "A"}}],
        collection="zhiyin_public_d", scope="public", source_id="jobicy", grade="D"),
        "payload 与调用参数 grade 不一致被拒", "PAYLOAD_SCOPE_OR_GRADE_MISMATCH")

    # 6) PII / 体积 / 批量
    blocked(lambda: v.guard_check(
        [{"id": "a", "text": "张三 13800138000 test@example.com", "title": "x"}]),
        "命中个人信息整批拒绝")
    blocked(lambda: v.guard_check([{"id": "a", "text": "x" * 3000, "title": "x"}]),
            "超长文本被拒", "TEXT_TOO_LONG")
    blocked(lambda: g.validate_outbound(
        [{"id": str(i), "vector": [0.0], "payload": dict(base_payload)}
         for i in range(g.OUTBOUND_POLICY["max_batch_points"] + 1)],
        collection="zhiyin_public_d", scope="public", source_id="jobicy", grade="D"),
        "超批量被拒", "BATCH_TOO_LARGE")
    blocked(lambda: g.validate_outbound(
        [{"id": "x", "vector": [float("nan")], "payload": dict(base_payload)}],
        collection="zhiyin_public_d", scope="public", source_id="jobicy", grade="D"),
        "非有限向量被拒", "VECTOR_NOT_FINITE")
    blocked(lambda: g.validate_delete(["x"] * 101, "zhiyin_public_d"),
            "超量删除被拒", "DELETE_BATCH_TOO_LARGE")

    # 7) 凭据与内容不回显
    st = g.status()
    check(st["api_key_never_returned"] is True, "状态明确标注不返回密钥")
    check("QDRANT_API_KEY" not in str(st) and "api-key" not in str(st),
          "状态输出不含凭据字面量")
    check(st["network_called_by_this_module"] is False, "防护模块自身不联网")
    check(st["audit_content"] == "digest_only", "审计只写摘要")
    check("raw_text" in st["audit_never_log"] and "vector_values" in st["audit_never_log"],
          "审计不写原文与向量")
    d = ok["digests"][0]
    check("text_sha256_16" in d and "clean" not in str(d), "摘要不含原文")
    check("outbound_guard" in v.status(), "vector 状态暴露防护块")

    print("\n=== 出境防护回归：PASS %d  FAIL %d ===" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())

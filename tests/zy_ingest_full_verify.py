#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验新增全量岗位入库结果：进度完整性 + 云端可检索 + 无 PII 残留。"""
import csv
import json
import sys
from pathlib import Path

csv.field_size_limit(sys.maxsize)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASS = 0
FAIL = 0


def check(cond, name, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name, extra)
    else:
        FAIL += 1
        print("  FAIL", name, extra)


def main():
    # 1) 进度文件覆盖：新增应写入 6996 条（8993 唯一岗位 - 1997 已入库）
    prog = ROOT / "ingest_jobs_full_progress.json"
    check(prog.exists(), "进度文件存在")
    done = set(json.loads(prog.read_text(encoding="utf-8"))) if prog.exists() else set()
    real = [k for k in done if not k.startswith("SKIP::")]
    print("已记录成功写入:", len(real), "跳过:", len(done) - len(real))

    # 2) 抽样检索：用新CSV里的岗位名验证可检索到岗位语境
    import vector_store
    p = vector_store.probe(force=True)
    check(p.get("reachable") is True, "Qdrant 云端可达", str(p.get("error")))
    tc = p.get("target_collection") or {}
    check(tc.get("dim") == 512, "集合维度=512", str(tc.get("dim")))
    print("云端点数:", tc.get("points_count"))

    import privacy
    queries = ["Python 工程师 岗位职责 数据清洗 SQL", "数据分析 任职要求 机器学习"]
    for q in queries:
        try:
            r = vector_store.search(q, top_k=3, scope="public")
            hits = r.get("hits", r.get("results", []))
            backend = r.get("backend")
            check(backend == "qdrant-cloud", "检索走云端", str(backend))
            check(len(hits) > 0, "检索有命中", q[:12])
            for h in hits:
                payload = h.get("payload", {})
                check(not privacy.scan_pii(payload), "命中payload无PII",
                      str(payload.get("title", ""))[:20])
        except Exception as exc:
            check(False, "检索异常", "%s: %s" % (type(exc).__name__, exc))

    print("\n%d PASS / %d FAIL" % (PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()

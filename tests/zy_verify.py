# -*- coding: utf-8 -*-
# 项目内回归用例（由临时验证脚本参数化迁移而来）。
# 路径 / 端口可通过 ZHIYIN_BASE_URL、ZHIYIN_CHROME 覆盖，换机器可复现。
import pathlib, sys, json

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import sources, market

print("=== fail-closed gate ===")
for sid in ("jobicy", "remoteok", "mohrss_public", "licensed_cn_platform", "domestic_crawler", "unknown"):
    try:
        sources.assert_fetchable(sid)
        print(f"  {sid:24s} ALLOWED")
    except sources.SourceBlocked as e:
        print(f"  {sid:24s} BLOCKED ({e})")

print("=== registry ===")
reg = sources.registry_view()
print("  sources:", len(reg["sources"]), "| legal questions:", len(reg["legal_checklist"]),
      "| bd items:", len(reg["bd_checklist"]), "| gate:", reg["gate"])
print("  statuses:", {s["id"]: s["legal_status"] for s in reg["sources"]})

print("=== applicability ===")
for grade, region in (("A", "CN"), ("B", "CN"), ("C", "CN"), ("D", "global_remote")):
    a = sources.applicability(grade, region, 50)
    print(f"  {grade}/{region:15s} cn={a['representative_of_cn']} forbidden={len(a['forbidden_claims'])}")

print("=== robots check (unreachable host, must deny) ===")
r = sources.inspect_robots("http://127.0.0.1:9", "/")
print("  reachable:", r["reachable"], "can_fetch:", r["can_fetch"])

print("=== market._compute with synthetic jobs (no network) ===")
fake = [{"title": "后端工程师", "text": "python sql docker api communication analytics", "cats": ["工程与开发"]}
        for _ in range(20)]
res = market._compute("验证占位", fake, {"name": "国内岗位样本", "url": "", "provider": "local_cn_sample"})
p, m = res["profile"], res["meta"]
print("  grade/region/representative_of_cn:", p["data_grade"], p["region"], p["representative_of_cn"])
print("  forbidden:", m["applicability"]["forbidden_claims"])
print("  source:", m["source"]["name"], "/", m["source"]["provider"])
print("  job basis:", p["jobs"][0].get("basis"), "| summary has market caveat:", "不代表全国就业市场" in p["summary"])
print("  no network: trend is None ->", p["trend"] is None)

print("=== server import ===")
try:
    import server
    print("  /api/sources ->", server.source_registry()["sources"].__len__(), "sources;",
          "health gate:", server.health()["source_gate"])
except Exception as e:
    print("  server import failed:", type(e).__name__, e)

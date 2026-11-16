#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B2 真实执行体实跑（独立脚本，不接入 run_all.py 默认组，避免拖慢回归）。

目标：**只**对公开声明允许爬取的练习沙盒 books.toscrape.com 逐页截图。
招聘网站与任何真实业务站点在代码层被拒绝，不会被访问。

用法：
    python tests/zy_capture_executor_test.py             # 默认 3 页，页间真实等待 ≥15s
    python tests/zy_capture_executor_test.py --pages 2
产物：tests/out/capture_executor_report.json（gitignored，不含位图 / 页面文本）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import capture_executor as ex
import research_capture as rc

# 仅用于演示「在代码层被拒绝」，不会发起任何请求
REJECTION_SAMPLES = (
    "https://www.zhipin.com/job_detail/",
    "https://www.lagou.com/wn/jobs",
    "https://www.liepin.com/zhaopin/",
    "https://jobicy.com/api/v2/remote-jobs",
    "https://remoteok.com/",
    "https://www.indeed.com/jobs",
    "https://example.com/",
    "https://books.toscrape.com/?q=inject",
    "http://books.toscrape.com/",
)


def main() -> int:
    ap = argparse.ArgumentParser(description="B2 真实执行体：练习沙盒限速截图实跑")
    ap.add_argument("--pages", type=int, default=3, help="截图页数，默认 3，单次上限 3")
    args = ap.parse_args()
    pages = max(1, min(args.pages, ex.EXECUTOR_POLICY["max_pages_per_run"]))

    print("=== B2 真实执行体实跑（仅练习沙盒 / 不去招聘网站）===")
    urls = rc.sandbox_page_urls(pages)
    print("  站点声明 :", rc.SANDBOX_SITES["books.toscrape.com"]["evidence_quote"][:90])
    print("  目标清单 :", urls)

    # 1) 白名单硬闸门：这些 URL 一律拒绝，且不会发起任何请求
    rejected = []
    for u in REJECTION_SAMPLES:
        v = rc.is_allowed_test_target(u)
        rejected.append({"url": u, "allowed": v["allowed"], "reason": v["reason"]})
        print("  拒绝样本 :", u, "->", v["reason"])
    if any(x["allowed"] for x in rejected):
        print("  FAIL 白名单异常：有禁止目标被放行")
        return 1
    if not rc.pace_test_plan(pages, urls)["all_targets_allowed"]:
        print("  FAIL 目标清单未通过准入核算")
        return 1

    # 2) 默认闸门必须是关闭的（逐次手动开关语义）
    if ex.readiness(pages, False, urls)["ok"]:
        print("  FAIL 默认闸门未关闭：未确认即放行")
        return 1
    print("  默认闸门 : 关闭（需 B2 回执 + 手动开关 + operator_ack + 限速契约）")

    # 3) 逐次手动开启 → 实跑 → 关闭
    rc.set_admin_toggle(True, True)
    try:
        report = ex.run(pages, operator_ack=True, urls=urls)
    except ex.ExecutorBlocked as e:
        print("  FAIL 执行体闸门拒绝：", e)
        return 1
    finally:
        rc.set_admin_toggle(False, False)

    print("  实际页间隔 :", report["actual_intervals_s"], "s（契约下限",
          report["interval_s"], "s）")
    print("  外部沙盒请求 :", report["external_sandbox_requests"],
          "· 第三方生产请求 :", report["third_party_production_requests"],
          "· 招聘站点接触 :", report["recruitment_sites_touched"])
    print("  位图留存 :", report["bitmap_retention"],
          "· 页面文本解析 :", report["page_text_parsed"],
          "· 生产数据 :", report["production_data_produced"])
    print("  robots :", report["robots"]["note"])
    print("  报告 :", (ROOT / "tests/out/capture_executor_report.json").relative_to(ROOT))
    print("=== %s ===" % ("PASS 真实执行体实跑" if report["ok"] else "FAIL 节奏或截图异常"))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

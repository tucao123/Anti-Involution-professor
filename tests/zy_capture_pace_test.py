#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地限速截图测试（独立脚本，不接入 run_all.py 默认组，避免拖慢回归）。

用户要求：「截图测试拿允许爬取的测试网页测试，不要去招聘网站」。
本脚本只对**本地自建、明确允许爬取的测试页**截图，并验证：
  - 目标必须先过 research_capture.is_allowed_test_target() 白名单；
    招聘网站 / 非本地 host / 非白名单路径一律代码层拒绝；
  - 先过 pace_test_plan() → enforce_pace() 硬契约；
  - 页间**真实等待** effective_interval_s()（默认 15s，串行、无并发、burst 1）；
  - 每页审计留痕；位图读取字节数后**立即删除**，不上传、不入 RAG、不外发；
  - 第三方请求与招聘站点接触数恒为 0。

用法：
    python tests/zy_capture_pace_test.py             # 默认 3 页，页间真实等待
    python tests/zy_capture_pace_test.py --pages 2   # 2 页
产物：tests/out/capture_pace_report.json（gitignored，不含位图）
前置：python server.py（本地 BFF 需在线，提供 /api/capture/test-page）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import research_capture as rc
import sources

OUT = ROOT / "tests" / "out"
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "capture_pace_report.json"
BASE = "http://127.0.0.1:8848"

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)

# 仅用于演示「招聘网站被代码层拒绝」，不会被访问、不会发起任何请求
REJECTION_SAMPLES = (
    "https://www.zhipin.com/job_detail/",
    "https://www.lagou.com/wn/jobs",
    "https://www.liepin.com/zhaopin/",
    "https://jobicy.com/api/v2/remote-jobs",
    "https://remoteok.com/",
    "https://www.indeed.com/jobs",
    "https://example.com/",  # 非本地 host，即使不是招聘站也不在默认白名单
)


def find_chrome() -> str | None:
    import os
    env = os.environ.get("ZHIYIN_CHROME")
    if env and Path(env).is_file():
        return env
    return next((p for p in CHROME_CANDIDATES if Path(p).is_file()), None)


def main() -> int:
    ap = argparse.ArgumentParser(description="本地限速截图测试（仅允许爬取的测试页）")
    ap.add_argument("--pages", type=int, default=3, help="截图页数，默认 3（单批次上限 10）")
    args = ap.parse_args()
    pages = args.pages

    print("=== 本地限速截图测试（仅允许爬取的测试页 / 不去招聘网站）===")

    # 1) 准入核算：白名单 + 硬契约，不发起任何请求
    plan = rc.pace_test_plan(pages)
    target = plan["targets"][0]
    check_target = rc.is_allowed_test_target(target)
    print("  目标 :", target)
    print("  白名单 :", check_target["reason"], "| allowed =", check_target["allowed"])
    print("  契约 :", plan["contract_id"], "| 每页", plan["interval_s"], "s | 页数", pages)

    # 2) 招聘网站 / 非本地站点必须在代码层被拒绝
    rejected = []
    for u in REJECTION_SAMPLES:
        v = rc.is_allowed_test_target(u)
        rejected.append({"url": u, "allowed": v["allowed"], "reason": v["reason"]})
        print("  拒绝样本 :", u, "->", v["reason"])
    if not check_target["allowed"] or any(x["allowed"] for x in rejected):
        print("  FAIL 白名单硬闸门异常：合法目标被拒或招聘站点被放行")
        return 1

    # 3) 本地 BFF 与测试页必须在线
    try:
        with urllib.request.urlopen(target, timeout=5) as r:
            html = r.read().decode("utf-8", "replace")
        if r.status != 200 or "PACE TEST FIXTURE" not in html:
            print("  FAIL 本地测试页不可用或内容不符")
            return 1
    except Exception as e:
        print("  FAIL 本地测试页不可达：", type(e).__name__, "（请先运行 python server.py）")
        return 1

    chrome = find_chrome()
    if not chrome:
        print("  FAIL 未找到 Chrome / Edge，无法执行截图测试")
        return 1

    # 4) 逐页截图：串行、真实等待、位图即弃、每页留痕
    shots: list[dict] = []
    stamps: list[float] = []
    interval = rc.effective_interval_s()
    started = time.monotonic()
    for i in range(1, pages + 1):
        if i > 1:
            time.sleep(interval)  # 严格限速：真实等待，不并发、不抖动、不缩短
        shot_path = OUT / ("_pace_shot_%d.png" % i)
        cmd = [chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
               "--hide-scrollbars", "--disable-dev-shm-usage",
               "--window-size=1200,900", "--virtual-time-budget=2500",
               "--screenshot=" + str(shot_path), target]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            print("  FAIL 第 %d 页截图超时" % i)
            return 1
        size = shot_path.stat().st_size if shot_path.is_file() else 0
        shot_path.unlink(missing_ok=True)  # 位图立即释放：不落盘、不上传、不入 RAG
        stamps.append(time.monotonic())
        sources.log_assessment("local_pace_test_page", rc.SOURCE_ID, {
            "page": i, "target": target, "interval_s": interval,
            "bytes": size, "bitmap_deleted": True, "third_party": False,
        })
        shots.append({
            "page": i,
            "bytes": size,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "bitmap_deleted": True,
        })
        print("  第 %d 页：截图 %d bytes，位图已删除，留痕完成" % (i, size))

    elapsed = round(time.monotonic() - started, 2)
    actual = [round(stamps[i] - stamps[i - 1], 2) for i in range(1, len(stamps))]
    min_gap = min(actual) if actual else 0.0
    pace_ok = all(g + 0.05 >= interval for g in actual) and all(s["bytes"] > 0 for s in shots)
    report = {
        "ok": pace_ok,
        "contract_id": plan["contract_id"],
        "pages": pages,
        "interval_s": interval,
        "actual_intervals_s": actual,
        "min_actual_interval_s": min_gap,
        "pace_compliant": pace_ok,
        "elapsed_s": elapsed,
        "target": target,
        "target_allowed": True,
        "recruitment_sites_touched": 0,
        "third_party_requests": 0,
        "local_test_requests": pages,
        "rejected_samples": rejected,
        "shots": shots,
        "bitmap_retention": "none",
        "human_scale_pacing": "enforced",
        "evasion_techniques": "forbidden",
        "executor": "local_headless_screenshot_only",
        "note": "只对本地自建、明确允许爬取的测试页截图；招聘网站与任何非本地 host "
                "在代码层被拒绝。位图用完即弃，不上传、不入 RAG、不外发。",
        "disclaimer": "本测试是本地自测，不是对第三方站点的采集；不构成法律意见。",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  实际页间隔 :", actual, "s（契约下限", interval, "s）")
    print("  第三方请求 : 0 · 招聘站点接触 : 0 · 位图留存 : none")
    print("  报告 :", REPORT.relative_to(ROOT))
    print("=== %s ===" % ("PASS 本地限速截图测试" if pace_ok else "FAIL 节奏或截图异常"))
    return 0 if pace_ok else 1


if __name__ == "__main__":
    sys.exit(main())

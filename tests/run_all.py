#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""职引者 · 一键回归套件

用法：
    python tests/run_all.py                 全部（缺服务 / 缺浏览器时自动跳过并说明）
    python tests/run_all.py --only unit    只跑某一组：static | unit | http | e2e | offline | visual
    python tests/run_all.py --skip-e2e     跳过所有浏览器用例
    python tests/run_all.py --list         列出分组

环境变量：
    ZHIYIN_BASE_URL   HTTP / E2E 用例的目标服务，默认 http://127.0.0.1:8848
    ZHIYIN_CHROME     显式指定 Chrome / Edge 可执行文件

说明：所有用例都不依赖系统临时目录里的历史脚本；截图与抽取产物写入 tests/out/（已忽略入库）。
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
OUT = TESTS / "out"
OUT.mkdir(parents=True, exist_ok=True)
BASE = os.environ.get("ZHIYIN_BASE_URL", "http://127.0.0.1:8848").rstrip("/")

GROUPS: dict[str, list[tuple[str, str]]] = {
    "static": [("python", "zy_audit.py")],
    "unit": [("python", "zy_verify.py"), ("python", "zy_jd_test.py"),
             ("python", "zy_rag_test.py"), ("python", "zy_phase2_test.py"),
             ("python", "zy_phase3_test.py"), ("python", "zy_approval_test.py"),
             ("python", "zy_outbound_guard_test.py")],
    "http": [("python", "zy_http_test.py"), ("python", "zy_cap_http.py"),
             ("python", "zy_rag_http.py"), ("python", "zy_phase2_http.py"),
             ("python", "zy_phase3_http.py"), ("python", "zy_approval_http.py")],
    "e2e": [("node", "zy_e2e.mjs"), ("node", "zy_cap_e2e.mjs"),
            ("node", "zy_rag_e2e.mjs"), ("node", "zy_persist_e2e.mjs")],
    "offline": [("node", "zy_offline.mjs"), ("node", "zy_cap_offline.mjs"),
                ("node", "zy_cdp.mjs")],
    "visual": [("python", "zy_browser.py"), ("node", "zy_frontend.mjs")],
}
NEEDS_SERVICE = {"http", "e2e"}
BROWSER_GROUPS = {"e2e", "offline", "visual"}

FAIL_RE = re.compile(r"^\s*FAIL\b|AUDIT FAILED|E2E FAILED|OFFLINE FAILED|"
                     r"Traceback \(most recent call last\)|MISSING: \[", re.M)
SKIP_RE = re.compile(r"SKIP BROWSER")


def service_up() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/api/health", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def run_script(exe: str, name: str) -> tuple[str, str]:
    try:
        p = subprocess.run([exe, str(TESTS / name)], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=240)
    except subprocess.TimeoutExpired:
        return "FAIL", "TIMEOUT after 240s"
    out = (p.stdout or "") + (p.stderr or "")
    if SKIP_RE.search(out):
        return "SKIP", "browser not found"
    if p.returncode != 0 or FAIL_RE.search(out):
        tail = "\n".join(out.strip().splitlines()[-12:])
        return "FAIL", (tail or "exit code %d" % p.returncode)
    return "PASS", out.strip().splitlines()[-1] if out.strip() else "ok"


def main() -> int:
    ap = argparse.ArgumentParser(description="职引者一键回归套件")
    ap.add_argument("--only", choices=list(GROUPS), help="只跑指定分组")
    ap.add_argument("--skip-e2e", action="store_true", help="跳过所有浏览器用例")
    ap.add_argument("--list", action="store_true", help="列出分组并退出")
    args = ap.parse_args()

    if args.list:
        for g, items in GROUPS.items():
            print("%-8s %d 项  %s" % (g, len(items), ", ".join(n for _, n in items)))
        return 0

    groups = [args.only] if args.only else list(GROUPS)
    if args.skip_e2e:
        groups = [g for g in groups if g not in BROWSER_GROUPS]

    have_service = service_up()
    print("目标服务 : %s (%s)" % (BASE, "在线" if have_service else "离线"))
    print("项目根   : %s" % ROOT)
    print("产物目录 : %s\n" % OUT)

    counted = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for group in groups:
        print("=== %s ===" % group)
        if group in NEEDS_SERVICE and not have_service:
            print("  SKIP 整组：服务未启动（先运行 python server.py 或 start.bat）")
            counted["SKIP"] += len(GROUPS[group])
            continue
        for exe, name in GROUPS[group]:
            status, detail = run_script(exe, name)
            counted[status] += 1
            print("  %-4s %-22s %s" % (status, name, detail.replace("\n", " ")[:110]))
            if status == "FAIL" and len(detail) > 110:
                print("       " + detail.replace("\n", "\n       "))
        if group == "static":
            for js in ("zy_plain.js", "zy_module.mjs"):
                f = OUT / js
                if not f.is_file():
                    print("  SKIP %s (未生成)" % js)
                    counted["SKIP"] += 1
                    continue
                p = subprocess.run(["node", "--check", str(f)], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace")
                ok = p.returncode == 0
                counted["PASS" if ok else "FAIL"] += 1
                print("  %-4s node --check %-10s %s" % ("PASS" if ok else "FAIL", js,
                                                        "syntax OK" if ok else (p.stderr or "")[:100]))
        print()

    print("=== 汇总 ===  PASS %d  FAIL %d  SKIP %d" %
          (counted["PASS"], counted["FAIL"], counted["SKIP"]))
    if not have_service and any(g in NEEDS_SERVICE for g in groups):
        print("提示：HTTP / E2E 用例因服务未启动而跳过，不算通过。")
    return 1 if counted["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())

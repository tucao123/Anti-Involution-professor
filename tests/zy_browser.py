# -*- coding: utf-8 -*-
# 项目内回归用例（由临时验证脚本参数化迁移而来）。
# 路径 / 端口可通过 ZHIYIN_BASE_URL、ZHIYIN_CHROME 覆盖，换机器可复现。
from pathlib import Path
import os
import subprocess, pathlib, re, sys

ROOT = Path(__file__).resolve().parents[1]
CHROME = os.environ.get("ZHIYIN_CHROME") or next((p for p in [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"
] if Path(p).is_file()), None)
URL = (ROOT / "index.html").as_uri()
TMP = ROOT / "tests" / "out"
TMP.mkdir(parents=True, exist_ok=True)

CHECKS = {
    "home":     ['data-profile="cs"', 'class="metric"', 'ISOMETRIC / 4-MODULE LOOP'],
    "insight":  ['data-profile="fin"', 'id="insightEmpty"', 'id="quadrantChart"'],
    "align":    ['viewBox="0 0 600 390"', 'id="gapGroups"', 'data-jd="ux"'],
    "learn":    ['STAGE 01', 'data-check="3"', '目标岗位 /'],
    "practice": ['STAGE 01 / 自我介绍', 'TONE <b>', 'class="msg ai"'],
    "arch":     ['class="layer"', 'P95 &lt; 2s', 'class="arch-item"'],
    "about":    ['SAFI（Skill Automation Feasibility Index）', '演示数据声明'],
}
SHOTS = ["home", "insight", "align", "practice", "arch"]

def run(args, timeout=45):
    try:
        return subprocess.run([CHROME] + args, capture_output=True, timeout=timeout).stdout.decode("utf-8", "ignore")
    except subprocess.TimeoutExpired:
        return ""

base = ["--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
        "--disable-dev-shm-usage", "--window-size=1440,1000", "--virtual-time-budget=3500"]

print("=== DOM runtime checks ===")
for view, needles in CHECKS.items():
    dom = run(base + ["--dump-dom", URL + "#" + view])
    miss = [n for n in needles if n not in dom]
    active = bool(re.search(r'id="view-%s"[^>]*class="[^"]*view[^"]*active' % view, dom) or
                  re.search(r'class="view[^"]*active" id="view-%s"' % view, dom))
    # active class may be on any view: count
    n_active = len(re.findall(r'class="view[^"]*\bactive\b', dom))
    print("%-9s dom=%5d KB  dynamic=%s  activeViews=%d  %s" %
          (view, len(dom)//1024, "OK" if not miss else "MISSING " + str(miss), n_active,
           "" if active else "(exact active marker not matched)"))

print("=== screenshots ===")
for view in SHOTS:
    out = TMP / ("zy_%s.png" % view)
    run(base + ["--screenshot=" + str(out), URL + "#" + view])
    print(view, "->", out.name, (str(out.stat().st_size//1024) + " KB") if out.exists() else "FAILED")

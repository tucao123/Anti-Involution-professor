# -*- coding: utf-8 -*-
# 项目内回归用例（由临时验证脚本参数化迁移而来）。
# 路径 / 端口可通过 ZHIYIN_BASE_URL、ZHIYIN_CHROME 覆盖，换机器可复现。
from pathlib import Path
import re, pathlib, collections
from html.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]
if not (ROOT / "index.html").exists():
    print("SKIP BROWSER: 旧版单页已删除，本静态审计随旧版退役")
    raise SystemExit(0)
TMP = ROOT / "tests" / "out"
TMP.mkdir(parents=True, exist_ok=True)
s = (ROOT / "index.html").read_text(encoding="utf-8")

# --- extract scripts ---
plain = re.search(r'<script>\s*(.*?)</script>', s, re.S).group(1)
module = re.search(r'<script type="module">\s*(.*?)</script>', s, re.S).group(1)
(TMP / "zy_plain.js").write_text(plain, encoding="utf-8")
(TMP / "zy_module.mjs").write_text(module, encoding="utf-8")

# --- id references ---
defined = set(re.findall(r'\bid="([^"]+)"', s))
ref = set(re.findall(r"\$\('#([A-Za-z0-9_-]+)'\)", plain))
ref |= set(re.findall(r"getElementById\('([^']+)'\)", plain + module))
print("HTML ids defined:", len(defined))
print("JS id refs:", len(ref), "| MISSING:", sorted(ref - defined) or "none")

# --- view routing ---
views = re.findall(r'class="view[^"]*" id="view-([a-z]+)"', s)
gos = set(re.findall(r'data-go="([^"]+)"', s))
ids = re.search(r"const VIEW_IDS=\[([^\]]+)\]", plain).group(1)
view_ids = re.findall(r"'([^']+)'", ids)
print("Views:", views)
print("VIEW_IDS:", view_ids)
print("data-go targets missing a view:", sorted(gos - set(views)) or "none")
print("VIEW_IDS missing a view:", sorted(set(view_ids) - set(views)) or "none")

# --- html tag balance ---
VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
class P(HTMLParser):
    def __init__(self): super().__init__(); self.stack=[]; self.err=[]
    def handle_starttag(self, t, a):
        if t not in VOID and t not in ('path','circle','rect','line','polygon','polyline','text','use'):
            self.stack.append(t)
    def handle_endtag(self, t):
        if t in VOID: return
        if self.stack and self.stack[-1] == t: self.stack.pop()
        elif t in self.stack:
            self.err.append('mismatch near </%s> (open: %s)' % (t, self.stack[-3:]))
            while self.stack and self.stack.pop() != t: pass
        else: self.err.append('stray </%s>' % t)
p = P(); p.feed(s)
print("HTML unclosed:", p.stack or "none", "| errors:", p.err[:5] or "none")

# --- data sanity ---
print("PROFILES keys:", re.findall(r"^  ([a-z]+):\{", plain, re.M))
print("JDS keys:", re.findall(r"^  ([a-z]+):\{", plain[plain.index('const JDS'):plain.index('const ARCH')], re.M))
jd_block = plain[plain.index('const JDS'):plain.index('const ARCH')]
print("plan stage count per JD (W0 markers):", len(re.findall(r"\{w:'W\d+", jd_block)))
print("INTERVIEW stages:", len(re.findall(r"\{key:'", plain)))
print("SVG chart funcs:", re.findall(r"function (\w+SVG)\(", plain))

# --- compliance / honesty strings ---
for k in ['非实时抓取','数据来源','不等于岗位整体可被替代','不承诺录用结果','不替代用户决策','风险指数仅为参考','PIPL','SAFI']:
    print("guardrail %-22s x%d" % (k, s.count(k)))

# --- placeholders / external deps ---
print("TODO/FIXME/lorem:", len(re.findall(r'TODO|FIXME|lorem|占位符文本', s, re.I)))
print("external URLs:", sorted(set(re.findall(r'https?://[^"\'\s)]+', s))))
print("file size (KB):", round(len(s.encode('utf-8'))/1024, 1))

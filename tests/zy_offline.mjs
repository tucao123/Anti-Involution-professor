// 项目内回归用例（由临时验证脚本参数化迁移而来）。
import { existsSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const ROOT = resolve(fileURLToPath(import.meta.url), '..', '..');
const OUT = join(ROOT, 'tests', 'out');
mkdirSync(OUT, { recursive: true });
const CHROME = process.env.ZHIYIN_CHROME || [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
].find(existsSync);
if (!CHROME) { console.log('SKIP BROWSER: Chrome / Edge not found'); process.exit(0); }

const PORT = 9342;
const URL = pathToFileURL(join(ROOT, 'index.html')).href;
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-sandbox','--hide-scrollbars',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'zhiyin-tests', 'zy_offline')}`,
  URL], { stdio: 'ignore' });

let ws, nextId = 1;
const pending = new Map(), errors = [];
function send(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evalJS(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' :: ' + (r.exceptionDetails.exception?.description || ''));
  return r.result.value;
}
let checks = 0;
function check(label, cond, extra = '') {
  checks++;
  console.log((cond ? 'PASS ' : 'FAIL ') + label + (extra ? '  -> ' + extra : ''));
  if (!cond) throw new Error(label);
}
try {
  let targets;
  for (let i = 0; i < 50; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); if (targets.length) break; } catch {}
    await sleep(250);
  }
  const page = targets.find(t => t.type === 'page');
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id).resolve(m.result || {}); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.text + ' ' + (m.params.exceptionDetails.exception?.description || ''));
  };
  await send('Runtime.enable'); await send('Page.enable');
  let ready = 0;
  for (let i = 0; i < 48; i++) {
    ready = await evalJS(`(typeof LLM !== 'undefined' && typeof MODE_FALLBACK !== 'undefined' && document.readyState === 'complete') ? 1 : 0`).catch(() => 0);
    if (ready) break;
    await sleep(250);
  }
  check('离线：页面脚本与 DOM 就绪', ready === 1);
  const cfg = JSON.parse(await evalJS(`JSON.stringify({loaded:!!window.ZHIYIN_DEMO,stamp:STAMP,scope:DEMO_SCOPE})`));
  check('离线：演示集配置从本地文件加载', cfg.loaded === true && cfg.stamp.length > 0 && cfg.scope.length > 0, JSON.stringify(cfg));

  const base = JSON.parse(await evalJS(`JSON.stringify({llm: LLM.live, modeData: modeData,
    modes: document.querySelectorAll('[data-imode]').length,
    cnFallbackReady: MODE_FALLBACK.modes.find(m=>m.id==='cn_market').ready})`));
  check('离线：未连接 BFF', base.llm === false && base.modeData === null);
  check('离线：双模式仍渲染', base.modes === 2);
  check('离线：兜底状态仍为待授权', base.cnFallbackReady === false);

  await evalJS(`switchView('insight',true);setInsightMode('cn_market');0`);
  await sleep(150);
  const cn = JSON.parse(await evalJS(`JSON.stringify({status: document.querySelector('#cnModeBody .pending-status').textContent,
    charts: document.querySelectorAll('#cnModeBody svg, #cnModeBody table').length})`));
  check('离线：待授权卡片无数值', cn.status.includes('待授权') && cn.charts === 0, JSON.stringify(cn));

  await evalJS(`switchView('align',true);document.querySelector('#jdSample').click();document.querySelector('#jdConsent').checked=true;document.querySelector('#jdSubmit').click();0`);
  await sleep(900);
  const local = JSON.parse(await evalJS(`JSON.stringify({shown: document.querySelector('#jdResult').classList.contains('show'),
    localPreview: !!userJdResult.local_preview, grade: userJdResult.grade,
    alignment: userJdResult.alignment, skills: userJdResult.skills.length,
    groups: document.querySelectorAll('#jdResult .jd-group').length,
    badgeText: document.querySelector('#jdResult .source-badge').textContent.slice(0,120),
    noPhone: !document.querySelector('#jdResult').textContent.includes('13812345678'),
    aiNote: document.querySelector('#jdResult').textContent.includes('本地预览'),
    tabs: document.querySelectorAll('[data-jd]').length,
    coverageNum: document.querySelector('#coverageNum').textContent,
    jdKey})`));
  check('离线：走本地预览降级', local.shown && local.localPreview === true && local.grade === 'C');
  check('离线：技能抽取仍可用', local.skills >= 10 && local.groups === 3, `${local.skills} 项`);
  check('离线：不推断覆盖率', local.alignment === null && local.tabs === 3 && local.jdKey === 'agent');
  check('离线：明确标注本地预览', local.aiNote === true && local.badgeText.includes('C 级'));
  check('离线：结果区无 PII 明文', local.noPhone === true);

  await evalJS(`document.querySelector('#jdWithdraw').click();0`);
  await sleep(250);
  const cleared = JSON.parse(await evalJS(`JSON.stringify({shown: document.querySelector('#jdResult').classList.contains('show'),
    session: userJdSession, input: document.querySelector('#jdInput').value.length})`));
  check('离线：撤回同样可清除', cleared.shown === false && cleared.session === null && cleared.input === 0);

  // 回归：洞察演示集 + 学习 + 面试
  await evalJS(`switchView('insight',true);setInsightMode('skill_reference');document.querySelector('[data-profile="fin"]').click();0`);
  await sleep(1100);
  const insight = JSON.parse(await evalJS(`JSON.stringify({shown: document.querySelector('#insightResult').classList.contains('show'),
    rows: document.querySelectorAll('#jobRows tr').length,
    quad: document.querySelectorAll('#quadrantChart svg').length,
    trend: document.querySelectorAll('#trendChart svg').length})`));
  check('离线回归：洞察演示集', insight.shown && insight.rows === 4 && insight.quad === 1 && insight.trend === 1, JSON.stringify(insight));

  await evalJS(`switchView('learn',true);document.querySelector('[data-check="0"]').click();0`);
  await sleep(150);
  const learn = JSON.parse(await evalJS(`JSON.stringify(document.querySelector('#learnPct').textContent)`));
  check('离线回归：学习进度', String(learn).includes('1 / 4'), learn);

  await evalJS(`switchView('practice',true);SAMPLE_ANSWERS.forEach(a=>sendAnswer(a));0`);
  await sleep(1300);
  const iv = JSON.parse(await evalJS(`JSON.stringify({finished: ivFinished, report: document.querySelector('#report').classList.contains('show')})`));
  check('离线回归：面试出报告', iv.finished && iv.report);

  console.log('uncaught exceptions:', errors.length, errors.slice(0, 3));
  check('0 运行时异常', errors.length === 0);
  console.log(`\n${checks} OFFLINE CHECKS PASSED`);
} catch (e) {
  console.log('OFFLINE FAILED:', e.message);
  console.log('uncaught exceptions:', errors.length, errors.slice(0, 3));
} finally {
  try { ws?.close(); } catch {}
  proc.kill();
}

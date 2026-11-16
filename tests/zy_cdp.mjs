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

const PORT = 9333;
const URL = pathToFileURL(join(ROOT, 'index.html')).href;
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-sandbox','--hide-scrollbars',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'zhiyin-tests', 'zy_cdp')}`,
  URL], { stdio: 'ignore' });

let ws, nextId = 1;
const pending = new Map(), errors = [], consoleErrors = [];
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
const J = obj => JSON.stringify(obj);

try {
  // wait for devtools endpoint
  let targets;
  for (let i = 0; i < 40; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); if (targets.length) break; } catch {}
    await sleep(250);
  }
  if (!targets || !targets.length) {
    console.log('SKIP BROWSER: CDP endpoint unavailable (Chrome started but /json not reachable)');
    try { proc.kill(); } catch {}
    process.exit(0);
  }
  const page = targets.find(t => t.type === 'page');
  if (!page) throw new Error('no page target');
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id).resolve(m.result || {}); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.text + ' ' + (m.params.exceptionDetails.exception?.description || ''));
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') consoleErrors.push(m.params.args.map(a => a.value || a.description).join(' '));
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') consoleErrors.push('[log] ' + m.params.entry.text);
  };
  await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable');
  let ready = 0;
  for (let i = 0; i < 48; i++) {
    ready = await evalJS(`(typeof currentView !== 'undefined' && typeof INTERVIEW !== 'undefined' && document.readyState === 'complete') ? 1 : 0`).catch(() => 0);
    if (ready) break;
    await sleep(250);
  }
  if (!ready) throw new Error('page script not ready');

  const q = async (label, expr) => {
    console.log('=== ' + label + ' ===');
    console.log(JSON.parse(await evalJS(`JSON.stringify(${expr})`)));
  };

  await q('initial state', `{view: currentView, chips: document.querySelectorAll('[data-profile]').length,
    jdTabs: document.querySelectorAll('[data-jd]').length,
    ivMsgs: document.querySelectorAll('.msg').length,
    archLayers: document.querySelectorAll('.layer').length}`);

  // 01 insight
  await evalJS(`switchView('insight',true);document.querySelector('[data-profile="fin"]').click();0`);
  await sleep(1100);
  await q('01 insight (金融学)', `{resultShown: document.querySelector('#insightResult').classList.contains('show'),
    title: document.querySelector('#insightTitle').textContent,
    jobRows: document.querySelectorAll('#jobRows tr').length,
    quadrantSVG: document.querySelectorAll('#quadrantChart svg').length,
    trendSVG: document.querySelectorAll('#trendChart svg').length,
    source: document.querySelector('#jobSource').textContent.slice(0,40)}`);

  // 02 align
  await evalJS(`switchView('align',true);document.querySelector('[data-jd="ux"]').click();0`);
  await sleep(250);
  await q('02 align (切换 JD)', `{jdKey, meta: document.querySelector('#jdMeta').textContent,
    coverage: document.querySelector('#coverageNum').textContent,
    radar: document.querySelectorAll('#radarChart svg polygon').length,
    gaps: document.querySelectorAll('.gap-line').length,
    learnTarget: document.querySelector('#learnTarget').textContent}`);

  // 03 learn
  await evalJS(`switchView('learn',true);document.querySelector('[data-check="0"]').click();0`);
  await sleep(150);
  await q('03 learn (勾选阶段)', `{progress: document.querySelector('#learnPct').textContent,
    bar: document.querySelector('#learnBar').style.width,
    stages: document.querySelectorAll('.stage').length,
    done: document.querySelectorAll('.stage.done').length}`);

  // 04 practice - full 4 rounds in pressure mode
  await evalJS(`switchView('practice',true);document.querySelector('[data-mode="pressure"]').click();0`);
  await sleep(150);
  await evalJS(`SAMPLE_ANSWERS.forEach(a=>sendAnswer(a));0`);
  await sleep(1300);
  await q('04 practice (压力面 4 轮)', `{finished: ivFinished, mode: ivMode,
    reportShown: document.querySelector('#report').classList.contains('show'),
    score: document.querySelector('#reportScore').textContent,
    rows: document.querySelectorAll('#reportRows tr').length,
    advice: document.querySelectorAll('#reportAdvice li').length,
    anchors: document.querySelectorAll('#reportAnchors li').length,
    reportRadar: document.querySelectorAll('#reportRadar svg').length,
    msgs: document.querySelectorAll('.msg').length}`);

  // slow, realistic pacing: one answer per interview stage
  await evalJS(`startInterview();0`);
  await sleep(520);
  for (let i = 0; i < 4; i++) {
    await evalJS(`sendAnswer(SAMPLE_ANSWERS[${i}]);0`);
    await sleep(760);
  }
  await sleep(950);
  await q('05 practice (正常节奏逐轮)', `{finished: ivFinished,
    reportShown: document.querySelector('#report').classList.contains('show'),
    aiMsgs: document.querySelectorAll('.msg.ai').length,
    meMsgs: document.querySelectorAll('.msg.me').length,
    questionTags: Array.from(document.querySelectorAll('.msg.ai .tag')).map(t => t.textContent).filter(t => t.startsWith('INTERVIEWER')),
    score: document.querySelector('#reportScore').textContent}`);

  // deep link
  await evalJS(`location.hash='#arch';0`);
  await sleep(200);
  console.log('=== deep link #arch ===');
  console.log(await evalJS(`currentView`));

  console.log('=== runtime errors ===');
  console.log('uncaught exceptions:', errors.length, errors.slice(0,3));
  console.log('console/log errors:', consoleErrors.length, consoleErrors.slice(0,4));
} catch (e) {
  console.log('AUDIT FAILED:', e.message);
} finally {
  try { ws?.close(); } catch {}
  proc.kill();
}

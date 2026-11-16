// 项目内回归用例（由临时验证脚本参数化迁移而来）。
import { existsSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { writeFileSync } from 'node:fs';

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

const PORT = 9334;
const URL = pathToFileURL(join(ROOT, 'index.html')).href + '#insight';
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-sandbox','--hide-scrollbars','--window-size=1440,1000',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'zhiyin-tests', 'zy_frontend')}`,
  URL], { stdio: 'ignore' });

let ws, nextId = 1;
const pending = new Map(), errors = [], consoleErrors = [];
function send(method, params = {}) {
  return new Promise((resolve) => {
    const id = nextId++;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evalJS(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text);
  return r.result.value;
}
try {
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
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m.result || {}); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') consoleErrors.push(m.params.args.map(a => a.value || a.description).join(' '));
  };
  await send('Runtime.enable'); await send('Page.enable');
  await sleep(2000);

  // 模拟 /api/market 返回：全球远程岗 D 级样本，无需真实网络
  await evalJS(`
    LIVE_PROFILES['cs'] = Object.assign({}, PROFILES.cs, {
      live:true, data_grade:'D', region:'global_remote', representative_of_cn:false,
      trend:null, summary: PROFILES.cs.summary
    });
    liveMeta = {
      source:{name:'Jobicy Public Remote Jobs API', url:'https://jobicy.com/api/',
              fetched_at:'2026-11-14 15:00 UTC', samples:50,
              scope:'全球远程岗位公开 API 样本（region=global_remote / grade=D）；不代表中国境内招聘市场'},
      applicability:{grade:'D', region:'global_remote', representative_of_cn:false,
        disclaimer:'本样本为全球远程岗位，不代表中国境内招聘市场。',
        forbidden_claims:['国内就业形势','需求同比与增长','薪资区间','蓝海岗位推荐','被替代风险结论']},
      history:{points:1, ready:false, needed:2},
      note:'单次快照无法计算同比。', cached:false
    };
    switchView('insight', true);
    renderInsight('cs','',false);
    0;
  `);
  await sleep(300);

  const q = await evalJS(`JSON.stringify({
    badge: document.querySelector('#sourceBadge b')?.textContent,
    scopeText: document.querySelectorAll('#sourceBadge .sb-scope')[0]?.textContent.slice(0,60),
    link: document.querySelector('#sourceBadge a')?.getAttribute('href'),
    rel: document.querySelector('#sourceBadge a')?.getAttribute('rel'),
    title: document.querySelector('#insightTitle')?.textContent,
    stamp: document.querySelector('#insightStamp')?.textContent,
    panelTitle: document.querySelector('#jobPanelTitle')?.textContent,
    heatHead: document.querySelector('#jobHeatHead')?.textContent,
    nameHead: document.querySelector('#jobNameHead')?.textContent,
    reasonHead: document.querySelector('#jobReasonHead')?.textContent,
    sourceLine: document.querySelector('#jobSource')?.textContent.slice(-90),
    emptyNote: document.querySelector('#insightEmpty p')?.textContent.includes('全球远程岗位'),
    resultShown: document.querySelector('#insightResult')?.classList.contains('show')
  })`);
  console.log(JSON.stringify(JSON.parse(q), null, 2));

  try {
    await evalJS(`document.querySelector('#sourceBadge').scrollIntoView({block:'center'});0`);
    await sleep(250);
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(OUT, 'zy_scope_live.png'), Buffer.from(shot.data, 'base64'));
    console.log('screenshot: zy_scope_live.png');
  } catch (e) {
    console.log('screenshot failed:', e.message);
  }

  console.log('uncaught exceptions:', errors.length, errors.slice(0, 2));
  console.log('console errors:', consoleErrors.length, consoleErrors.slice(0, 3));
} catch (e) {
  console.log('FAILED:', e.message);
} finally {
  try { ws?.close(); } catch {}
  proc.kill();
}

// 项目内回归用例（由临时验证脚本参数化迁移而来）。
import { existsSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const ROOT = resolve(fileURLToPath(import.meta.url), '..', '..');
if (!existsSync(join(ROOT, 'index.html'))) {
  console.log('SKIP BROWSER: 旧版单页已删除，本用例随旧版退役');
  process.exit(0);
}
const OUT = join(ROOT, 'tests', 'out');
mkdirSync(OUT, { recursive: true });
const CHROME = process.env.ZHIYIN_CHROME || [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
].find(existsSync);
if (!CHROME) { console.log('SKIP BROWSER: Chrome / Edge not found'); process.exit(0); }

const PORT = 9344;
const URL = pathToFileURL(join(ROOT, 'index.html')).href;
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-sandbox','--hide-scrollbars',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'zhiyin-tests', 'zy_cap_offline')}`,
  URL], { stdio: 'ignore' });

let ws, nextId = 1;
const pending = new Map(), errors = [], consoleErrors = [], apiCalls = [];
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
  if (!cond) throw new Error(label + ' ' + extra);
}
try {
  let targets;
  for (let i = 0; i < 50; i++) {
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
    if (m.id && pending.has(m.id)) { pending.get(m.id).resolve(m.result || {}); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.text);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') consoleErrors.push(m.params.args.map(a => a.value || a.description).join(' '));
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') consoleErrors.push('[log] ' + m.params.entry.text);
    if (m.method === 'Network.requestWillBeSent' && /\/api\//.test(m.params.request.url)) apiCalls.push(m.params.request.url);
  };
  await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable'); await send('Network.enable');
  let ready = 0;
  for (let i = 0; i < 48; i++) {
    ready = await evalJS(`(typeof LLM !== 'undefined' && typeof CAPTURE_FALLBACK !== 'undefined' && document.readyState === 'complete') ? 1 : 0`).catch(() => 0);
    if (ready) break;
    await sleep(250);
  }
  check('离线：页面脚本与 DOM 就绪', ready === 1);

  const base = JSON.parse(await evalJS(`JSON.stringify({offline:OFFLINE_FILE,llm:LLM.live,
    capture:!!document.querySelector('#jdCapture'),fallbackScenarios:CAPTURE_FALLBACK.scenarios.length,
    rag:!!document.querySelector('#ragPanel'),ragStatus:document.querySelector('#ragStatus').textContent.slice(0,40),
    researchFallback:RESEARCH_FALLBACK.verdict})`));
  check('file:// 下判定为离线', base.offline === true && base.llm === false);
  check('截图入口与 4 种兜底情形都在', base.capture === true && base.fallbackScenarios === 4);
  check('研究索引面板离线降级', base.rag === true && base.ragStatus.includes('本地服务未启动') &&
    base.researchFallback === 'pending_legal', base.ragStatus);

  await evalJS(`switchView('align',true);document.querySelector('#jdCapPolicyToggle').click();0`);
  await sleep(350);
  const pol = JSON.parse(await evalJS(`JSON.stringify({hidden:document.querySelector('#jdCapPolicyBody').hidden,
    loaded:capPolicyLoaded,scenarios:document.querySelectorAll('#jdCapPolicyBody .jd-cap-sc').length,
    allowed:document.querySelectorAll('#jdCapPolicyBody .jd-cap-sc.allowed').length,
    prohibited:document.querySelectorAll('#jdCapPolicyBody .jd-cap-sc.prohibited').length,
    pending:document.querySelectorAll('#jdCapPolicyBody .jd-cap-sc.pending').length,
    forbids:document.querySelectorAll('#jdCapPolicyBody .jd-cap-list li').length,
    hasEvasion:document.querySelector('#jdCapPolicyBody').textContent.includes('技术可行不等于法律可行')})`));
  check('离线：策略面板用内置兜底口径渲染', pol.hidden === false && pol.loaded === true && pol.scenarios === 4, JSON.stringify(pol));
  check('离线：2 允许 / 1 待法务 / 1 永久禁止',
    pol.allowed === 2 && pol.prohibited === 1 && pol.pending === 1, JSON.stringify(pol));
  check('离线：保留规避技术措施说明', pol.hasEvasion === true && pol.forbids >= 5);

  // 离线 + 预置本地引擎：截图 OCR → 文本框 → 本地预览分析
  await evalJS(`window.Tesseract={recognize:async()=>({data:{text:SAMPLE_JD,confidence:90}})};
    (async()=>{
      const c=document.createElement('canvas');c.width=300;c.height=150;
      const blob=await new Promise(r=>c.toBlob(r,'image/png'));
      setCaptureImage(new File([blob],'offline.png',{type:'image/png'}));
    })();0`);
  await sleep(250);
  await evalJS(`document.querySelector('#jdCapRun').click();0`);
  await sleep(500);
  const ocr = JSON.parse(await evalJS(`JSON.stringify({jd:document.querySelector('#jdInput').value.length,
    note:document.querySelector('#jdCapNote').textContent.slice(0,50),
    scriptTags:document.querySelectorAll('script[src*="tesseract"]').length})`));
  check('离线：预置引擎即可本地识别', ocr.jd > 100 && ocr.scriptTags === 0, JSON.stringify(ocr));
  check('离线：仍提示人工核对', ocr.note.includes('请核对错字'));

  await evalJS(`document.querySelector('#jdConsent').checked=true;document.querySelector('#jdSubmit').click();0`);
  await sleep(600);
  const sub = JSON.parse(await evalJS(`JSON.stringify({shown:document.querySelector('#jdResult').classList.contains('show'),
    localPreview:!!userJdResult.local_preview,grade:userJdResult.grade,
    skills:userJdResult.skills.length,blob:capBlobUrl,
    previewHidden:document.querySelector('#jdCapPreview').hidden})`));
  check('离线：走浏览器内本地预览', sub.shown && sub.localPreview === true && sub.grade === 'C' && sub.skills >= 10, JSON.stringify(sub));
  check('离线：图片同样用完释放', sub.blob === null && sub.previewHidden === true);

  console.log('api/ 请求（应为空）:', apiCalls.length, apiCalls.slice(0, 3));
  console.log('uncaught exceptions:', errors.length, errors.slice(0, 3));
  console.log('console/log errors:', consoleErrors.length, consoleErrors.slice(0, 4));
  check('离线：未发起注定失败的 API 请求', apiCalls.length === 0, String(apiCalls.length));
  check('0 运行时异常', errors.length === 0, String(errors.length));
  console.log(`\n${checks} CAP OFFLINE CHECKS PASSED`);
} catch (e) {
  console.log('CAP OFFLINE FAILED:', e.message);
  console.log('uncaught exceptions:', errors.length, errors.slice(0, 3));
  console.log('console/log errors:', consoleErrors.length, consoleErrors.slice(0, 4));
  process.exitCode = 1;
} finally {
  try { ws?.close(); } catch {}
  proc.kill();
}

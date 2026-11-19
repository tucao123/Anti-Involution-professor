// 本地 UI 偏好持久化：验证「该存的能恢复、不该存的一律不落盘」。
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const ROOT = resolve(fileURLToPath(import.meta.url), '..', '..');
if (!existsSync(join(ROOT, 'index.html'))) {
  console.log('SKIP BROWSER: 旧版单页已删除，本用例随旧版退役');
  process.exit(0);
}
const CHROME = process.env.ZHIYIN_CHROME || [
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe'
].find(existsSync);
if (!CHROME) { console.log('SKIP BROWSER: Chrome / Edge not found'); process.exit(0); }

const PORT = 9345;
const URL = (process.env.ZHIYIN_BASE_URL || 'http://127.0.0.1:8848').replace(/\/$/, '') + '/';
const proc = spawn(CHROME, ['--headless=new', '--disable-gpu', '--no-sandbox', '--hide-scrollbars',
  '--window-size=1440,1000', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'zhiyin-tests', 'zy_persist')}`, URL], { stdio: 'ignore' });

let ws, nextId = 1, checks = 0;
const pending = new Map(), errors = [], consoleErrors = [];
function send(method, params = {}) {
  return new Promise((resolve_, reject) => {
    const id = nextId++;
    pending.set(id, { resolve: resolve_, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
}
async function evalJS(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' :: ' +
    (r.exceptionDetails.exception?.description || ''));
  return r.result.value;
}
function check(label, cond, extra = '') {
  checks++;
  console.log((cond ? 'PASS ' : 'FAIL ') + label + (extra ? '  -> ' + extra : ''));
  if (!cond) throw new Error(label);
}
async function waitReady() {
  for (let i = 0; i < 60; i++) {
    const ok = await evalJS(`(typeof switchView === 'function' && typeof learnDone !== 'undefined' && document.readyState === 'complete') ? 1 : 0`).catch(() => 0);
    if (ok) return;
    await sleep(250);
  }
  throw new Error('page script not ready');
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
  const page = targets.find(t => t.type === 'page' && t.url.startsWith(URL));
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id).resolve(m.result || {}); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.text);
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')
      consoleErrors.push(m.params.args.map(a => a.value || a.description).join(' '));
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') consoleErrors.push(m.params.entry.text);
  };
  await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable');
  await waitReady();

  const cfg = JSON.parse(await evalJS(`JSON.stringify({
    loaded: !!window.ZHIYIN_DEMO, stamp: typeof STAMP !== 'undefined' ? STAMP : '',
    scope: typeof DEMO_SCOPE !== 'undefined' ? DEMO_SCOPE : ''
  })`));
  check('演示集配置外部脚本已加载', cfg.loaded === true && cfg.stamp.length > 0 && cfg.scope.length > 0, JSON.stringify(cfg));

  await evalJS(`localStorage.clear();0`);
  await evalJS(`switchView('align',true);document.querySelector('[data-jd="ux"]').click();0`);
  await evalJS(`document.querySelector('#jdInput').value='ZY_SECRET_12345 13800000000 张伟 任职要求';0`);
  await evalJS(`switchView('learn',true);document.querySelector('[data-check="0"]').click();0`);
  await evalJS(`switchView('practice',true);document.querySelector('[data-mode="pressure"]').click();0`);
  await sleep(400);

  const stored = JSON.parse(await evalJS(`localStorage.getItem('zhiyin.ui.v1')||'{}'`));
  const keys = JSON.parse(await evalJS(`JSON.stringify(Object.keys(localStorage))`));
  const raw = await evalJS(`JSON.stringify(localStorage)`);
  check('保存了学习进度', Array.isArray(stored.learnDone) && stored.learnDone.includes('ux#0'), JSON.stringify(stored.learnDone));
  check('保存了 JD 标签 / 视图 / 面试模式',
    stored.jdKey === 'ux' && stored.view === 'practice' && stored.ivMode === 'pressure', JSON.stringify(stored));
  check('localStorage 只有约定的一个键', keys.length === 1 && keys[0] === 'zhiyin.ui.v1', keys.join(','));
  check('JD 原文 / PII 未落盘',
    !/ZY_SECRET_12345|13800000000|张伟|任职要求|hr@example\.com|岗位名称/.test(raw), raw.slice(0, 120));

  await send('Page.reload', { ignoreCache: true });
  await waitReady();
  await sleep(300);
  const after = JSON.parse(await evalJS(`JSON.stringify({
    pct:document.querySelector('#learnPct').textContent,
    jdOn:document.querySelector('#jdTabs .tab[data-jd="ux"]').classList.contains('on'),
    modeOn:document.querySelector('#modeSeg button[data-mode="pressure"]').classList.contains('on'),
    keys:Object.keys(localStorage)
  })`));
  check('刷新后学习进度恢复', after.pct.includes('1 / 4'), after.pct);
  check('刷新后 JD 标签与面试模式恢复', after.jdOn === true && after.modeOn === true, JSON.stringify(after));
  check('刷新后仍只有约定键', after.keys.length === 1 && after.keys[0] === 'zhiyin.ui.v1', after.keys.join(','));

  await evalJS(`history.replaceState(null,'',location.pathname);0`);
  await send('Page.reload', { ignoreCache: true });
  await waitReady();
  await sleep(300);
  const view = await evalJS(`currentView`);
  check('无 hash 时视图偏好也能恢复', view === 'practice', view);

  await evalJS(`switchView('about',true);document.querySelector('#clearUIPrefs').click();0`);
  await sleep(250);
  const cleared = JSON.parse(await evalJS(`JSON.stringify({
    keys:Object.keys(localStorage),
    pct:document.querySelector('#learnPct').textContent,
    mode:document.querySelector('#modeSeg button.on').dataset.mode
  })`));
  check('清除按钮清空本地偏好', cleared.keys.length === 0, JSON.stringify(cleared.keys));
  check('清除后状态复位', cleared.pct.includes('0 / 4') && cleared.mode === 'standard', JSON.stringify(cleared));

  check('运行时异常 0', errors.length === 0, errors.join(' | '));
  check('控制台错误 0', consoleErrors.length === 0, consoleErrors.join(' | '));
  console.log(`PERSIST E2E CHECKS PASSED: ${checks} checks`);
} catch (e) {
  console.log('PERSIST E2E FAILED:', e.message);
  console.log('uncaught exceptions:', errors.length, errors);
  console.log('console errors:', consoleErrors.length, consoleErrors);
  process.exitCode = 1;
} finally {
  try { ws && ws.close(); } catch {}
  try { proc.kill(); } catch {}
}

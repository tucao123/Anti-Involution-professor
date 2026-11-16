// 项目内回归用例（由临时验证脚本参数化迁移而来）。
import { existsSync, mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import { writeFileSync } from 'node:fs';
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

const PORT = 9344;
const URL = (process.env.ZHIYIN_BASE_URL || 'http://127.0.0.1:8848').replace(/\/$/, '') + '/';
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-sandbox','--hide-scrollbars',
  '--window-size=1440,1050', `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'zhiyin-tests', 'zy_rag_e2e')}`, URL],
  { stdio: 'ignore' });

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
  const r = await send('Runtime.evaluate',
    { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text + ' :: ' +
    (r.exceptionDetails.exception?.description || ''));
  return r.result.value;
}
const q = async (label, expr) => {
  const raw = await evalJS(`JSON.stringify(${expr})`);
  const obj = JSON.parse(raw);
  console.log('=== ' + label + ' ===');
  console.log(obj);
  return obj;
};
let checks = 0;
function check(label, cond, extra = '') {
  checks++;
  console.log((cond ? 'PASS ' : 'FAIL ') + label + (extra ? '  -> ' + extra : ''));
  if (!cond) throw new Error('assertion: ' + label + ' ' + extra);
}

const JD = [
  '岗位名称：AI 应用工程师（校招）',
  '联系人：张伟　电话：13812345678　邮箱：hr@example.com',
  '岗位职责：必须熟练掌握 Python，负责大模型应用与 RAG 检索增强链路设计；要求熟悉 LangChain、向量数据库与混合检索，能独立完成评测集构建与召回率优化。',
  '任职要求：熟悉 Docker 与 Kubernetes 者优先；具备跨团队协作、方案汇报与技术文档撰写能力。',
  '加分项：了解 Prompt 工程与智能体编排，有 A/B 实验与离线评估经验。'
].join('\n');

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
  if (!page) throw new Error('no page target');
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id).resolve(m.result || {}); pending.delete(m.id); return; }
    if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails.text + ' ' +
      (m.params.exceptionDetails.exception?.description || ''));
    if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error')
      consoleErrors.push(m.params.args.map(a => a.value || a.description).join(' '));
    if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error')
      consoleErrors.push('[log] ' + m.params.entry.text);
  };
  await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable');
  await sleep(2400);

  await evalJS(`switchView('align',true);0`);
  await sleep(350);

  const init = await q('研究索引初始态', `({
    view:currentView,
    hasPanel:!!document.querySelector('#ragPanel'),
    status:document.querySelector('#ragStatus').textContent.slice(0,60),
    sources:[...document.querySelectorAll('#ragSource option')].map(o=>o.value),
    selected:document.querySelector('#ragSource').value,
    consent:document.querySelector('#ragConsent').checked,
    hits:document.querySelectorAll('.rag-hit').length,
    scopeHidden:document.querySelector('#ragScope').hidden,
    policyHidden:document.querySelector('#ragPolicyBody').hidden
  })`);
  check('研究索引面板已渲染', init.hasPanel === true && init.view === 'align');
  check('本地服务状态已就绪', init.status.includes('已就绪'), init.status);
  check('来源只列 C 级用户自带数据', init.sources.join(',') === 'user_screenshot,user_jd',
    init.sources.join(','));
  check('默认来源为手动截图 OCR', init.selected === 'user_screenshot', init.selected);
  check('授权默认不勾选', init.consent === false);
  check('初始无命中且口径隐藏', init.hits === 0 && init.scopeHidden === true);

  // 未授权时不得入库
  await evalJS(`document.querySelector('#jdInput').value=${JSON.stringify(JD)};
    document.querySelector('#ragIngest').click();0`);
  await sleep(350);
  const noConsent = await q('未授权点击', `({hint:document.querySelector('#ragHint').textContent,
    toast:(document.querySelector('#toast')||{}).textContent||''})`);
  check('未授权时不入库', !noConsent.hint.includes('已入库'), noConsent.hint);

  // 正式入库
  await evalJS(`document.querySelector('#ragConsent').checked=true;
    document.querySelector('#ragTitle').value='E2E 研究样本';
    document.querySelector('#ragIngest').click();0`);
  await sleep(900);
  const ingested = await q('入库存根', `({hint:document.querySelector('#ragHint').textContent,
    status:document.querySelector('#ragStatus').textContent,
    consent:document.querySelector('#ragConsent').checked})`);
  check('入库成功并回显片段数/脱敏/C 级', ingested.hint.includes('已入库') &&
    ingested.hint.includes('脱敏') && ingested.hint.includes('C 级'), ingested.hint);
  check('状态栏刷新文档数', /1 份文档/.test(ingested.status), ingested.status.slice(0, 80));

  // 检索
  await evalJS(`document.querySelector('#ragQueryInput').value='RAG 检索增强 召回率';
    document.querySelector('#ragSearch').click();0`);
  await sleep(750);
  const hits = await q('检索结果', `({count:document.querySelectorAll('.rag-hit').length,
    scopeHidden:document.querySelector('#ragScope').hidden,
    scope:document.querySelector('#ragScope').textContent,
    first:(document.querySelector('.rag-hit p')||{}).textContent||'',
    head:(document.querySelector('.rag-hit-head')||{}).textContent||''})`);
  check('检索有命中片段', hits.count >= 1, hits.count);
  check('口径条已展开且含禁止结论', hits.scopeHidden === false &&
    hits.scope.includes('禁止结论') && hits.scope.includes('不代表中国境内招聘市场'), hits.scope.slice(0, 90));
  check('命中片段含技能原文', /RAG|LangChain|Python/.test(hits.first), hits.first.slice(0, 60));
  check('命中头部为 C 级（非海外样本）', hits.head.includes('C 级') &&
    !hits.head.includes('jobicy'), hits.head.slice(0, 70));

  // 研究判定面板
  await evalJS(`document.querySelector('#ragPolicyToggle').click();0`);
  await sleep(650);
  const policy = await q('研究合规判定', `({hidden:document.querySelector('#ragPolicyBody').hidden,
    text:document.querySelector('#ragPolicyBody').textContent})`);
  check('判定面板已展开', policy.hidden === false);
  check('明确否定「研究/非商用」豁免', policy.text.includes('不是合规豁免'), policy.text.slice(0, 80));
  check('显示待法务结论状态', policy.text.includes('pending_legal'), '');
  check('说明限速定位与永久禁止项', policy.text.includes('不干扰网络服务') &&
    policy.text.includes('永久禁止'), '');
  check('列出准入条件', policy.text.includes('解锁需同时满足'), '');

  // 截图对齐页研究面板
  await evalJS(`document.querySelector('#ragPanel').scrollIntoView({block:'start'});0`);
  await sleep(300);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(OUT, 'zy_rag_panel.png'),
    Buffer.from(shot.data, 'base64'));

  // 清空
  await evalJS(`document.querySelector('#ragClearAll').click();0`);
  await sleep(750);
  const cleared = await q('清空后状态', `({hint:document.querySelector('#ragHint').textContent,
    status:document.querySelector('#ragStatus').textContent,
    hits:document.querySelectorAll('.rag-hit').length,
    scopeHidden:document.querySelector('#ragScope').hidden})`);
  check('清空提示正确', cleared.hint.includes('已清空'), cleared.hint);
  check('状态栏归零', /0 份文档/.test(cleared.status), cleared.status.slice(0, 80));
  check('命中与口径条已清除', cleared.hits === 0 && cleared.scopeHidden === true);

  // 原有闭环未受影响
  const regression = await q('原有模块回归', `(()=>{
    switchView('insight',true);document.querySelector('[data-profile="fin"]').click();
    return {view:currentView, jdTabs:document.querySelectorAll('[data-jd]').length};
  })()`);
  check('洞察视图仍可切换', regression.view === 'insight', regression.view);

  console.log('=== runtime errors ===', errors.length, errors.slice(0, 3));
  console.log('=== console/log errors ===', consoleErrors.length, consoleErrors.slice(0, 3));
  check('0 运行时异常', errors.length === 0, JSON.stringify(errors.slice(0, 2)));
  check('0 控制台错误', consoleErrors.length === 0, JSON.stringify(consoleErrors.slice(0, 2)));

  console.log(`\nE2E CHECKS PASSED: ${checks} checks, screenshot zy_rag_panel.png`);
} catch (e) {
  console.log('\nE2E FAILED:', e.message);
  console.log('runtime errors:', errors.slice(0, 5));
  console.log('console errors:', consoleErrors.slice(0, 5));
  process.exitCode = 1;
} finally {
  try { ws?.close(); } catch {}
  try { proc.kill(); } catch {}
}

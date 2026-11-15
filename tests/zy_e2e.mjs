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

const PORT = 9341;
const URL = (process.env.ZHIYIN_BASE_URL || 'http://127.0.0.1:8848').replace(/\/$/, '') + '/';
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-sandbox','--hide-scrollbars',
  '--window-size=1440,1000',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'zhiyin-tests', 'zy_e2e')}`,
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
  if (!cond) throw new AssertionError(label);
}
class AssertionError extends Error {}

try {
  let targets;
  for (let i = 0; i < 50; i++) {
    try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); if (targets.length) break; } catch {}
    await sleep(250);
  }
  const page = targets.find(t => t.type === 'page' && t.url.startsWith(URL));
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
  await sleep(2400);

  // ---------- 双模式 ----------
  const init = await q('initial', `{view: currentView, modeBtns: document.querySelectorAll('[data-imode]').length,
    modesFromServer: modeData ? modeData.modes.map(m=>m.id+':'+m.ready) : null,
    llmLive: LLM.live, jdTabs: document.querySelectorAll('[data-jd]').length}`);
  check('两个模式按钮渲染', init.modeBtns === 2);
  check('服务端就绪状态已加载', Array.isArray(init.modesFromServer) && init.modesFromServer.includes('cn_market:false'), JSON.stringify(init.modesFromServer));
  check('BFF 已连接', init.llmLive === true);

  await evalJS(`switchView('insight',true);setInsightMode('cn_market');0`);
  await sleep(250);
  const cn = await q('cn_market 待授权', `{mode: insightMode,
    skillHidden: document.querySelector('#skillModeBody').style.display,
    cnVisible: document.querySelector('#cnModeBody').style.display,
    pending: document.querySelectorAll('#cnModeBody .pending-card').length,
    statusText: document.querySelector('#cnModeBody .pending-status') ? document.querySelector('#cnModeBody .pending-status').textContent : '',
    blockers: document.querySelectorAll('#cnModeBody .pending-list li').length,
    chartsInCn: document.querySelectorAll('#cnModeBody svg, #cnModeBody table').length,
    text: document.querySelector('#cnModeBody').textContent.slice(0,90)}`);
  check('切到国内市场模式', cn.mode === 'cn_market');
  check('技能模式内容已隐藏', cn.skillHidden === 'none');
  check('显示待授权卡片', cn.pending === 1 && cn.statusText.includes('待授权'));
  check('列出阻塞源与准入条件', cn.blockers >= 7, String(cn.blockers));
  check('待授权模式零图表零数值表', cn.chartsInCn === 0);

  // 未授权模式下禁止触发公开样本检索
  await evalJS(`runInsight();0`);
  await sleep(120);
  const blocked = await evalJS(`JSON.stringify({loading: document.querySelector('#insightLoading').classList.contains('show'),
    result: document.querySelector('#insightResult').classList.contains('show')})`);
  check('未授权模式不触发检索', JSON.parse(blocked).loading === false && JSON.parse(blocked).result === false, blocked);

  await evalJS(`setInsightMode('skill_reference');0`);
  await sleep(150);
  const back = await evalJS(`JSON.stringify({mode: insightMode,
    skill: document.querySelector('#skillModeBody').style.display,
    cn: document.querySelector('#cnModeBody').style.display,
    queryVisible: !!document.querySelector('#queryInput').offsetParent})`);
  check('切回技能词频参考', JSON.parse(back).mode === 'skill_reference' && JSON.parse(back).skill !== 'none' && JSON.parse(back).cn === 'none', back);

  // ---------- C 级用户自带 JD ----------
  await evalJS(`switchView('align',true);0`);
  await sleep(150);
  // 未勾选授权必须先拦住
  await evalJS(`document.querySelector('#jdSample').click();document.querySelector('#jdSubmit').click();0`);
  await sleep(200);
  const noConsent = await evalJS(`JSON.stringify({shown: document.querySelector('#jdResult').classList.contains('show'),
    toast: document.querySelector('#toast').textContent})`);
  check('未勾选授权被拒绝', JSON.parse(noConsent).shown === false, noConsent);

  await evalJS(`document.querySelector('#jdConsent').checked=true;document.querySelector('#jdSubmit').click();0`);
  await sleep(1400);
  const jd = await q('用户自带 JD 解析结果', `{shown: document.querySelector('#jdResult').classList.contains('show'),
    badge: document.querySelector('#jdResult .source-badge b') ? document.querySelector('#jdResult .source-badge b').textContent : '',
    skillItems: document.querySelectorAll('#jdResult .jd-skill').length,
    groups: document.querySelectorAll('#jdResult .jd-group').length,
    localPreview: !!userJdResult.local_preview,
    grade: userJdResult.grade,
    hasAlignment: !!userJdResult.alignment,
    coverageFromServer: userJdResult.alignment ? userJdResult.alignment.coverage : null,
    session: userJdSession,
    tabs: document.querySelectorAll('[data-jd]').length,
    jdKey,
    coverageNum: document.querySelector('#coverageNum').textContent,
    radarPolygons: document.querySelectorAll('#radarChart svg polygon').length,
    gapLines: document.querySelectorAll('.gap-line').length,
    learnTarget: document.querySelector('#learnTarget').textContent,
    stages: document.querySelectorAll('.stage').length,
    withdrawEnabled: !document.querySelector('#jdWithdraw').disabled,
    resultHasPhone: document.querySelector('#jdResult').textContent.includes('13812345678'),
    resultHasEmail: document.querySelector('#jdResult').textContent.includes('hr@example.com'),
    resultHasName: document.querySelector('#jdResult').textContent.includes('张伟'),
    aiBtn: !!document.querySelector('[data-action="jd-ai"]')}`);
  check('结果区显示', jd.shown === true);
  check('C 级徽章与口径', jd.badge.includes('C 级') && jd.grade === 'C', jd.badge);
  check('走真实服务端而非本地预览', jd.localPreview === false && !!jd.session, String(jd.session));
  check('技能抽取明细', jd.skillItems >= 10 && jd.groups === 3, `${jd.skillItems} 项 / ${jd.groups} 组`);
  check('覆盖率与雷达已生成', jd.hasAlignment === true && jd.radarPolygons >= 2 && jd.coverageNum.includes('%'), jd.coverageNum);
  check('装入动态 JD 标签', jd.tabs === 4 && jd.jdKey === 'user');
  check('学习模块跟随目标', jd.learnTarget.includes('我的 JD') && jd.stages === 4, jd.learnTarget);
  check('撤回按钮可用', jd.withdrawEnabled === true);
  check('结果区无手机号/邮箱/姓名', !jd.resultHasPhone && !jd.resultHasEmail && !jd.resultHasName);
  check('AI 建议入口存在', jd.aiBtn === true);

  // AI 建议的离线降级（不触发真实上游调用）
  await evalJS(`LLM.live=false;document.querySelector('[data-action="jd-ai"]').click();0`);
  await sleep(200);
  const aiOffline = await evalJS(`JSON.stringify(document.querySelector('#toast').textContent)`);
  check('AI 建议离线降级不抛错', String(aiOffline).includes('离线模式'), aiOffline);

  // 截图：C 级结果区
  try {
    const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    writeFileSync(join(OUT, 'zy_jd_result.png'), Buffer.from(shot.data, 'base64'));
    console.log('screenshot: zy_jd_result.png');
  } catch (e) { console.log('screenshot skipped:', e.message); }

  // 撤回
  await evalJS(`document.querySelector('#jdWithdraw').click();0`);
  await sleep(600);
  const withdrawn = await q('撤回后状态', `{shown: document.querySelector('#jdResult').classList.contains('show'),
    tabs: document.querySelectorAll('[data-jd]').length,
    jdKey,
    hasUserJD: Object.prototype.hasOwnProperty.call(JDS,'user'),
    session: userJdSession,
    input: document.querySelector('#jdInput').value.length,
    consent: document.querySelector('#jdConsent').checked,
    withdrawDisabled: document.querySelector('#jdWithdraw').disabled,
    status: document.querySelector('#jdStatus').textContent.slice(0,30)}`);
  check('结果已清除', withdrawn.shown === false && withdrawn.input === 0 && withdrawn.consent === false);
  check('动态 JD 已移除', withdrawn.tabs === 3 && withdrawn.jdKey === 'agent' && withdrawn.hasUserJD === false);
  check('会话已清空', withdrawn.session === null && withdrawn.withdrawDisabled === true);

  // ---------- 回归：原有四模块链路 ----------
  await evalJS(`LLM.live=true;switchView('practice',true);document.querySelector('[data-mode="pressure"]').click();0`);
  await sleep(150);
  await evalJS(`SAMPLE_ANSWERS.forEach(a=>sendAnswer(a));0`);
  await sleep(1300);
  const iv = await q('模拟面试回归', `{finished: ivFinished, report: document.querySelector('#report').classList.contains('show'),
    rows: document.querySelectorAll('#reportRows tr').length,
    score: document.querySelector('#reportScore').textContent}`);
  check('模拟面试仍可跑通', iv.finished === true && iv.report === true && iv.rows === 4, JSON.stringify(iv));

  console.log('=== runtime errors ===');
  console.log('uncaught exceptions:', errors.length, errors.slice(0, 3));
  console.log('console/log errors:', consoleErrors.length, consoleErrors.slice(0, 4));
  check('0 运行时异常', errors.length === 0, String(errors.length));
  console.log(`\n${checks} CHECKS PASSED`);
} catch (e) {
  console.log('E2E FAILED:', e.message);
  console.log('uncaught exceptions:', errors.length, errors.slice(0, 3));
  console.log('console/log errors:', consoleErrors.length, consoleErrors.slice(0, 4));
} finally {
  try { ws?.close(); } catch {}
  proc.kill();
}

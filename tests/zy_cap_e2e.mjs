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

const PORT = 9343;
const URL = (process.env.ZHIYIN_BASE_URL || 'http://127.0.0.1:8848').replace(/\/$/, '') + '/';
const proc = spawn(CHROME, ['--headless=new','--disable-gpu','--no-sandbox','--hide-scrollbars',
  '--window-size=1440,1000',
  `--remote-debugging-port=${PORT}`,
  `--user-data-dir=${join(tmpdir(), 'zhiyin-tests', 'zy_cap_e2e')}`,
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
  if (!cond) throw new Error('assertion: ' + label + ' ' + extra);
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
  // 轮询业务函数就绪（页面含 module script top-level await，固定等待会 flaky）
  {
    let ready = false;
    for (let i = 0; i < 40; i++) {
      try {
        const r = await send('Runtime.evaluate', { expression: 'typeof switchView === "function"', returnByValue: true });
        if (r.result && r.result.value === true) { ready = true; break; }
      } catch {}
      await sleep(250);
    }
    if (!ready) await sleep(2400);
  }

  await evalJS(`switchView('align',true);0`);
  await sleep(200);

  const init = await q('截图入口初始态', `{view:currentView,
    hasDrop:!!document.querySelector('#jdDrop'),
    previewHidden:document.querySelector('#jdCapPreview').hidden,
    runDisabled:document.querySelector('#jdCapRun').disabled,
    blob:capBlobUrl,
    targets:[...document.querySelectorAll('#jdCapTarget option')].map(o=>o.value),
    policyHidden:document.querySelector('#jdCapPolicyBody').hidden,
    note:document.querySelector('#jdCapNote').textContent.slice(0,40)}`);
  check('截图入口已渲染', init.hasDrop === true);
  check('无图片时识别按钮禁用', init.previewHidden === true && init.runDisabled === true && init.blob === null);
  check('可写入 JD 或简历两个目标', init.targets.join(',') === 'jd,resume', init.targets.join(','));

  // ---- 非法格式 / 超大文件 ----
  const bad = await q('非法输入拦截', `(()=>{
    setCaptureImage({type:'application/pdf',size:1000,name:'x.pdf'});
    const a=document.querySelector('#jdCapNote').textContent;
    const hiddenA=document.querySelector('#jdCapPreview').hidden;
    setCaptureImage({type:'image/png',size:9*1024*1024,name:'big.png'});
    const b=document.querySelector('#jdCapNote').textContent;
    return {badType:a,hiddenA,tooBig:b,hiddenB:document.querySelector('#jdCapPreview').hidden,blob:capBlobUrl};
  })()`);
  check('拒绝非图片格式', bad.badType.includes('PNG') && bad.hiddenA === true, bad.badType);
  check('拒绝超过 8 MB 图片', bad.tooBig.includes('8 MB') && bad.hiddenB === true, bad.tooBig);
  check('拒绝后内存中无残留', bad.blob === null);

  // ---- 真实构造一张 PNG 并通过 input change 进入 ----
  await evalJS(`(async()=>{
    const c=document.createElement('canvas');c.width=760;c.height=420;
    const x=c.getContext('2d');x.fillStyle='#fff';x.fillRect(0,0,760,420);
    x.fillStyle='#111';x.font='22px sans-serif';
    x.fillText('岗位名称：AI 应用工程师',24,48);
    x.fillText('必须熟练掌握 Python / SQL / Docker',24,88);
    const blob=await new Promise(r=>c.toBlob(r,'image/png'));
    const file=new File([blob],'jd-shot.png',{type:'image/png'});
    const dt=new DataTransfer();dt.items.add(file);
    const inp=document.querySelector('#jdFile');
    Object.defineProperty(inp,'files',{value:dt.files,configurable:true});
    inp.dispatchEvent(new Event('change',{bubbles:true}));
  })()`);
  await sleep(350);
  const loaded = await q('图片载入', `{hidden:document.querySelector('#jdCapPreview').hidden,
    name:document.querySelector('#jdCapName').textContent,
    size:document.querySelector('#jdCapSize').textContent,
    imgSrc:document.querySelector('#jdCapImg').getAttribute('src'),
    blobIsObjectURL:typeof capBlobUrl==='string'&&capBlobUrl.startsWith('blob:'),
    runDisabled:document.querySelector('#jdCapRun').disabled,
    note:document.querySelector('#jdCapNote').textContent.slice(0,30)}`);
  check('预览显示且文件名正确', loaded.hidden === false && loaded.name === 'jd-shot.png', loaded.name);
  check('图片以 blob: 存在于内存', loaded.blobIsObjectURL === true && String(loaded.imgSrc).startsWith('blob:'));
  check('识别按钮已启用', loaded.runDisabled === false);
  check('声明「仅在内存」', loaded.size.includes('仅在内存'), loaded.size);

  // ---- 策略说明（来自真实 BFF） ----
  await evalJS(`document.querySelector('#jdCapPolicyToggle').click();0`);
  await sleep(450);
  const pol = await q('合规判定面板', `{hidden:document.querySelector('#jdCapPolicyBody').hidden,
    expanded:document.querySelector('#jdCapPolicyToggle').getAttribute('aria-expanded'),
    scenarios:document.querySelectorAll('#jdCapPolicyBody .jd-cap-sc').length,
    allowed:document.querySelectorAll('#jdCapPolicyBody .jd-cap-sc.allowed').length,
    prohibited:document.querySelectorAll('#jdCapPolicyBody .jd-cap-sc.prohibited').length,
    pending:document.querySelectorAll('#jdCapPolicyBody .jd-cap-sc.pending').length,
    forbids:document.querySelectorAll('#jdCapPolicyBody .jd-cap-list li').length,
    loaded:capPolicyLoaded,
    text:document.querySelector('#jdCapPolicyBody').textContent.slice(0,70)}`);
  check('策略面板展开并取自服务端', pol.hidden === false && pol.loaded === true && pol.expanded === 'true');
  check('四种情形：2 允许 / 1 待法务 / 1 永久禁止',
    pol.scenarios === 4 && pol.allowed === 2 && pol.pending === 1 && pol.prohibited === 1,
    JSON.stringify(pol));
  check('列出禁止能力清单', pol.forbids >= 5, String(pol.forbids));

  // 截图：上传区域
  try {
    const rect = JSON.parse(await evalJS(`JSON.stringify((()=>{const r=document.querySelector('#jdCapture').getBoundingClientRect();
      return {x:Math.max(0,r.x),y:Math.max(0,r.y+window.scrollY),width:r.width,height:r.height};})())`));
    await evalJS(`document.querySelector('#jdCapture').scrollIntoView({block:'start'});0`);
    await sleep(250);
    const r2 = JSON.parse(await evalJS(`JSON.stringify((()=>{const r=document.querySelector('#jdCapture').getBoundingClientRect();
      return {x:r.x,y:r.y,width:r.width,height:r.height};})())`));
    const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false,
      clip: { x: Math.max(0,r2.x), y: Math.max(0,r2.y), width: Math.min(r2.width,1400), height: Math.min(r2.height,760), scale: 1 } });
    writeFileSync(join(OUT, 'zy_capture.png'), Buffer.from(shot.data, 'base64'));
    console.log('screenshot: zy_capture.png  rect=' + JSON.stringify(r2));
  } catch (e) { console.log('screenshot skipped:', e.message); }

  // ---- 注入本地 OCR 引擎 stub（等价于自托管 Tesseract.js，避免下载 15MB 语言包） ----
  await evalJS(`window.Tesseract={recognize:async(image,lang)=>{
    window.__ocrSeen={image:String(image),lang};
    return {data:{text:SAMPLE_JD,confidence:91}};
  }};0`);
  await evalJS(`document.querySelector('#jdCapRun').click();0`);
  await sleep(650);
  const ocr = await q('本地 OCR 结果', `{seen:window.__ocrSeen,
    jdLen:document.querySelector('#jdInput').value.length,
    jdHead:document.querySelector('#jdInput').value.slice(0,24),
    resumeLen:document.querySelector('#jdResume').value.length,
    note:document.querySelector('#jdCapNote').textContent,
    consent:document.querySelector('#jdConsent').checked,
    resultShown:document.querySelector('#jdResult').classList.contains('show'),
    scriptTags:document.querySelectorAll('script[src*="tesseract"]').length}`);
  check('OCR 收到本地 blob 而非上传地址', ocr.seen && ocr.seen.image.startsWith('blob:') && ocr.seen.lang === 'chi_sim+eng', JSON.stringify(ocr.seen));
  check('复用已有引擎，不注入 CDN 脚本', ocr.scriptTags === 0, String(ocr.scriptTags));
  check('识别结果写入 JD 文本框', ocr.jdLen > 100 && ocr.jdHead.includes('岗位名称'), ocr.jdHead);
  check('未擅自写简历框 / 未自动勾选授权 / 未自动提交',
    ocr.resumeLen === 0 && ocr.consent === false && ocr.resultShown === false);
  check('提示用户核对后再提交', ocr.note.includes('请核对错字') && ocr.note.includes('不会自动提交'), ocr.note.slice(0,80));

  // ---- 写入目标切到简历，验证追加逻辑 ----
  await evalJS(`document.querySelector('#jdCapTarget').value='resume';
    window.Tesseract={recognize:async()=>({data:{text:'熟悉 Python，做过检索链路评测，召回率从 0.61 提升到 0.78。',confidence:88}})};
    document.querySelector('#jdCapRun').click();0`);
  await sleep(500);
  const tgt = await q('切换到简历目标', `{resume:document.querySelector('#jdResume').value.slice(0,30),
    jdLen:document.querySelector('#jdInput').value.length,
    note:document.querySelector('#jdCapNote').textContent.slice(0,60)}`);
  check('识别结果写入简历要点', tgt.resume.includes('熟悉 Python'), tgt.resume);
  check('JD 文本框未被覆盖', tgt.jdLen > 100);

  // ---- 提交：图片用完即释放 ----
  await evalJS(`document.querySelector('#jdConsent').checked=true;document.querySelector('#jdSubmit').click();0`);
  await sleep(1500);
  const submit = await q('提交后状态', `{shown:document.querySelector('#jdResult').classList.contains('show'),
    blob:capBlobUrl,
    previewHidden:document.querySelector('#jdCapPreview').hidden,
    imgSrc:document.querySelector('#jdCapImg').getAttribute('src'),
    runDisabled:document.querySelector('#jdCapRun').disabled,
    skills:document.querySelectorAll('#jdResult .jd-skill').length,
    grade:userJdResult?userJdResult.grade:null,
    localPreview:userJdResult?!!userJdResult.local_preview:null,
    session:userJdSession,
    tabs:document.querySelectorAll('[data-jd]').length,
    hasPhone:document.querySelector('#jdResult').textContent.includes('13812345678')}`);
  check('走真实服务端解析', submit.shown === true && submit.localPreview === false && !!submit.session, String(submit.session));
  check('技能抽取有效', submit.skills >= 8 && submit.grade === 'C', String(submit.skills));
  check('提交后图片从内存释放', submit.blob === null && submit.previewHidden === true && !submit.imgSrc && submit.runDisabled === true);
  check('结果区无 PII', submit.hasPhone === false);
  check('装入动态 JD 标签', submit.tabs === 4, String(submit.tabs));

  // ---- 撤回后清空 ----
  await evalJS(`document.querySelector('#jdWithdraw').click();0`);
  await sleep(600);
  const wd = await q('撤回后', `{shown:document.querySelector('#jdResult').classList.contains('show'),
    blob:capBlobUrl, jd:document.querySelector('#jdInput').value.length,
    resume:document.querySelector('#jdResume').value.length,
    tabs:document.querySelectorAll('[data-jd]').length}`);
  check('撤回清空文本与图片引用', wd.shown === false && wd.jd === 0 && wd.resume === 0 && wd.blob === null && wd.tabs === 3);

  // ---- OCR 不可用时的降级 ----
  await evalJS(`delete window.Tesseract;0`);
  const stub = await evalJS(`(async()=>{
    const c=document.createElement('canvas');c.width=200;c.height=100;
    const blob=await new Promise(r=>c.toBlob(r,'image/png'));
    setCaptureImage(new File([blob],'x.png',{type:'image/png'}));
    return 'ok';
  })()`);
  await sleep(150);
  // 让 CDN 加载直接失败，不等待 20s 超时
  await evalJS(`loadOcr=()=>Promise.reject(new Error('OCR_CDN_FAILED'));document.querySelector('#jdCapRun').click();0`);
  await sleep(400);
  const fb = await q('OCR 不可用降级', `{note:document.querySelector('#jdCapNote').textContent,
    warn:document.querySelector('#jdCapNote').classList.contains('warn'),
    jdLen:document.querySelector('#jdInput').value.length,
    runDisabled:document.querySelector('#jdCapRun').disabled,
    blob:capBlobUrl}`);
  check('OCR 失败给出明确提示', fb.note.includes('本地 OCR 不可用') && fb.note.includes('请直接粘贴文字') && fb.warn === true, fb.note.slice(0,90));
  check('降级不破坏文本输入、图片未上传', fb.jdLen === 0 && fb.blob !== null);

  console.log('=== runtime errors ===');
  console.log('uncaught exceptions:', errors.length, errors.slice(0, 3));
  console.log('console/log errors:', consoleErrors.length, consoleErrors.slice(0, 4));
  check('0 运行时异常', errors.length === 0, String(errors.length));
  console.log(`\n${checks} CAP E2E CHECKS PASSED`);
} catch (e) {
  console.log('CAP E2E FAILED:', e.message);
  console.log('uncaught exceptions:', errors.length, errors.slice(0, 3));
  console.log('console/log errors:', consoleErrors.length, consoleErrors.slice(0, 4));
  process.exitCode = 1;
} finally {
  try { ws?.close(); } catch {}
  proc.kill();
}

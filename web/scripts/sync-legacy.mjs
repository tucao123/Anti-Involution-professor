// 把项目根的页面同步到 Next public/ 下零改动托管：
//   - workbench.html -> public/workbench.html （JTBD v2.0 主工作台，/workbench.html）
//   - index.html + demo-config.js -> public/legacy/ （既有单页，仅供 E2E 契约 / 历史回放）
// 用法：npm run sync-legacy   （改动任一页面后执行）
import { copyFileSync, mkdirSync, statSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', '..');
const pub = resolve(here, '..', 'public');
const dest = resolve(pub, 'legacy');

mkdirSync(dest, { recursive: true });

// 主工作台 + 登录页（单文件，放 public 根，免费获得 /workbench.html 与 /login.html 路由）
for (const name of ['workbench.html', 'login.html']) {
  const src = resolve(root, name);
  copyFileSync(src, resolve(pub, name));
  console.log(`[sync-legacy] ${name} -> public/${name} (${(statSync(src).size / 1024).toFixed(1)} KB)`);
}

for (const name of ['index.html', 'demo-config.js']) {
  const src = resolve(root, name);
  const dst = resolve(dest, name);
  copyFileSync(src, dst);
  const kb = (statSync(dst).size / 1024).toFixed(1);
  console.log(`[sync-legacy] ${name} -> public/legacy/${name} (${kb} KB)`);
}

console.log('[sync-legacy] done. 工作台走 /api/* rewrite；legacy 走 /legacy/api/* rewrite。');

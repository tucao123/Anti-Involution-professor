// 把项目根的唯一前端页面同步到 Next public/ 下托管：
//   - workbench.html -> public/workbench.html （JTBD v2.0 唯一工作台，/workbench.html）
//   - login.html     -> public/login.html
// 旧版单页已删除，不再同步 legacy 目录。
// 用法：npm run sync-legacy   （改动页面后执行）
import { copyFileSync, statSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', '..');
const pub = resolve(here, '..', 'public');

// 工作台 + 登录页（单文件，放 public 根，免费获得 /workbench.html 与 /login.html 路由）
for (const name of ['workbench.html', 'login.html']) {
  const src = resolve(root, name);
  copyFileSync(src, resolve(pub, name));
  console.log(`[sync-pages] ${name} -> public/${name} (${(statSync(src).size / 1024).toFixed(1)} KB)`);
}

console.log('[sync-pages] done. 工作台走 /api/* rewrite。');

// 把项目根的既有单页同步到 public/legacy/，保持零改动托管。
// 用法：npm run sync-legacy   （改动 index.html / demo-config.js 后执行）
import { copyFileSync, mkdirSync, statSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', '..');
const dest = resolve(here, '..', 'public', 'legacy');

mkdirSync(dest, { recursive: true });

for (const name of ['index.html', 'demo-config.js']) {
  const src = resolve(root, name);
  const dst = resolve(dest, name);
  copyFileSync(src, dst);
  const kb = (statSync(dst).size / 1024).toFixed(1);
  console.log(`[sync-legacy] ${name} -> public/legacy/${name} (${kb} KB)`);
}

console.log('[sync-legacy] done. 注意：legacy 页面通过 /legacy/api/* rewrite 访问 BFF。');

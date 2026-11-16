// Windows 兼容：Next.js 15 在 build/dev 时会用 fs.createWriteStream(..., {flags:'a'})
// 追加写 <distDir>/trace；部分 Windows 环境（文件锁 / 实时防护）会抛
// EPERM: operation not permitted, open ...\.next\trace，导致构建直接崩溃。
// 该 shim 只把 trace 文件的写入替换成空 Writable，其他文件写入完全不变。
// 仅在 Windows 手动构建时通过 `node -r` 预加载；Docker（Linux）不需要。
'use strict';

const fs = require('fs');
const { Writable } = require('stream');

const isTraceFile = (p) =>
  typeof p === 'string' && /[\\/]\.next[\\/]trace$/.test(p);

const original = fs.createWriteStream;

fs.createWriteStream = function patched(p, opts) {
  if (isTraceFile(p) && opts && opts.flags === 'a') {
    const sink = new Writable({
      write(_chunk, _enc, cb) { cb(); },
    });
    // to-json.js 的 RotatingWriteStream 只调用 write()/end()，不会用到 fd，
    // 但显式触发 ready 更贴近原生流行为，避免任何等待 open 的逻辑卡住。
    process.nextTick(() => sink.emit('ready'));
    return sink;
  }
  return original.apply(this, arguments);
};

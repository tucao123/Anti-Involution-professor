# 职引者 ZHIYIN · Next.js 控制台

这一层是**状态控制台 + 反向代理宿主**，不是对既有单页应用的重写。

## 为什么要这样设计

`index.html`（约 200 KB）包含 7 个视图、hash 深链接、SVG 图表、规则引擎面试、
浏览器本地 OCR 与离线降级，并且已通过一整套 E2E / 离线回归。把它整体重写成
React 会引入大面积回归风险。因此：

- 原页面原样复制到 `public/legacy/index.html`，**零改动**；
- `next.config.mjs` 把 `/legacy/api/*` 也代理到 FastAPI，抵消相对路径
  `fetch('api/health')` 在 `/legacy/index.html` 下会解析成 `/legacy/api/health` 的差异；
- `public/legacy/demo-config.js` 同目录托管，原页面脚本引用照常生效；
- Next.js 首页只做组件状态观测，不复制业务逻辑。

## 启动

```bash
# 1. 先启动 FastAPI BFF（项目根）
python server.py                 # http://127.0.0.1:8848

# 2. 同步既有单页（改动 index.html / demo-config.js 后都要执行）
npm --prefix web run sync-legacy

# 3. 安装并启动控制台
cd web
npm install
npm run dev                     # http://127.0.0.1:3000
```

生产模式：`npm run build && npm run start`。

> **构建期约定**：Next.js 会把 `rewrites` 的目标地址固化进构建产物，所以 BFF 地址必须在
> **构建前**通过 `ZHIYIN_BFF_URL` 指定，例如
> `ZHIYIN_BFF_URL=http://127.0.0.1:8852 npm run build`。
> 运行时再改这个变量只影响服务端组件（控制台状态页），不改变 rewrite 目标。
> `Dockerfile` 已用 build-arg 在 builder 阶段注入 `http://api:8848`，并在 runner 阶段同样设置。

**Windows 构建提示**：Next.js 15 在本机追加写 `.next/trace` 时可能报
`EPERM: operation not permitted`。此时改用兼容脚本（已内置，只屏蔽 trace 文件写入，
不影响其他文件与产物）：

```bash
npm run build:win      # 代替 npm run build
npm run dev:win        # 代替 npm run dev
```

Docker / Linux 直接使用标准 `npm run build`，无需该 shim。

## 路由

| 路径 | 说明 |
| --- | --- |
| `/` | 组件控制台（服务端拉取各 `/api/*` 状态，BFF 不可达时优雅降级） |
| `/health` | 逐端点连通性探测 |
| `/api/health` | Next 侧部署探针，返回 web 与 BFF 可达性 |
| `/legacy/index.html` | 完整应用（7 视图，零改动） |
| `/legacy/api/*` → BFF | 供 legacy 页相对路径请求使用 |
| `/api/*` → BFF | 供控制台与客户端使用 |
| `/metrics` → BFF | Prometheus 文本指标 |

## 数据边界

控制台只显示**是否配置 / 来源类别 / 形态**，`/api/config` 不返回任何密钥值，
URL 形态也会隐藏云实例主机名。C 级用户自带数据默认不出本机；
D 级公开样本不代表中国境内招聘市场。详见项目根 `合规与授权沟通稿.md`。

## 依赖

`next` / `react` / `react-dom` + TypeScript。未安装 `node_modules` 时，
FastAPI BFF 与原单页应用仍然完全可用——Next.js 是可选的一层。

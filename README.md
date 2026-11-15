# 职引者 ZHIYIN

AI 职业生涯韧性与反内卷管家：洞察 → 对齐 → 学习 → 实战 四模块闭环。

> **当前状态**：可运行的产品原型 + 本地 FastAPI BFF + 合规闸门。四模块交互闭环、数据源分级、用户自带 JD（C 级）、手动截图本地 OCR、本地研究检索索引均已落地；国内市场洞察（A 级）、简历 PDF 解析、语音面试、Qdrant/LangGraph 生产管线**尚未实现**。请勿把架构页的目标设计误读为运行清单。

## 快速启动

Windows 双击 `start.bat`，或在项目根目录执行：

```bash
python -m pip install -r requirements.txt
python server.py
```

打开 <http://127.0.0.1:8848/>。指定端口：`python server.py --port 8849`。
服务只绑定 `127.0.0.1`，静态资源只放行 `index.html`；TLS、反向代理与公网部署属后续阶段。

`start.bat` 会检查 Python 与四个运行依赖（缺失时自动安装），并提示 `.env` 配置状态。**它不会复制、生成或打印任何密钥。**

### 语言模型凭据（可选）

未配置时前端自动降级为规则引擎与内置叙述，四模块照常可用。启用 DeepSeek 叙述层：

1. 复制 `.env.example` 为 `.env`（已被 `.gitignore` 排除，绝不入库）；
2. 填入 `deepseek:<你的 Key>`；
3. 重启服务。也可用 `ZHIYIN_ENV_FILE` 指向任意位置的 key 文件，或用 `python server.py --env-file <路径>` 显式指定。

`GET /api/health` 的 `credential_source` 只返回凭据来源类别（不返回路径或值）。历史版本曾回退到本机桌面的一个固定路径，现为**可选兼容项**：设 `ZHIYIN_DISABLE_LEGACY_KEY=1` 可彻底关闭，推荐改用 `.env`。

### 研究索引的跨重启保留（可选）

默认刻意采用最小留存：密钥只在进程内存，服务重启研究索引自动清空。若确需保留，显式设置 `ZHIYIN_PII_KEY`（32 字节 base64）：

```powershell
$env:ZHIYIN_PII_KEY = [Convert]::ToBase64String((1..32 | ForEach-Object { Get-Random -Max 256 }) -as [byte[]])
python server.py
```

密钥必须自行妥善保管并保持不变；丢失后已加密片段不可恢复。片段默认 30 天 TTL，可单文档删除或整库清空。

## 数据与合规口径

| 模式 / 来源 | 分级 | 状态 | 能得出的结论 |
|---|---|---|---|
| 技能词频参考（Jobicy / RemoteOK） | D · 全球远程岗 | 可用 | 技能出现率、学习方向；**非国内市场、非同比** |
| 国内市场洞察 | A · 境内授权数据 | **待授权** | 零数值、零图表、零排序，绝不拿海外样本顶替 |
| 用户自带 JD / 手动截图 OCR | C · 用户自带 | 可用 | 仅这一份 JD 的能力差距，不产生市场级统计 |
| 受控研究采集 | B | 待法务书面结论 | 执行体禁用，`gate_ready=false` |
| 隐身浏览器 / 登录态复用 / 规避技术措施的自动截图 | B | **永久封禁** | 不实施 |

「研究用途、非商用」**不是合规豁免**；截图也不是。完整法规基线、法务 17 问、BD 8 项要点见 [`合规与授权沟通稿.md`](合规与授权沟通稿.md)，设计与红线见 [`技术文档.md`](技术文档.md) §2。

**不改代码就无法解锁的事**：`licensed_cn_platform`（商务合同）、`mohrss_public`（公共数据书面许可）、`research_capture`（法务结论）。工程侧不会自行把 `pending_*` 改为 `approved`。

## 测试

```bash
python tests/run_all.py              # 全部：单元 + HTTP + 静态 + 浏览器 E2E
python tests/run_all.py --only unit  # unit | static | http | e2e | offline
python tests/run_all.py --skip-e2e   # 无 Chrome 环境时跳过浏览器用例
```

HTTP / E2E 用例需要服务已在运行，默认 `http://127.0.0.1:8848`，可用 `ZHIYIN_BASE_URL` 覆盖。浏览器用例自动探测 Chrome / Edge，找不到会跳过并说明。测试会产生临时文档，结束时自动删除。

## 目录

```
index.html            单文件前端（7 视图 + 离线降级），零构建
server.py             FastAPI BFF / 20 个 API 端点 / JSON Schema 输出校验
sources.py            10 源注册表 · fail-closed 闸门 · 分级 · 截图判定
market.py             公开远程岗 D 级适配（确定性统计 + 快照）
user_jd.py            C 级用户自带 JD：技能抽取 / PII 脱敏 / 原文零落盘
rag_store.py          本地研究检索：稀疏 TF-IDF / 加密片段 / TTL / 删除
research_capture.py   受控采集护栏：限速 / robots / 双闸门 / 执行体禁用
privacy.py            AES-256-GCM / 脱敏 / 一键删除 / 审计哈希链
tests/                自包含回归套件（单元 / HTTP / 静态 / 浏览器）
技术文档.md           v1.3 架构设计与红线
合规与授权沟通稿.md    v1.2 法务确认函与 BD 授权需求邮件
参考素材-*.html       外部设计稿，非本项目文档
```

## 已知边界

- 前端只在 `localStorage` 保存非敏感 UI 状态（学习进度、视图、JD 标签、面试模式）；**不保存** JD 原文、简历、OCR 文本、面试回答或报告。
- 演示数据集与时间戳为内置样例（`window.ZHIYIN_DEMO` 可覆盖），不是实时行情。
- 前端按需从 CDN 加载 Tesseract.js / Google Fonts / Three.js；离线时字体退化、OCR 降级为「直接粘贴文字」。可预置 `window.Tesseract` 或自托管。
- 所有外部结论未回之前，本仓库中所有 `blocked` / `pending_*` 状态都是刻意保留的，不是待修的 bug。

本项目文档中的合规判断均为工程侧过程控制，**不构成法律意见**，最终边界以法务书面结论为准。

# 职引者 ZHIYIN

AI 职业生涯韧性与反内卷管家：洞察 → 对齐 → 学习 → 实战 四模块闭环。

> **当前状态（Phase 2）**：可运行的产品原型 + 本地 FastAPI BFF + 合规闸门 + 全套可选组件的适配器层。
> Qdrant / BGE / LangGraph / PostgreSQL / Redis / Neo4j / Celery / Next.js / Prometheus SLO 均已接入，
> **但全部是「真服务可用则用、不可用则本地降级」**，降级状态会在 `/api/*` 与 `/metrics` 如实标注。
> 国内市场洞察（A 级）、简历 PDF 解析、语音面试**尚未实现**；请勿把架构页的目标设计误读为运行清单。

## 快速启动（零外部依赖，全降级可跑）

Windows 双击 `start.bat`，或在项目根目录执行：

```bash
python -m pip install -r requirements.txt
python server.py
```

打开 <http://127.0.0.1:8848/>。指定端口：`python server.py --port 8849`；
容器内用 `python server.py --host 0.0.0.0 --port 8848`（宿主机仍只映射 127.0.0.1）。
核心依赖只有 4 个：`fastapi / uvicorn / pydantic / cryptography`。

`start.bat` 会检查 Python 与运行依赖（缺失时自动安装），并提示配置状态。**它不会复制、生成或打印任何密钥。**

### 语言模型：qwen（可选，默认关闭）

未配置 / 未启用时前端自动降级为规则引擎与内置叙述，7 个视图照常可用。启用步骤：

1. 复制 `.env.example` 为 `.env`（已被 `.gitignore` 排除，绝不入库）；
2. 填入 `QWEN_API_KEY=<DashScope 官方 key>`；若你用的是第三方兼容端点，
   同时填 `QWEN_BASE_URL=<你的端点>`；
3. 设 `LLM_ENABLED=true`，重启服务。

也可用 `ZHIYIN_ENV_FILE` 指向任意位置的 key 文件，或 `python server.py --env-file <路径>` 显式指定。
配置层支持标准 `KEY=value` 与「标签行 + 下一行才是值」的笔记式文件；只加载本项目需要的键，
其他 provider 的行一律跳过。`GET /api/config` 只返回「是否配置 / 来源类别 / 形态」，
**不返回任何密钥值，URL 形态会隐藏云实例主机名**。

> 当前 `key.env` 的 qwen 段是一串 `sk-ws-…` 形态的 key，与 DashScope 官方 `sk-<32hex>` 不符，
> 因此 `qwen_gate()` 判定端点不可信、默认不外发。填入官方 key 或补全 `QWEN_BASE_URL` 即可解锁。
> DeepSeek 的 key 仍在兼容读取范围内，但本版默认 provider 已是 qwen。

### 组件凭据

`.env.example` 含全部配置模板。Qdrant Cloud 的 endpoint 与 API key、PostgreSQL 密码已从外部
`key.env` 读到内存；云端写入由 `QDRANT_ALLOW_WRITE` **与 `.privacy/approvals/Q18.json` 回执双闸门**共同控制（本机已按项目所有者 2026-11-16 决策开启，法务书面结论仍待回）。只读连通性探测始终允许。

### 研究索引的跨重启保留（可选）

默认刻意采用最小留存：密钥只在进程内存，服务重启研究索引自动清空。若确需保留，显式设置 `ZHIYIN_PII_KEY`（32 字节 base64）。密钥必须自行妥善保管并保持不变；丢失后已加密片段不可恢复。片段默认 30 天 TTL，可单文档删除或整库清空。

## 数据与合规口径（不因架构升级而放宽）

| 模式 / 来源 | 分级 | 状态 | 能得出的结论 |
|---|---|---|---|
| 技能词频参考（Jobicy / RemoteOK） | D · 全球远程岗 | 可用 | 技能出现率、学习方向；**非国内市场、非同比** |
| 国内市场洞察 | A · 境内授权数据 | **待授权** | 零数值、零图表、零排序，绝不拿海外样本顶替 |
| 用户自带 JD / 手动截图 OCR | C · 用户自带 | 可用 | 仅这一份 JD 的能力差距，不产生市场级统计 |
| 受控研究采集 | B | 待法务书面结论 | 执行体仅限练习沙盒、生产采集未解锁，`gate_ready=false` |
| 隐身浏览器 / 登录态复用 / 规避技术措施的自动截图 | B | **永久封禁** | 不实施 |

**新增云组件的边界**：

- C 级用户自带数据（JD / 简历 / 本地 OCR）**默认不出本机**：`ALLOW_CLOUD_FOR_USER_DATA=false`。
- Qdrant Cloud 集合 `zhiyin_public_d` **只收 D 级公开岗位样本**；逐条过来源闸门与 PII 扫描，
  命中即整批拒绝；云端写入开关默认关闭，关闭或不可达时降级本地进程内索引（不落盘、重启清空）。
- 云端 qwen 只收结构化技能字段；JD 原文、证据短句、简历原文一律前置拦截。
- BGE 本地语义模型未安装时降级为确定性字符 n-gram 投影，状态如实标注「非语义向量」，**不冒充 BGE**。

### 项目性质与使用限制（2026-11-16 全项目条款）

> **「本项目仅用于研究与学习，不得用于任何商业行为。」**
> `USAGE-RESTRICTION-2026-11-16` · 代码单一事实源 `usage_policy.py` · 只读端点 `GET /api/project/usage`
> · **统一适用于 B1 / B2 / Q18 / Q19 / Q21 / B3 及全部数据链路**。

条款禁止商业化部署、付费 / 经营性服务、转售与对外分发样本 / 向量 / 研究结果，
禁止把产出用于招聘筛选 / 广告 / 信用评估等商业决策。它是**收紧**用途的约束，
**不是合规豁免**：不改变任何回执 `decision` 或双闸门状态，不替代法务书面结论 / 站点许可 /
商务合同 / 告知同意，也不解锁自动采集执行体与规避检测。其余待决事项「同理」指的是
**同一条限制逐项施加**（5 份回执、状态端点、文档与界面统一标注），不是逐项放行。

「研究用途、非商用」**不是合规豁免**；截图也不是。完整法规基线、法务 18 问、BD 8 项要点见
[`合规与授权沟通稿.md`](合规与授权沟通稿.md) 附录 F，回执台账见
[`待书面结论事项与回执清单.md`](待书面结论事项与回执清单.md) §0.1，
设计与红线见 [`技术文档.md`](技术文档.md) §2。

**不改代码就无法解锁的事**：`licensed_cn_platform`（商务合同）、`mohrss_public`（公共数据书面许可）、
`research_capture`（法务结论）。工程侧不会自行把 `pending_*` 改为 `approved`。

## Phase 2 组件

| 组件 | 真服务 | 降级实现 | 状态端点 |
|---|---|---|---|
| 语言模型 | qwen（DashScope 兼容端点） | 离线规则引擎 | `/api/llm/status` |
| Embedding | BGE 本地 / DashScope | 确定性 hash 投影 | `/api/embedding/status` |
| 向量库 | Qdrant Cloud REST | 本地内存索引 | `/api/vector/status` |
| 编排 | LangGraph StateGraph | 同语义标准库状态图 | `/api/graph/status` |
| 关系库 | PostgreSQL | SQLite（gitignored） | `/api/stores/status` |
| 缓存 / Broker | Redis | 进程内 TTL 字典 | `/api/stores/status` |
| 图库 | Neo4j | 本地邻接表 | `/api/stores/status` |
| 异步任务 | Celery + Redis | eager 同步执行 | `/api/tasks/status` |
| 前端 | Next.js 控制台 `web/` | 直接访问 `index.html` | `/api/health` |
| 可观测 | Prometheus / Grafana | 纯标准库 `/metrics` | `/metrics`、`/api/slo` |

本机已装并生效：`psycopg[binary]`（PostgreSQL 18.7 已连接）、`langgraph`（真 `StateGraph` 接管编排）、`celery`（无 broker 时 eager）；`redis` 客户端与 `neo4j` 驱动已就绪，但服务端未运行仍降级。**「不需要 API / 密钥」的完整清单见 `ROADMAP.md`**。所有可选依赖都在 `requirements-optional.txt`，缺失不影响启动。

新增端点：`/api/config`、`/api/llm/status`、`/api/embedding/status`、`/api/vector/{status,probe,ingest,search,delete,clear-local}`、
`/api/graph/{status,run}`、`/api/stores/status`、`/api/tasks/{status,dispatch}`、`/api/slo`、`/metrics`。
原有 `/api/*` 契约全部保留。

### Next.js 控制台（可选）

```bash
python server.py                                   # 先起 BFF
node web/scripts/sync-legacy.mjs                   # 同步既有单页到 public/legacy/
cd web && npm install && npm run dev               # http://127.0.0.1:3000
```

Windows 上若 `npm run build` 报 `.next/trace` 的 EPERM，改用 `npm run build:win`（内置 shim）。
控制台通过 rewrite 把 `/api/*`、`/legacy/api/*`、`/metrics` 代理到 BFF，
**既有 200 KB 单页零改动**。详见 [`web/README.md`](web/README.md)。

### 容器部署

```bash
cp .env.example .env
node web/scripts/sync-legacy.mjs
docker compose up -d                               # api + web + 三库
docker compose --profile celery up -d              # 追加独立 worker
docker compose --profile monitoring up -d          # 追加 Prometheus / Grafana
```

⚠ 容器只能读取显式写进 `.env` / 环境变量的凭据，**不要挂载个人桌面上的 key.env**。
SLO 目标、错误预算、告警规则与降级策略见 [`SLO.md`](SLO.md)。

## Phase 3 新增能力（2026-11-15，零密钥）

| 项 | 实现 | 状态 |
|---|---|---|
| qwen 叙述层 | 官方 DashScope 兼容端点实测 200，`LLM_ENABLED=true`，`/api/health` 为 `mode=live`；PII / 原文字段仍前置拦截 | 已启用 |
| BGE 本地语义向量 | `torch` 2.15 + `BAAI/bge-small-zh-v1.5` 512 维权重；主环境 `transformers 4.57.6` / `sentence-transformers 6.3.0` 互斥且混入两套文件触发循环导入，改用 `pip --target=.bge_env` 干净组合（transformers 4.49.0 / ST 3.4.1 / tokenizers 0.21.0 / hf-hub 0.29.3 / Pillow 11.1.0），`embedding.py` 前置到 `sys.path` | ✅ 已恢复：`bge_available=true`、按官方 `modules.json` 加载 CLS pooling、`user` 侧 512 维本地语义向量（`cloud_used=false`）；`public` 侧为与既有 1024 维 Qdrant 集合保持一致仍优先 DashScope。`.bge_env/` 为 gitignored 本机产物，缺失时自动退回原降级链路 |
| A1 多轮辅导 | `coach.py` + 面试视图面板：会话仅内存、原文零落盘、零云调用、可撤回、护栏固定 | 已做 |
| A2 浏览器语音 | Web Speech 语音输入 / 朗读，逐次授权、不支持时降级文字；**服务端零音频上传**，并如实告知浏览器 ASR 可能由厂商云端完成 | 已做 |
| A3 公开仓库元数据 | `github_meta.py`：匿名只读、限频、回链、不落盘、不构成能力评分 | 已做（法务 Q21 待书面结论） |
| A4 混合检索 | `hybrid.py`：稀疏 + 稠密双路 → RRF(k=60) → 分级过滤 → 去重，带三路对比 | 已做 |
| 阶段状态汇总 | `GET /api/phase3/status` | 已做 |

新增端点：`/api/coach/*`、`/api/voice/policy`、`/api/github/*`、`/api/hybrid/*`、`/api/phase3/status`。
配置写在项目根 `.env`（已被 `.gitignore` 忽略）；`.env.example` 只保留空占位模板，**不要把真实密钥填进模板**。

## 2026-11-16 决策落地（回执闸门 / B1 / 管理员采集设置）

| 项 | 实现 | 状态 |
|---|---|---|
| 代码级回执闸门 | `approval_gate.py`：编号 / 出具方 / 有效期 / 范围 + 注册表或配置双闸门；只读、无自我授权入口；`/api/approvals/status` | 已做 |
| B1 文件解析 | `resume_parse.py` + `/api/resume/*`：仅 pdf / docx、文件与原文只在内存、解析完即释放、只输出大致岗位信息、有撤回与删除入口；云端解析需逐次单独同意且只发去标识化岗位字段 | 已做（`legal_review=pending`） |
| 岗位信息抽取 | `job_info.py`：不解析个人信息、只保留岗位方向 / 技能方向 / 级别词；截图 / `local_ocr` 入库时个人信息行整行剔除、不入库 | 已做 |
| 个人画像 | 由用户主动提供 JD / 简历的本地 `/api/jd/analyze` 链路提供（覆盖率 / 技能差距），与截图入库链路分离 | 保留 |
| Q18 云写入 | `QDRANT_ALLOW_WRITE=true` + `Q18.json` 回执 + 双闸门；实测真实写入成功 | 已做（法务结论待回） |
| B2 采集设置 | 独立管理员视图 + `/api/capture/admin`：默认不自动爬取、逐次手动开关；开关只记录意图，四重闸门未齐备时不发起任何第三方请求 | 已做 |
| Q19 / Q21 材料 | `数据出境与公开元数据条款清单.md`：GitHub ToS §H / AUP §5·§6 等需核对条款、单独告知同意模板、DPA 条款清单 | 已做（结论待回） |
| **全项目使用限制条款** | `usage_policy.py` + `GET /api/project/usage`：条款 `USAGE-RESTRICTION-2026-11-16`，5 份回执顶层 / 状态端点 / 文档 / 界面统一标注；`changes_gate_state=false` | 已做（B1 / Q18 仍启用，Q19 / Q21 / B3 仍 CLOSED） |
| **B2 自担风险放行 + 严格限速** | `.privacy/approvals/B2.json`（`issuer=项目所有者`、`risk_bearing=project_owner`、`legal_review=pending`，不冒充法务结论 / 站点许可）；`PACE_CONTRACT` + `enforce_pace()` 代码级硬闸门；`GET /api/capture/pace` | 已做（决策闸门放行；生产 `effective=false`、`requests_issued=0`） |
| **Q18 出境写入防护** | `outbound_guard.py`：端点官方域 / 集合 / 分级 D / 已批准来源 / payload 字段白名单 / 逐字段 PII / 体积批量上限；凭据与原文不回显，审计只写 digest；`GET /api/vector/guard`、dry-run `POST /api/vector/guard/check` | 已做（不产生新的境外写入） |
| **B2 真实执行体 + 练习沙盒** | `capture_executor.py`：七道闸门 + 严格限速 + 真实 UA + 每页留痕 + 位图即弃；白名单仅本地测试页与 `books.toscrape.com`（公开声明 Sandbox / scraping purposes）；`GET /api/capture/executor/status`、`POST /api/capture/executor/run` | 已做并实跑（仅沙盒；`production_capture_unlocked=false`） |
| **Q18 维持允许（不回滚）** | `Q18.json` 新增 `owner_confirmation`：所有者明确维持 `QDRANT_ALLOW_WRITE=true`、不回滚已发生写入、不关闭双闸门；`approval_gate` 把确认摘要暴露到 `/api/approvals/status` | 已做（`legal_review=pending`，出站防护一格未放宽，数据出境评估仍待回） |
| **BGE 恢复** | `.bge_env/` 独立依赖目录 + `embedding.py` 的 `sys.path` 前置；不修补被污染的主环境 | 已做并实测（`selected_backend.user=bge`、512 维零云调用；`public` 维持 1024 维 DashScope 以匹配既有集合；`ensure_collection()` 增维度冲突硬拒绝） |
| **B2 生产执行体** | `production_capture.py`：生产能力 + 九道闸门，目标只由第三方签发的站点书面许可函回执 `.privacy/site_licenses/*.json` 驱动，并须法务书面结论 `B2-legal.json` + 注册表 `approved`；`GET /api/capture/production/status` 只读 | 已接入能力，**默认不解锁**（两份材料未回、`pending_legal`、`production_capture_unlocked=false`、生产请求 0）；工程侧无许可写入 API、不假造许可、不指向无许可招聘站 |

新增端点：`/api/project/usage`、`/api/approvals/status`、`/api/resume/{status,consent,parse,withdraw,delete}`、`/api/capture/admin`、`/api/capture/admin/toggle`、`/api/capture/pace`、`/api/vector/guard`、`/api/vector/guard/check`、`/api/capture/executor/{status,run}`、`/api/capture/production/status`。

**B2 口径**：所有者「自担风险直接放行，但截图不能太快、必须严格控制速度模拟人类」，本轮追加
「截图爬取测试网站 https://books.toscrape.com/」「接入 B2 真实执行体」。放行的是**工程决策
闸门**，限速升级为代码级硬契约——单域名串行 1 / burst 1 / 每页 ≥ max(15s, 人类阅读静置
12s, Crawl-delay) / 每日 60 / 单批次 10 页 / 冷却 300s / 真实 UA / 每页留痕，违反即抛
`PaceViolation`。「模拟人类」只指**人类可读的低频节奏**，不是伪装身份或躲风控。
用户手动截图 → 本地 OCR → 只留岗位信息 → 本地 RAG → AI 回答的合规替代链路继续开放。

**Q18 防护**（所有者「已发生境外写入没事，但要做好 Qdrant 防护机制避免数据写泄露」）：
在「`QDRANT_ALLOW_WRITE` + `Q18.json` 回执」双闸门之后，`vector_store._request()` 每次请求
前都校验端点必须是 https、Qdrant Cloud 官方域或显式白名单、无 URL 内嵌凭据；写入前
`validate_outbound()` 锁定集合名 `^zhiyin_public_[a-z0-9_]+$`、`scope=public`、`grade=D`、
已批准 D 级来源、payload 字段白名单与必需键、逐字段 PII（命中整批拒绝）、文本 2000 字符 /
payload 8192 字节 / 单批 20 条 / 向量 4096 维上限。异常只回状态码，审计只写 sha256 摘要与
计数。`POST /api/vector/guard/check` 是 dry-run，**不联网、不产生境外写入**。

**截图测试目标白名单**：`is_allowed_test_target()` 只允许本地自建测试页
`GET /api/capture/test-page`、显式精确白名单、以及 `SANDBOX_SITES` 中经核实的练习沙盒
`books.toscrape.com` 公开分页路径（页面自述 Sandbox / scraping purposes、随机价格评分、
无真实个人信息；`is_site_written_license=false`，公开声明**不是**书面许可函）。
招聘域名关键词、任何其他 host、query 注入、非 https 在代码层硬拒绝。

```bash
python server.py                                    # 本地 BFF
python tests/zy_capture_pace_test.py --pages 3       # 本地测试页限速自测，约 30s+
python tests/zy_capture_executor_test.py --pages 3   # 真实执行体对练习沙盒实跑，约 45s+
```

2026-11-16 实跑结果：3 页截图、实际页间隔 `[22.67, 25.72]s`（≥15s 契约）、
`robots.txt` 返回 404（未视为许可）、外部沙盒请求 4、第三方生产请求 0、招聘站接触 0、
位图留存 `none`、页面文本未解析、未入 RAG、未产出生产数据。报告在
`tests/out/capture_executor_report.json`（gitignored）。

**B2 生产执行体（第三轮）**：`production_capture.py` 已接入生产能力与九道闸门
（B2 回执 / 逐次手动开关 / `operator_ack` / 研究学习条款 / `enforce_pace` / 站点书面许可回执 /
法务书面结论 / 注册表 `approved` / Chrome）。许可回执由**站点方签发、人线下放置**到
`.privacy/site_licenses/*.json`（gitignored、工程侧只读、无写入 API），校验 `license_ref` /
`issuer` / `signed_by` / 有效期 / `self_issued=false` / `decision=approved` / `conditions_met` /
`legal_opinion=false` / `scope.allowed_hosts` + `allowed_path_prefixes` + 仅 D 级；
自签发、过期、格式错误一律 fail-closed。法务结论另需 `.privacy/approvals/B2-legal.json`
（`legal_opinion=true`、`legal_review=received`、范围含 `production_capture`）。
`production_run()` 只在九道闸门全过后发起请求，目标必须被许可的 host + path 覆盖，
执行期沿用真实 UA / robots / 页间 ≥15s / 每页留痕 / 位图即弃 / 不解析文本 / 不 OCR / 不入 RAG。
`tests/zy_production_capture_test.py`（43 项，已接入 `run_all.py`）用临时目录校验许可与闸门，
**不真的联网**。

**仍如实标注 / 拒绝实现**：站点书面许可函与法务书面结论仍未回，注册表仍为
`pending_legal`，故 `production_capture_unlocked=false`、生产 `requests_issued=0`、
`effective=false`；实际沙盒请求另以 `sandbox_test_requests_total` 如实记录。
**能力已接入 ≠ 已解锁**：练习沙盒公开声明、所有者自担风险、研究学习条款都不替代两份第三方
书面材料；许可齐备后无需改代码即可解锁。伪装真人身份、按「刚好不被发现」调参、随机抖动
躲风控、UA / IP 轮换、隐身、绕过验证码 / WAF / Cloudflare、登录墙 / 字体反爬一律永久禁止；
「研究学习 / 非商用」仍不构成合规豁免，也不据此放行其余待书面结论事项。
Q18 已按所有者确认维持允许、不回滚，`outbound_guard.py` 六道出站校验一格未放宽，
`legal_review=pending`、数据出境评估仍待回。

## 测试

```bash
python tests/run_all.py              # 全部：静态 + 单元 + HTTP + 浏览器 E2E + 离线 + 视觉
python tests/run_all.py --only unit  # static | unit | http | e2e | offline | visual
python tests/run_all.py --skip-e2e   # 无 Chrome 环境时跳过浏览器用例
```

HTTP / E2E 用例需要服务已在运行，默认 `http://127.0.0.1:8848`，可用 `ZHIYIN_BASE_URL` 覆盖。
Phase 2 新增 `zy_phase2_test.py`（单元）与 `zy_phase2_http.py`（HTTP）两组回归。
浏览器用例自动探测 Chrome / Edge，找不到会跳过并说明。

## 目录

```
index.html            单文件前端（7 视图 + 离线降级），零构建
demo-config.js        演示集展示口径（时间戳 / 来源标签），外置可改
server.py             FastAPI BFF / JSON Schema 校验 / 静态白名单 / 全部路由
secure_config.py      统一安全配置（凭据仅在内存，视图不泄露值）
llm.py                qwen provider / 结构化输出契约 / 外发前置拦截
embedding.py          BGE → DashScope → 本地 hash 三级适配
vector_store.py       Qdrant REST 适配 + 本地降级索引 + D 级闸门
orchestration.py      LangGraph 状态图 + 同语义标准库兼容引擎
stores/               PostgreSQL / Redis / Neo4j 适配器 + DDL / Cypher
celery_app.py         Celery 装配 + eager 降级；tasks.py 任务函数；celery_worker.py worker 入口
metrics.py            Prometheus 文本指标 + SLO 视图（纯标准库）
coach.py              A1 本地多轮辅导（会话仅内存 / 原文零落盘 / 零云调用）
hybrid.py             A4 稀疏 + 稠密双路 RRF 融合与重排
github_meta.py        A3 GitHub 公开仓库匿名只读元数据（限频 / 回链 / 不落盘）
web/                  Next.js 控制台（public/legacy 托管既有单页）
deploy/               SQL / Cypher 初始化、prometheus.yml、alerts.yml、slo.yaml
Dockerfile            docker-compose.yml  SLO.md
sources.py            11 源注册表 · fail-closed 闸门 · 分级 · 截图判定
market.py             公开远程岗 D 级适配（确定性统计 + 快照）
user_jd.py            C 级用户自带 JD：技能抽取 / PII 脱敏 / 原文零落盘
rag_store.py          本地研究检索：稀疏 TF-IDF / 加密片段 / TTL / 删除
research_capture.py   受控采集护栏：限速 / robots / 四重闸门 / 练习沙盒白名单
capture_executor.py   B2 真实截图执行体：七道闸门 / 严格限速 / 位图即弃（仅练习沙盒）
production_capture.py B2 生产执行体：九道闸门 / 站点书面许可回执驱动 / 默认不解锁
outbound_guard.py     Q18 出境写入防护：目标 / 集合 / 分级 / 字段 / PII / 体积锁定
privacy.py            AES-256-GCM / 脱敏 / 一键删除 / 审计哈希链
tests/                自包含回归套件
技术文档.md           架构设计与红线      合规与授权沟通稿.md  法务 / BD 沟通稿
待书面结论事项与回执清单.md   B1 / Q18 / Q19 / Q21 / B3 请求单 · 回执台账 · 启用闸门（v1.4，含 §7.7 第三轮决策）
数据出境与公开元数据条款清单.md   Q19 / Q21 需补条款 · 单独告知同意模板 · DPA 条款清单
参考素材-*.html       外部设计稿，非本项目文档
```

## 已知边界

- 前端只在 `localStorage` 保存非敏感 UI 状态（学习进度、视图、JD 标签、面试模式）；**不保存** JD 原文、简历、OCR 文本、面试回答或报告。
- 演示集的时间戳与来源标签外置在 `demo-config.js`（`window.ZHIYIN_DEMO`）；数据集本体仍是内置样例，不是实时行情。
- 前端按需从 CDN 加载 Tesseract.js / Google Fonts / Three.js；离线时字体退化、OCR 降级为「直接粘贴文字」。
- 所有外部结论未回之前，本仓库中所有 `blocked` / `pending_*` 状态都是刻意保留的，不是待修的 bug。
- **五项待书面结论**（B1 / Q18 / Q19 / Q21 / B3）统一见 [`待书面结论事项与回执清单.md`](待书面结论事项与回执清单.md)。
  2026-11-16 项目所有者逐项决策后 **B1 / Q18 已按工程开关启用**（回执 `issuer=项目所有者`、`legal_review=pending`，不等于法务结论）；
  Q19 / Q21 因只是复述问题栏而保持 CLOSED，需补条款见 [`数据出境与公开元数据条款清单.md`](数据出境与公开元数据条款清单.md)；B3 未提及、保持关闭。
  代码级回执闸门已接线（`approval_gate.py`），`/api/approvals/status` 可实时核查。
- Qdrant Cloud 已按 Q18 回执开启云写入并实测 `backend=qdrant-cloud`：集合 `zhiyin_public_d` 已创建，目前含 2 条 D 级公开样本（回归用例写入），临时验收样本已删除。2026-11-16 17:24 所有者明确「**境外写入允许**」，`Q18.json` 已记入 `owner_confirmation`（维持 `QDRANT_ALLOW_WRITE=true`、不回滚、不关闭双闸门），`/api/approvals/status` 可核查该确认摘要。
  **数据出境评估与来源方条款的法务书面结论仍未回，`legal_review=pending`**；`outbound_guard.py` 六道出站校验一格未放宽，`ALLOW_CLOUD_FOR_USER_DATA=false`，C 级用户数据仍禁止入云。回滚：`.env` 置回 `QDRANT_ALLOW_WRITE=false` 或移走 `.privacy/approvals/Q18.json`，写入立即停止；`/api/vector/delete` 可删除已写入点。
- 本机 PostgreSQL 18 已接线（库 / 角色 `zhiyin`，端口 5432，驱动 `psycopg[binary]`）；`/api/stores/status` 的 `postgresql.backend` 应为 `postgresql`。密码由外部 `key.env` 提供，不写进任何项目文件；未装驱动或服务未启动时自动降级 SQLite。
- 8848 现为 Phase 3 服务，`start.bat` 可直接启动；无需再处理上一版遗留进程。

- qwen 已启用但**不代表放宽数据边界**：`ALLOW_CLOUD_FOR_USER_DATA=false`、C 级原文零落盘零外发；Q18 云写入虽已开启，仍只收 D 级公开样本，书面结论未回。
- **BGE 已恢复（2026-11-16 17:24）**：病因是主环境 `transformers 4.57.6` 与 `sentence-transformers 6.3.0` 版本互斥（后者要求 transformers 5.x）且目录混入两套文件，触发 `_LazyModule` 循环导入。不再覆盖修补主环境，改用 `pip install --no-deps --target=.bge_env` 装干净组合（transformers 4.49.0 / ST 3.4.1 / tokenizers 0.21.0 / hf-hub 0.29.3 / Pillow 11.1.0），`embedding.py` 把 `.bge_env` 前置到 `sys.path`，并把 HF repo id 解析为本地 hub snapshot 绝对路径——否则 huggingface-hub 的 `.no_exist` 缓存标记会让 SentenceTransformer 误判 `modules.json` 不存在、回退成 mean pooling。实测 `/api/embedding/status` 返回 `bge_available=true`、`bge_runtime=isolated:.bge_env`、`bge_model_source=local_hub_snapshot`（模型含 Transformer + Pooling(CLS) + Normalize 三层）、`selected_backend.user=bge`，`embed(scope=user)` 产出 512 维归一化语义向量、`cloud_used=false`。
  **维度一致性约束**：既有 Qdrant 集合 `zhiyin_public_d` 是按 DashScope `text-embedding-v3` 的 1024 维建立的，BGE 只有 512 维。故 `EMBED_BACKEND=auto` 下 `public` 优先 1024 维 DashScope、`user` 优先本地 BGE；`vector_store.ensure_collection()` 在检测到集合维度不符时明确抛 `COLLECTION_DIM_MISMATCH`，避免 512 维向量发到云端被拒后静默降级为本地写入。`.bge_env/` 是 gitignored 本机产物；**目录不存在时自动沿用主环境并退回原降级链路**，不影响启动。应用再次升级清空主环境时，重跑一次该 `--target` 安装即可恢复。
- Docker Desktop 启动被本机安全策略拦截（`wsl.exe` 黑名单），未重试或绕行；Redis / Neo4j 服务端未运行，继续走本地降级。
- Chrome 的 CDP 远程调试端口在本环境不监听，交互 E2E 用例优雅 SKIP；DOM 静态审计与渲染后 DOM 检查仍通过。
- **Python 环境会被应用升级重置**：`python` 指向 WorkBuddy 内置解释器（`3.13.12` 是软链，实际 3.13.14）。2026-11-16 升级后 `site-packages` 被清空，BFF 因缺 `cryptography` 无法启动。恢复方式：先 `python -m pip install -r requirements.txt`（核心可一次装），再对 `requirements-optional.txt` 中的包**逐个**执行 `python -m pip install --no-clean "<包名>"`——一次装多个、或让 pip 清理临时目录，都会触发沙箱的 `SAFE_DELETE_BULK_GUARD_ERROR`。BGE 权重在 `~/.cache/huggingface`、干净依赖栈在项目内 `.bge_env/`，两者都不受应用升级影响。彻底隔离建议改用项目级 venv，**尚未实施**。
- **B2 生产执行体默认不解锁**：`production_capture.py` 能力已接入，但 `.privacy/site_licenses/` 无第三方站点书面许可函、`B2-legal.json` 法务结论未回、注册表仍 `pending_legal`，故 `GET /api/capture/production/status` 返回 `production_capture_unlocked=false`、生产请求 0。这是**设计内状态，不是待修 bug**；工程侧不提供许可写入 API、不假造许可、不指向无许可招聘站。两份书面材料由人线下放置后无需改代码即可解锁。

本项目文档中的合规判断均为工程侧过程控制，**不构成法律意见**，最终边界以法务书面结论为准。

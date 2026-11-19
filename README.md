# 职引者 ZHIYIN

给「急着面试却不知道投哪儿」的应届生，一份**能照着做决定的证据清单**。
主入口是 **决策工作台**（`http://127.0.0.1:8848/workbench.html`），左侧 3 步主流程（洞察对齐 → 定向学习 → 模拟面试），
AI 辅导改为右下角常驻按钮召唤的功能侧栏、任何视图都能随时对话；隐私合规并入个人中心的「用户须知」。
输入解析已并入「洞察对齐」，解析结果自动带入洞察与面试字段；视觉沿用 HOMETRY 极简等距风不变。
前端洞察统一以**国内样本**口径呈现，已移除原「样本口径」字段与冗长免责文字，只保留必要讲解。

> **前端模块归属（最新）**：左侧主步骤为 ①洞察对齐（含输入解析）②定向学习 ③模拟面试；
> AI 辅导是常驻侧栏（右下按钮召唤，不占主视图）；隐私合规并入个人中心「用户须知」；
> 投递看板 / 说明 / 个人中心 / 管理后台为辅助入口。
> **定向学习不靠用户自己猜**：要补的技能由「洞察对齐」结果自动带入——优先取 JD 与经历对比得出的
> `to_add` 缺口，无个人 JD 时用国内样本「低自动化可行性 × 高需求」技能作方向性参考；手改字段不被覆盖。
> **输入自动流转**：在洞察页粘贴文本，或把简历 图片 / PDF / DOCX 拖到「解析」按钮上（也可点击选文件），
> 由**本机 Qwen3-VL 视觉模型**抽取求职意向、薪资意向、专业、学历、证书、荣誉、技能方向（含具体技术栈）与项目/实习经历；
> 解析字段作为方向约束进入 RAG 检索与专家解读。用户手工改过的字段不会被覆盖；姓名、手机、邮箱、证件、
> 住址、性别等身份信息不读取、不留存、不写入向量库。
> **材料检索 / 入库只给管理员**：从右上角个人菜单进入「管理后台」，在该页完成向量库检索、知识入库与按 id 删除；用户侧不暴露该入口。
> 全部功能复用既有 BFF 端点：洞察走 `/api/market` + `/api/jtbd/*` + `/api/llm`（行业专家 agent 解读），
> 输入解析走 `/api/local-vlm/extract`（本机视觉模型，图片不出机），专家上下文走 `/api/insight/context` 与
> `/api/expert/context`（岗位材料 + 面试话术双索引），学习路径可勾选（仅本机）。
> **模拟面试深化接入向量库**：开局走 `/api/hybrid/search`（稠密路 Qdrant、本地降级）取脱敏岗位片段，
> 素材参与三环节——`interview_question` 动态出题、`answer_feedback` 逐题反馈、`report` 最终报告；
> LLM 不可用时回退固定四阶段题，仍展示素材。只传向量库 D 级脱敏片段 + 岗位/阶段元数据，
> 用户项目 / 回答原文不作为用户数据字段外发。辅导走 `/api/coach/chat`，
> 隐私走 `/api/privacy/*`。管理员页新增 **CSV 解析**（浏览器本地解析成文本）与
> **模拟面试素材入库**（`job_only=false`，脱敏后保留 STAR 案例全文），两种模式都切片向量化写入 Qdrant；
> 管理员页检索走 `/api/hybrid/search`（**稠密路走 Qdrant，本地仅降级**）。
> 服务端保留可选字段 `origin=admin_csv|interview_material` 与 `job_only`（默认 true，不影响既有行为）。


> **知识来源变更（最新）**：自动爬取与截图执行体已**整体移除**——`research_capture.py` /
> `capture_executor.py` / `production_capture.py` 及所有 `/api/capture/*` 执行端点全部删除，
> 系统不再访问任何第三方页面。知识只由**管理员主动提供**：在管理后台「知识入库」面板粘贴文本 /
> 上传截图 OCR / 导入 CSV / 导入模拟面试话术。入库链路固定为
> **PII 脱敏 → 仅岗位信息 → 切片 → 本地 BGE 向量化 → Qdrant 云 RAG**（经 `outbound_guard` + Q18 双闸门），
> 可按入库返回的 chunk id 删除。用户侧简历解析则走独立的 `/api/local-vlm/extract`（本机视觉模型），
> 与管理员入库互不影响。下方 2026-11-16 B2 决策段落为**历史留档**，其执行体均已移除。

> **2026-11-16 前端重设计**：JTBD 决策工作台位于 `http://127.0.0.1:8848/workbench.html`
> （HOMETRY 极简等距视觉，左侧步骤轨道 + 右侧工作区 + 底部操作条），
> 实现三态对照（有证据 / 待核实 / 明确缺口 / 未知）、硬条件单独阻断、无百分比总分、
> 按差距类型分流、针对单个缺口的追问演练与投递看板。
> 根路径 `/` 与 `/index.html` 均指向唯一新版决策工作台；旧版 8 视图单页已删除。
> 二者共用同一 BFF，不是两套并行产品。详细设计见 `技术文档.md` 的 JTBD 章节。

> **当前状态**：可运行的产品原型 + 本地 FastAPI BFF + 合规闸门 + 可选组件适配层。
> 简历解析已由**本机 Qwen3-VL 视觉模型**承担（图片 / PDF / DOCX 不出机），洞察 / 辅导 / 面试走
> 用户自带 LLM 的专家 agent + RAG 混合检索；市场洞察只呈现国内样本口径，不预测薪资、不下岗位判决。
> Qdrant / BGE / PostgreSQL 等外部组件「真服务可用则用、不可用则本地降级」，状态在 `/api/*` 与 `/metrics` 如实标注。
> 下方带日期的历史决策段落（B1/B2/Q18 等）为**留档**，其采集执行体均已移除。

## 快速启动与配置须知

### 1. 环境与一行启动

- Python 3.10+；核心依赖仅 4 个：`fastapi / uvicorn / pydantic / cryptography`（见 `requirements.txt`）。
- Windows 双击 `start.bat`（自动检查并安装依赖），或在项目根目录执行：

```bash
python -m pip install -r requirements.txt
python server.py
```

- 访问 **<http://127.0.0.1:8848/workbench.html>** 进入唯一前端「决策工作台」；根路径 `/` 也指向它。
- 端口 / 地址：`python server.py --port 8849` 改端口；容器内 `--host 0.0.0.0 --port 8848`。
- **先登录再用**：登录页支持「微信扫码演示」与「管理员密码」。未配 `ADMIN_PASSWORD_HASH` 时使用页面标注的本机演示密码（仅 127.0.0.1/::1 可用）。用户 LLM 在「个人中心」自带；知识入库 / 向量检索仅管理员可见。
- `start.bat` 不复制、不生成、不打印任何密钥。

### 2. 配置清单（全部可选，留空走默认值）

配置来源优先级：进程环境变量 > `--env-file` / `ZHIYIN_ENV_FILE` > 项目根 `.env` > `.zhiyin_key_path` 指向的外部文件。
复制 `.env.example` 为 `.env` 即可开始；`.env` 已被 gitignore，绝不入库。完整键名与注释见 `.env.example`。

| 配置 | 用途 | 默认值 / 说明 |
|---|---|---|
| **本地视觉模型（解析必需）** | 简历截图 / PDF / DOCX 在本机识别岗位字段；图片与原文不出本机 | 需本机安装 Ollama 并执行 `ollama pull qwen3-vl:4b-instruct`；`LOCAL_VLM_*` 见 `.env.example`。服务未启动时解析会报错，可直接手写下方字段 |
| **用户自带 LLM（专家 agent）** | 洞察 / 辅导 / 面试的云端解读，按登录身份隔离 | 个人中心选厂商、端点、模型、API key；key 经 AES-256-GCM 加密写入 `.privacy/user_llm.json`（gitignored），页面看不到完整密钥 |
| 服务端兜底 LLM | 未自带 key 时的可选后端 | 复制 `.env.example` → `.env`，填 `QWEN_API_KEY` 并设 `LLM_ENABLED=true`（默认 false）；也可 `--env-file` 指定 |
| 向量库 Qdrant Cloud | 只放 D 级公开岗位样本 / 脱敏技能标签 | `QDRANT_*`；云端写入需 `QDRANT_ALLOW_WRITE=true` **且** `.privacy/approvals/Q18.json` 回执双闸门；只读探测始终允许 |
| Embedding | RAG 检索与入库向量化 | 服务端固定本地 BGE（`BAAI/bge-small-zh-v1.5`，512 维），用户不可覆盖；`EMBED_BACKEND=bge` |
| 会话签名 / 账号加密 | 跨重启保持登录与账号可读 | `AUTH_SECRET`（强烈建议配置）；不配则每次启动随机，重启后需重新登录 |
| 管理员密码哈希 | 管理员密码登录入口 | `python -c "import auth;print(auth.make_admin_hash('你的强密码'))"` 生成后填 `ADMIN_PASSWORD_HASH` |
| 研究索引跨重启留存 | 加密片段密钥 | 默认最小留存、重启清空；需保留则设 `ZHIYIN_PII_KEY`（32 字节 base64），丢失不可恢复，片段默认 30 天 TTL |

补充约束：

- 用户 LLM 端点只允许 https（本地开发可 http 回环），拒绝 URL 内账号 / 查询串、私网与元数据地址；`assert_outbound_safe()` 仍拦截 PII 与 C 级原文，不因自带密钥而放宽。
- `GET /api/config` 只返回「是否配置 / 来源类别 / 形态」，**不返回任何密钥值**；`credential_boundary` 明示 LLM 用户可选、embedding 服务端固定。
- 本机视觉模型职责边界：**只解析岗位字段 → 进 RAG 检索增强 → 交云端专家模型生成内容**；embedding 固定服务端本地 BGE，旧 `QWEN_EMBED_MODEL`（text-embedding-v3）已移除。
- 状态自检端点：`/api/local-vlm/status`（本机模型是否就绪）、`/api/config`（配置形态）、`/api/approvals/status`（回执闸门）。
- 本地数据都在 gitignored 目录：`.privacy/`（账号、审计、用户 LLM key、审批回执）、`.rag/`、`.stores/`、`.env`、`.ollama-models/`；仓库不含真实密钥或个人数据。

## 数据与合规口径（不因架构升级而放宽）

| 模式 / 来源 | 分级 | 状态 | 能得出的结论 |
|---|---|---|---|
| 国内岗位样本（中国公共招聘网计算机类精选 3000，采集时已 PII 清洗） | D · 国内样本 | 可用 | 技能出现率、学习方向；**仅国内样本，不做市场级推断** |
| 国内市场洞察（市场级统计 / 薪资） | A · 境内授权数据 | **未开放** | 零数值、零图表、零排序，不预测薪资、不下岗位判决 |
| 用户自带 JD / 手动截图 OCR | C · 用户自带 | 可用 | 仅这一份 JD 的能力差距，不产生市场级统计 |
| 管理员提供的岗位文本 / 截图 OCR 文本 | D · 脱敏后入库 | 可用 | 岗位知识检索参考；经 PII 脱敏 + 仅岗位信息 + Qdrant 云 RAG |
| 受控研究采集（历史登记） | B | 执行体已移除 | 来源仍登记为待法务以留档，但不再有任何采集执行能力 |
| 隐身浏览器 / 登录态复用 / 规避技术措施的自动截图 | B | **永久封禁** | 不实施 |

**新增云组件的边界**：

- C 级用户自带数据（JD / 简历 / 本地 OCR）**默认不出本机**：`ALLOW_CLOUD_FOR_USER_DATA=false`。
- Qdrant Cloud 集合 `zhiyin_public_d_bge`（**BGE 本地 512 维**）**只收 D 级公开岗位样本**；
  逐条过来源闸门与 PII 扫描，命中即整批拒绝；关闭或不可达时降级本地进程内索引（不落盘、重启清空）。
  旧集合 `zhiyin_public_d`（1024 维，云端模型）保留只读、不再写入，勿与新集合混写。
- 向量化统一走**本地 BGE**（`EMBED_BACKEND=bge`）：入库与检索同模型同维度，不依赖云端 embedding，
  无「云端超时导致维度漂移」风险。C 级用户原文仍前置拦截，不外发。
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
| 前端 | 决策工作台 `workbench.html`（Next.js 控制台见 `web/`） | 直接访问 `workbench.html` | `/api/health` |
| 可观测 | Prometheus / Grafana | 纯标准库 `/metrics` | `/metrics`、`/api/slo` |

本机已装并生效：`psycopg[binary]`（PostgreSQL 18.7 已连接）、`langgraph`（真 `StateGraph` 接管编排）、`celery`（无 broker 时 eager）；`redis` 客户端与 `neo4j` 驱动已就绪，但服务端未运行仍降级。**「不需要 API / 密钥」的完整清单见 `ROADMAP.md`**。所有可选依赖都在 `requirements-optional.txt`，缺失不影响启动。

新增端点：`/api/config`、`/api/llm/status`、`/api/embedding/status`、`/api/vector/{status,probe,ingest,search,delete,clear-local}`、
`/api/graph/{status,run}`、`/api/stores/status`、`/api/tasks/{status,dispatch}`、`/api/slo`、`/metrics`、
`/api/resume/parse-text`（OCR / 粘贴文本的岗位字段抽取，不写原文与向量库）。
原有 `/api/*` 契约全部保留。

### Next.js 控制台（可选）

```bash
python server.py                                   # 先起 BFF
node web/scripts/sync-legacy.mjs                   # 同步工作台/登录页到 public/
cd web && npm install && npm run dev               # http://127.0.0.1:3000
```

Windows 上若 `npm run build` 报 `.next/trace` 的 EPERM，改用 `npm run build:win`（内置 shim）。
控制台通过 rewrite 把 `/api/*`、`/metrics` 代理到 BFF，
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

## JTBD v2.0 决策工作台（2026-11-16，主入口）

按 `JTBD需求文档.md` v2.0 重做的前端主流程，视觉沿用 HOMETRY 极简等距风（酸性绿 `#E4FF00`、
几何切角、切屏遮罩），但排版改为**左侧步骤轨道 + 右侧工作区 + 底部操作条**，与旧版顶部导航布局不同。

| 步骤 | 前端 | 后端 API | 关键约束 |
|---|---|---|---|
| 01 找方向 | 粘 5–10 份 JD 比较 | `POST /api/jtbd/compare` | 只在样本内比较，输出共同要求 / 各自特有 / 补证据成本最低的 2–3 个方向 |
| 02 做核对 | 要求—证据三态对照 | `POST /api/jtbd/evidence` | 有证据 / 待核实 / 明确缺口 / 未知四态；关键词命中照旧（可反驳），未命中时用服务端固定本地 BGE 算相似度、超阈值标「待核实」而非「明确缺口」（`JTBD_SEMANTIC_FALLBACK`，hash 降级时自动关闭）；学历按「JD 要求 vs 你提供的学历」真实比较，届别地点等硬条件单独阻断；**无百分比总分**；每条可反驳 |
| 03 定行动 | 按差距类型分流 | `POST /api/jtbd/actions`、`/diagnose` | 缺表达 / 缺证据 / 缺能力 / 缺硬条件四类动作不同；「先投」与「放弃」都是合法结论；简历诊断两栏「JD 要你没写」/「你写了 JD 不看」 |
| 04 过追问 | 针对一个缺口三层追问 | `POST /api/jtbd/drill`、`/review` | 反馈引用用户原话，三维（表达 / 判断 / 证据）不打分；压力模式可选 |
| 05 记看板 | 投递记录 + 材料库 | 纯前端 `localStorage` | 只存本机，由用户自己保存 / 删除；不上传服务端 |

只读 `GET /api/jtbd/status` 列出全部端点与边界。全部推导在 `jtbd.py`，C 级文本只在本次请求内存里处理：
不落盘、不外发、不进 RAG / 向量库；「没提供」一律标未知、不编造经历、不输出市场级结论。
`tests/zy_jtbd_test.py`（38 项）已接入全套回归。

## 登录与分角色（研究学习原型）

**必须先登录才能操作**：未登录访问 `/workbench.html` 会被服务端 302 到登录页（前端另有兜底跳转），登录成功按 `next` 回跳；根路径 `/` 与 `/index.html` 同样指向工作台，但保留为公开的 HTTP 契约入口。入口 `http://127.0.0.1:8848/login.html`，Tab 式界面：登录 / 注册 / 管理员，另有「改密码」入口。工作台右上角个人按钮进个人中心 / 管理后台 / 退出。个人中心可配置自己的大模型 API（厂商 / 端点 / 模型 / key），embedding 仍为服务器固定本地 BGE。

- **个人 · 账号密码**（`local_accounts.py`）：用户名（3-32 位字母数字下划线）或邮箱 + 密码（8-128 位）注册 / 登录 / 改密。
  密码用标准库 `pbkdf2_hmac`（18 万次、逐账号随机盐）哈希；标识与昵称 AES-GCM 加密落 `.privacy/accounts.json`（gitignored），
  磁盘上只有不可逆查找索引，无明文账号 / 密码。连续失败 5 次锁定 60 秒。**注册恒为 personal，无任何自我提权到 admin 的路径。**
  账号只用于鉴权，不产出用户画像、不入向量库 / RAG、不外发，符合「不提取隐私 / 不做用户画像」的边界。
- **个人 · 微信扫码快捷登录**：`auth.py` 按开放平台标准 OAuth2（`qrconnect` → `code` → `access_token` → `userinfo`）。
  配齐 `WECHAT_APPID` / `WECHAT_APPSECRET` / `WECHAT_REDIRECT_URI`（https 公网回调）走真实扫码；
  **未配置时降级为本地演示扫码**，页面与登录态都标注「演示 / 非真实微信」。openid 只留截断哈希。
- **个人 · GitHub 快捷登录**：标准 OAuth2 授权码流程（`/login/oauth/authorize` → `code` → `/login/oauth/access_token` → `/user`）。
  配齐 `GITHUB_CLIENT_ID` / `GITHUB_SECRET` / `GITHUB_REDIRECT_URI`（https 回调）走真实授权，scope 仅 `read:user user:email`；
  **未配置时降级为本地演示授权并明确标注**。GitHub id 只留截断哈希，不落盘。
  > 本地 `127.0.0.1` 无法走真实第三方登录：微信 / GitHub 都要求已备案的公网回调域名。这是平台约束，不是实现缺陷。
- **管理员：独立密码登录**，Tab 独立入口，不提供扫码 / 第三方授权。密码用 `ADMIN_PASSWORD_HASH`（pbkdf2 sha256）校验，前端不存明文。
  生成：`python -c "import auth;print(auth.make_admin_hash('你的强密码'))"`。未配置时用本机演示密码（仅 `127.0.0.1/::1` 可用，页面标注演示）。
- **会话与鉴权**：HMAC 签名令牌存 httpOnly cookie（JS 读不到）；state 防 CSRF、一次性；登出即进程内撤销。
  `AUTH_REQUIRE_LOGIN`（默认 true）控制**页面级**强制登录；`AUTH_MODE`：`off` 不校验 / `optional`（默认，本地回归）带令牌才校验角色 /
  `required`（正式部署）所有受保护写端点强制校验。管理员是个人能力的超集。写端点角色映射集中在 `server.py` 的 `_ROLE_FOR_WRITE`。
- 真实凭证 / 密钥只在本进程内存，不写文件、不回显浏览器；`AUTH_SECRET` 不配则每次启动随机（重启即掉登录、本地账号不可读）。
  正式部署请配置 `AUTH_SECRET` + `AUTH_MODE=required` + 各第三方 OAuth 凭据。
- `tests/zy_auth_test.py`（36 项，含 TestClient 分角色 HTTP 用例）、`tests/zy_local_accounts_test.py`、`tests/zy_login_flow_test.py` 已接入回归。

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

## 决策落地（回执闸门 / B1 / 管理员知识入库）

> 下表 B2 采集相关条目为 **2026-11-16 历史决策留档**，其执行体已随爬取功能整体移除；
> 当前知识来源见文首「知识来源变更」与下方「管理员知识入库」行。

| 项 | 实现 | 状态 |
|---|---|---|
| 代码级回执闸门 | `approval_gate.py`：编号 / 出具方 / 有效期 / 范围 + 注册表或配置双闸门；只读、无自我授权入口；`/api/approvals/status` | 已做 |
| B1 文件解析 | `resume_parse.py` + `/api/resume/*`：仅 pdf / docx、文件与原文只在内存、解析完即释放、只输出大致岗位信息、有撤回与删除入口；云端解析需逐次单独同意且只发去标识化岗位字段 | 已做（`legal_review=pending`） |
| 岗位信息抽取 | `job_info.py`：个人信息＝姓名 / 手机 / 邮箱 / 证件号 / 住址 / 联系方式等身份标识；求职岗位 / 专业 / 证书属于岗位颗粒度信息，**简历解析会读取**并带入洞察对齐，输出这些字段与技能方向 / 级别词；截图 / `local_ocr` 入库链路仍严格剔除个人信息行、不入库；解析结果在前端以可读卡片展示，不暴露 JSON schema | 已做 |
| 个人画像 | 由用户主动提供 JD / 简历的本地 `/api/jd/analyze` 链路提供（覆盖率 / 技能差距），与截图入库链路分离 | 保留 |
| Q18 云写入 | `QDRANT_ALLOW_WRITE=true` + `Q18.json` 回执 + 双闸门；实测真实写入成功 | 已做（法务结论待回） |
| **管理员知识入库** | `knowledge_ingest.py` + `/api/admin/knowledge/{status,ingest,delete}`（管理员角色鉴权）：文本 / 截图 OCR 文本 → PII 脱敏 → 仅岗位信息 → 切片 → 向量化 → Qdrant；无图片上传端点；可按 chunk id 删除 | 已做（`admin_curated` / D 级 / 浏览器本地 OCR） |
| **种子数据切片入库** | `ingest_seed_data.py`：读国内岗位精选3000（拆长句走 `job_only=True`）+ 面试话术库.md / 通用话术.csv / 质询流程.csv（`job_only=False`），复用 `knowledge_ingest` 的脱敏 / 过滤 / 闸门，批量写本地 BGE 512 维集合 `zhiyin_public_d_bge`；带 `--dry-run` / `--resume` / `--only` / `--limit`，断点只记稳定 key 不记原文 | 已入库：岗位 2022 条 + 话术 84 条，共 2280 chunk，0 失败 |
| ~~B2 采集设置~~ | ~~`/api/capture/admin` 默认不自动爬取、逐次手动开关~~ | **已移除**（端点删除） |
| Q19 / Q21 材料 | `数据出境与公开元数据条款清单.md`：GitHub ToS §H / AUP §5·§6 等需核对条款、单独告知同意模板、DPA 条款清单 | 已做（结论待回） |
| **全项目使用限制条款** | `usage_policy.py` + `GET /api/project/usage`：条款 `USAGE-RESTRICTION-2026-11-16`，5 份回执顶层 / 状态端点 / 文档 / 界面统一标注；`changes_gate_state=false` | 已做（B1 / Q18 仍启用，Q19 / Q21 / B3 仍 CLOSED） |
| ~~**B2 自担风险放行 + 严格限速**~~ | ~~`B2.json` + `PACE_CONTRACT` + `enforce_pace()` + `GET /api/capture/pace`~~ | **已移除**（执行体与限速契约删除；B2.json 回执留档，条款仍统一适用） |
| **Q18 出境写入防护** | `outbound_guard.py`：端点官方域 / 集合 / 分级 D / 已批准来源 / payload 字段白名单 / 逐字段 PII / 体积批量上限；凭据与原文不回显，审计只写 digest；`GET /api/vector/guard`、dry-run `POST /api/vector/guard/check` | 已做（不产生新的境外写入） |
| ~~**B2 真实执行体 + 练习沙盒**~~ | ~~`capture_executor.py` 七道闸门 + 严格限速；`GET/POST /api/capture/executor/*`~~ | **已移除**（执行体与练习沙盒白名单删除；本地 OCR 保留，仅浏览器内识别管理员截图） |
| **Q18 维持允许（不回滚）** | `Q18.json` 新增 `owner_confirmation`：所有者明确维持 `QDRANT_ALLOW_WRITE=true`、不回滚已发生写入、不关闭双闸门；`approval_gate` 把确认摘要暴露到 `/api/approvals/status` | 已做（`legal_review=pending`，出站防护一格未放宽，数据出境评估仍待回） |
| **BGE 恢复** | `.bge_env/` 独立依赖目录 + `embedding.py` 的 `sys.path` 前置；不修补被污染的主环境 | 已做并实测（`selected_backend.user=bge`、512 维零云调用；`public` 维持 1024 维 DashScope 以匹配既有集合；`ensure_collection()` 增维度冲突硬拒绝） |
| ~~**B2 生产执行体**~~ | ~~`production_capture.py` 九道闸门 + 站点书面许可函回执驱动；`GET /api/capture/production/status`~~ | **已移除**（生产采集能力整体删除，不存在生产请求） |

相关端点：`/api/project/usage`、`/api/approvals/status`、`/api/resume/{status,consent,parse,withdraw,delete}`、`/api/admin/knowledge/{status,ingest,delete}`、`/api/vector/guard`、`/api/vector/guard/check`；只读策略端点 `/api/capture/policy`、`/api/research/policy` 保留（仅声明策略、不执行采集）。原 `/api/capture/{admin,toggle,pace,executor/*,production/*,test-page}` 执行端点均已删除。

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

**管理员知识入库链路**（取代原截图采集 / 执行体）：`knowledge_ingest.py` 提供
`prepare()`（纯本地加工：用途授权 → 来源形态 → 图片 payload 拦截 → PII 脱敏 → 仅岗位信息过滤
→ 切片 → 标题脱敏）与 `ingest()`（分批 `vector_store.ingest(scope=public, source_id=admin_curated)`）。
截图由 **Tesseract.js 在浏览器本地 OCR** 成文本后再提交（`origin=admin_ocr`），位图不上传、
不落盘；服务端只有 `/api/admin/knowledge/ingest` 收文本，`extra=forbid` 拒绝任何 image 字段。
两种来源统一经相同脱敏与岗位过滤，写入前仍过 `outbound_guard` 与 Q18 双闸门；入库返回
`chunk_ids`，可经 `/api/admin/knowledge/delete` 按 id 删除。D 级知识只作研究参考，
不产出市场级或个人画像结论。

```bash
python server.py                          # 本地 BFF
python tests/zy_knowledge_test.py         # 知识入库单元（加工链 / 闸门 / 口径）
python tests/zy_knowledge_http.py         # 知识入库 HTTP 端到端（入库 → 向量化 → 删除）
python tests/run_all.py                   # 全套回归
```

**仍如实标注 / 拒绝实现**：自动爬取与所有截图执行体已删除，系统不访问任何第三方页面、
不再有生产采集请求；原 `books.toscrape.com` 练习沙盒与 B2 限速契约随执行体一起移除。
「研究学习 / 非商用」不构成合规豁免，也不据此放行其余待书面结论事项。
Q18 已按所有者确认维持允许、不回滚，`outbound_guard.py` 出站校验一格未放宽，
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
workbench.html        主入口「决策工作台」（洞察对齐 → 定向学习 → 模拟面试，解析框三合一按钮）
workbench.html        唯一前端页面（决策工作台，零构建）
demo-config.js        演示集展示口径（时间戳 / 来源标签），外置可改
server.py             FastAPI BFF 组装：app / 中间件（鉴权闸门 + body 限额）/ 异常兜底 / 静态页 / 启动
routers/              按域拆分的 API 路由（8 域：auth/jtbd/privacy/resume/vector/knowledge/expert/ops）
  common.py           共享层：请求模型 / 共享 helper / 业务模块导入（各域 from common 复用）
  <域>.py             各域 APIRouter；@app 路由在拆分后统一为 @router，路径与鉴权完全不变
secure_config.py      统一安全配置（凭据仅在内存，视图不泄露值）
local_vlm.py          本机 Qwen3-VL 视觉模型解析（双接口 ollama/openai，图片不出机）
                      + coerce_profile 字段白名单闸门（VLM 输出收敛为岗位字段）
llm.py                用户自带 LLM + 云端专家 agent prompt / 结构化输出契约 / 外发前置拦截
user_llm.py           用户自带 LLM 配置：厂商预设 / AES 加密存储 / 端点闸门
local_accounts.py     本机账号存储（PBKDF2 + AES-256-GCM）
embedding.py          本地 BGE 向量化（固定 512 维，不依赖云端模型）
vector_store.py       Qdrant REST 适配 + 本地降级索引 + D 级闸门
orchestration.py      LangGraph 状态图 + 同语义标准库兼容引擎
stores/               PostgreSQL / Redis / Neo4j 适配器 + DDL / Cypher
celery_app.py         Celery 装配 + eager 降级；tasks.py 任务函数；celery_worker.py worker 入口
metrics.py            Prometheus 文本指标 + SLO 视图（纯标准库）
coach.py              A1 本地多轮辅导（会话仅内存 / 原文零落盘 / 零云调用）
hybrid.py             A4 稀疏 + 稠密双路 RRF 融合与重排
github_meta.py        A3 GitHub 公开仓库匿名只读元数据（限频 / 回链 / 不落盘）
web/                  Next.js 组件状态控制台（public/ 托管工作台与登录页）
deploy/               SQL / Cypher 初始化、prometheus.yml、alerts.yml、slo.yaml
Dockerfile            docker-compose.yml  SLO.md
sources.py            源注册表（含 admin_curated）· fail-closed 闸门 · 分级 · 只读策略
market.py             国内市场静态统计（读 data/domestic_market_stats.json，零网络请求）
user_jd.py            C 级用户自带 JD：技能抽取 / PII 脱敏 / 原文零落盘
rag_store.py          本地研究检索：稀疏 TF-IDF / 加密片段 / TTL / 删除
knowledge_ingest.py   管理员知识入库：文本 / OCR / CSV / 面试话术 → PII 脱敏 → 切片 → 本地 BGE → Qdrant
ingest_seed_data.py    国内岗位样本种子数据入库脚本
hybrid.py             混合检索：TF-IDF 稀疏 + BGE 稠密 + RRF 融合 + 精排（洞察/专家/面试三类上下文）
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
- 用户侧简历解析走本机 Qwen3-VL（Ollama），图片 / PDF / DOCX 在浏览器内读成 base64 上传、服务端内存处理，不落盘不外发；本机模型服务未启动时解析会报错，可直接手写下方字段。管理员入库的截图 OCR 与用户侧解析是两条独立链路。
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
  **维度一致性约束（2026-11-18 统一口径）**：当前集合 `zhiyin_public_d_bge` 为本地 BGE 512 维，`EMBED_BACKEND=bge` 为服务器固定配置，public / user 两条链路同模型同维度；不再调用云端 embedding-v3，也不再由 `auto` 在 1024 维 DashScope 与 512 维 BGE 间切换。旧集合 `zhiyin_public_d`（1024 维）保留只读、不再写入。`vector_store.ensure_collection()` 在检测到维度不符时仍明确抛 `COLLECTION_DIM_MISMATCH`。`.bge_env/` 是 gitignored 本机产物；目录不存在时自动沿用主环境并降级本地 hash 投影（如实标注非语义向量）。
- Docker Desktop 启动被本机安全策略拦截（`wsl.exe` 黑名单），未重试或绕行；Redis / Neo4j 服务端未运行，继续走本地降级。
- Chrome 的 CDP 远程调试端口在本环境不监听，交互 E2E 用例优雅 SKIP；DOM 静态审计与渲染后 DOM 检查仍通过。
- **Python 环境会被应用升级重置**：`python` 指向 WorkBuddy 内置解释器（`3.13.12` 是软链，实际 3.13.14）。2026-11-16 升级后 `site-packages` 被清空，BFF 因缺 `cryptography` 无法启动。恢复方式：先 `python -m pip install -r requirements.txt`（核心可一次装），再对 `requirements-optional.txt` 中的包**逐个**执行 `python -m pip install --no-clean "<包名>"`——一次装多个、或让 pip 清理临时目录，都会触发沙箱的 `SAFE_DELETE_BULK_GUARD_ERROR`。BGE 权重在 `~/.cache/huggingface`、干净依赖栈在项目内 `.bge_env/`，两者都不受应用升级影响。彻底隔离建议改用项目级 venv，**尚未实施**。
- **爬取功能已整体移除**：`research_capture.py` / `capture_executor.py` / `production_capture.py` 及所有 `/api/capture/*` 执行端点已删除，不存在生产采集请求、站点书面许可回执驱动或限速契约。原 B2 回执留档但不再有对应执行能力；知识来源改为管理员文本 / 截图 OCR 文本。这是**当前设计状态，不是待修 bug**。

本项目文档中的合规判断均为工程侧过程控制，**不构成法律意见**，最终边界以法务书面结论为准。

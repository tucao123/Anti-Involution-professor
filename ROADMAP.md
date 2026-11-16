# 职引者 ZHIYIN · 剩余工作顺序（Phase 2 之后）

> 本文回答「其他按什么顺序做」。排序依据：**先做零新授权、可本地闭环、可独立验收的；
> 需要改变 C 级数据承诺或法务结论的，一律停在授权边界等确认；高风险与外部依赖放最后。**
> 截至 2026-11-15，Phase 2 的**适配器**已全部接入并通过 20/20 回归；「已接入」不等于「真服务已接管」，
> 每个组件的实际引擎以 `/api/*/status` 与 `/metrics` 为准。见 [`README.md`](README.md) 与 [`技术文档.md`](技术文档.md) §9。

## 排序原则

1. **不因赶进度放宽数据边界**：任何一项若要把 C 级原文落盘、发往云端或新增对外抓取，必须先有书面依据。
2. **可独立验收**：每项都配单元 + HTTP + 前端回归，不依赖后续项。
3. **复用已有适配器**：新功能优先接 LangGraph 编排、本地检索库与规则引擎，不新增外部强依赖。
4. **降级优先**：新功能在依赖不可用时也必须能退回现有能力，不允许出现空白页或假数据。

## 不需要你提供 API / 密钥的配置清单

「不需要 API」不等于「零工作量」，按**还需要什么**分成四类。✅ = 已生效，🟡 = 客户端就绪但缺本地服务，⬜ = 尚未做。

### 1. 纯 Python 包，`pip install` 即可，零密钥 —— 已补齐

| 项目 | 状态 | 说明 |
|---|---|---|
| `langgraph` 1.2.22 | ✅ 真 `StateGraph` 已接管 | 原先只有适配层代码、包没装，所以一直报 `compat-stdlib`；装好并修正 state schema 后 `/api/graph/status` 返回 `engine=langgraph` |
| `psycopg[binary]` 3.3.9 | ✅ 已连本机 PostgreSQL 18.7 | 库 / 角色 `zhiyin`，密码由外部 `key.env` 持有 |
| `celery` 5.7.0 | ✅ 已装，强制 eager 本地执行 | 跨进程 worker 还需 Redis 服务端；6 个白名单任务本地可跑 |
| `redis` 8.1.0（客户端） | 🟡 客户端就绪 | 缺 Redis 服务端 → 内存 TTL 降级 |
| `neo4j` 6.4.1（驱动） | 🟡 驱动就绪 | 缺 Neo4j 服务端 → 本地邻接表降级 |
| `qdrant-client` / `prometheus-client` | ⬜ 未装且**不需要** | 向量库走标准库 REST、指标走标准库文本输出，装了也不会改变行为 |

核心依赖仍只有 `requirements.txt` 的 4 个包；以上都在 `requirements-optional.txt`，全部缺失也能启动。

### 2. 纯代码 / 浏览器能力，零密钥零服务 —— 可直接做完

| 项目 | 状态 | 说明 |
|---|---|---|
| 浏览器本地 OCR（Tesseract.js） | ✅ 已做 | 图片仅内存、用完释放；CDN 可改为自托管 |
| A1 本地多轮对话辅导 | ✅ 已做 | 复用状态图 + 规则引擎，会话仅内存、原文不落盘、不外发 |
| A2 Web Speech API 语音输入 / 朗读 | ✅ 已做 | 服务端零音频上传、零新增上传端点；逐次授权、无麦克风权限时降级为文字；浏览器 ASR 可能由厂商云端完成、音频可能离开设备（如实告知，不承诺「音频不出设备」） |
| A4 RRF 混合检索与重排 | ✅ 已做 | 稀疏 TF-IDF + 稠密向量融合，纯算法、可离线验收 |
| A3 GitHub 公开仓库只读元数据 | ✅ 已做 | 公开 REST 无需 key（有匿名限频），但需先在 `sources.py` 登记来源并同步法务口径 |

### 3. 零密钥，但需要本地服务或大文件下载

| 项目 | 当前障碍 | 代价 |
|---|---|---|
| Redis / Neo4j / Qdrant **本地实例** | ❌ 被本机安全策略拦截 | `docker desktop start` 因 `wsl.exe` 位于程序黑名单而失败，未重试或绕行；编排文件仅静态校验 |
| BGE 本地语义向量 | ⚠️ 权重已下载、依赖栈待恢复 | torch 2.15 + `BAAI/bge-small-zh-v1.5` 512 维权重在缓存；2026-11-16 应用升级重置依赖后，`sentence-transformers` 6.3 所需的 `transformers>=5` 受沙箱删除保护无法装全，当前如实降级。普通终端一次 `pip install "sentence-transformers>=3.0"` 即可恢复 |
| Ollama 等本地 LLM | 需另装推理服务 + 模型 | 数 GB 下载；可作为 qwen 的离线替代，但会新增服务依赖 |

### 4. 必须等你提供东西（不在此清单内）

A 级境内数据商务合同与法务结论（B3）、简历上传授权文案（B1）、Qdrant 云写入的法务书面结论（Q18）、GitHub 公开元数据的法务书面结论（Q21）。qwen 的 key 已提供并实测可用，不再需要用户提供，但 Q19 书面结论仍未回。**网络可达、依赖装好都不等于获得数据出境授权。**

## 阶段 A：无需新授权，可直接推进

> **2026-11-15 已全部完成**：A1 / A2 / A3 / A4 及 qwen、BGE、LangGraph、PostgreSQL 接线均已落地，
> 见 [`README.md`](README.md)「Phase 3 新增能力」与 [`技术文档.md`](技术文档.md) §10。
> 下表已按最终如实口径校正（尤其 A2：浏览器 ASR 可能由厂商云端完成，不承诺音频不出设备），保留为核查清单。

> **回执闸门接线已完成（2026-11-16）**：`approval_gate.py` 实现编号 / 出具方 / 有效期 /
> 范围 + 注册表或配置双闸门，工程侧只读、无自我授权入口；`/api/approvals/status` 可实时核查。
> 实测临时移走 `Q18.json` 后云写入立即被拒。详见
> [`待书面结论事项与回执清单.md`](待书面结论事项与回执清单.md) §0 / §3 / §7。

| # | 项目 | 复用 | 产出 | 验收标准 |
|---|---|---|---|---|
| A1 | **本地多轮对话辅导** | `orchestration.py` 状态图、规则引擎、`user_jd` 技能本体 | `coach.py` + `/api/coach/chat\|delete\|status`；会话仅内存、原文不落盘、不外发；意图识别 + 护栏文案 | 多轮上下文保持；PII 前置脱敏；不承诺录用/不预测薪资/不制造焦虑；可撤回删除；离线可跑 |
| A2 | **语音 ASR / TTS（浏览器 Web Speech）** | 现有面试视图 | 用 Web Speech API 做语音输入与朗读；**服务端零新增上传端点、零音频上传**；逐次授权、不自动启动 | 无麦克风权限或 API 不支持时禁用按钮并优雅降级为文字输入；**如实告知浏览器 ASR 可能由厂商云端完成、音频可能离开设备**，不虚报「音频不出设备」；不引入自建云端 ASR |
| A3 | **GitHub 公开仓库元数据（只读）** | `sources.py` 闸门、`stores/graph.py` | 新登记 1 个 D 级来源 `github_public_repos`，只取公开仓库的语言/结构元数据；不读用户资料、私有仓库、代码内容，不绕登录态 | 注册表 + 法务口径同步（新增 Q21，书面结论待回）；匿名无 token、串行 ≥2 秒 / 每日 60 次、真实 UA、结果不落盘；明确标注「不构成能力评分」 |
| A4 | **混合检索与重排** | `rag_store.py` 稀疏 + `vector_store.py` 稠密 | RRF 融合、来源分级过滤、结果去重 | 检索质量对比用例；C 级数据仍只走本地稀疏链路 |

## 阶段 B：需要你先确认（停在这里等授权）

| # | 项目 | 为什么停 | 需要你提供 |
|---|---|---|---|
| B1 | ~~简历上传 / 文件解析~~ | ✅ 已完成（2026-11-16）：仅 pdf / docx 内存解析、不留存、只输出大致岗位信息、有撤回删除入口；云端解析需逐次单独同意 | 仍需产品负责人 + 法务确认告知文案；Q19 书面结论未回，`legal_review=pending` |
| B2 | ~~开启 qwen 云端叙述层~~ | ✅ 已完成（2026-11-15）：端点实测 200、`LLM_ENABLED=true`、`mode=live`；`qwen_gate()` 改为按端点域判定 | 仍需 Q19 书面结论：云端只收结构化技能字段，C 级原文零外发承诺不变 |
| B3 | **A 级境内招聘数据** | 商务合同 / 公共数据书面许可未回，`cn_market` 必须保持零数值 | BD 合同编号 + 法务对 `licensed_cn_platform` / `mohrss_public` 的书面结论 |

> **回执要求（2026-11-16，v1.1 更新）**：项目所有者已逐项决策，B1 / Q18 按工程开关启用
> （出具方为项目所有者、`legal_review=pending`）；Q19 / Q21 只是复述问题栏、保持 CLOSED；
> B3 未提及、保持关闭；需补条款见 `数据出境与公开元数据条款清单.md`。五项统一登记在
> [`待书面结论事项与回执清单.md`](待书面结论事项与回执清单.md)，每项含需要的一句话结论、
> 可直发的请求正文、回执 JSON 格式与启用清单。**对话里的口头确认、清单复述或一句「继续」
> 都不构成书面结论**；回执须带编号 / 出具方 / 日期 / 范围 / 有效期，落到 `.privacy/approvals/`
> 后才进入逐项启用流程。特别提示：Q19 是「已运行、待追认」，Q21 的 `approved` 是工程口径而非法律结论。

## 阶段 C：高风险或外部依赖，最后做

| # | 项目 | 前置条件 |
|---|---|---|
| C1 | 代码沙箱（在线编译 / 评测） | 容器隔离、资源配额、网络禁用、审计与超时；需独立安全评审 |
| C2 | 生产化（TLS / 鉴权 / 多租户 / 备份 / 灾备） | 明确部署形态与责任边界；目前服务刻意只绑 127.0.0.1 |
| C3 | 模型评测与成本治理 | qwen 可用后才有评测对象；需建立回归集、幻觉率与 Schema 通过率指标 |

## 需要你决定的三件事

1. **B1 产品侧授权文案与 Q19 法务结论**：B1 工程能力已按项目所有者决策开放（pdf / docx、不留存、可撤回删除、云端逐次单独同意），但产品告知文案与派生字段出境的合法性基础仍需书面确认。
2. **Q18 数据出境评估的法务书面结论**：工程开关已开启且发生真实境外写入（`backend=qdrant-cloud`），但结论未回；`ALLOW_CLOUD_FOR_USER_DATA=false` 不变。
3. **B3 A 级境内数据**：BD 合同编号 + 法务对 `licensed_cn_platform` / `mohrss_public` 的书面结论；`cn_market` 保持零数值。
   另：Q19（qwen 派生字段出境）、Q21（GitHub 公开元数据）两项书面结论待回，期间工程侧维持最小必要范围。

## 当前环境的已知限制（设计内，不是 bug）

- Qdrant Cloud 已按 Q18 回执开启写入，集合 `zhiyin_public_d` 已创建、`backend=qdrant-cloud` 实测成功；数据出境评估的法务书面结论仍未回，C 级用户数据仍禁止入云。
- LangGraph 真 `StateGraph`、本机 PostgreSQL 18.7、qwen（live）均已接管并复核；Celery 因无 Redis broker 强制 eager（设计内）。BGE 512 维语义向量**当前降级**为确定性 hash，原因见下条与上表。
- Redis / Neo4j 客户端与驱动已装，但**服务端**未运行：`docker desktop start` 被本机安全策略（`wsl.exe` 黑名单）拦截，未重试或绕行 → 内存 TTL / 本地邻接表降级。
- Chrome 可跑 `--dump-dom` / `--screenshot`，但 CDP 远程调试端口在本环境不监听 → 8 个交互 E2E 用例优雅 SKIP 并标注「browser not found」，DOM 静态审计与渲染后 DOM 检查仍通过。
- 8848 为 Phase 3 服务，`start.bat` 可直接启动；3000 控制台的 rewrite 目标已固化为 8848，改 BFF 端口需重新 `next build`。
- 真实凭据在项目根 `.env`（gitignored），`.env.example` 只保留空占位；`BGE_AUTOLOAD` 默认 false，避免健康检查触发权重下载。
- `QDRANT_ALLOW_WRITE=true` 已按 Q18 回执开启并发生真实境外写入；数据出境评估的法务书面结论仍未回，C 级用户数据仍禁止入云。
- 采集设置已移入管理员视图：默认不自动爬取、逐次手动开关；四重闸门未齐备时开关打开也不发起任何第三方请求，且拒绝「模拟人类节奏规避封禁」与「非商用豁免」口径。
- **全项目使用限制条款（2026-11-16 追加）**：`usage_policy.py` / `GET /api/project/usage`，条款 `USAGE-RESTRICTION-2026-11-16`——「本项目仅用于研究与学习，不得用于任何商业行为」。B2 中「毕设 / 非商用 / 实验」的含糊表述已改写为成文条款，并对 B1 / B2 / Q18 / Q19 / Q21 / B3 **同理适用**（5 份回执顶层 + `approval_gate.state().uniform_usage_restriction` + 文档 / 界面统一标注）。该条款**收紧用途但不改闸门**：`changes_gate_state=false`、`is_legal_exemption=false`，B1 / Q18 仍启用、Q19 / Q21 / B3 仍 CLOSED，自动采集执行体与拟人化规避仍禁用。B2 允许的是已有的本地 OCR → 仅岗位信息 → 本地 RAG → AI 合规替代链路。
- **B2 自担风险放行 + 严格限速（2026-11-16 再追加）**：所有者明确「由我自担风险直接放行 B2，但截图不能太快、必须严格控制速度模拟人类」。新增只读回执 `.privacy/approvals/B2.json`（`issuer=项目所有者`、`risk_bearing=project_owner`、`legal_review=pending`，不冒充法务结论 / 站点许可），放行**工程决策闸门**（`gates.owner_risk_release=true`、手动开启后 `decision_gate_released=true`）。「不能太快」升级为 `PACE_CONTRACT` + `enforce_pace()` 代码级硬契约：串行 1 / burst 1 / 每页 ≥ max(15s, 12s, Crawl-delay) / 每日 60 / 批次 10 / 冷却 300s / 真实 UA / 每页留痕，违反即抛 `PaceViolation`；新增 `GET /api/capture/pace`。「模拟人类」只落地为**人类可读的低频节奏**，规避检测式拟人化（伪装身份 / 抖动躲风控 / UA 轮换 / WAF 绕过）仍永久禁止。**截至本条时放行决策闸门不等于造出执行体**：`effective=false`、`requests_issued=0`、`capture_executor_implemented=false`、`legal_status=pending_legal` 不变；**下一条起已接入仅限练习沙盒的真实执行体**，但 `effective=false`、生产 `requests_issued=0`、`production_capture_unlocked=false` 与 `legal_status=pending_legal` 仍未放宽，默认仍不自动爬取、逐次手动开关，站点许可与法务结论仍未回。
- **截图测试目标白名单（同轮追加）**：所有者要求「拿允许爬取的测试网页测试，不要去招聘网站」。`is_allowed_test_target()` 默认只允许本机自建测试页 `GET /api/capture/test-page`（自述允许爬取、全为虚构演示文本、无真实个人信息），招聘域名关键词与非本地 host 代码层硬拒绝，`EXTRA_ALLOWED_TEST_TARGETS` 默认为空；`pace_test_plan()` 合并白名单与限速准入核算。独立脚本 `tests/zy_capture_pace_test.py`（不接入默认回归）只对该本地页截图，页间真实等待 15s、每页审计留痕、位图用完即弃，报告 `tests/out/capture_pace_report.json` 标注第三方请求 0 / 招聘站接触 0，`GET /api/capture/pace-test/status` 可读。**截至本条时这是本地自测，不是采集执行体**；下一条起已接入真实执行体。
- **Q18 出境防护 / 练习沙盒 / B2 真实执行体（同日第三轮追加）**：所有者确认已发生的境外写入不回滚、要求补齐 Qdrant 防护，指定 `books.toscrape.com` 并要求接入真实执行体。① `outbound_guard.py` 在双闸门后加端点官方域 / 集合 / 分级 D / 已批准来源 / 字段白名单 / 逐字段 PII / 体积批量上限，凭据与原文不回显、审计只写 digest；`GET /api/vector/guard`、dry-run `POST /api/vector/guard/check`（不联网不写入）。② `SANDBOX_SITES` 记录练习沙盒公开声明证据并显式加入白名单，`is_site_written_license=false`。③ `capture_executor.py` 真实 headless 截图：七道闸门、robots best-effort、页间真实 ≥15s、真实 UA、每页留痕、位图即弃、不解析文本 / 不 OCR / 不入 RAG；`GET /api/capture/executor/status`、`POST /api/capture/executor/run`。实跑 3 页、间隔 `[22.67, 25.72]s`、robots 404、外部沙盒请求 4、生产请求 0、招聘站接触 0、位图 `none`。**生产采集闸门一格未放**：`production_capture_unlocked=false`、`effective=false`、生产 `requests_issued=0`、`legal_status=pending_legal`；站点书面许可函与法务结论、Q18 数据出境评估仍待回。
- **Python 依赖不属于「一次装好」**：WorkBuddy 内置解释器随应用升级被替换，2026-11-16 曾清空 `site-packages` 导致 BFF 启动失败。已恢复：核心依赖一次装完，可选依赖逐包 + `--no-clean` 安装（一次装多个会触发沙箱批量删除保护）。若要把这个脆弱点彻底关掉，可增加一项零密钥工作：建项目级 `.venv` 并让 `start.bat` / `tests/run_all.py` 优先使用它，避免共享解释器被外部升级冲掉。

- **第四轮（2026-11-16 17:24）**：所有者「境外写入允许」「恢复 BGE」「接入 B2 生产执行体」。① `Q18.json` 记入 `owner_confirmation`（维持 `QDRANT_ALLOW_WRITE=true`、不回滚、不关闭双闸门，`issuer=项目所有者`、`risk_bearing=project_owner`、`legal_review=pending`），`approval_gate` 暴露确认摘要；`outbound_guard` 一格未放宽，**数据出境评估仍待回**。② BGE 用 `.bge_env/` 独立依赖目录（transformers 4.49.0 / ST 3.4.1 等）+ `embedding.py` 的 `sys.path` 前置恢复，实测 `bge_model_source=local_hub_snapshot`（官方 CLS pooling）、`selected_backend.user=bge`、512 维语义向量、`cloud_used=false`；`public` 为与既有 1024 维 Qdrant 集合一致仍优先 DashScope，`ensure_collection()` 增维度冲突硬拒绝；目录 gitignored、缺失时自动退回原降级链路。③ `production_capture.py` 接入**生产能力 + 九道闸门**，目标只由第三方站点书面许可回执 `.privacy/site_licenses/*.json` 驱动，并须法务书面结论 `B2-legal.json` + 注册表 `approved`；`GET /api/capture/production/status` 只读，`tests/zy_production_capture_test.py` 43 项接入 `run_all.py`（不联网）。**能力已接入 ≠ 已解锁**：两份材料未回、`pending_legal`，故 `production_capture_unlocked=false`、生产请求 0；不假造许可、不指向无许可招聘站、禁止规避检测。§4 待回结论一项未减少。

本项目所有合规判断均为工程侧过程控制，**不构成法律意见**，最终边界以法务书面结论为准。

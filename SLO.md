# 职引者 ZHIYIN · 部署与 SLO

> 本文是工程运维约定，不构成法律意见。合规闸门的目标恒为 1.0，
> **不允许用错误预算换取数据边界放宽**。

## 1. SLO 目标（30 天滚动窗口）

| 目标 | 指标 | 目标值 | 错误预算 |
| --- | --- | --- | --- |
| 可用性 | 非 5xx 响应 / 全部 HTTP 响应 | ≥ 99.5% | 约 216 分钟 / 30d |
| 延迟 | `/api/*` 请求耗时 P95 | ≤ 800 ms | P95 持续超标即计消耗 |
| 输出校验 | 模型输出通过 Pydantic JSON Schema 校验的比例 | ≥ 99% | 校验失败即降级，不渲染未校验内容 |
| 合规闸门 | C 级数据外发 / 未批准来源绕过次数 | **0** | 零容忍，无错误预算 |

定义文件：`deploy/slo.yaml`；运行时视图：`GET /api/slo`；指标：`GET /metrics`。

## 2. 范围内外

**范围内**：FastAPI BFF 的 `/api/*` 与 `/metrics`、Next.js 控制台 `/` 与 `/health`、
三库 / Celery / Qdrant 适配器的可用性与降级状态。

**范围外**（不计入错误预算，但必须自动降级）：

- Jobicy / RemoteOK 公开 API 不可用 → 前端切回内置演示集，不编造数值；
- Qdrant Cloud 已按 Q18 回执 + 双闸门开启云写入（实测 `backend=qdrant-cloud`）；法务书面结论仍待回，C 级用户数据仍禁止入云，Qdrant 不可达时仍降级为本地进程内向量索引（不落盘、重启清空）；
- qwen 未配置 / 未启用 / 输出未过校验 → 离线规则引擎；
- BGE / DashScope embedding 不可用 → 确定性字符 n-gram 投影，并如实标注「非语义向量」。

## 3. 降级策略（硬性要求）

| 组件 | 真服务 | 降级实现 | 状态可观测 |
| --- | --- | --- | --- |
| 向量库 | Qdrant Cloud | 本地内存索引 | `/api/vector/status` |
| Embedding | 本地 BGE（512 维）/ DashScope | 本地 hash 投影 | `/api/embedding/status` |
| 关系库 | PostgreSQL | SQLite（`.stores/`，gitignored） | `/api/stores/status` |
| 缓存 / Broker | Redis | 进程内 TTL 字典 | `/api/stores/status` |
| 图库 | Neo4j | 本地邻接表 | `/api/stores/status` |
| 编排 | LangGraph | 同语义标准库状态图 | `/api/graph/status` |
| 异步任务 | Celery + Redis | eager 同步执行 | `/api/tasks/status` |
| 语言模型 | qwen | 规则引擎 | `/api/llm/status` |
| 前端 | Next.js 控制台 | 直接访问 `index.html` | 两者共用同一 BFF |

**任一外部依赖缺失都不得让既有 7 个视图失效**；降级必须在状态接口与
`zhiyin_component_up` 指标中如实标注，不把降级实现冒充为已连接的真服务。

## 4. 指标清单（纯标准库输出，无 prometheus-client 依赖）

| 指标 | 类型 | 说明 |
| --- | --- | --- |
| `zhiyin_http_requests_total{method,path,status}` | counter | 路径已归一化，标签低基数 |
| `zhiyin_http_request_duration_seconds_bucket{path,le}` | histogram | 桶：10ms→10s |
| `zhiyin_http_5xx_total{path}` | counter | 可用性 SLI |
| `zhiyin_gate_rejections_total{path,code}` | counter | 400/403 拒绝，多数是预期合规拦截 |
| `zhiyin_component_up{component}` | gauge | 1=真服务已连接，0=本地降级 |
| `zhiyin_build_info` / `zhiyin_process_uptime_seconds` | gauge | 版本与进程信息 |

指标不记录请求体、响应体、请求头、查询串、凭据或任何用户文本。

## 5. 告警规则

`deploy/alerts.yml`，由 Prometheus 每 15s 评估：

| 告警 | 级别 | 触发条件 |
| --- | --- | --- |
| `ZHIYINBFFDown` | critical | `up{job="zhiyin-bff"} == 0` 持续 1m |
| `ZHIYINHigh5xxRate` | warning | 5m 窗口 5xx > 1%，持续 5m |
| `ZHIYINErrorBudgetFastBurn` | critical | 1h 窗口 5xx > 5%，持续 10m |
| `ZHIYINLatencyP95High` | warning | P95 > 800ms 持续 10m |
| `ZHIYINGateRejectionSpike` | info | 闸门拒绝速率上升，需人工复盘是否有绕过尝试 |
| `ZHIYINComponentDegraded` | info | 组件处于降级状态超过 15m（设计内行为，不算故障） |

## 6. 部署

```bash
# 1. 配置（.env 不入库；容器不能读个人桌面上的 key.env）
cp .env.example .env

# 2. 同步既有单页到 Next.js public/legacy/
npm --prefix web run sync-legacy      # Windows CMD 若 --prefix 失效：cd web && node scripts/sync-legacy.mjs

# 3. 启动核心 + 三库
docker compose up -d

# 4. 可选：独立 Celery worker / 监控栈
docker compose --profile celery up -d
docker compose --profile monitoring up -d
```

| 服务 | 地址 | 说明 |
| --- | --- | --- |
| BFF | `http://127.0.0.1:8848` | 容器内监听 0.0.0.0，宿主机仅映射 127.0.0.1 |
| 控制台 | `http://127.0.0.1:3000` | `/legacy/index.html` 为完整应用 |
| Prometheus | `http://127.0.0.1:9090` | `--profile monitoring` |
| Grafana | `http://127.0.0.1:3001` | `--profile monitoring` |
| Neo4j Browser | `http://127.0.0.1:7474` | 开发用 |

本机零容器启动（默认全降级，功能完整）：`python server.py` 或 `start.bat`。

## 7. 凭据与数据边界

- 凭据优先级：进程环境变量 > `--env-file` / `ZHIYIN_ENV_FILE` > 项目 `.env` >
  `.zhiyin_key_path` 指向的外部文件；密钥只在服务端进程内存。
- `/api/config` 只返回「是否配置 / 来源类别 / 形态」，URL 形态会隐藏云实例主机名。
- C 级用户自带数据默认不出本机；云向量库只收 D 级公开岗位样本。截图 / 本地 OCR 链路的隐私信息整行剔除、不入库；B1 文件与原文只在内存、不留存；个人画像走本地 `/api/jd/analyze`。
- D 级样本为全球远程岗，**不代表中国境内招聘市场**；国内市场洞察仍为待授权、零数值。
- **项目性质与使用限制**（`USAGE-RESTRICTION-2026-11-16`，`GET /api/project/usage`）：「本项目仅用于研究与学习，不得用于任何商业行为」，对 B1 / B2 / Q18 / Q19 / Q21 / B3 统一适用；收紧用途、禁止商业化与对外分发，**不是合规豁免**、不改变回执闸门状态。该条款的可观测性端点为只读 GET，不产生任何数据写入。
- **B2 放行与限速可观测性**（`GET /api/capture/pace`、`GET /api/capture/admin`）：`owner_risk_release.released`、`decision_gate_released`、`strict_pace_contract`、`effective`、`requests_issued` 五个状态位必须如实上报；**执行体存在不等于生产采集解锁**，`effective=false` / 生产 `requests_issued=0` / `production_capture_unlocked=false` 是设计内状态，实际练习沙盒请求另以 `sandbox_test_requests_total` 如实记录，不得把沙盒实跑冒充为生产采集。限速契约（每页 ≥ 15s、串行 1、每日 60、批次 10、冷却 300s）由 `enforce_pace()` 在准入前强制，违反即拒绝。
- **截图测试目标白名单**：本地自建测试页 `GET /api/capture/test-page` 与经核实的练习沙盒 `books.toscrape.com`（`SANDBOX_SITES` 附公开声明证据、`is_site_written_license=false`）是仅有的两类允许目标；招聘域名关键词、任何其他 host、query 注入、非 https 由 `is_allowed_test_target()` 代码层硬拒绝。`GET /api/capture/pace-test/status` 与 `GET /api/capture/executor/status` 只读展示报告，须如实标注第三方生产请求 0 / 招聘站接触 0 / `bitmap_retention=none`；实跑脚本**不接入默认回归**，避免真实等待拖慢套件。
- **Q18 出境写入防护可观测性**（`GET /api/vector/guard`）：`endpoint.ok`、`collection`、`allowed_grades`、`allowed_source_ids`、`payload_key_allowlist`、`limits`、`api_key_never_returned`、`audit_content=digest_only`、`network_called_by_this_module=false` 必须如实上报；`POST /api/vector/guard/check` 为 dry-run，SLO 要求其 `network_called=false` 且 `data_written=false`。任何出站请求前必须过 `validate_endpoint()`，写入前必须过 `validate_outbound()`，禁止以本地降级绕过校验。
- **B2 执行体可观测性**（`GET /api/capture/executor/status`）：`policy.scope=test_sandbox_only`、`production_capture_unlocked=false`、`requests_issued=0` 恒定；`report_present` / `last_run_ok` / `last_intervals_s` / `sandbox_requests_total` / `bitmap_retention` 如实反映最近一次练习沙盒实跑。`POST /api/capture/executor/run` 必须要求逐次手动开关 + `operator_ack`，目标由服务端白名单生成，跑完自动关闭开关。

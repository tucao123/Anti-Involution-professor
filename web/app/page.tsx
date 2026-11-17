import Link from 'next/link';

export const dynamic = 'force-dynamic';

const BFF = process.env.ZHIYIN_BFF_URL || 'http://127.0.0.1:8848';

async function safeGet(path: string): Promise<any | null> {
  try {
    const r = await fetch(`${BFF}${path}`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(3000),
    });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

type Card = { title: string; value: string; note: string; warn?: boolean };

export default async function Page() {
  const [health, stores, tasks, vector, embed, llm, graph, config, phase3,
         approvals, resumeSt, knowledge, usage, guard] = await Promise.all([
    safeGet('/api/health'),
    safeGet('/api/stores/status'),
    safeGet('/api/tasks/status'),
    safeGet('/api/vector/status'),
    safeGet('/api/embedding/status'),
    safeGet('/api/llm/status'),
    safeGet('/api/graph/status'),
    safeGet('/api/config'),
    safeGet('/api/phase3/status'),
    safeGet('/api/approvals/status'),
    safeGet('/api/resume/status'),
    safeGet('/api/admin/knowledge/status'),
    safeGet('/api/project/usage'),
    safeGet('/api/vector/guard'),
  ]);

  const bffOnline = Boolean(health?.ok);
  const cards: Card[] = [
    {
      title: 'FastAPI BFF',
      value: bffOnline ? `在线 · Phase ${health.phase}` : '未连接',
      note: bffOnline ? `仅绑定 ${'127.0.0.1'} · 静态白名单 · fail-closed 闸门` : '请先运行 python server.py 或 start.bat',
      warn: !bffOnline,
    },
    {
      title: '语言模型',
      value: llm ? `${llm.provider} / ${llm.mode}` : '未连接',
      note: llm?.gate?.reason || 'qwen 未配置时自动降级为离线规则引擎',
      warn: !llm?.configured,
    },
    {
      title: 'Embedding',
      value: embed?.selected_backend?.public || '未连接',
      note: embed?.bge_available
        ? `BGE 本地 512 维语义向量已恢复 · ${embed.bge_runtime || '独立依赖目录'} · 用户侧向量不外发云端`
        : (embed?.bge_reason || '本地语义模型未就绪；按配置降级，后端如实标注'),
      warn: !embed?.bge_available,
    },
    {
      title: 'Qdrant 向量库',
      value: vector?.backend_in_use || '未连接',
      note: vector?.qdrant?.reachable
        ? `已连通 · 云写入${vector.cloud_writes_enabled ? '开启（Q18 所有者回执，legal_review=pending）' : '关闭(默认)'} · 出站六道硬校验`
        : `云实例当前不可达（${vector?.qdrant?.error || '未探测'}），自动降级本地内存索引`,
      warn: !vector?.qdrant?.reachable,
    },
    {
      title: 'Q18 出境写入防护',
      value: guard?.guard_id ? '已生效 · 六道出站校验' : '未连接',
      note: guard
        ? `端点目标锁定 ${guard.endpoint?.ok ? '通过' : '拒绝'} · 集合 ${guard.collection?.collection || '-'} · 分级 ${(guard.allowed_grades || []).join('/')} · 字段白名单 ${(guard.payload_key_allowlist || []).length} 项 · PII 命中整批拒绝 · 审计仅 digest · API key 永不返回`
        : '未连接到 /api/vector/guard',
      warn: !guard,
    },
    {
      title: '关系库 PostgreSQL',
      value: stores?.postgresql?.backend || '未连接',
      note: stores?.postgresql?.fallback_reason || 'DDL 见 deploy/sql/001_init.sql',
      warn: stores?.postgresql?.backend !== 'postgresql',
    },
    {
      title: '缓存 / Broker Redis',
      value: stores?.redis?.backend || '未连接',
      note: stores?.redis?.fallback_reason || '同时作为 Celery broker 与 result backend',
      warn: stores?.redis?.backend !== 'redis',
    },
    {
      title: '图库 Neo4j',
      value: stores?.neo4j?.backend || '未连接',
      note: stores?.neo4j?.fallback_reason || 'Cypher 见 deploy/cypher/001_init.cypher',
      warn: stores?.neo4j?.backend !== 'neo4j',
    },
    {
      title: '编排 LangGraph',
      value: graph?.engine || '未连接',
      note: graph?.langgraph_installed
        ? '真 LangGraph StateGraph 已启用'
        : 'langgraph 未安装，使用同语义标准库状态图（节点 / 边 / 条件分支一致）',
      warn: !graph?.langgraph_installed,
    },
    {
      title: '异步任务 Celery',
      value: tasks?.engine || '未连接',
      note: tasks?.eager_reason || `已注册 ${tasks?.registered_tasks?.length ?? 0} 个任务`,
      warn: Boolean(tasks?.eager),
    },
    {
      title: '数据闸门',
      value: health?.source_gate || 'fail-closed',
      note: health?.insight_modes?.join(' · ') || '仅 A 级境内授权数据可解锁国内市场洞察',
    },
    {
      title: 'Phase 3 · A1 多轮辅导',
      value: phase3?.A1_coach?.ready ? '已就绪 · 本地规则' : '未连接',
      note: '会话仅进程内存 · 用户原文零落盘 · 零云调用 · 可撤回 · 固定护栏（不承诺录用 / 不预测薪资 / 不制造焦虑）',
      warn: !phase3?.A1_coach?.ready,
    },
    {
      title: 'Phase 3 · A2 浏览器语音',
      value: phase3?.A2_voice?.ready ? '已启用 · 逐次授权' : '未连接',
      note: '服务端零音频上传、无上传端点；浏览器 ASR 可能由厂商云端完成，音频可能离开设备（已在应用内如实告知）',
      warn: !phase3?.A2_voice?.ready,
    },
    {
      title: 'Phase 3 · A3 公开仓库元数据',
      value: phase3?.A3_github?.ready ? '已就绪 · 匿名只读' : '未连接',
      note: '单仓库串行 ≥2s / 每日 60 次 · 结果不落盘 · 不构成能力评分 · 法务 Q21 书面结论待回',
      warn: !phase3?.A3_github?.ready,
    },
    {
      title: 'Phase 3 · A4 混合检索',
      value: phase3?.A4_hybrid?.ready ? '已就绪 · RRF(k=60)' : '未连接',
      note: '稀疏 + 稠密双路融合 · 来源分级过滤 · 去重；C 级用户文本只走本地稀疏路且默认不返回',
      warn: !phase3?.A4_hybrid?.ready,
    },
    {
      title: '项目性质与使用限制',
      value: usage?.policy?.commercial_use === false ? '研究学习 · 禁止商业行为' : '未连接',
      note: `USAGE-RESTRICTION-2026-11-16 · 对 5 项待决事项同理适用（${approvals?.uniform_usage_restriction ? '5/5' : '未确认'}）· 收紧用途但 is_legal_exemption=false、changes_gate_state=false`,
      warn: !usage?.ok,
    },
    {
      title: '书面结论回执闸门',
      value: approvals ? `已启用 ${(approvals.enabled || []).length} / ${Object.keys(approvals.items || {}).length}` : '未连接',
      note: `B1 / Q18 依项目所有者决策启用（legal_review=pending，不等于法务结论）；Q19 / Q21 / B3 保持 CLOSED · 代码只读、无自我授权入口`,
      warn: !approvals,
    },
    {
      title: 'B1 文件解析',
      value: resumeSt?.resume_parse_open ? '已开放 · 仅岗位信息' : '未开放',
      note: '仅 pdf / docx · 文件与原文只在内存、不留存 · 撤回删除入口齐备 · 云端需逐次单独同意 · 个人画像走本地 /api/jd/analyze',
      warn: !resumeSt?.resume_parse_open,
    },
    {
      title: '管理员知识入库',
      value: knowledge?.ok ? `${knowledge.source_id || 'admin_curated'} · ${knowledge.grade || 'D'} 级` : '未连接',
      note: knowledge
        ? `自动爬取 / 截图执行体已移除，系统不访问任何第三方页面 · 知识只由管理员提供文本或截图（${knowledge.ocr_location || '浏览器本地 OCR'}）· PII 脱敏 + 仅岗位信息 · 服务端只收文本、位图不上传`
        : '未连接到 /api/admin/knowledge/status',
      warn: !knowledge,
    },
    {
      title: '知识向量库（Qdrant）',
      value: knowledge?.cloud_writes_enabled ? '云写入开启 · 可按 id 删除' : '云写入关闭 / 未连接',
      note: knowledge
        ? `集合 ${knowledge.collection || '-'} · 向量后端 ${knowledge.backend_in_use || '-'} · 出站防护按目标/集合/分级/来源/PII 逐字段锁定 · D 级知识只作研究参考`
        : '未连接到 /api/admin/knowledge/status',
      warn: !knowledge,
    },
  ];

  return (
    <main className="wrap">
      <header>
        <div className="eyebrow">ZHIYIN / PHASE 3 + DECISIONS CONSOLE</div>
        <h1>职引者 · 组件控制台</h1>
        <p className="sub">
          这一页只做状态观测，包含 Phase 3 的 A1–A4 与决策落地（回执闸门 / B1 文件解析 /
          管理员知识入库 / 项目性质与使用限制条款 / Q18 出境写入防护 / BGE 恢复）。
          自动爬取与截图执行体已整体移除，知识只由管理员提供文本或截图（浏览器本地 OCR）。
          面向用户的主入口是 <b>JTBD v2.0 决策工作台</b>（方向 → 核对 → 行动 → 演练 → 看板）；既有 8 视图单页保留为 legacy，仅供历史回放与 E2E 契约，二者共用同一个 FastAPI BFF。
        </p>
        <p className="meta">
          凭据来源：{config?.file_source || '未知'} · 密钥仅在服务端进程内存 · 页面不接触任何密钥
        </p>
      </header>

      <div className="actions">
        <Link className="btn btn-primary" href="/workbench.html">打开决策工作台 →</Link>
        <Link className="btn btn-ghost" href="/login.html">登录（微信扫码 / 管理员密码）</Link>
        <Link className="btn btn-ghost" href="/legacy/index.html">旧版视图</Link>
        <Link className="btn btn-ghost" href="/health">查看健康详情</Link>
        <a className="btn btn-ghost" href="/metrics">Prometheus 指标</a>
      </div>

      <h2>组件状态</h2>
      <div className="grid">
        {cards.map((c) => (
          <div className="card" key={c.title}>
            <h3>{c.title}</h3>
            <span className={`tag${c.warn ? ' warn' : ''}`}>{c.warn ? '降级 / 待配置' : '正常'}</span>
            <div className="value">{c.value}</div>
            <div className="note">{c.note}</div>
          </div>
        ))}
      </div>

      <div className="notice">
        <strong>数据边界（不因架构升级而放宽）</strong>
        <ul>
          <li><b>JTBD 决策工作台</b>（<code>/workbench.html</code>）：C 级 JD / 经历文本只在本次请求内存里推导，零落盘、零外发、不进 RAG / 向量库；硬条件单独阻断、无百分比总分、「没提供」一律标未知，结论限定在你提供的 JD 内。看板与材料库只存浏览器 localStorage，由用户自己保存 / 删除。</li>
          <li>C 级用户自带 JD / 简历 / 本地 OCR 文本：原文零落盘、PII 前置脱敏、可撤回删除，默认不出本机。</li>
          <li>云向量库只收 D 级公开岗位样本；Q18 写入依据项目所有者回执开启（<code>legal_review=pending</code>），叠加配置 + 回执双闸门与 <code>outbound_guard.py</code> 出站硬校验：端点官方域 / 集合名 / 分级 D / 已批准来源 / 字段白名单 / 逐字段 PII / 体积批量上限，凭据与原文不回显。</li>
          <li>D 级样本为全球远程岗，<b>不代表中国境内招聘市场</b>；国内市场洞察仍为 pending_license、零数值。</li>
          <li><b>全项目条款：本项目仅用于研究与学习，不得用于任何商业行为。</b>对 B1 / B2 / Q18 / Q19 / Q21 / B3 同理适用，禁止商业化、对外分发与商业决策用途；该条款只收紧用途，不构成合规豁免、不改变任何回执闸门状态，也不减少待回的书面结论。</li>
          <li><b>自动爬取与截图执行体已整体移除</b>：<code>research_capture.py</code> / <code>capture_executor.py</code> / <code>production_capture.py</code> 与所有 <code>/api/capture/*</code> 执行端点全部删除，系统不再访问任何第三方页面、不再有生产采集请求。原 B2 放行 / 限速契约 / 测试目标白名单随执行体一并移除；使用限制条款仍对全部事项统一适用，不因移除而改变任何回执状态。</li>
          <li><b>知识只由管理员提供</b>：在决策工作台 <code>/workbench.html</code> 的「知识入库」面板粘贴文本，或上传截图由 <b>Tesseract.js 浏览器本地 OCR</b> 识别成文本；位图只在浏览器内存处理，<b>不上传、不落盘</b>，服务端自始至终只接收文本。<code>/api/admin/knowledge/*</code> 按管理员角色鉴权。</li>
          <li><b>入库链路固定为「PII 脱敏 → 仅岗位信息 → 切片 → 向量化 → Qdrant」</b>：无论文本还是 OCR 链路都先脱敏再按岗位语境过滤，个人信息整行剔除；写入经 <code>outbound_guard</code> 逐字段 PII / 分级 D / 来源白名单硬校验，可按入库返回的 chunk id 删除。D 级知识只作研究参考，不产出市场级或个人画像结论。数据出境评估与法务书面结论仍待回。</li>
          <li>qwen 已启用（mode=live）但 <code>ALLOW_CLOUD_FOR_USER_DATA=false</code>：云端只收结构化技能字段，C 级原文前置拦截；Q19 / Q21 书面结论仍待回。Q18 所有者已明确<b>维持境外写入允许、不回滚</b>（回执 <code>owner_confirmation</code>，<code>legal_review=pending</code>），出站防护一格未放宽，数据出境评估仍待回。<b>BGE 本地语义向量已恢复</b>：独立依赖目录 <code>.bge_env</code>（transformers 4.49.0 / ST 3.4.1）避开主环境混合损坏并按官方 modules.json 加载 CLS pooling；user 侧为 512 维本地语义向量、零云端调用，public 侧为与既有 1024 维 Qdrant 集合保持一致仍优先 DashScope（集合维度不符时明确拒绝写入，不静默降级）。</li>
          <li>语音：服务端零音频上传、无音频上传端点；<b>浏览器 ASR 可能由厂商云端完成</b>，音频可能离开设备，不承诺「音频不出设备」。</li>
        </ul>
      </div>

      <h2>端口与代理</h2>
      <table>
        <tbody>
          <tr><th>控制台</th><td><code>http://127.0.0.1:3000</code>（Next.js）</td></tr>
          <tr><th>BFF</th><td><code>http://127.0.0.1:8848</code>（FastAPI，仅本机）</td></tr>
          <tr><th>代理规则</th><td><code>/api/*</code>、<code>/legacy/api/*</code>、<code>/metrics</code> → BFF</td></tr>
          <tr><th>环境变量</th><td><code>ZHIYIN_BFF_URL</code>（容器内设为 <code>http://api:8848</code>）</td></tr>
        </tbody>
      </table>

      <footer>
        所有新增组件均为「可选依赖 + 本地降级」。任一服务缺失都不会让既有 7 个视图失效；
        本页如实标注实际后端，不把降级实现冒充为已连接的真服务。
      </footer>
    </main>
  );
}

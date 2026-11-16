import Link from 'next/link';

export const dynamic = 'force-dynamic';

const BFF = process.env.ZHIYIN_BFF_URL || 'http://127.0.0.1:8848';

async function probe(path: string): Promise<{ status: number; body: string }> {
  try {
    const r = await fetch(`${BFF}${path}`, { cache: 'no-store', signal: AbortSignal.timeout(3000) });
    const text = await r.text();
    return { status: r.status, body: text.slice(0, 260) };
  } catch (e) {
    return { status: 0, body: e instanceof Error ? e.name : 'unreachable' };
  }
}

const PATHS = [
  '/api/health', '/api/config', '/api/schema', '/api/sources',
  '/api/insight/modes', '/api/capture/policy', '/api/research/policy',
  '/api/embedding/status', '/api/vector/status', '/api/vector/probe',
  '/api/llm/status', '/api/graph/status', '/api/stores/status',
  '/api/tasks/status', '/api/rag/status', '/api/jd/status',
  '/api/privacy/status', '/api/slo', '/metrics',
  '/api/phase3/status', '/api/coach/status', '/api/voice/policy',
  '/api/github/status', '/api/hybrid/status',
  '/api/approvals/status', '/api/resume/status', '/api/resume/consent',
  '/api/capture/admin', '/api/capture/pace', '/api/capture/test-page',
  '/api/capture/pace-test/status', '/api/capture/executor/status',
  '/api/capture/production/status',
  '/api/vector/guard', '/api/project/usage',
];

export default async function HealthPage() {
  const results = await Promise.all(PATHS.map(async (p) => ({ path: p, ...(await probe(p)) })));
  const ok = results.filter((r) => r.status === 200).length;

  return (
    <main className="wrap">
      <header>
        <div className="eyebrow">BFF HEALTH CHECK</div>
        <h1>接口连通性</h1>
        <p className="sub">
          逐一探测 FastAPI BFF 的端点。503 表示对应可选组件尚未加载（不影响核心功能），
          0 表示 BFF 未启动或网络不可达。
        </p>
        <p className="meta">{ok} / {results.length} 个端点返回 200 · 目标 <code>{BFF}</code></p>
      </header>
      <div className="actions">
        <Link className="btn btn-ghost" href="/">← 返回控制台</Link>
      </div>
      <table>
        <thead><tr><th>端点</th><th>状态</th><th>响应摘要</th></tr></thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.path}>
              <td><code>{r.path}</code></td>
              <td>{r.status === 200 ? '200 OK' : r.status === 0 ? '不可达' : String(r.status)}</td>
              <td style={{ color: '#5b6875' }}>{r.body}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <footer>密钥值永不出现在任何响应中；/api/config 只返回「是否配置 / 来源类别 / 形态」。</footer>
    </main>
  );
}

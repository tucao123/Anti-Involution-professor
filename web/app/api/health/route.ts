import { NextResponse } from 'next/server';

export const dynamic = 'force-dynamic';

const BFF = process.env.ZHIYIN_BFF_URL || 'http://127.0.0.1:8848';

/** 部署探针：只报告控制台自身与 BFF 是否可达，不返回任何凭据或上游响应体。 */
export async function GET() {
  const started = Date.now();
  try {
    const r = await fetch(`${BFF}/api/health`, {
      cache: 'no-store',
      signal: AbortSignal.timeout(3000),
    });
    const data = (await r.json()) as Record<string, unknown>;
    return NextResponse.json(
      {
        ok: r.ok,
        web: 'ok',
        bff_reachable: true,
        bff_phase: data.phase ?? null,
        bff_mode: data.mode ?? null,
        latency_ms: Date.now() - started,
      },
      { status: r.ok ? 200 : 503, headers: { 'Cache-Control': 'no-store' } },
    );
  } catch {
    return NextResponse.json(
      { ok: false, web: 'ok', bff_reachable: false, latency_ms: Date.now() - started },
      { status: 503, headers: { 'Cache-Control': 'no-store' } },
    );
  }
}

/** @type {import('next').NextConfig} */
// 注意：rewrites 的 destination 会在 `next build` 时固化进 .next/routes-manifest.json。
// 若 BFF 不在默认端口，请在构建前设置 ZHIYIN_BFF_URL（Dockerfile 已通过 build-arg 处理）。
// 运行时修改该变量只影响服务端组件，不会改变 rewrite 目标。
const BFF = process.env.ZHIYIN_BFF_URL || 'http://127.0.0.1:8848';

const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      // 控制台自身调用 BFF
      { source: '/api/:path*', destination: `${BFF}/api/:path*` },
      // 既有单页放在 /legacy/index.html 下，其 fetch('api/xxx') 会解析成 /legacy/api/xxx，
      // 这条 rewrite 让它零改动继续工作（无需修改 200KB 的 index.html）。
      { source: '/legacy/api/:path*', destination: `${BFF}/api/:path*` },
      // 指标与 SLO
      { source: '/metrics', destination: `${BFF}/metrics` },
    ];
  },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'no-referrer' },
          { key: 'X-Frame-Options', value: 'SAMEORIGIN' },
        ],
      },
    ];
  },
};

export default nextConfig;

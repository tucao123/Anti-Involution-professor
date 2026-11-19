import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '职引者 ZHIYIN · 组件控制台',
  description: 'Phase 2 组件状态与数据边界；完整应用在 /workbench.html',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

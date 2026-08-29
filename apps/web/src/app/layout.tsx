import type { Metadata } from "next";
import { AntdRegistry } from "@ant-design/nextjs-registry";
import { Providers } from "@/components/providers";
import "antd/dist/reset.css";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "仓库品类分析决策工具",
    template: "%s · 仓库品类分析决策工具",
  },
  description:
    "本地优先的库存品类分析工作台：看清库存、识别动销与积压、辅助补货决策。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>
        <AntdRegistry>
          <Providers>{children}</Providers>
        </AntdRegistry>
      </body>
    </html>
  );
}

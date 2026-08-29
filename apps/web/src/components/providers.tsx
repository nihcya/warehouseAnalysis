"use client";

import "@ant-design/v5-patch-for-react-19";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp, ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { useState, type ReactNode } from "react";

// 视觉基线（spec §42.2）：主色 #173B5F、背景 #F4F7FA、圆角 ≤ 6px
const themeConfig = {
  token: {
    colorPrimary: "#173B5F",
    colorBgLayout: "#F4F7FA",
    borderRadius: 6,
  },
};

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <ConfigProvider theme={themeConfig} locale={zhCN}>
        <AntApp>{children}</AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  );
}

"use client";

// antd 组件需在客户端边界内渲染（React 19 兼容补丁仅在 client bundle 生效）
import { Button, Typography } from "antd";
import Link from "next/link";

const { Paragraph, Title } = Typography;

export default function HomePage() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 24,
        padding: 24,
        textAlign: "center",
      }}
    >
      <Title level={1} style={{ marginBottom: 0 }}>
        仓库品类分析决策工具
      </Title>
      <Paragraph style={{ fontSize: 18, maxWidth: 560 }}>
        本地优先的库存品类分析工作台：看清库存、识别动销与积压、算准补货，
        数据留在商户自己的电脑上。
      </Paragraph>
      <Link href="/login">
        <Button type="primary" size="large">
          进入商户工作台
        </Button>
      </Link>
    </main>
  );
}

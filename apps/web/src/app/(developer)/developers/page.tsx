"use client";

// antd 组件需在客户端边界内渲染（React 19 兼容补丁仅在 client bundle 生效）
import { Card, Typography } from "antd";

const { Paragraph, Title } = Typography;

export default function DevelopersPage() {
  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 24,
      }}
    >
      <Card style={{ width: 480 }}>
        <Title level={3}>开发者门户</Title>
        <Paragraph type="secondary">
          M0 占位页：商户授权、许可证、配置、设备状态与技术日志的管理入口将在后续里程碑开放。
        </Paragraph>
      </Card>
    </main>
  );
}

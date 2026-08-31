"use client";

import { ApiRequestError, authApi, clearTokens, devicesApi, fetchHealth, type AccountMeData, type HealthResponse, type LicenseData } from "@/api/client";
import { useStatusStream, type StreamChannel } from "@/hooks/use-status-stream";
import {
  Alert,
  App,
  Avatar,
  Button,
  Card,
  Descriptions,
  Empty,
  Space,
  Table,
  Tag,
  Typography,
} from "antd";
import {
  CloudOutlined,
  CloudServerOutlined,
  DesktopOutlined,
  KeyOutlined,
  LogoutOutlined,
  ReloadOutlined,
  ScheduleOutlined,
  SyncOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

const { Paragraph, Text, Title } = Typography;

/** 许可证状态 → 标签（颜色只是辅助，状态必须有文字，主基线 §6.1）。 */
const LICENSE_TAG: Record<string, { color: string; label: string }> = {
  ACTIVE: { color: "success", label: "有效" },
  GRACE: { color: "warning", label: "宽限期内" },
  EXPIRED: { color: "error", label: "已过期" },
  REVOKED: { color: "error", label: "已吊销" },
  MISSING: { color: "default", label: "未开通" },
};

const DEVICE_TAG: Record<string, { color: string; label: string }> = {
  REGISTERED: { color: "processing", label: "已注册" },
  ONLINE: { color: "success", label: "在线" },
  DEGRADED: { color: "warning", label: "降级" },
  OFFLINE: { color: "default", label: "离线" },
  REVOKED: { color: "error", label: "已吊销" },
};

function channelTag(channel: StreamChannel) {
  if (channel === "sse") {
    return (
      <Tag color="success" icon={<CloudServerOutlined />}>
        实时（SSE）
      </Tag>
    );
  }
  if (channel === "polling") {
    return (
      <Tag color="warning" icon={<SyncOutlined spin />}>
        轮询降级（30 秒）
      </Tag>
    );
  }
  return (
    <Tag color="default" icon={<SyncOutlined spin />}>
      连接中…
    </Tag>
  );
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export default function DashboardPage() {
  const { message } = App.useApp();
  const router = useRouter();
  const [me, setMe] = useState<AccountMeData | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const { snapshot, channel, error: streamError, refresh } = useStatusStream(true);

  const load = useCallback(async () => {
    try {
      const [meData, healthData] = await Promise.all([authApi.me(), fetchHealth()]);
      setMe(meData);
      setHealth(healthData);
      setLoadError(null);
    } catch (cause) {
      if (cause instanceof ApiRequestError && cause.status === 401) {
        clearTokens();
        router.replace("/login");
        return;
      }
      setLoadError(cause instanceof Error ? cause.message : "加载失败");
    }
  }, [router]);

  useEffect(() => {
    void load();
  }, [load]);

  const onLogout = async () => {
    try {
      await authApi.logout();
    } catch {
      // 令牌已失效时注销接口返回 401：本地清理后回到登录页即可
    } finally {
      clearTokens();
      message.success("已退出登录");
      router.replace("/login");
    }
  };

  const license: LicenseData | null = snapshot?.license ?? me?.license ?? null;
  const devices = snapshot?.devices ?? [];
  const features = license?.features ?? [];
  const licenseTag = license ? LICENSE_TAG[license.status] : null;

  const columns = [
    { title: "设备名称", dataIndex: "name", key: "name" },
    {
      title: "类型",
      dataIndex: "device_type",
      key: "device_type",
      width: 110,
      render: (value: string) =>
        value === "DESKTOP" ? (
          <Tag icon={<DesktopOutlined />}>工作台</Tag>
        ) : value === "MINI_PROGRAM" ? (
          <Tag>小程序</Tag>
        ) : (
          <Tag>Web</Tag>
        ),
    },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      width: 130,
      render: (value: string) => {
        const tag = DEVICE_TAG[value] ?? { color: "default", label: value };
        return <Tag color={tag.color}>{tag.label}</Tag>;
      },
    },
    {
      title: "客户端版本",
      dataIndex: "app_version",
      key: "app_version",
      width: 130,
      render: (value: string | null) => value ?? "—",
    },
    {
      title: "最后心跳",
      dataIndex: "last_seen_at",
      key: "last_seen_at",
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
    {
      title: "注册时间",
      dataIndex: "registered_at",
      key: "registered_at",
      width: 180,
      render: (value: string | null) => formatDateTime(value),
    },
  ];

  return (
    <main style={{ minHeight: "100vh", padding: 24 }}>
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        {/* 顶栏：账号、通道状态与登出 */}
        <Card styles={{ body: { display: "flex", alignItems: "center", gap: 16 } }}>
          <Avatar icon={<UserOutlined />} />
          <div style={{ flex: 1 }}>
            <Title level={4} style={{ margin: 0 }}>
              {me?.tenant?.name ?? "商户工作台"}
            </Title>
            <Text type="secondary">
              {me?.account.login_name ?? "…"} · 控制面 {health?.app_version ?? "—"} ·
              数据库 {health?.database === "up" ? "正常" : health ? "不可达" : "…"}
            </Text>
          </div>
          {channelTag(channel)}
          <Button icon={<ReloadOutlined />} onClick={refresh}>
            刷新
          </Button>
          <Button icon={<LogoutOutlined />} onClick={() => void onLogout()}>
            退出登录
          </Button>
        </Card>

        {loadError !== null && (
          <Alert type="error" showIcon message="加载失败" description={loadError} />
        )}
        {streamError !== null && (
          <Alert type="warning" showIcon message="状态流异常" description={streamError} />
        )}

        {/* 许可证与授权（M2 第 1 项：登录、许可证、离线宽限期） */}
        <Card title="许可证与授权" extra={<KeyOutlined />}>
          {license === null || licenseTag === null ? (
            <Empty description="暂无许可证信息" />
          ) : (
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
                  {license.status === "GRACE" && (
                    <Alert
                      type="warning"
                      showIcon
                      message="许可证已过期，处于离线宽限期内"
                      description={`宽限期截止 ${formatDateTime(license.grace_ends_at)}（宽限 ${license.grace_days ?? 0} 天），到期后本地功能进入只读模式。`}
                    />
                  )}
              {license.status === "ACTIVE" &&
                typeof license.days_remaining === "number" &&
                license.days_remaining <= 30 && (
                  <Alert
                    type="info"
                    showIcon
                    message={`许可证将在 ${license.days_remaining} 天后到期，请联系服务商续期`}
                  />
                )}
              <Descriptions
                column={3}
                size="small"
                items={[
                  {
                    key: "status",
                    label: "状态",
                    children: <Tag color={licenseTag.color}>{licenseTag.label}</Tag>,
                  },
                  {
                    key: "profile",
                    label: "行业类型",
                    children: license.product_profile_code ?? "—",
                  },
                  {
                    key: "expires",
                    label: "到期日",
                    children: license.expires_at ?? "—",
                  },
                  {
                    key: "grace",
                    label: "离线宽限期",
                    children: `${license.grace_days} 天`,
                  },
                  {
                    key: "max_devices",
                    label: "设备数上限",
                    children: `${devices.filter((device) => device.status !== "REVOKED").length} / ${license.max_devices}`,
                  },
                  {
                    key: "license_id",
                    label: "许可证编号",
                    children: license.license_id ?? "—",
                  },
                ]}
              />
              <Space wrap size={4}>
                <Text type="secondary">已开通能力：</Text>
                {features.length === 0 ? (
                  <Text type="secondary">—</Text>
                ) : (
                  features.map((feature) => (
                    <Tag key={feature} color="blue">
                      {feature}
                    </Tag>
                  ))
                )}
              </Space>
            </Space>
          )}
        </Card>

        {/* 设备列表（M2 第 2 项） */}
        <Card
          title={
            <Space>
              <DesktopOutlined />
              设备列表
            </Space>
          }
          extra={
            <Button
              size="small"
              onClick={() => {
                void devicesApi
                  .register({
                    name: `Web 会话 ${new Date().toLocaleString("zh-CN", { hour12: false })}`,
                    fingerprint: `web-${Math.random().toString(36).slice(2, 10)}`,
                    device_type: "WEB",
                  })
                  .then(() => {
                    message.success("设备已注册");
                    refresh();
                  })
                  .catch((cause: unknown) => {
                    message.error(
                      cause instanceof ApiRequestError ? cause.message : "设备注册失败",
                    );
                  });
              }}
            >
              注册当前浏览器
            </Button>
          }
        >
          <Table
            rowKey="device_id"
            size="small"
            columns={columns}
            dataSource={devices}
            pagination={false}
            locale={{ emptyText: "尚无设备，桌面工作台首次登录后会自动注册" }}
          />
        </Card>

        {/* 任务与同步积压（M3 交付：如实标注，不伪造数据） */}
        <Card title="调度任务" extra={<ScheduleOutlined />}>
          <Empty
            description={
              <span>
                任务计划与执行状态随 M3 托盘 Agent 交付
                <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                  （POST /tasks、GET /tasks/pull 当前为 stub，返回 501）
                </Paragraph>
              </span>
            }
          />
        </Card>
        <Card title="同步积压" extra={<CloudOutlined />}>
          <Empty
            description={
              <span>
                待同步事件与备份状态随 M3 同步链路交付
                <Paragraph type="secondary" style={{ marginBottom: 0 }}>
                  （心跳、同步事件拉取与 ACK 当前为 stub，返回 501）
                </Paragraph>
              </span>
            }
          />
        </Card>
      </Space>
    </main>
  );
}

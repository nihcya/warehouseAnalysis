"use client";

import { ApiRequestError, authApi, setTokens } from "@/api/client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Alert, App, Button, Card, Form, Input, Typography } from "antd";

type LoginFormValues = {
  username: string;
  password: string;
};

const { Paragraph, Text } = Typography;

export default function LoginPage() {
  const { message } = App.useApp();
  const router = useRouter();
  const [form] = Form.useForm<LoginFormValues>();
  const [submitting, setSubmitting] = useState(false);
  const [failureCode, setFailureCode] = useState<string | null>(null);

  const onFinish = async (values: LoginFormValues) => {
    setSubmitting(true);
    setFailureCode(null);
    try {
      const data = await authApi.login({
        username: values.username,
        password: values.password,
        client_type: "WEB",
      });
      setTokens(data.tokens.access_token, data.tokens.refresh_token);
      message.success(`欢迎回来，${data.account.login_name}`);
      router.replace("/dashboard");
    } catch (cause) {
      if (cause instanceof ApiRequestError) {
        setFailureCode(cause.code);
      } else {
        setFailureCode("INTERNAL_ERROR");
      }
    } finally {
      setSubmitting(false);
    }
  };

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
      <Card title="登录商户工作台" style={{ width: 380 }}>
        <Form<LoginFormValues>
          form={form}
          layout="vertical"
          onFinish={onFinish}
          requiredMark={false}
        >
          <Form.Item
            name="username"
            label="账号"
            rules={[{ required: true, message: "请输入账号" }]}
          >
            <Input placeholder="账号" autoComplete="username" />
          </Form.Item>
          <Form.Item
            name="password"
            label="密码"
            rules={[{ required: true, message: "请输入密码" }]}
          >
            <Input.Password placeholder="密码" autoComplete="current-password" />
          </Form.Item>
          {failureCode !== null && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              message={
                failureCode === "AUTH_FORBIDDEN"
                  ? "账号被锁定、停用或商户已暂停服务"
                  : "账号或密码错误"
              }
              description={<Text code>{failureCode}</Text>}
            />
          )}
          <Button type="primary" htmlType="submit" block loading={submitting}>
            登录
          </Button>
        </Form>
        <Paragraph type="secondary" style={{ marginTop: 16, marginBottom: 0 }}>
          本地演示：CONTROL_PLANE_REPOSITORY=memory 时使用
          <Text code> merchant_demo </Text>
          登录（口令见启动日志或 CONTROL_PLANE_DEMO_PASSWORD）。
        </Paragraph>
      </Card>
    </main>
  );
}

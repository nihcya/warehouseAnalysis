import type { components } from "./types/schema";

/**
 * 登录请求 DTO。
 * 类型取自 openapi-typescript 生成产物（generate:api），禁止手写请求 DTO。
 */
export type LoginRequest = components["schemas"]["LoginRequest"];

/** /health 响应 DTO（应用状态、版本与控制库可达性）。 */
export type HealthResponse = components["schemas"]["HealthResponse"];

/** 控制面 API 基地址（M0 占位：本地开发默认指向 control-plane）。 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** M0 占位：健康检查请求（后端 stub 恒 501，仅用于联调打通）。 */
export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/health`);
  if (!response.ok) {
    throw new Error(`健康检查失败：HTTP ${response.status}`);
  }
  return (await response.json()) as HealthResponse;
}

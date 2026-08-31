import type { components } from "./types/schema";

/** 控制面 API 基地址（默认指向本地 control-plane）。 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/** 登录请求 DTO（取自 openapi-typescript 生成产物，禁止手写请求 DTO）。 */
export type LoginRequest = components["schemas"]["LoginRequest"];
/** 刷新请求 DTO。 */
export type RefreshRequest = components["schemas"]["RefreshRequest"];
/** 设备注册请求 DTO。 */
export type DeviceRegisterRequest =
  components["schemas"]["DeviceRegisterRequest"];

/** 登录 / 刷新响应载荷。 */
export type AuthData = components["schemas"]["AuthData"];
/** 当前账号上下文。 */
export type AccountMeData = components["schemas"]["AccountMeData"];
/** 设备信息。 */
export type DeviceData = components["schemas"]["DeviceData"];
/** 状态快照（SSE 首帧与轮询降级共用）。 */
export type SnapshotData = components["schemas"]["SnapshotData"];
/** 许可证与离线宽限期评估结果。 */
export type LicenseData = components["schemas"]["LicenseData"];
/** /health 响应（应用状态、版本与控制库可达性）。 */
export type HealthResponse = components["schemas"]["HealthResponse"];

/** 统一错误体（与后端 {"error": {...}} 一一对应）。 */
type ErrorBody = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id: string;
};

/**
 * 控制面请求错误：保留后端错误码与 request_id，
 * UI 只允许按 code 判断行为，不解析中文文案（主基线 §21.4）。
 */
export class ApiRequestError extends Error {
  readonly code: string;
  readonly status: number;
  readonly details: Record<string, unknown>;
  readonly requestId: string;

  constructor(body: ErrorBody, status: number) {
    super(body.message);
    this.name = "ApiRequestError";
    this.code = body.code;
    this.status = status;
    this.details = body.details ?? {};
    this.requestId = body.request_id;
  }
}

/** 令牌在本地的存放键（登出时一并清理）。 */
const ACCESS_TOKEN_KEY = "warehouse.access_token";
const REFRESH_TOKEN_KEY = "warehouse.refresh_token";

/**
 * 读取本地访问令牌。
 *
 * 已知限制：第一版把令牌放在 localStorage，便于本地联调；
 * 生产建议改为 httpOnly Cookie 或内存 + 刷新令牌轮换（见 m2-handover-a.md）。
 */
export function getAccessToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(REFRESH_TOKEN_KEY);
}

export function setTokens(accessToken: string, refreshToken: string): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
}

export function clearTokens(): void {
  if (typeof window === "undefined") return;
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
}

/** 带令牌的 Authorization 头；无令牌时返回空对象。 */
export function authHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

type RequestOptions = Omit<RequestInit, "body"> & {
  body?: unknown;
  /** 是否附加访问令牌（默认 true）。 */
  auth?: boolean;
};

/**
 * 调用控制面并拆开成功信封 `{"data": ...}`；失败统一抛 ApiRequestError。
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, auth = true, headers, ...rest } = options;
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...rest,
    headers: {
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(auth ? authHeaders() : {}),
      ...headers,
    },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });

  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const error = (payload as { error?: ErrorBody } | null)?.error;
    throw new ApiRequestError(
      error ?? {
        code: "INTERNAL_ERROR",
        message: "请求失败。",
        request_id: "",
      },
      response.status,
    );
  }
  return (payload as { data: T }).data;
}

/** 认证相关接口。 */
export const authApi = {
  login(body: LoginRequest): Promise<AuthData> {
    return apiFetch<AuthData>("/api/v1/auth/login", {
      method: "POST",
      body,
      auth: false,
    });
  },
  refresh(refreshToken: string): Promise<AuthData> {
    const body: RefreshRequest = { refresh_token: refreshToken };
    return apiFetch<AuthData>("/api/v1/auth/refresh", {
      method: "POST",
      body,
      auth: false,
    });
  },
  logout(): Promise<{ session_id: string; revoked: boolean }> {
    return apiFetch<{ session_id: string; revoked: boolean }>("/api/v1/auth/logout", {
      method: "POST",
    });
  },
  me(): Promise<AccountMeData> {
    return apiFetch<AccountMeData>("/api/v1/account/me");
  },
};

/** 设备相关接口。 */
export const devicesApi = {
  list(): Promise<DeviceData[]> {
    return apiFetch<DeviceData[]>("/api/v1/devices");
  },
  register(body: DeviceRegisterRequest): Promise<DeviceData> {
    return apiFetch<DeviceData>("/api/v1/devices/register", {
      method: "POST",
      body,
    });
  },
};

/** 状态流相关接口（轮询降级入口；主通道为 SSE）。 */
export const eventsApi = {
  snapshot(): Promise<SnapshotData> {
    return apiFetch<SnapshotData>("/api/v1/events/snapshot");
  },
  streamUrl(): string {
    return `${API_BASE_URL}/api/v1/events/stream`;
  },
};

/** 健康检查（用于展示控制面版本与数据库可达性）。 */
export function fetchHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health", { auth: false });
}

# M2 A 侧与 B 侧联调：本地工作台基本工作流 Spec

## Why

M2 已分别交付 A 侧控制平面（认证/设备/SSE 端点）和 B 侧引擎（5 个分析技能），Web 前端也已接通登录与状态流。但桌面工作台仍使用 M0 的 `OfflineApiClient` 占位，无法登录控制平面、注册设备、接收状态流，也没有在线/授权状态展示。需要打通"登录→设备注册→接收状态→导入数据→分析→报告"的完整本地工作流，让工作台真正连上云端控制面。

## What Changes

- **实装工作台真实 API 客户端**：替换 `OfflineApiClient`，基于 `httpx` 调用控制平面 `/api/v1/*` 端点（登录/刷新/注销/账号上下文/设备注册/设备列表/SSE 状态流/轮询快照），令牌持久化到本地、Access Token 过期自动用 Refresh Token 刷新
- **桌面端登录流程**：启动时如无有效令牌则显示登录对话框（用户名/密码/服务地址），登录成功后持久化令牌对与服务器地址，重启免重新登录
- **设备注册与许可证展示**：首次登录后自动注册设备（指纹用机器标识），总览页展示许可证状态（ACTIVE/GRACE/EXPIRED）、在线/离线状态、最后连接时间
- **SSE 状态流接收**：工作台 Agent 后台线程订阅 `/events/stream`，失败降级 30 秒轮询 `/events/snapshot`；总览页展示设备/任务/同步状态
- **离线降级**：控制平面不可达时工作台进入离线模式（沿 M1 本地业务闭环继续可用），状态栏明示"离线"，许可证宽限期内本地功能不受阻
- **工作流闭环验证**：端到端测试覆盖"登录→注册设备→导入 CSV→分析→报告导出→查看状态"全链路

### 明确不做（留给 M3）

- 托盘 Agent 心跳、任务拉取、同步信封（M3 交付）
- 配置验签、客户端升级、自动备份调度（M3 交付）
- 真实引擎 wheel 接入（B 侧已交付技能，但工作台仍默认用 FakeEngine/LocalEngine，不在本 spec 范围切换）

## Impact

- Affected specs: `deliver-m0-platform-baseline`（替换 OfflineApiClient）、`deliver-m1-local-business-loop`（在其本地业务闭环上叠加云端连接层）
- Affected code:
  - `apps/workbench-desktop/app/infrastructure/api_client/`（新增 `HttpApiClient`、`token_store`、`auth_service`）
  - `apps/workbench-desktop/app/presentation/`（新增登录对话框、总览页在线状态卡片）
  - `apps/workbench-desktop/app/main.py`（组合根切换为真实客户端，支持环境变量配置服务地址）
  - `apps/workbench-desktop/app/workers/`（新增状态流后台线程）
  - `apps/workbench-desktop/tests/`（新增联调测试）
- 契约不变：控制平面 OpenAPI 已冻结，工作台作为消费方按生成类型调用

## ADDED Requirements

### Requirement: 工作台真实 API 客户端

系统 SHALL 提供基于 `httpx` 的 `HttpApiClient`，调用控制平面全部 M2 实装端点（登录/刷新/注销/账号上下文/设备注册/设备列表/状态流/快照），令牌对持久化到本地存储，Access Token 过期时自动用 Refresh Token 刷新。

#### Scenario: 登录并持久化令牌

- **WHEN** 用户在登录对话框输入正确凭据并提交
- **THEN** 获取令牌对与账号上下文，令牌持久化到本地，重启后免重新登录

#### Scenario: Access Token 过期自动刷新

- **WHEN** Access Token 过期，工作台发起需鉴权的 API 调用
- **THEN** 自动用 Refresh Token 获取新令牌对，原请求重试成功；Refresh Token 也过期时跳转登录

#### Scenario: 离线降级

- **WHEN** 控制平面不可达（网络断开或服务未启动）
- **THEN** API 客户端标记为离线，工作台进入离线模式，本地导入/分析/报告功能继续可用，状态栏明示"离线"

### Requirement: 桌面端登录流程

系统 SHALL 在工作台启动时检查本地令牌有效性：有有效令牌则直接进入主界面，无令牌或令牌失效则弹出登录对话框。

#### Scenario: 首次启动登录

- **WHEN** 首次启动工作台（本地无令牌）
- **THEN** 弹出登录对话框，用户输入用户名/密码/服务地址后登录

#### Scenario: 重启免登录

- **WHEN** 工作台重启且本地存在有效令牌
- **THEN** 直接进入主界面，后台静默验证令牌并刷新账号上下文

### Requirement: 设备注册与许可证展示

系统 SHALL 在首次登录后自动注册当前设备（指纹基于机器标识生成），总览页展示许可证状态、在线状态与设备信息。

#### Scenario: 首次登录自动注册设备

- **WHEN** 用户首次登录成功且本地未注册设备
- **THEN** 自动调用 `/devices/register` 注册设备，设备信息持久化到本地

#### Scenario: 许可证状态展示

- **WHEN** 总览页加载
- **THEN** 展示许可证状态（ACTIVE/GRACE/EXPIRED）、到期天数、设备数量上限与已注册数

### Requirement: SSE 状态流接收

系统 SHALL 在工作台后台线程订阅 `/events/stream`，接收设备/任务/同步状态更新；SSE 连接失败时降级为 30 秒轮询 `/events/snapshot`。

#### Scenario: SSE 正常接收

- **WHEN** 工作台在线且 SSE 连接成功
- **THEN** 总览页实时展示设备状态、任务状态等更新

#### Scenario: SSE 失败降级轮询

- **WHEN** SSE 连接断开
- **THEN** 自动切换到 30 秒轮询 `/events/snapshot`，状态栏明示"轮询降级"

### Requirement: 完整工作流闭环

系统 SHALL 支持用户完成"登录→注册设备→导入 CSV→分析→报告导出→查看状态"的完整工作流，每一步都有 UI 反馈。

#### Scenario: 端到端工作流

- **WHEN** 用户登录后在离线模式下导入 CSV、发起分析、导出报告
- **THEN** 全流程无报错，报告文件生成，总览页状态更新

## MODIFIED Requirements

### Requirement: 工作台 API 客户端端口（原 M0 离线占位）

`ApiClient` 端口 SHALL 由 `HttpApiClient` 实装替代 `OfflineApiClient`，组合根根据配置选择真实客户端或离线占位（测试保留离线模式）；`online` 属性反映实际连接状态。

### Requirement: 工作台组合根（原 M0/M1）

`main.py` 组合根 SHALL 注入 `HttpApiClient`（替代 `OfflineApiClient`），支持通过环境变量 `WORKBENCH_API_URL` 配置控制平面地址，默认 `http://localhost:8000`。

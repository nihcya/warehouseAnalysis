# M2 A 侧与 B 侧联调：本地工作台基本工作流 验收 Checklist

## HTTP API 客户端

- [x] `HttpApiClient` 可调用控制平面全部 M2 实装端点（login/refresh/logout/account-me/devices/events）
- [x] Access Token 过期时自动用 Refresh Token 刷新，原请求重试成功
- [x] 令牌对与服务器地址持久化到本地，重启后可读取免重新登录
- [x] 控制平面不可达时 `online=False`，不抛异常，工作台进入离线模式
- [x] 设备指纹基于机器标识生成，稳定且唯一

## 桌面端登录流程

- [x] 首次启动（本地无令牌）弹出登录对话框
- [x] 登录成功后持久化令牌对与服务器地址，关闭对话框进入主窗口
- [x] 重启时本地有有效令牌则静默验证后直接进入主界面
- [x] 令牌失效时弹出登录对话框，不直接报错

## 设备注册与许可证展示

- [x] 首次登录成功后自动调用 `/devices/register` 注册设备
- [x] 总览页展示在线/离线状态、许可证状态（ACTIVE/GRACE/EXPIRED）、到期天数
- [x] 设备数量展示（已注册/上限）
- [x] 控制平面不可达时卡片展示"离线"，许可证宽限期内本地功能不受阻

## SSE 状态流接收

- [x] 后台线程订阅 `/events/stream`，接收设备/任务/同步状态更新
- [x] SSE 帧通过 Qt 信号推送到 UI 主线程
- [x] SSE 连接失败降级 30 秒轮询 `/events/snapshot`
- [x] 状态栏明示当前通道（实时 / 轮询降级 / 离线）

## 组合根与总览页整合

- [x] `main.py` 注入 `HttpApiClient` 替代 `OfflineApiClient`
- [x] `WORKBENCH_API_URL` 环境变量配置服务地址（默认 `http://localhost:8000`）
- [x] 离线模式下本地导入/分析/报告功能不受阻
- [x] 总览页与已有导入/分析/报告页面并存无冲突

## 端到端工作流

- [x] 完整工作流（登录→注册设备→导入 CSV→分析→报告导出）全链路无报错
- [x] 离线场景下本地导入/分析/报告继续可用
- [x] 全量 `uv run pytest` 无回归（334 passed, 1 skipped）
- [x] `pnpm lint` + `pnpm typecheck` 全绿

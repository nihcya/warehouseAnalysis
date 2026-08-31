# Tasks

- [x] Task 1: 工作台 HTTP API 客户端实装
  - [ ] 1.1 新增 `infrastructure/api_client/http_client.py`：基于 `httpx` 的 `HttpApiClient`，调用控制平面 `/api/v1/auth/login`、`/auth/refresh`、`/auth/logout`、`/account/me`、`/devices/register`、`/devices`、`/events/snapshot` 端点；Access Token 过期自动刷新
  - [ ] 1.2 新增 `infrastructure/api_client/token_store.py`：令牌对（access/refresh）与服务器地址持久化到本地 JSON（`%LOCALAPPDATA%\WarehouseWorkbench\auth.json`），重启可读取
  - [ ] 1.3 新增 `infrastructure/api_client/device_fingerprint.py`：基于机器标识（hostname + MAC 哈希）生成稳定设备指纹
  - [ ] 1.4 测试：`HttpApiClient` 对内存仓储控制平面的登录/刷新/注销/设备注册往返；令牌持久化读写；离线时 `online=False` 且不抛异常

- [x] Task 2: 桌面端登录对话框
  - [ ] 2.1 新增 `presentation/login_dialog.py`：PySide6 登录对话框（用户名/密码/服务地址，服务地址默认 `http://localhost:8000`，可编辑保存）
  - [ ] 2.2 登录成功后持久化令牌对与服务器地址，关闭对话框进入主窗口；登录失败显示错误码与消息
  - [ ] 2.3 工作台启动逻辑：本地有有效令牌 → 静默验证（调 `/account/me`）→ 成功直接进主界面；失败/无令牌 → 弹登录对话框
  - [ ] 2.4 测试（pytest-qt offscreen）：登录对话框流转、错误提示、服务地址保存

- [x] Task 3: 设备注册与许可证状态展示
  - [ ] 3.1 首次登录成功后自动调用 `/devices/register`（指纹取 Task 1.3），设备信息持久化到本地
  - [ ] 3.2 总览页新增"在线状态"卡片：在线/离线、许可证状态（ACTIVE/GRACE/EXPIRED）、到期天数、设备数（已注册/上限）
  - [ ] 3.3 控制平面不可达时卡片展示"离线"，许可证宽限期内本地功能不受阻
  - [ ] 3.4 测试：首次登录触发设备注册、总览页卡片内容正确、离线状态展示

- [x] Task 4: SSE 状态流后台线程
  - [ ] 4.1 新增 `workers/status_stream_worker.py`：QThread 后台线程订阅 `/events/stream`（携带 Authorization 头），解析 SSE 帧更新状态；连接失败降级 30 秒轮询 `/events/snapshot`
  - [ ] 4.2 线程通过 Qt 信号把状态更新推送到 UI 主线程；降级时发信号通知状态栏
  - [ ] 4.3 总览页状态卡片接收信号实时更新设备/任务/同步状态
  - [ ] 4.4 测试：SSE 帧解析、降级轮询触发、Qt 信号传递

- [x] Task 5: 组合根切换与总览页整合
  - [ ] 5.1 `main.py` 组合根：`HttpApiClient` 替代 `OfflineApiClient`，`WORKBENCH_API_URL` 环境变量配置服务地址（默认 `http://localhost:8000`）
  - [ ] 5.2 总览页整合：在线状态卡片 + SSE 状态流 + 许可证信息，与已有分析/导入/报告页面并存
  - [ ] 5.3 离线模式：API 客户端不可达时，`MainWindow` 接收离线信号，状态栏明示"离线"，本地导入/分析/报告不受阻
  - [ ] 5.4 测试：组合根注入真实客户端、离线降级不阻断本地功能

- [x] Task 6: 端到端工作流验证
  - [ ] 6.1 集成测试：启动内存仓储控制平面 + 工作台 → 登录 → 注册设备 → 导入 CSV → 发起分析 → 导出报告，全链路无报错
  - [ ] 6.2 离线场景测试：断开控制平面后本地导入/分析/报告继续可用，状态栏展示"离线"
  - [ ] 6.3 全量回归测试（`uv run pytest` + `pnpm lint` + `pnpm typecheck`）无回归

# Task Dependencies

- Task 2/3/4 依赖 Task 1（HTTP 客户端与令牌存储）
- Task 5 依赖 Task 1-4（组合根整合全部新组件）
- Task 6 依赖 Task 5（端到端验证在整合完成后）

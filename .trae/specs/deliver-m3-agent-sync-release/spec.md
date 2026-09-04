# M3：托盘 Agent、同步链路与发布 Spec

## Why

M2 已完成认证、设备与 SSE 状态流，但 8 个 stub 端点（heartbeat/config/tasks/sync 等）未实装，工作台无后台常驻能力，小程序事件无法进入本地库，也没有安装包。M3 是 A 侧核心交付：让工作台成为"在线可控、离线可用、可安装升级"的桌面产品。

## What Changes

- **云端控制平面**：新增 Alembic 迁移（配置版本、任务/心跳、同步信封表）；实装 `POST /heartbeat`、`GET /config`（含验签）、`GET /tasks`、`POST /tasks/pull`、`GET /sync/events/pull`、`POST /sync/ack` 六组端点；提供 Mock 小程序事件注入（dev 工具）。
- **工作台托盘 Agent**：QSystemTrayIcon 托盘常驻（关窗隐藏、显式退出）；Agent worker 心跳定时上报（指数退避）、配置拉取验签与本地缓存、任务拉取执行与状态上报。
- **小程序事件同步链路**：本地新增 `0007_sync_config` 迁移（sync_inbox/sync_outbox）；拉取加密信封 → 解密校验 → 落库 `inventory_event` → ACK；幂等（event_id 唯一）、断网重试、失败保留密文与错误码。
- **配置与升级安全**：启动迁移失败进入安全模式（不覆盖数据库）；自动备份接入调度；升级前备份 + 回滚。
- **打包**：PyInstaller `--onedir` spec + Inno Setup 脚本 + 打包脚本；代码签名在有证书前使用测试签名/跳过并记录。
- **验收**：干净机安装/卸载/升级/断网/恢复/诊断包检查清单。

## Impact

- Affected specs: `deliver-m2-ab-integration-local-workflow`（http_client 扩展）、`deliver-m2-analysis-skills`（任务执行复用分析用例）
- Affected code:
  - `services/control-plane/`（routes/schemas/container/repositories/迁移/memory 仓储/OpenAPI）
  - `apps/workbench-desktop/`（main.py 组合根、workers/、presentation/main_window.py、托盘）
  - `local-data/`（0007_sync_config 迁移、models/repository）
  - `scripts/`（打包脚本、Mock 小程序事件）

## ADDED Requirements

### Requirement: 设备心跳上报

系统 SHALL 提供 `POST /api/v1/heartbeat` 端点，工作台 Agent 定时上报 `app_version`、`db_schema_version`、`engine_version`、`pending_sync_count` 与运行状态；云端保存最新投影（device_id 主键）。

#### Scenario: 心跳成功
- **WHEN** 已登录设备携带有效令牌每 60 秒 POST /heartbeat
- **THEN** 返回 200 与最新 `last_seen_at`，设备在 Web 端显示为在线

#### Scenario: 断网退避
- **WHEN** 网络不可达
- **THEN** Agent 指数退避重试（上限 10 分钟），恢复后首次心跳立即发送

### Requirement: 配置下发与验签

系统 SHALL 提供 `GET /api/v1/config` 返回商户生效配置（版本号、内容、SHA-256 签名）；客户端验证签名后才应用，并缓存到本地供离线启动读取。

#### Scenario: 验签失败
- **WHEN** 配置签名与内容不匹配
- **THEN** 客户端拒绝应用，保留旧配置并告警

### Requirement: 任务拉取与执行

系统 SHALL 提供 `POST /api/v1/tasks/pull`（设备拉取待执行任务）与任务状态上报；任务由云端定义、本地执行，完整结果留在本地，云端只收状态/耗时/版本/脱敏摘要。

#### Scenario: 任务闭环
- **WHEN** 云端下发分析任务且设备在线
- **THEN** 设备拉取 → 本地执行 → 上报状态（含 run_id、耗时、错误码）→ 云端任务状态机流转

### Requirement: 小程序事件同步链路

系统 SHALL 提供 `GET /api/v1/sync/events/pull`（拉取加密信封）与 `POST /api/v1/sync/ack`（确认）；Mock 小程序事件写入云端 `sync_envelope`（密文、幂等键、TTL 过期清理），工作台拉取后本地校验成功才写 `inventory_event`，事务成功后 ACK；失败事件保留错误码与原密文，不伪造成功。

#### Scenario: 幂等重放
- **WHEN** 同一 event_id 被重复拉取/应用
- **THEN** 本地唯一约束拦截，重复 ACK 幂等成功

#### Scenario: 断网重试
- **WHEN** 拉取或落库失败
- **THEN** 信封保持 PENDING，按指数退避重试，云端 TTL 过期后自动清理

### Requirement: 托盘常驻 Agent

系统 SHALL 在工作台提供系统托盘图标；关闭窗口仅隐藏到托盘，后台 Agent（心跳/同步/调度）继续运行；显式"退出工作台"才停止。

#### Scenario: 关窗常驻
- **WHEN** 用户点击窗口关闭按钮
- **THEN** 窗口隐藏、托盘图标存活、心跳与同步继续

### Requirement: 迁移失败安全模式

系统 SHALL 在启动迁移失败时进入安全模式：不覆盖当前数据库，UI 明示错误并提供"从备份恢复"入口。

#### Scenario: 升级迁移失败
- **WHEN** 新版本迁移在用户库上执行抛错
- **THEN** 启动进入安全模式页，数据库保持迁移前状态，可一键回滚到升级前备份

### Requirement: 安装包

系统 SHALL 提供 PyInstaller `--onedir` 打包 spec 与 Inno Setup 安装脚本；安装到 Program Files、数据放 `%LOCALAPPDATA%\WarehouseWorkbench`；有证书时签名，无证书时记录 SHA-256 并在发布说明标注未签名。

#### Scenario: 干净机安装
- **WHEN** 在无 Python 的 Windows 干净机运行安装包
- **THEN** 安装、启动、导入、分析、备份、卸载全流程可用，卸载后用户数据目录保留（提示手动清理）

## MODIFIED Requirements

### Requirement: stub 端点（M2）

M2 保留的 8 个 stub 端点中，`/heartbeat`、`/config`、`/tasks`、`/tasks/pull`、`/sync/events/pull`、`/sync/ack` 在 M3 实装；`/telemetry`、`/merchants` 维持 stub（随开发者端页面交付）。

### Requirement: 工作台主窗口生命周期

主窗口关闭行为从"退出应用"改为"隐藏到托盘"；新增托盘菜单（显示/退出）与退出确认。

## REMOVED Requirements

（无）

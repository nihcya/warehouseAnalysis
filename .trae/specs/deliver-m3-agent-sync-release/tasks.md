# Tasks

- [x] Task 1: 云端控制平面数据层：新增迁移与仓储
  - [x] SubTask 1.1: 新增云端迁移（配置版本 config_version、task/task_run/heartbeat、sync_envelope），含枚举种子与唯一约束（event_id UNIQUE、device_id 主键投影）
  - [x] SubTask 1.2: 扩展 PostgreSQL 与内存双仓储（ConfigRepository、TaskRepository、HeartbeatRepository、SyncEnvelopeRepository），内存实现同步播种
  - [x] SubTask 1.3: 迁移测试（空库 upgrade/downgrade 往返、唯一约束）
- [x] Task 2: 控制平面端点实装（替换 stub）
  - [x] SubTask 2.1: `POST /heartbeat`（版本/Schema/待同步数上报，更新设备在线状态）
  - [x] SubTask 2.2: `GET /config`（返回生效配置 + SHA-256 签名；内存仓储种子一份演示配置）
  - [x] SubTask 2.3: `GET /tasks` + `POST /tasks/pull`（设备拉取待执行任务并锁定状态机）
  - [x] SubTask 2.4: `GET /sync/events/pull` + `POST /sync/ack`（信封拉取、幂等确认、TTL 过期清理）
  - [x] SubTask 2.5: Mock 小程序事件注入工具（dev 脚本或端点：生成加密信封写入 sync_envelope）
  - [x] SubTask 2.6: schemas/deps/OpenAPI 同步更新，重导出 openapi.json，更新 stub 测试（API 契约）
- [x] Task 3: 工作台托盘与 Agent
  - [x] SubTask 3.1: QSystemTrayIcon 托盘（关窗隐藏、托盘菜单"显示/退出"、显式退出才停止）
  - [x] SubTask 3.2: AgentWorker 线程：心跳定时上报（默认 60s，指数退避上限 10min，恢复即发）
  - [x] SubTask 3.3: 配置拉取与验签：SHA-256 验证后应用，本地缓存供离线启动读取；验签失败保留旧配置
  - [x] SubTask 3.4: 任务拉取执行：调用现有分析用例，上报状态/耗时/版本/错误码
- [x] Task 4: 小程序事件同步链路（本地）
  - [x] SubTask 4.1: local-data 新增 `0007_sync_config` 迁移（sync_inbox/sync_outbox 表）
  - [x] SubTask 4.2: SyncWorker：拉取信封 → 解密 → 校验 → 写 sync_inbox → 事务成功写 inventory_event → ACK；失败保留密文与错误码
  - [x] SubTask 4.3: 幂等（event_id 唯一约束 + 重复 ACK 幂等）、断网重试（指数退避）、总览页显示待同步数
- [x] Task 5: 配置与升级安全
  - [x] SubTask 5.1: 启动迁移失败安全模式：捕获迁移异常进入安全模式页（不覆盖库），提供"从备份恢复"
  - [x] SubTask 5.2: 自动备份接入调度（每日，复用 BackupManager）；升级前备份 + 版本回滚入口
- [x] Task 6: 打包与发布脚本
  - [x] SubTask 6.1: PyInstaller `--onedir` spec + 打包脚本（含数据文件/迁移脚本资源）
  - [x] SubTask 6.2: Inno Setup 安装脚本（Program Files 安装、卸载保留用户数据、版本升级覆盖）
  - [x] SubTask 6.3: 干净机验收清单文档化（安装/卸载/升级/断网/恢复/诊断包检查项）
- [x] Task 7: 全量验证
  - [x] SubTask 7.1: pytest（control-plane + workbench）全绿、ruff/mypy 无错误、pnpm lint/typecheck、openapi.json 与路由一致
  - [x] SubTask 7.2: 端到端联调脚本：登录 → 心跳 → 配置验签 → Mock 事件注入 → 拉取落库 → ACK → 断网重试 → 幂等重放

# Task Dependencies

- Task 2 depends on Task 1（端点需要仓储与迁移）
- Task 3 depends on Task 2（Agent 调用实装端点）
- Task 4 depends on Task 2（信封拉取/ACK 端点）且与 Task 3 可并行
- Task 5 依赖 Task 3（托盘调度挂载自动备份）
- Task 6 依赖 Task 3/4（打包包含 Agent 与同步）
- Task 7 依赖全部

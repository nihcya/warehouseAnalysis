# 仓库品类分析决策工具：开发需求 A（平台、官网与工作台）

**文档版本**：V1.0  
**适用角色**：开发者 A（官网、商户 Web、开发者管理、FastAPI、桌面工作台、数据库、同步与发布）  
**上位基线**：`仓库品类分析决策工具-开发规划与协作需求文档.md` V2.0  
**文档性质**：A 的执行需求和验收清单，不得改变上位基线的数据库边界、公共契约和版本规则

## 1. A 的目标

- 交付客户可以下载安装的 Windows 本地优先工作台。
- 交付同一套 Web 应用中的官网、商户管理模式和开发者管理模式。
- 交付 FastAPI 控制平面，统一管理账号、许可证、设备、配置、任务、同步状态和脱敏技术日志。
- 交付本地 SQLite、导入、库存事件、分析编排、报告、备份、恢复和同步 Agent。
- 让 B 的 `warehouse-engine` wheel 只通过公共契约接入，不要求 A 读取 B 的内部代码。

## 2. 不在 A 范围内

- 不实现 KPI、ABC、库龄、补货和预测公式；这些由 B 的引擎提供。
- 不开发微信小程序页面、微信审核和正式发布；只实现后端接口、Schema、Mock 和集成测试入口。
- 不把商户完整 SKU、库存、成本、采购和报告上传到云端控制库。
- 不为每个商户复制一套代码；行业差异通过 `product_profile`、配置和许可证实现。
- 第一版不实现员工邀请、复杂角色、多主工作台、多仓库跨节点合并和 Redis/MQ。

## 3. A 负责的模块和边界

| 模块 | 目录建议 | 主要交付 | 依赖规则 |
|---|---|---|---|
| Public Web | `apps/web/app/(public)` | 官网、下载、MDX 文档、隐私和 FAQ | 只调用生成的 API client |
| Merchant Console | `apps/web/app/(merchant)` | 账号、许可证、设备、任务、同步状态 | 只能访问商户 Scope |
| Developer Console | `apps/web/app/(developer)` | 商户、配置、Skill、版本、日志和支持 | 必须 `developer:*` Scope |
| Control API | `services/control-plane` | 认证、设备、配置、任务、SSE、同步、日志 | route 不直接写 ORM |
| Local App Service | `apps/workbench-desktop/app/application` | 导入、库存、分析、备份、同步用例 | 统一事务边界 |
| Workbench UI | `apps/workbench-desktop/app/presentation` | PySide6 页面、表格、状态和交互 | 不直接访问 DB/HTTP |
| Workbench Agent | `apps/workbench-desktop/app/workers` | 心跳、任务、配置、同步和日志 | 只调用 Application Service |
| Local DB | `local-data` | SQLite Model、Alembic、Repository、备份 | 安装目录之外存数据 |
| Release/CI | `scripts`、`.github` | 打包、签名、升级、回滚和发布 | 所有版本可追溯 |

## 4. 推荐实现顺序

### M0：公共骨架和冻结（第 1 周）

1. 建立 `uv`、`pnpm` workspace、`.env.example`、README 和 CI。
2. 合并公共 `contracts`、错误码、OpenAPI、同步信封 Schema 和兼容矩阵。
3. 建立 FastAPI `/health`、Next.js 路由组、PySide6 空窗口和 FakeEngine 注入点。
4. 建立 SQLite/PostgreSQL Alembic 环境；完成 `0001_meta` 和 `0001_control_meta`。
5. 记录 `DECISIONS.md`：数据边界、单主工作台、SSE、托盘 Agent、MFA 和签名策略。

### M1：本地业务闭环（第 2-4 周）

1. 实现 SQLite 主数据、库存事件、余额投影和导入错误隔离。
2. 实现导入向导：文件选择、字段映射、预览、校验、提交和错误修复。
3. 实现 `EngineDataset` 适配器；先接 FakeEngine，再接 B 的真实 wheel。
4. 实现分析运行、结果原样持久化、报告生成和导出。
5. 实现备份、恢复、完整性检查和失败不覆盖当前库。

### M2：控制平面和 Web（第 4-7 周）

1. 实现登录、刷新、注销、设备注册、许可证和离线宽限期。
2. 实现商户端设备、任务、同步积压和版本状态页面。
3. 实现开发者端商户、许可证、产品类型、配置、Skill、版本和技术日志页面。
4. 所有管理操作接入 Scope、审计、限时支持授权和操作确认。
5. 实现 SSE 状态流和 30 秒轮询降级。

### M3：Agent、同步和发布（第 7-12 周）

1. 实现托盘 Agent 的心跳、配置验签、任务拉取、状态上报和同步重试。
2. 实现 Mock 小程序事件进入云端临时加密中继、工作台拉取、落库和 ACK。
3. 实现配置/客户端升级、自动备份、迁移失败安全模式和回滚。
4. 使用 PyInstaller `--onedir` + Inno Setup/NSIS 生成签名安装包。
5. 完成干净 Windows 机器安装、卸载、升级、断网、恢复和诊断包测试。

## 5. 前后端分离实现要求

- FastAPI `openapi.json` 是唯一接口来源；执行 `openapi-typescript` 生成 Web 类型和 client。
- Web 页面通过 Query hooks 调用 API，不在组件中写 `fetch`、SQL 或业务公式。
- API 路由只做参数解析和权限依赖，事务由 Application Service 处理，数据访问由 Repository 处理。
- 商户范围由 Access Token 的 `tenant_id` 和后端查询条件决定，禁止信任前端传来的 `merchant_id`。
- 开发者接口单独挂载 `require_developer_scope`，商户接口挂载 `require_tenant_access`。
- 写接口要求 `request_id` 和 `Idempotency-Key`；错误返回统一 `error_code/message/details/request_id`。
- 浏览器生产环境只能通过 HTTPS；CORS 使用明确域名白名单，禁止 `*`。
- Web 通过 SSE 接收设备、任务和同步状态；断线使用 `Last-Event-ID`，失败后轮询。

## 6. 第一版 UI 需求

### 6.1 视觉基线

- 产品气质：仓库运营控制台，克制、清晰、可扫描、适合长时间工作。
- 主色 `#173B5F`，成功 `#0D766E`，警告 `#A55A05`，错误 `#B42318`，背景 `#F4F7FA`。
- Ant Design 统一表格、筛选、抽屉、表单和状态标签；圆角不超过 6px，不使用渐变和装饰性大卡片。
- 正文使用 16px 中文系统字体；颜色不能是唯一状态信息，状态必须有文字和图标。

### 6.2 工作台页面

| 页面 | 必须内容 | 关键验收 |
|---|---|---|
| 总览 | 在线、授权、待同步、最近任务、异常摘要 | 断网和授权状态始终可见 |
| 货品 | SKU、条码、分类、单位、成本、启用状态 | 支持筛选、分页、导入和导出 |
| 库存流水 | 事件、来源、数量、仓库、时间、冲销关系 | 只能追加或冲销，不能静默覆盖 |
| 导入向导 | 映射、预览、行级错误和修复 | 错误码可定位到行/字段 |
| 分析 | 期间、范围、版本、进度、指标、预警 | 结果含引擎/公式版本和警告 |
| 调度 | 任务计划、状态、下次执行和失败原因 | UI 不直接修改运行状态 |
| 报告 | HTML/PDF/CSV 导出、运行追溯 | 报告与 `run_id` 一一对应 |
| 备份 | 自动备份、手动备份、恢复和校验 | 恢复失败不覆盖当前数据库 |

### 6.3 Web 页面

- 商户端重点显示许可证、设备在线/离线、版本、最后心跳、任务成功率、同步积压和绑定状态。
- 开发者端提供商户筛选、设备详情、配置发布、版本灰度、日志检索、诊断包和高风险操作审计。
- 移动 Web 只保证登录、设备状态、任务状态和绑定管理；导入和分析留在桌面端。

## 7. 数据库实施任务

### 7.1 本地 SQLite

- 依次提交 `0001_meta` 至 `0007_sync_config`；每次迁移同时提交 Model、Repository、Factory 和测试。
- 表按主数据、库存事实、分析治理、同步配置分组，具体字段以主基线第 35 节为准。
- 连接启动执行 `foreign_keys=ON`、`journal_mode=WAL`、`busy_timeout=5000`；只允许一个写入进程。
- `inventory_event` 是事实来源，`inventory_balance` 是可重建投影；重复 `event_id` 必须幂等。
- 数据库和备份放在 `%LOCALAPPDATA%\WarehouseWorkbench`，不放安装目录。

### 7.2 云端 PostgreSQL

- 依次提交 `0001_control_meta` 至 `0007_release`；生产和 staging 使用同主版本。
- 使用 `identity`、`entitlement`、`capability`、`operations`、`sync`、`observability` 逻辑 Schema。
- 所有租户表含 `tenant_id`；评估并实施 RLS；Refresh Token 只存哈希或指纹。
- 云端只保存控制面、状态和短期加密信封；禁止建立完整商户 `sku/movement/unit_cost` 业务表。

## 8. 同步和调度实现

1. Agent 启动读取本地配置缓存，向 `/heartbeat` 上报版本、Schema、Engine、待同步数。
2. 网络正常时通过 SSE 收到通知，并用 `/tasks/pull`、`/sync/events/pull` 拉取实际数据。
3. 小程序事件进入 `sync_envelope`，工作台拉取后写入 `sync_inbox`，本地校验成功才写 `inventory_event`。
4. 本地事务成功后发送 ACK；失败事件保留错误码和原密文状态，不自动伪造成功。
5. 任务由云端定义、本地执行；完整分析结果和报告留在本地，云端只收状态、耗时、版本和脱敏摘要。
6. 所有重试使用指数退避和幂等键；状态严格遵守主基线的任务、同步和设备状态机。

## 9. A 的测试和验收

### 9.1 自动化测试

- API：认证、Scope、租户隔离、幂等、配置签名、过期和限流。
- 数据库：空库迁移、升级迁移、外键/唯一/CHECK、事件守恒、备份恢复和 SQLite 锁。
- 工作台：导入、错误修复、FakeEngine、真实 Engine 接入、报告、托盘、单实例和断网。
- Web：Playwright 覆盖登录、设备状态、配置发布、任务状态、开发者越权和移动宽度。
- 发布：安装、卸载、升级、回滚、代码签名、SHA-256、干净机和无 Python 环境运行。

### 9.2 A 完成定义

- `uv run pytest`、`pnpm lint`、`pnpm typecheck`、`pnpm test:e2e` 全部通过。
- 空库、升级库和恢复库有实际执行记录；迁移失败进入安全模式。
- FakeEngine 和真实 engine wheel 都能完成“导入→分析→报告→备份”。
- 商户 API 无法跨租户；开发者 API 无法通过商户 Scope 调用。
- 安装包、OpenAPI、数据库 Schema、客户端和日志均可追溯到版本 Tag。

## 10. 交给 B 的内容与接收 B 的内容

### A 交给 B

- `EngineDataset` 适配器示例、脱敏 fixture、输入列映射、单位和错误码。
- 固定运行命令、Python/依赖版本、样例输入和预期序列化结果。
- 本地数据库查询结果必须通过 JSON/Schema 导出，B 不打开 A 的数据库文件。

### A 接收 B

- 版本化 engine wheel、`AnalysisRequest/Result` Schema、公式版本和兼容矩阵。
- 黄金数据、边界数据、Warning 码、性能基线和变更日志。
- 不接受直接复制公式到 UI、数据库查询或报告模板的实现。

## 11. A 的风险控制

| 风险 | 控制措施 |
|---|---|
| 本地数据泄露 | 用户目录 ACL、DPAPI、导出加密、日志白名单；不虚假承诺文件被盗后不可读 |
| 云端误收业务数据 | API 字段白名单、日志脱敏测试、同步信封 TTL 和 ACK 删除 |
| 两人接口漂移 | OpenAPI/Schema 版本化、契约测试、PR 必须附兼容说明 |
| 升级损坏数据库 | 升级前备份、临时恢复、完整性检查、失败安全模式 |
| 开发者越权 | 后端 Scope、RLS、限时支持授权、审计和只读优先 |

**A 的发布门槛：G5 安装/升级/恢复/安全证据、G6 试点反馈和 G7 审批未完成前，不得宣称生产可用。**

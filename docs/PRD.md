# 仓库品类分析决策工具 —— 产品需求文档（PRD）

| 项 | 值 |
|---|---|
| 文档版本 | **1.0** |
| 更新时间 | **2026-08-31** |
| 文档状态 | 基于代码现状逆向梳理（不描述未实现能力） |
| 对应代码基线 | `master` @ `5f66454`（PR #12 合并后），分支 `feature/a-m2-ab-integration-workflow` |
| 适用范围 | `apps/workbench-desktop`、`apps/web`、`services/control-plane`、`local-data`、`packages/warehouse-engine`、`packages/contracts-*` |

## 版本基线对照表

本文所有描述均以以下组件版本为准，任一组件版本变更需同步更新本文。

| 组件 | 版本 | 版本定义位置 |
|---|---|---|
| `warehouse-engine`（分析引擎） | **0.2.0** | `packages/warehouse-engine/src/warehouse_engine/__version__.py` |
| 公式口径 `formula_version` | **0.1.0** | 同上（口径冻结于 `docs/formula-spec.md`） |
| `contracts-python` | **0.2.0** | `packages/contracts-python/pyproject.toml` |
| 契约 Schema `schema_version` | **1.0** | `contracts/analysis.py::SCHEMA_VERSION` |
| `control-plane`（云端控制平面） | **0.1.0** | `services/control-plane/pyproject.toml`、`app/settings.py::APP_VERSION` |
| `workbench-desktop`（桌面工作台） | **0.1.0** | `apps/workbench-desktop/pyproject.toml`、`app/presentation/main_window.py::APP_VERSION` |
| `local-data`（本地库包） | **0.1.0** | `local-data/pyproject.toml` |
| `web`（Web 端） | **0.1.0** | `apps/web/package.json` |
| 本地库 Schema | **local-0006** | `local_meta.db_schema_version`（Alembic revision `0006_report_backup`） |
| 云端库 Schema | **control-0004** | `control_meta.db_schema_version`（Alembic revision `control_0004`） |
| Skill 清单 `manifest_version` | **1.0** | `skills/manifest.json` |

---

## 一、术语表（三份文档统一口径）

| 术语 | 英文/代码标识 | 含义 |
|---|---|---|
| 商户 | `tenant` | 使用本工具的客户主体，主数据隔离单位 |
| 开发者 | `developer` | 平台运营/技术支持方，拥有 `developer` Scope |
| 工作台 | Workbench | 商户本机运行的 PySide6 桌面应用，本地业务数据的唯一落点 |
| 控制平面 | Control Plane | 云端 FastAPI 服务，负责账号、许可证、设备、状态，不存业务明细 |
| 引擎 | `warehouse-engine` | 纯 Python 分析引擎，无 UI/HTTP/ORM 依赖 |
| Skill | Skill | 分析能力编排层（参数校验 → 计算顺序 → 结果组合），不执行计算 |
| 库存事件 | `inventory_event` | 库存事实来源，任何库存变化都以此表记录 |
| 余额 | `inventory_balance` | 由事件重放得出的**可重建投影**，非事实来源 |
| 导入批次 | `import_batch` | 一次 CSV 导入的执行记录与统计 |
| 分析运行 | `analysis_run` | 一次分析执行的元数据记录 |
| 报告产物 | `report_artifact` | 一次报告导出的产物登记（HTML / CSV） |
| 备份记录 | `backup_record` | 一次全库备份的登记与校验状态 |
| 许可证 | `license` | 商户的服务授权，决定功能可用性与设备数上限 |
| 离线宽限期 | Grace | 许可证到期后仍可继续使用的天数，来自运行配置而非许可证表 |
| 设备 | `device` | 已注册的工作台实例，按 `(tenant_id, fingerprint)` 唯一 |
| 会话 | `session` | 一次登录，承载 Refresh Token 轮换状态 |
| 分析期间 | `[start_date, end_date)` | **左闭右开**区间，是全部计算口径的时间基准 |

---

## 二、项目背景与目标

### 2.1 背景

中小仓库在库存管理上面临三类痛点：

1. **数据有但不会用** —— 库存流水散落在 Excel/CSV 中，缺少统一、可对比的指标口径。
2. **采购靠经验** —— 缺少周转率、动销率、库龄结构等量化依据。
3. **结果无法持续触达** —— 分析结论停留在一次性报表里，无法形成持续的经营动作。

### 2.2 产品定位

**本地优先（Local-First）** 的库存品类分析决策工具：

- 商户的货品、库存流水、成本、采购与分析明细**全部保存在商户自己的电脑上**（SQLite）；
- 云端控制平面只管理账号、许可证、设备、配置与状态，**不建 `sku`/`movement`/`unit_cost` 表，不保存商户业务明细**（DECISIONS.md D-011，隐私红线）；
- 断网时本地核心工作（导入 → 分析 → 报告 → 备份）不中断，恢复网络后再同步状态。

### 2.3 产品目标（可验证）

| 目标 | 当前实现状态 | 验证依据 |
|---|---|---|
| G1 业务数据不出本机 | ✅ 已达成 | 云端库 10 张表中无任何业务明细表；`inventory_event` 等仅存在于本地库 |
| G2 数据可追溯 | ✅ 已达成 | 每次分析运行落 `analysis_run` 全量 `AnalysisResult` JSON + `input_summary.dataset_digest`（SHA-256） |
| G3 数据可恢复 | ✅ 已达成 | 全量备份（`VACUUM INTO`）+ SHA-256 校验 + 恢复前安全备份 + 原子替换，失败时当前库零改动 |
| G4 指标口径可复现 | ✅ 部分达成 | KPI/COGS 9 个公式已实装且确定性（同输入同输出逐字节一致）；ABC/库龄/补货/预测/基准 5 类仍为占位 |
| G5 授权可控 | ✅ 已达成 | 许可证 + 离线宽限期 + 设备数上限 + Scope 鉴权 + 审计日志 |
| G6 错误不静默 | ✅ 已达成 | 导入错误进 `import_error` 并给出中文修复建议；分析异常以结构化 Warning 返回 |

---

## 三、用户角色

### 3.1 角色清单

| 角色 | 代码标识 | 数据归属 | 主要诉求 |
|---|---|---|---|
| 商户主账号 | `MERCHANT_OWNER` | 本商户（必须有 `tenant_id`） | 导入自己的数据、跑分析、出报告、做备份 |
| 开发者 | `DEVELOPER` | 无租户（`tenant_id` 为 `NULL`） | 管理商户授权、许可证、配置、查看设备与技术日志 |

### 3.2 角色与权限（Scope）映射

来自 `services/control-plane/app/domain/account.py::ROLE_SCOPES`：

| 角色 | Scope 集合 | 可访问 |
|---|---|---|
| `MERCHANT_OWNER` | `{merchant}` | 商户侧接口：账号上下文、设备、事件流、配置、任务、同步、心跳 |
| `DEVELOPER` | `{developer}` | 开发者侧接口：商户列表、技术日志 |

约束（数据库 CHECK `ck_account_tenant_required_for_merchant`）：`DEVELOPER` 角色账号的 `tenant_id` 可为 `NULL`，`MERCHANT_OWNER` 必须有 `tenant_id`。

### 3.3 账号状态机

`AccountStatus`：`ACTIVE` → {`LOCKED`, `DISABLED`}；`LOCKED` → {`ACTIVE`, `DISABLED`}；`DISABLED` 为终态。

- 连续登录失败 **5 次**（`MAX_FAILED_ATTEMPTS`）→ 置 `LOCKED`，锁定 **15 分钟**（`LOCK_DURATION`）；
- 锁定期到期**不自动改状态**，由 `is_locked()` 按 `now < locked_until` 判定为已恢复；
- `DISABLED` 为终态，不可登录。

### 3.4 商户状态机

`TenantStatus`：`ACTIVE` ⇄ `SUSPENDED`（双向，可恢复）。`SUSPENDED` 商户的账号在登录阶段被拒绝（`AUTH_FORBIDDEN` / `reason=TENANT_SUSPENDED`）。

---

## 四、核心业务流程

### 4.1 总体数据流

```text
【本地数据面 · 商户电脑】
  CSV 文件
    │ 导入向导（选文件 → 字段映射 → 预览 → 校验 → 提交）
    ▼
  SQLite 本地库 ── sku / inventory_event / stock_snapshot ──┐
    │                                                       │ EngineDataset 适配器
    │  余额投影重建 inventory_balance                        ▼
    │                                              【引擎计算面 · 进程内调用】
    │                                                validate_dataset → analyze
    │                                                （KPI/COGS 9 公式真实计算）
    │                                                       │ AnalysisResult
    ▼                                                       ▼
  analysis_run / analysis_result ◄───────────────── 结果持久化
    │
    ├──► report_artifact（HTML / CSV 报告导出）
    └──► backup_record（全量备份 / 恢复）

【云端控制面 · 开发者】
  FastAPI /api/v1 ◄── HTTPS ── 工作台（登录 / 设备注册 / SSE 状态流）
        │                    ◄── HTTPS ── Web 端（登录 / 设备列表 / 状态看板）
        ▼
  PostgreSQL 控制库（tenant / account / session / device / license / feature_grant / audit_log）
```

### 4.2 流程一：数据导入（桌面工作台）

```text
开始
 └─ 选择导入类型：主数据(master_data) | 库存事件(inventory_events)
 └─ 选择 CSV 文件 → 编码探测（utf-8-sig → gbk，均失败则阻断）
 └─ 字段映射（契约字段 → CSV 列；归一化精确匹配优先，≥6 字符才做保守模糊匹配）
 └─ 预览前 5 行
 └─ 【仅事件导入】前置依赖检测：sku 表非空 且 warehouse 表非空，否则阻断
 └─ 执行导入
     ├─ 文件 SHA-256 命中已 COMPLETED 批次 → 判为重复导入，不创建批次
     ├─ 创建 import_batch（status=RUNNING）
     ├─ 逐行校验 → 错误行进 import_error（错误隔离），合法行入库
     ├─ 事件 upsert（重复 event_id 幂等跳过并计数）
     └─ 登记行数/错误数 → COMPLETED（全行失败则 FAILED）
 └─ 事件导入且有新增 → 重建 inventory_balance 投影
 └─ 错误明细页展示（错误码分布 + 逐行明细含修复建议）
```

### 4.3 流程二：本地分析（桌面工作台）

```text
点击「运行分析」
 └─ SqliteDatasetAdapter.load()
     ├─ 期间 = 覆盖 inventory_event.occurred_at 与 stock_snapshot.snapshot_date 的日期范围
     │       （空库则取当日单日）
     ├─ warehouse_ids = 全部 is_active=True 的仓库（升序）
     └─ 构造 EngineDataset：skus / movements / snapshots（replenishment 恒为空）
 └─ 生成新 run_id（run-{uuid4前12位}），保证可重复运行不撞 UNIQUE
 └─ 无事实数据（movements 与 snapshots 均为空）→ 直接返回「无数据」提示，不校验不落库
 └─ validate_dataset → 阻断性错误则展示 issues，不调用 analyze
 └─ analyze（progress 回调 0.0 → 0.3 → 0.9 → 1.0）
 └─ 落库：analysis_run（SUCCEEDED）+ analysis_result（result_type='full_result'）
 └─ 结果表格展示 9 条指标 + warnings
```

### 4.4 流程三：报告导出（桌面工作台）

```text
选择 run_id + 格式（HTML | CSV）
 └─ 从 analysis_result 取回 AnalysisResult
 └─ 渲染
     ├─ HTML：元信息块 + 指标表 6 列 + 警告表 5 列（内联 CSS，无外部资源）
     └─ CSV：UTF-8 with BOM，4 列（指标名 / SKU / 分类 / 值）
 └─ 写文件到 reports 目录（文件名 {run_id}.{ext}，防路径穿越）
 └─ 计算 SHA-256 → upsert report_artifact（同 run_id+format 覆盖更新，不重复登记）
```

### 4.5 流程四：备份与恢复（桌面工作台）

```text
备份：生成 backup-{时间戳}-{backup_id}.db
      → sqlite VACUUM INTO（全量一致快照）
      → 计算 SHA-256 / size_bytes / db_schema_version
      → 登记 backup_record（CREATED）
      → 打开备份文件本身复验（PRAGMA integrity_check + 6 张关键表可读）
      → 通过置 VERIFIED（写 verified_at），失败置 FAILED

恢复：校验备份文件 SHA-256 与登记值一致（不一致即放弃，当前库零改动）
      → 复制为临时库并复验（integrity_check + alembic_version 一致 + 关键表可读）
      → 自动安全备份当前库（AUTO），失败则中止恢复
      → 关闭连接池 → WAL checkpoint → os.replace 原子替换
      → 补登记安全备份记录（原记录随旧库被替换）
      → 清理临时文件
```

### 4.6 流程五：登录与设备注册（工作台 / Web → 控制平面）

```text
POST /api/v1/auth/login（username + password + client_type）
 └─ 账号存在？（不存在不校验密码，防账号枚举）
 └─ 可登录？（DISABLED 或锁定期内 → 403）
 └─ 密码校验（Argon2id）→ 失败则计数，5 次锁定
 └─ 商户是否 SUSPENDED？
 └─ 建会话 + 签发令牌对（Access 15 分钟 / Refresh 30 天，可轮换）
 └─ 评估许可证（不阻断登录）
 └─ 写审计 AUTH_LOGIN / SUCCESS
 └─ 发布 auth.login 事件到 SSE 状态流

POST /api/v1/devices/register（name + fingerprint）
 └─ 鉴权：merchant Scope + 许可证放行（ACTIVE/GRACE）
 └─ 同 (tenant_id, fingerprint) 已存在 → 幂等返回已有设备
 └─ 已存在且状态 REVOKED → 拒绝（DEVICE_REVOKED）
 └─ 在册设备数 ≥ max_devices → 拒绝（DEVICE_LIMIT_EXCEEDED）
 └─ 写审计 DEVICE_REGISTER
```

---

## 五、功能清单及优先级

优先级定义：**P0** 已实装且为核心闭环；**P1** 已实装但为占位/骨架；**P2** 契约或数据模型已就绪、实现待后续里程碑。

### 5.1 桌面工作台（`apps/workbench-desktop`）

| # | 功能 | 优先级 | 状态 | 代码位置 |
|---|---|---|---|---|
| F-A-01 | CSV 主数据导入（5 步向导） | P0 | 已实装 | `application/import_manager.py`、`presentation/import_wizard.py` |
| F-A-02 | CSV 库存事件导入（含前置依赖检测） | P0 | 已实装 | 同上 |
| F-A-03 | 导入错误隔离与修复建议 | P0 | 已实装 | `import_error` 表 + `ERROR_CODE_LABELS` |
| F-A-04 | 重复文件导入检出（SHA-256） | P0 | 已实装 | `import_manager.run_import` |
| F-A-05 | 本地分析执行（进度回调） | P0 | 已实装 | `application/analysis_usecase.py` |
| F-A-06 | 分析历史列表与结果重开 | P0 | 已实装 | `presentation/analysis_page.py`、`SqlResultStore` |
| F-A-07 | 报告导出（HTML / CSV） | P0 | 已实装 | `infrastructure/report/exporter.py` |
| F-A-08 | 全量备份（VACUUM INTO + 校验） | P0 | 已实装 | `infrastructure/backup/backup_service.py` |
| F-A-09 | 备份恢复（安全备份 + 原子替换） | P0 | 已实装 | 同上 |
| F-A-10 | 云端登录（对话框） | P0 | 已实装 | `presentation/login_dialog.py` |
| F-A-11 | 设备自动注册（设备指纹） | P0 | 已实装 | `infrastructure/api_client/device_fingerprint.py` |
| F-A-12 | SSE 状态流消费（后台线程） | P0 | 已实装 | `workers/status_stream_worker.py` |
| F-A-13 | 在线状态卡片 | P1 | 部分实装（仅一张在线状态卡） | `presentation/status_card.py` |
| F-A-14 | 货品管理页 | P2 | 占位空页 | `main_window.py` NAV `货品` |
| F-A-15 | 库存流水页 | P2 | 占位空页 | `main_window.py` NAV `库存流水` |
| F-A-16 | 调度页（定时任务） | P2 | 占位空页（M3 托盘 Agent） | `main_window.py` NAV `调度` |
| F-A-17 | 设置页 | P2 | 占位空页 | `main_window.py` NAV `设置` |
| F-A-18 | 结构化日志落盘 | P2 | 目录占位，未实装 | `infrastructure/logging/__init__.py` |

主窗口导航项固定 9 项：`总览 / 货品 / 库存流水 / 导入 / 分析 / 调度 / 报告 / 备份 / 设置`，启动默认选中「分析」。

### 5.2 云端控制平面（`services/control-plane`）

| # | 功能 | 优先级 | 状态 | 接口 |
|---|---|---|---|---|
| F-C-01 | 健康检查 | P0 | 已实装 | `GET /health` |
| F-C-02 | 账号登录 / 刷新 / 注销 | P0 | 已实装 | `POST /api/v1/auth/*` |
| F-C-03 | 当前账号上下文 | P0 | 已实装 | `GET /api/v1/account/me` |
| F-C-04 | 设备注册（幂等 / 吊销 / 上限） | P0 | 已实装 | `POST /api/v1/devices/register` |
| F-C-05 | 设备列表 | P0 | 已实装 | `GET /api/v1/devices` |
| F-C-06 | SSE 状态流 | P0 | 已实装 | `GET /api/v1/events/stream` |
| F-C-07 | 状态快照（轮询降级） | P0 | 已实装 | `GET /api/v1/events/snapshot` |
| F-C-08 | 许可证评估与离线宽限期 | P0 | 已实装（内嵌于鉴权链） | `require_tenant_with_license` |
| F-C-09 | 审计日志写入 | P0 | 已实装 | `application/audit.py` |
| F-C-10 | 配置下发 | P2 | stub（恒 501） | `GET /api/v1/config` |
| F-C-11 | 设备心跳 | P2 | stub（恒 501） | `POST /api/v1/heartbeat` |
| F-C-12 | 任务列表 / 拉取 | P2 | stub（恒 501） | `GET /api/v1/tasks`、`POST /api/v1/tasks/pull` |
| F-C-13 | 同步事件拉取 / ACK | P2 | stub（恒 501） | `GET /api/v1/sync/events/pull`、`POST /api/v1/sync/ack` |
| F-C-14 | 技术日志查询 | P2 | stub（恒 501） | `GET /api/v1/telemetry` |
| F-C-15 | 商户列表（开发者端） | P2 | stub（恒 501） | `GET /api/v1/merchants` |

### 5.3 Web 端（`apps/web`）

| # | 功能 | 优先级 | 状态 | 路由 |
|---|---|---|---|---|
| F-W-01 | 官网首页 | P1 | 已实装（静态介绍 + 登录入口） | `/` |
| F-W-02 | 登录页 | P0 | 已实装 | `/login` |
| F-W-03 | 商户看板（许可证 / 设备 / 状态流） | P1 | 已实装 | `/dashboard` |
| F-W-04 | 开发者门户 | P2 | 占位页 | `/developers` |

### 5.4 分析引擎（`packages/warehouse-engine`）

| # | 功能（Skill） | 公式 ID | 优先级 | 状态 |
|---|---|---|---|---|
| F-E-01 | `kpi`：库存 KPI 与 COGS | F-KPI-001~008、F-COGS-001 | P0 | **已实现** |
| F-E-02 | `abc-aging`：ABC 分层 / 库龄 / 呆滞 | F-ABC-001、F-AGE-001、F-STALE-001 | P2 | 存根（仅冻结公式 ID） |
| F-E-03 | `replenishment`：安全库存 / 补货点 / 建议量 | F-REPL-001~003 | P2 | 存根（公式以注释冻结） |
| F-E-04 | `forecasting`：需求预测与误差 | F-FCST-001~002 | P2 | 存根 |
| F-E-05 | `benchmark`：行业基准比较 | F-BM-001 | P2 | 存根 |

**关键现状**：`WarehouseEngine.analyze()` 当前**只调用 `inventory_kpi.calculate()`** 一个计算器；其余四类的 `calculate()` 均为 `raise NotImplementedError`，但**从未被引擎主流程调用**，因此不会抛错。结果中统一以 `code=ANALYSIS_PLACEHOLDER`（`severity=INFO`、`blocking=False`）的非阻断 Warning 标注。

---

## 六、功能详细说明

### 6.1 F-A-01 / F-A-02：CSV 导入

**前置条件**
- 文件为 CSV（扩展名过滤器 `*.csv`）；
- 编码可被 `utf-8-sig` 或 `gbk` 解码；
- 文件非空（存在表头行）；
- 必填契约字段已映射到存在的 CSV 列。

**契约字段与 CSV 列**

主数据（`导入类型 = master_data`）：

| 契约字段 | 必填 | 目标列 | 说明 |
|---|---|---|---|
| `sku_id` | ✅ | `sku.sku_id` | 唯一键，重复即 `SKU_DUPLICATE` |
| `name` | ✅ | `sku.name` | |
| `category` | — | `sku.category` | |
| `sub_category` | — | `sku.sub_category` | |
| `unit` | — | `sku.unit` | |
| `unit_cost` | — | `sku.unit_cost` | 非负；Decimal 字符串 |
| `industry` | — | `sku.industry` | |

库存事件（`导入类型 = inventory_events`）：

| 契约字段 | 必填 | 目标列 | 说明 |
|---|---|---|---|
| `event_id` | ✅ | `inventory_event.event_id` | 唯一键，重复幂等跳过 |
| `sku_id` | ✅ | `inventory_event.sku_id` | 必须已存在于 `sku` 表 |
| `warehouse_id` | ✅ | `inventory_event.warehouse_id` | 必须已存在于 `warehouse` 表 |
| `move_type` | ✅ | `inventory_event.move_type` | 8 选 1（见下） |
| `quantity` | ✅ | `inventory_event.quantity` | 恒 > 0，方向由 `move_type` 表达 |
| `occurred_at` | ✅ | `inventory_event.occurred_at` | 严格 `YYYY-MM-DD` → 转 `YYYY-MM-DDT00:00:00+00:00` |
| `unit_cost` | — | `inventory_event.unit_cost` | 非负 |
| `source_ref` | — | `inventory_event.source_ref` | 来源单据号 |

**`move_type` 取值（本地库口径，8 种）**

| 值 | 余额方向 | 语义 |
|---|---|---|
| `INBOUND` | +q | 入库 |
| `OUTBOUND` | −q | 出库 |
| `RETURN` | +q | 退货入库 |
| `SCRAP` | −q | 报废 |
| `ADJUSTMENT` | 差额 | 盘点：`quantity` 记实盘数，调整量 = 实盘数 − 当前账面 |
| `TRANSFER_IN` | +q | 调拨入仓（单仓口径下调拨拆为出/入两条事件） |
| `TRANSFER_OUT` | −q | 调拨出仓 |
| `REVERSAL` | 反转 | 冲销：按 `reversal_of` 指向的原事件取反方向 |

> **边界规则**：契约层的 `MoveType` 有 7 个值（`TRANSFER`/`STOCKTAKE` 等），与本地库 8 值口径不同，导入时使用**本地库口径**；契约的 `TRANSFER`/`STOCKTAKE` 会被导入校验拦截为 `MOVE_TYPE_INVALID`。二者由 `SqliteDatasetAdapter` 的映射表转换。

**异常与边界规则**

| 场景 | 处理 | 错误码 |
|---|---|---|
| 编码无法识别 | 阻断，不创建批次 | `IMPORT_ENCODING_FAILED` |
| 必填字段未映射 / 映射列不在表头 | 阻断，不创建批次 | `MISSING_REQUIRED_COLUMN` |
| 无表头行 | 阻断 | `EMPTY_FILE` |
| 事件导入但 `sku` 表为空 | 阻断，提示「请先导入 SKU 主数据后再导入库存事件」 | 前置依赖（非错误码） |
| 事件导入但 `warehouse` 表为空 | 阻断，提示「仓库数据缺失，请先创建仓库或导入主数据」 | 前置依赖 |
| 文件 SHA-256 命中已 COMPLETED 批次 | 不创建批次，提示已导入过 | `DuplicateImportFound` |
| 数量 ≤ 0 | 该行丢弃 | `QUANTITY_INVALID` |
| `sku_id` 不存在 | 该行丢弃 | `SKU_NOT_FOUND` |
| `warehouse_id` 不存在 | 该行丢弃 | `WAREHOUSE_NOT_FOUND` |
| `move_type` 非法 | 该行丢弃 | `MOVE_TYPE_INVALID` |
| 日期非严格 `YYYY-MM-DD` | 该行丢弃（如 `2026-8-9` 判非法） | `DATE_INVALID` |
| 数值无法解析 / NaN / Infinity | 该行丢弃 | `DECIMAL_INVALID` |
| `unit_cost` < 0 | 该行丢弃 | `UNIT_COST_INVALID` |
| 主数据 `sku_id` 已存在 | 该行丢弃（**create-only，不覆盖**） | `SKU_DUPLICATE` |
| 事件 `event_id` 重复 | 静默跳过并计数（含批内重复） | 非错误，计入 `skipped` |

**其他规则**
- `row_no = 行下标 + 2`（表头为第 1 行）；
- 错误行进 `import_error` 并附中文 `suggestion`；合法行正常入库（**错误隔离**）；
- 批次状态：`RUNNING` → `COMPLETED`；若 `row_count > 0 且 error_count == row_count` → `FAILED`；
- 事件导入且 `inserted > 0` 时触发 `inventory_balance` 投影重建。

### 6.2 F-A-05：本地分析

**前置条件**：本地库已完成 Alembic 迁移（启动时自动 `upgrade head`）；至少存在 1 条 `inventory_event` 或 `stock_snapshot`。

**数据集构造口径**
- 期间：覆盖 `inventory_event.occurred_at` 与 `stock_snapshot.snapshot_date` 的日期范围；空库取当日单日；
- 仓库范围：全部 `is_active=True` 的 `warehouse.warehouse_id`（升序），空列表视为不限；
- SKU **不过滤 `is_active`**（期间内事件可能引用已停用 SKU，避免引擎误报 `SKU_NOT_FOUND`）；
- 事件排序 `(occurred_at, event_id)`，保证确定性；
- 快照按 `(sku_id, snapshot_date, warehouse_id)` 聚合求和（契约 `SnapshotRecord` 无库位/批次维度）；
- `replenishment` 分区**恒为空列表**；`currency` 固定 `CNY`。

**异常与边界规则**

| 场景 | 处理 |
|---|---|
| 本地库无任何事实数据 | 不校验、不计算、不落库，UI 提示「本地库暂无分析数据：请先通过"导入"页录入主数据与库存事件」 |
| 校验未通过 | 不调用 `analyze`、不落库，UI 逐条展示 `[code] field: reason` |
| 单位成本缺失 | 出库成本按 0 计、库存价值回退快照 `inventory_value`；仍缺失则该 SKU 不计入库存价值合计；并发 `UNIT_COST_MISSING` 警告（**不静默丢弃 SKU**） |
| 期末库存 ≤ 0 | 库存价值记 0，不发 `UNIT_COST_MISSING` |
| 期内任一时点余额 < 0 | 该 SKU 期间 COGS 置 0（其他 SKU 不受影响），并发 `NEGATIVE_BALANCE` 警告 |
| 期间天数 = 0（`start == end`） | `F-KPI-008` 覆盖天数降级为 `null`，`reason=zero_period_days` |

**降级 `reason` 取值（代码中仅此 6 个）**：`empty_dataset`、`avg_inventory_value_nonpositive`、`turnover_nonpositive`、`nonpositive_closing`、`zero_period_days`、`no_outflow`。

> 指标值为 `null` 时，契约中以字符串 `"null"` 表示（`ResultMetric.value` 不支持 `None`）。

### 6.3 F-A-07：报告导出

**前置条件**：目标 `run_id` 在 `analysis_run` 中存在。

| 格式 | 内容 | 编码 | 可复现性 |
|---|---|---|---|
| HTML | 元信息块（运行编号 / 期间 / 引擎版本 / 公式版本 / **生成时间**）+ 指标表 6 列 + 警告表 5 列 | UTF-8 | ❌ 含生成时间戳，跨秒重复导出字节不同 |
| CSV | 4 列：指标名 / SKU / 分类 / 值 | UTF-8 with BOM | ✅ 同 run 重复导出字节一致 |

**边界规则**
- 文件名 `re.sub(r"[^A-Za-z0-9._-]+", "_", run_id).strip("._") or "run"`，防路径穿越；
- 同 `(run_id, format)` 重复导出走 **upsert 更新**，不产生重复登记；
- 产物 SHA-256 写入 `report_artifact.sha256`，可校验完整性；
- `run_id` 不存在时返回失败文案「未找到分析运行：{run_id}（无法导出报告）」。

### 6.4 F-A-08 / F-A-09：备份与恢复

**前置条件**：当前数据库文件存在（备份）；备份文件存在且已登记（恢复）。

| 环节 | 校验 |
|---|---|
| 备份后 | `PRAGMA integrity_check` = `ok`；6 张关键表（`sku`/`inventory_event`/`inventory_balance`/`import_batch`/`analysis_run`/`analysis_result`）可读 |
| 备份后 | SHA-256 与 `size_bytes` 登记；`VERIFIED` 必须有 `verified_at` |
| 恢复前 | 备份文件 SHA-256 与 `backup_record.sha256` 一致（不一致即放弃） |
| 恢复前 | 临时副本 `integrity_check` 通过 |
| 恢复前 | 临时副本 `alembic_version` 与当前库一致（禁止静默升/降级） |
| 恢复前 | 自动安全备份（`AUTO`）成功 |
| 恢复 | `PRAGMA wal_checkpoint(TRUNCATE)` → 清理 `-wal`/`-shm` → `os.replace` 原子替换 |

**核心不变量**：任一步失败，**当前库字节零改动**；失败路径不写任何 `backup_record`。

### 6.5 F-C-02：认证（登录 / 刷新 / 注销）

**令牌**

| 令牌 | 类型 | 有效期 | 说明 |
|---|---|---|---|
| Access Token | JWT（HS256） | **15 分钟** | 携带 `account_id`/`tenant_id`/`role`/`scopes`/`session_id`/`device_id` |
| Refresh Token | 不透明随机串 | **30 天** | 明文仅在签发响应中出现一次，库内存 SHA-256 指纹 |

**异常与边界规则**

| 场景 | HTTP | 错误码 | 说明 |
|---|---|---|---|
| 账号不存在 | 401 | `AUTH_REQUIRED` | **不执行密码校验**，与密码错误返回同一文案，防账号枚举 |
| 密码错误 | 401 | `AUTH_REQUIRED` | `failed_attempts += 1`，达 5 次锁定 15 分钟 |
| 账号 `DISABLED` 或锁定期内 | 403 | `AUTH_FORBIDDEN` | `details.reason = ACCOUNT_UNAVAILABLE` |
| 商户 `SUSPENDED` | 403 | `AUTH_FORBIDDEN` | `details.reason = TENANT_SUSPENDED` |
| 刷新令牌对应会话已撤销/过期 | 401 | `AUTH_REQUIRED` | `details.reason = SESSION_INACTIVE` |
| **刷新令牌重放**（命中 `previous_refresh_token_hash`） | 401 | `AUTH_REQUIRED` | **撤销该账号全部会话**，`details.reason = REFRESH_TOKEN_REUSED` |
| 刷新令牌无效（两个指纹均未命中） | 401 | `AUTH_REQUIRED` | 不写审计 |
| 重复注销 | 200 | — | 幂等，已撤销的会话也返回 200 |

**关键设计**：注销**立即生效** —— 受保护接口在校验令牌后还会回查会话 `revoked_at`，不等令牌自然过期。

### 6.6 F-C-04：设备注册

**前置条件**：持有有效 `merchant` Scope 令牌 且 许可证状态为 `ACTIVE` 或 `GRACE`。

| 场景 | HTTP | 错误码 |
|---|---|---|
| 同 `(tenant_id, fingerprint)` 已存在且未吊销 | 200 | — 幂等返回已有设备，刷新 `name`/`app_version`/`updated_at` |
| 同指纹设备状态为 `REVOKED` | 403 | `DEVICE_REVOKED` |
| 在册（未吊销）设备数 ≥ `max_devices` | 403 | `AUTH_FORBIDDEN`（`reason=DEVICE_LIMIT_EXCEEDED`） |
| 许可证 `EXPIRED`/`REVOKED`/`MISSING` | 403 | `LICENSE_EXPIRED` |

### 6.7 F-C-06 / F-C-07：状态流（SSE + 轮询降级）

- 主通道 **SSE**（DECISIONS.md D-006，第一版不引入 WebSocket）；
- 首帧发送快照，随后推送实时事件；**每 15 秒空闲发保活帧** `: keepalive`；
- 断线重连由浏览器自动携带 `Last-Event-ID`，首次连接可用查询参数 `last_event_id` 指定起点；
- 服务端维护**环形缓冲 200 条**事件用于续传补发；订阅者队列上限 100 条（慢消费者丢弃最旧事件，不阻塞发布方）；
- 响应头 `X-Polling-Fallback: /api/v1/events/snapshot`、`X-Polling-Interval: 30`；
- 连接失败时客户端降级为 **30 秒轮询** `GET /api/v1/events/snapshot`。

### 6.8 F-E-01：库存 KPI 计算（唯一已实现的分析能力）

**9 个公式（formula_version = 0.1.0）**

| 公式 ID | 指标名 | 单位 | 口径 |
|---|---|---|---|
| F-KPI-001 | `KPI.OPENING_QTY` | 件 | 期间初库存 = Σ 各 SKU 各仓桶期初 |
| F-KPI-002 | `KPI.CLOSING_QTY` | 件 | 期末库存，可为负，原样输出 |
| F-KPI-003 | `KPI.OUT_QTY` | 件 | 出库量，可为负（退货冲减），原样输出 |
| F-KPI-004 | `KPI.ACTIVE_RATIO` | ratio | 动销率 = 有出库的 SKU 数 ÷（期末 > 0 或期间有事件的 SKU 数） |
| F-KPI-005 | `KPI.INVENTORY_VALUE` | CNY | 库存价值 = Σ（期末 × 单位成本），缺失时回退快照 `inventory_value` |
| F-COGS-001 | `KPI.COGS` | CNY | 销售成本，移动加权平均法 |
| F-KPI-006 | `KPI.TURNOVER` | 次/期间 | 周转率 = COGS ÷ 平均库存价值 |
| F-KPI-007 | `KPI.TURNOVER_DAYS` | 天 | 周转天数 = 期间天数 ÷ 周转率 |
| F-KPI-008 | `KPI.COVERAGE_DAYS` | 天 | 覆盖天数 = 期末 ÷ 日均出库 |

**精度与舍入**
- 中间计算**不舍入**（全程 `Decimal` 精确算术）；
- 金额输出 `ROUND_HALF_UP` 至 **0.01**；
- 比率/天数输出保留 **6 位小数**；
- 数量保留输入 scale；
- JSON 传输与落库一律用 **Decimal 字符串**（如 `"123.45"`），禁止 float 作真值。

**确定性保证**：同一输入重复调用，序列化结果**逐字节一致**（无随机性、无时间依赖）。

---

## 七、非功能性需求

| 类别 | 要求 | 当前实现 / 依据 |
|---|---|---|
| **数据隐私** | 商户业务数据不出本机；云端不建业务明细表 | ✅ DECISIONS.md D-011；云端 10 张表无 sku/movement/unit_cost |
| **数据隐私** | 本地库第一版不加密（不启用 SQLCipher），依赖 Windows 用户 ACL；敏感凭证走 DPAPI | ✅ DECISIONS.md D-003；隐私说明须明示「数据库文件被盗后可读」 |
| **数据隐私** | 审计 `detail_json` 只允许 9 个白名单键，值必须是标量 | ✅ `domain/audit.py::ALLOWED_DETAIL_KEYS`，越界键静默丢弃 |
| **可追溯** | 每次分析记录 `run_id`/`engine_version`/`formula_version`/`dataset_digest` | ✅ `analysis_run` + `input_summary` |
| **可复现** | 同输入同输出（字节级） | ✅ 引擎测试 `test_analyze_deterministic_byte_identical` |
| **可恢复** | 恢复失败时当前库零改动 | ✅ 原子替换 + 恢复前双重校验 |
| **性能** | 1 万行 analyze ≤ 5 秒；10 万行 analyze ≤ 40 秒、全链路 ≤ 60 秒 | ✅ M1 实测：1 万行 1.49/1.80 秒；10 万行 15.80/12.38 秒、全链路约 36 秒（`scripts/perf_bench.py`，seed=20260829） |
| **性能** | 100 万行阈值 M3 冻结（当前 1 次实测 171.25 秒） | ⏳ 待冻结 |
| **可用性** | 断网时本地核心工作不中断 | ✅ 工作台登录失败/控制面不可达时进入离线模式，状态栏明示「离线」，不阻断本地操作 |
| **可用性** | 数据库不可达时 `/health` 仍返回 200，`database` 如实为 `down` | ✅ 不伪报 |
| **可观测** | 每个 HTTP 响应带 `X-Request-ID`（32 位 hex） | ✅ `RequestIDMiddleware` |
| **安全** | 密码哈希 Argon2id | ✅ `infrastructure/auth/passwords.py` |
| **安全** | 生产环境强制显式配置 `AUTH_SECRET`，禁止临时密钥 | ✅ `settings.resolve_auth_secret()` 在 `production` 下空密钥抛 `RuntimeError` |
| **安全** | 生产环境禁止使用内存仓储 | ✅ `build_memory_repositories()` 在 `production` 抛 `RuntimeError` |
| **安全** | 审计日志保留 180 天 | ⚠️ 迁移文档串声明，清理任务**未实现** |
| **兼容性** | 版本不兼容时显示明确升级提示 | ⏳ 矩阵已定义（`docs/compatibility-matrix.md`），工作台启动检查**未实现** |
| **编码** | 全链路 UTF-8；CSV 导出带 BOM 以兼容 Excel | ✅ |

---

## 八、版本规划

### 8.1 已交付

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M0** | 引擎骨架 + 契约冻结（B）；控制平面 / Web / 工作台 / 双库骨架（A） | ✅ PR #1、#2、#4 |
| **M1** | KPI/COGS 引擎实现 + 0.2.0 wheel（B）；本地业务闭环：导入 → 分析 → 报告 → 备份（A） | ✅ PR #3、#4 |
| **M2** | 五类分析 Skill 实现（B，engine 0.3.0）；真实认证 / 许可证 / 设备 / SSE 实装 + A/B 集成（A） | ✅ PR #5、#12 |

### 8.2 进行中 / 待办（依据代码中的占位与 stub 推导）

| 里程碑 | 待办项 | 依据 |
|---|---|---|
| **M3** | 托盘常驻 Agent（APScheduler + SQLAlchemyJobStore）、设备心跳、在线/降级/离线判定 | `POST /api/v1/heartbeat` 当前恒 501；设备状态 `ONLINE`/`DEGRADED`/`OFFLINE` 无任何代码路径写入 |
| **M3** | 配置下发（`GET /api/v1/config`）、任务队列（`GET /api/v1/tasks`、`POST /api/v1/tasks/pull`） | 3 个 stub 端点 |
| **M3** | 小程序同步信封中继（`GET /api/v1/sync/events/pull`、`POST /api/v1/sync/ack`）；AES-256-GCM，TTL 30 分钟，云端不解密 | 2 个 stub 端点 + `sync-envelope.schema.json` 已冻结 |
| **M3** | 开发者端：商户列表、技术日志 | 2 个 stub 端点 |
| **M3** | 100 万行性能阈值冻结 | CHANGELOG 明确「阈值 M3 冻结」 |
| **M2+** | ABC / 库龄 / 呆滞 / 补货 / 预测 / 行业基准 六类计算器实现 | `calculators/` 下 4 个存根 + `analyze()` 的 `ANALYSIS_PLACEHOLDER` 警告 |
| **M2+** | 工作台「货品」「库存流水」「调度」「设置」四个占位页实装 | `main_window.py` NAV 占位 |
| **M2+** | 结构化日志与脱敏规则落地 | `infrastructure/logging/` 仅目录占位 |
| **M2+** | 审计日志 180 天清理任务 | 迁移 0004 文档串声明，代码未实现 |
| **后续** | Web 端令牌从 localStorage 改为 httpOnly Cookie 或内存 + 轮换 | `apps/web/src/api/client.ts` 已知限制注释 |
| **后续** | 备份恢复 RPO/RTO 演练标准（方向：RPO ≤ 24h、RTO ≤ 4h） | DECISIONS.md 待补 ADR |
| **后续** | 开发者支持访问的限时授权与审计细则 | DECISIONS.md 待补 ADR |
| **后续** | 签名证书与密钥管理方案（M3 打包前冻结） | DECISIONS.md 待补 ADR |

### 8.3 版本规则（`docs/compatibility-matrix.md`）

- **Patch**：只修问题，不改 Schema；
- **Minor**：允许增加可选字段和功能；
- **Major**：才允许删除字段或改变公式口径；
- 删除公共字段、改类型或改单位**必须提升 `schema_version`**，补正反例与迁移说明，并经 A、B 共同评审；
- 任何规则变更**必须提升 `formula_version`**，重新生成黄金结果，并说明对旧报告的影响。

---

## 九、已知限制与待确认事项

| # | 限制 | 影响 | 建议 |
|---|---|---|---|
| L-1 | 分析能力无 HTTP 接口，仅进程内 Python 调用 | Web 端无法直接发起分析 | 若需远程分析，需新增网关端点 |
| L-2 | `POST /api/v1/heartbeat` 为 stub，设备状态恒为 `REGISTERED`，`last_seen_at` 永不更新 | 设备在线状态不可用 | M3 托盘 Agent 交付后解除 |
| L-3 | 工作台分析在 UI 线程同步执行，进度条在计算完成前不重绘 | 大数据量时界面卡顿 | 需移入工作线程 |
| L-4 | HTML 报告含生成时间戳，跨秒重复导出字节不同 | 无法用哈希比对 HTML 产物 | 如需零差异，移除时间戳或改为产物外挂元信息 |
| L-5 | 生产 PG 路径下 `save_session` 未持久化 `expires_at`，刷新不会延长会话 TTL | 30 天后需重新登录 | 需修复 `repositories.py` 的 `save_session` |
| L-6 | `license.starts_at/expires_at` ORM 注解为 `Mapped[datetime]` 但列类型为 `Date`，`_license_from_row` 调用 `.date()` 会抛 `AttributeError` | 生产 PG 路径读取许可证会报错 | 需修正类型注解或转换逻辑 |
| L-7 | 主数据导入为 create-only，不支持覆盖更新 | 修改 SKU 需先删除或走其他途径 | 待产品确认是否需要 upsert |
| L-8 | 事件导入的 `reversal_of` 字段无 CSV 列映射 | REVERSAL 事件导入后 `reversal_of` 为 NULL，投影重建记 `REVERSAL_TARGET_MISSING` 并按 0 处理 | 需补充字段映射或改用其他录入方式 |
| L-9 | Web 端令牌存于 localStorage | XSS 风险 | 生产前改 httpOnly Cookie |
| L-10 | 单实例锁模块已实现但未接入 `main.py`，且不做进程存活探测 | 崩溃遗留锁不会自动清理 | M3 打包前接入并加存活探测 |

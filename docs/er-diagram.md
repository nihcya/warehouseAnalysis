# ER 图：本地业务库与云端控制库（M0 基线）

**归档目的**：关闭《项目开发文档评审报告.md》§8 修订清单第 1 项（P0-1）——"将主基线中的本地/云端 ER 关系归档为仓库文件"。
**对齐依据**：字段与约束 100% 取自实际迁移与 ORM 源码（非主基线示意稿）：

| 库 | 迁移文件 | ORM / 模型 |
|---|---|---|
| 本地业务库（SQLite） | `local-data/alembic/versions/0001_meta.py`、`local-data/alembic/versions/0002_analysis_m0.py` | `local-data/src/local_data/models.py` |
| 云端控制库（PostgreSQL） | `services/control-plane/alembic/versions/0001_control_meta.py`（revision `control_0001`） | M0 迁移手写、无 ORM；引入模型后挂载 Alembic 元数据 |

**M0 范围说明**：主基线 §35.2 的完整表组（本地 `sku`、`inventory_event`、`inventory_balance`、`sync_outbox` 等；云端商户/设备/许可/任务等业务表）尚未建表。本图只收录已落地迁移实际创建的表；后续迁移遵循"只加不改"，新增表/字段时须同步更新本图。

---

## 1. 本地业务库（SQLite）

- **数据库文件**：`<数据目录>/warehouse.db`；数据目录解析优先级 = 显式参数 > `WORKBENCH_DATA_DIR` > `%LOCALAPPDATA%\WarehouseWorkbench\data`
- **连接**：`sqlite+pysqlite:///`；每个连接执行 `PRAGMA foreign_keys=ON`、`PRAGMA journal_mode=WAL`、`PRAGMA busy_timeout=5000`（见 `local_data.connection`）
- **存储约定**：时间字段为 UTC ISO 8601 文本；日期字段为 `YYYY-MM-DD` 文本；金额/数量以 TEXT 存 Decimal 序列化字符串（禁止 float 真值）
- **ORM**：`local_data.models`（SQLAlchemy 2.0 declarative，约束显式命名，保证错误可定位、迁移可回滚）

```mermaid
erDiagram
    local_meta["local_meta（迁移 0001_meta）"] {
        TEXT key PK "约束名 pk_local_meta"
        TEXT value "NOT NULL"
    }
    analysis_run["analysis_run（迁移 0002_analysis_m0）"] {
        INTEGER id PK "自增主键 pk_analysis_run"
        TEXT run_id UK "UNIQUE 约束 uq_analysis_run_run_id，NOT NULL"
        TEXT task_id "云端任务标识，M0 可空"
        TEXT start_date "分析期间起始日 YYYY-MM-DD"
        TEXT end_date "分析期间结束日 YYYY-MM-DD"
        TEXT scope_json "仓库范围等 scope 快照（JSON 文本）"
        TEXT engine_version "引擎版本，NOT NULL"
        TEXT formula_version "公式版本，NOT NULL"
        TEXT status "状态机取值，NOT NULL"
        TEXT started_at "开始时间 UTC ISO 8601"
        TEXT finished_at "结束时间 UTC ISO 8601"
        TEXT error_code "失败错误码，取 contracts.ErrorCode 值"
        TEXT created_at "创建时间 UTC ISO 8601，NOT NULL"
        TEXT updated_at "更新时间 UTC ISO 8601，NOT NULL"
    }
    analysis_result["analysis_result（迁移 0002_analysis_m0）"] {
        INTEGER id PK "自增主键 pk_analysis_result"
        TEXT run_id FK "约束名 fk_analysis_result_run_id_analysis_run，指向 analysis_run.run_id，NOT NULL"
        TEXT result_type "M0 固定 full_result，NOT NULL"
        TEXT sku_id "SKU 标识，full_result 单行存整体结果时为空"
        TEXT category "分类"
        TEXT metric_json "指标 JSON 文本，NOT NULL（金额/数量为 Decimal 字符串）"
        TEXT warning_json "警告 JSON 文本，NOT NULL（code/severity/message/fields/blocking 五要素）"
        TEXT created_at "创建时间 UTC ISO 8601，NOT NULL"
    }
    alembic_version["alembic_version（Alembic 自动维护）"] {
        VARCHAR version_num PK "upgrade head 后值为 0002_analysis_m0"
    }

    analysis_run ||--o{ analysis_result : "run_id 1:N，复合索引 ix_analysis_result_run_id_result_type_sku_id"
```

### 1.1 analysis_run.status 状态机（M0 冻结）

`CREATED / QUEUED / RUNNING / SUCCEEDED / FAILED / CANCELLED`（Repository 写入，UI 只读展示；新增状态须走"只加不改"迁移）。

### 1.2 种子数据（随迁移写入 local_meta）

| key | value | 写入迁移 |
|---|---|---|
| `db_schema_version` | `local-0002`（0002 升级后；0002 降级回 `local-0001`） | 0001_meta 写入，0002_analysis_m0 更新 |
| `install_instance_id` | UUID4（首次建库生成一次；downgrade base 后重新 upgrade 会生成新值） | 0001_meta |
| `single_primary_workbench` | `1`（单主工作台标识，§35.3 单写入进程原则） | 0001_meta |

---

## 2. 云端控制库（PostgreSQL）

- **连接**：`DATABASE_URL`（默认 `postgresql+psycopg://postgres:postgres@localhost:5432/warehouse_control`）；本地开发用 `docker compose -f docker-compose.dev.yml up -d postgres`
- **迁移 revision**：`control_0001`（文件 `0001_control_meta.py`）；依据主基线 §35.7，M0 只建元数据与枚举字典，不建任何商户业务明细表
- **枚举设计**：`code` 采用 `<kind>:<value>` 形式保证全局唯一（不同 kind 可能重名，如 task_status 与 sync_status 都有 CREATED）

```mermaid
erDiagram
    control_meta["control_meta（迁移 0001_control_meta）"] {
        TEXT key PK "主键（约束未显式命名）"
        TEXT value "NOT NULL"
        TIMESTAMPTZ created_at "NOT NULL，server_default now()"
        TIMESTAMPTZ updated_at "NOT NULL，server_default now()"
    }
    control_enum["control_enum（迁移 0001_control_meta）"] {
        INTEGER id PK "自增主键"
        TEXT code UK "UNIQUE，形式 kind:value"
        TEXT kind "枚举类别，NOT NULL"
    }
    alembic_version["alembic_version（Alembic 自动维护）"] {
        VARCHAR version_num PK "upgrade head 后值为 control_0001（revision ID 不允许含 '-'）"
    }
```

`control_meta` 与 `control_enum` 之间无外键（两表职责独立：键值元信息 / 枚举字典，先删子表后删父表的顺序无依赖）。

### 2.1 种子数据

`control_meta`（2 行，随迁移 bulk_insert）：

| key | value |
|---|---|
| `db_schema_version` | `control-0001` |
| `control_plane_version` | `0.1.0` |

`control_enum`（36 行，`code = <kind>:<value>`）：

| kind | 取值 |
|---|---|
| `task_status` | CREATED、QUEUED、RUNNING、SUCCEEDED、FAILED、CANCELLED、MISSED、RETRYING |
| `run_status` | 同 task_status（与任务状态机同构） |
| `device_status` | REGISTERED、ONLINE、DEGRADED、OFFLINE、REVOKED |
| `sync_status` | CREATED、ENQUEUED、DELIVERED、APPLIED、ACKED、EXPIRED、REJECTED、RETRYING |
| `move_type` | INBOUND、OUTBOUND、RETURN、SCRAP、TRANSFER、STOCKTAKE、REVERSAL（取自 `contracts.MoveType`） |

---

## 3. 结构校验入口

`uv run python scripts/verify_schema.py`（仓库根执行）对本图两库的迁移结果做机器校验：

- 表存在性（本地 3 表 / 云端 2 表 + 各自 `alembic_version`）；
- 关键约束（`analysis_run.run_id` UNIQUE、`analysis_result` FK 与复合索引、`control_enum.code` UNIQUE、两库主键）；
- `alembic_version` 值（本地 `0002_analysis_m0` / 云端 `control_0001`）与种子数据（`db_schema_version` 等）。

云端无本地 PostgreSQL 时自动打印 skip（连接探测失败不视为失败）；测试侧同口径验证见 `local-data/tests/test_migrations.py` 与 `services/control-plane/tests/test_migrations.py`。

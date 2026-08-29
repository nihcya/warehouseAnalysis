# 开发者 A：M1 交接说明（本地业务闭环验收材料）

**日期**：2026-08-29  
**交付人**：开发者 A（平台集成：control-plane、local-data、workbench-desktop、apps/web、契约文件）  
**对应里程碑**：M1 本地业务闭环（`开发规划与协作需求文档.md` V2.0 §22.3、`开发需求-A平台工作台.md` §4 M1）  
**代码分支**：`feature/a-m1-local-business-loop`（自合并 PR #2 后的最新 `master` 切出，M0 基线 114 passed + 1 skipped 起点）  
**版本基线**：control-plane 0.1.0（未动）/ 本地库 schema `local-0006`（新增 4 个迁移）/ 云端库 schema `control-0001`（未动）/ web 0.1.0（未动）/ contracts 1.0（**零改动**，见 §5）/ engine 0.1.0 / formula 0.1.0-draft  
**对应文档**：`docs/data-dictionary.md` §1.4~1.21 与 §5 版本记录、`docs/er-diagram.md` §1.3 M1 扩展图注、`.trae/specs/deliver-m1-local-business-loop/spec.md`

---

## 1. 安装与启动命令

```powershell
# 环境安装（与 M0 相同，无新增系统依赖；Web 另需 Node.js 22 LTS + pnpm）
uv sync --all-packages --group dev
pnpm install

# 本地 PostgreSQL（可选：云端迁移测试用；无 PG 时相关测试自动 skip）
docker compose -f docker-compose.dev.yml up -d postgres

# 启动桌面工作台（PySide6；仍须在应用目录运行，见 m0-handover-a.md §1）
#   环境变量：WORKBENCH_ENGINE=fake（默认）|local
#             WORKBENCH_DATASET=local（默认，M1 起本地库为默认数据源）|fixture
#             WORKBENCH_DATA_DIR（数据目录重定向，测试/演示用）
cd apps/workbench-desktop; uv run python -m app

# 启动云端控制平面与 Web（M0 行为不变，M1 未改动）
cd services/control-plane; uv run uvicorn app.main:app --reload --port 8000
pnpm --filter web dev

# 全量检查（mypy 须分两批：control-plane 与 workbench-desktop 顶层包同名 app）
uv run pytest                                   # 196 passed, 1 skipped（见 §4）
uv run ruff check packages scripts tests services local-data apps
uv run mypy services/control-plane/app local-data/src scripts
uv run mypy apps/workbench-desktop/app
pnpm --filter web lint
pnpm --filter web typecheck
pnpm --filter web build

# M1 新增的机器验证脚本
uv run python scripts/verify_schema.py           # 迁移结构校验（含 M1 17 表，28 项）
uv run python scripts/verify_backup_restore.py   # 备份恢复演练（18 项，见 §4.2）
```

工作台 UI 测试与无头联调：`QT_QPA_PLATFORM=offscreen`（`apps/workbench-desktop/tests/conftest.py` 已默认设置）。

**数据目录约定**（`local_data.connection.resolve_data_dir`）：显式参数 > `WORKBENCH_DATA_DIR` > `%LOCALAPPDATA%\WarehouseWorkbench\data`；报告目录与备份目录与数据目录同级（`…\WarehouseWorkbench\reports\`、`…\backups\`），见 `app/main.py` 的 `create_report_export_manager` / `create_backup_manager`。

## 2. M1 公开入口（工作台新增）

导航（`main_window.py`）：总览 / 货品 / 库存流水 / **导入** / **分析** / 调度 / **报告** / **备份** / 设置——加粗四页为 M1 实装（M0 仅"分析"），其余仍占位。

| 入口 | 位置 | 说明 |
|---|---|---|
| 导入页 + CSV 导入向导 | `app/presentation/import_page.py`、`import_wizard.py` | 五步 QWizard：文件选择（编码探测 UTF-8/GBK）→ 导入类型与字段映射（同名自动预选，缺必填禁用"下一步"）→ 预览前 20 行 → 执行与结果（inserted/skipped/error 汇总）→ 错误明细（行号/字段/错误码/原始值/修复建议）；重复 file_hash 弹窗提示不静默重复入库 |
| 分析页（页内页签） | `app/presentation/analysis_page.py` | "运行分析"（M0 结构原样移入页签）+ "历史运行"（run_id/期间/状态/版本/时间列表，双击或"查看结果"按 run_id 重查）；空库时"本地库暂无分析数据"提示不抛异常 |
| 报告页 | `app/presentation/report_page.py` | 历史 run 列表 → 导出 HTML/CSV（重复导出幂等更新）→ 打开产物目录 |
| 备份页 | `app/presentation/backup_page.py` | 手动备份（VACUUM INTO + SHA-256 + `backup_record` 登记 VERIFIED）、备份列表（编号/时间/类型/大小/状态/验证时间）、恢复（二次确认 → 先校验后原子替换） |
| 本地库新表组 | `local-data/alembic/versions/0003~0006_*.py` | 主数据 7 表 + 事件 6 表 + 导入治理 2 表 + 报告备份 2 表（共 17 表，字段与约束见 `data-dictionary.md` §1.4~1.20） |

`docs/data-dictionary.md` 与 `docs/er-diagram.md` 已按"只加不改"扩展收录全部 17 表（M0 段落原样保留），`scripts/verify_schema.py` 同步扩展（本地 SQLite 现为 28 项检查：M0 13 项 + M1 15 项）。

## 3. 导入演示路径

### 3.1 演示数据（可直接复制为 CSV 文件）

与 `apps/workbench-desktop/tests/conftest.py` 的 `SAMPLE_MASTER_CSV` / `SAMPLE_EVENTS_CSV` 同源（测试即演示脚本）：

`skus.csv`（UTF-8 保存）：

```csv
sku_id,name,category,unit,unit_cost
SKU-0001,矿泉水 550ml,饮料,瓶,1.20
SKU-0002,能量饮料 250ml,饮料,罐,3.50
```

`events.csv`（UTF-8 保存，期间 2026-06-02 ~ 2026-06-15）：

```csv
event_id,sku_id,warehouse_id,move_type,quantity,occurred_at,unit_cost
EVT-0001,SKU-0001,WH-01,INBOUND,100,2026-06-02,1.20
EVT-0002,SKU-0001,WH-01,OUTBOUND,30,2026-06-05,
EVT-0003,SKU-0002,WH-01,TRANSFER_OUT,10,2026-06-12,
EVT-0004,SKU-0002,WH-01,ADJUSTMENT,60,2026-06-15,
```

### 3.2 UI 手动演示（全闭环）

1. **启动**：`cd apps/workbench-desktop; uv run python -m app`（默认 `WORKBENCH_ENGINE=fake`、`WORKBENCH_DATASET=local`，本地库自动迁移到 head）。
2. **主数据导入**：导航"导入" → "打开导入向导..." → 选择 `skus.csv`（首页显示文件名/大小/编码探测结果）→ 映射页同名自动预选 → 预览前 20 行 → "执行"→ 完成页显示"入库 2、跳过 0、错误 0"，入口页回显"最近导入：批次 IMP-…"。
3. **仓库预置**（M1 已知限制：仓库无 UI/导入入口）：事件导入校验仓库存在性（`WAREHOUSE_NOT_FOUND`），演示事件导入前预置一次仓库：
   ```powershell
   cd apps/workbench-desktop
   uv run python -c "from app.main import create_session_factory; from local_data.repository import MasterDataRepository; MasterDataRepository(create_session_factory()).add_warehouse(warehouse_id='WH-01', name='主仓')"
   ```
4. **事件导入与错误隔离演示**：再次打开向导导入 `events.csv` → 完成页"入库 4、跳过 0、错误 0"；导入含 5 行非法（数量为负、SKU 不存在等）的混合质量文件时合法行入库、非法行进错误明细页（行号/字段/错误码/原始值/中文修复建议），不互相阻塞。导入完成后余额投影自动重建（`inventory_balance` 按 `occurred_at` 回放）。
5. **分析**：导航"分析" → "运行分析" → 组合根经 `SqliteDatasetAdapter` 从本地库构造 `EngineDataset` → 真实校验规则（`validate_dataset`）→ FakeEngine 返回结果 → 落库 `analysis_run`/`analysis_result` → 结果表格一行五列；"历史运行"页签双击重查。
6. **报告**：导航"报告" → 刷新列表 → 选中 run → "导出 HTML"/"导出 CSV" → "打开所在目录"查看产物（HTML 含指标与警告明细分节中文表头；CSV 为 UTF-8 BOM 纯指标清单，Excel 直开）。
7. **备份**：导航"备份" → "手动备份"（列表新增 MANUAL/VERIFIED 行）→ "恢复"→ 二次确认 → 先校验后原子替换，失败当前库零改动。

### 3.3 脚本化演示（无 UI，可重复）

```powershell
# 导入→适配→分析→报告→备份 全链路（含 UI 向导的 offscreen 测试）
QT_QPA_PLATFORM=offscreen uv run pytest apps/workbench-desktop -q

# 备份恢复完整演练（建库→造数→备份→篡改→恢复→行数守恒→损坏备份零改动）
uv run python scripts/verify_backup_restore.py

# 迁移结构校验（17 新表存在性 + 关键约束 + alembic head = 0006_report_backup）
uv run python scripts/verify_schema.py
```

## 4. 验证记录（2026-08-29，分支 `feature/a-m1-local-business-loop`）

环境：Windows + CPython 3.11.15（uv.lock 锁定）+ Node 22 LTS。

### 4.1 质量门禁

| 检查 | 命令 | 结果 |
|---|---|---|
| 全量测试 | `uv run pytest` | **196 passed, 1 skipped**（197 collected；skip = 云端迁移 roundtrip `services/control-plane/tests/test_migrations.py`，本地无 PostgreSQL，CI 由 PG service 容器执行；M0 基线 114 passed + 1 skipped 无回归，B 的 47 例引擎测试无回归） |
| Python 静态检查 | `uv run ruff check packages scripts tests services local-data apps` | All checks passed（CI 口径 `ruff check .` 在提交树上等价通过；工作区未跟踪草稿目录 `.task4-scratch/` 不入 PR） |
| 类型检查（批 1） | `uv run mypy services/control-plane/app local-data/src scripts` | Success: no issues found in 35 source files |
| 类型检查（批 2） | `uv run mypy apps/workbench-desktop/app` | Success: no issues found in 33 source files |
| 迁移结构校验 | `uv run python scripts/verify_schema.py` | 本地 SQLite **28 项全通过、0 失败**：17 新表按迁移分组建表、`alembic_version=0006_report_backup`、`local_meta.db_schema_version=local-0006`、`sku.sku_id`/`inventory_event.event_id` UNIQUE、`quantity>0` CHECK、快照五元组 UNIQUE、`(run_id, format)` UNIQUE、backup 枚举 CHECK 及唯一索引/主键实际生效（重复插入被拒、孤儿外键被拒）；云端 PostgreSQL skip（CI 由 service 容器执行） |
| 备份恢复演练 | `uv run python scripts/verify_backup_restore.py` | **18 项全通过、0 失败**（明细见 §4.2） |

测试分布（`pytest --collect-only`：197 例）：根 `tests/` 57（B 基线 47：engine 30 + contract 17；A 契约 sync-envelope 10）+ control-plane 34（M0 原样）+ local-data 38（M0 15 → M1 +23：主数据 7、事件与投影 7、导入治理 4、报告备份仓储 5）+ workbench-desktop 68（M0 9 → M1 +59：导入用例 10、导入向导 11、数据集适配 6、分析历史 3、本地闭环 4、报告导出 11、备份恢复 14）。**M1 净增 82 例。**

### 4.2 备份与恢复演练记录（`verify_backup_restore.py`，2026-08-29 实跑）

| 步骤 | 验证点 | 结果 |
|---|---|---|
| step 1 建临时库 | tmp 目录建库并 `alembic upgrade head` | [ok] 迁移到 `0006_report_backup` |
| step 2 造数据 | 分析结果 + 主数据（sku=2）+ 库存事件（3 条） | [ok] 落库成功 |
| step 3 手动备份 | VACUUM INTO + SHA-256 + 登记 | [ok] `status=VERIFIED`、文件 `backup-20260829T133250Z-bak-*.db`、SHA-256 与登记值一致、`verified_at` 已记录、`db_schema_version=local-0006`（读自 local_meta）、关键表行数可读（analysis_run=1） |
| step 4 篡改后恢复 | 新增 1 个 run（2 个）后恢复 | [ok] 先校验后原子替换成功；行数守恒（analysis_run 回到 1）；恢复前自动安全备份当前库（AUTO 类型，§35.9）；安全备份补登记进恢复后的库（非孤儿）；alembic 版本保持 head；业务数据可读 |
| step 5 损坏备份 | 篡改备份文件后恢复 | [ok] 恢复被拒绝（失败返回而非崩溃）；失败信息明确当前库未改动；当前库文件字节零改动（前后 SHA-256 一致） |

末行输出：`通过 18 项；失败 0 项 —— 全部通过：备份可验证、恢复守恒、失败零改动`。

## 5. 对 B 的协作说明（EngineDataset 实测反馈）

**契约零改动声明**：M1 未修改 `contracts` 包（1.0 冻结遵守），以下为 `SqliteDatasetAdapter`（`apps/workbench-desktop/app/infrastructure/db/dataset_adapter.py`）实测反馈，供 B 参考，均不阻塞：

1. **TRANSFER 方向语义**：本地库按单仓口径把调拨拆为 `TRANSFER_IN`/`TRANSFER_OUT` 两条事件（同表简化，M1 无跨仓结构），契约 `MoveType.TRANSFER` 无方向——适配器已折叠映射（双向 → TRANSFER）。请 B 在真实引擎的调拨类 KPI 计算中明确 TRANSFER 的方向口径；若需区分方向，建议后续版本"只加不改"扩展枚举（本地库已按带方向取值落库，无改造负担）。
2. **STOCKTAKE 口径对齐确认**：本地 `ADJUSTMENT`（quantity 记实盘数、投影按"实盘数 − 当前账面"取差额）映射 `STOCKTAKE`，语义一致，无异议。
3. **ReplenishmentRecord 恒空**：本地 `supplier_sku` 无 `avg_daily_demand` 等补货参数字段，M1 适配器 replenishment 恒为空列表（spec 明确不做）。B 的 replenishment 里程碑落地时请同步对齐字段来源——本地表结构已建（`supplier_sku`），可承接扩展。
4. **SnapshotRecord 无库位/批次维度**：本地 `stock_snapshot` 唯一键为五元组（含库位/批次），适配器按 (sku, 日期, 仓库) 聚合求和为时点总量。若 B 的快照类 KPI 需要库位/批次粒度，需在契约后续版本扩展（本地数据无损，仅契约粒度问题）。
5. **source 自由文本映射**：本地 `inventory_event.source` 为自由文本（`IMPORT_CSV`/`UI` 等），适配器映射到 `EventSource`，未知取值回退 `IMPORT` 不中断构造。建议 B 维护一份契约词表与本地取值约定的同步清单。
6. **validate_dataset 实测行为**：空库/仅主数据时构造空数据集不抛异常（工作台应用层给"无数据"提示）；重复 `event_id` 经导入侧幂等去重（skipped 计数）后不再触发 `DUPLICATE_EVENT`——事实来源唯一化在导入侧完成，引擎侧校验作为第二道防线保持不变。
7. **FakeEngine 联调路径确认**：FakeEngine 校验规则与真实引擎一致（复用 `validation/rules.py`）、结果为冻结 fixture——本地数据经真实校验规则，`WORKBENCH_ENGINE=local` 切换真实 wheel 无缝（组合根单点切换，UI 无引擎分支）。B 的 wheel 就绪后可直接联调。

另：`docs/m0-handover-a.md` §5 对 B 契约的三条小建议（Warning 码受控词表、ResultMetric 值类型标注、summary 结构化）在 M1 UI（错误明细表、结果表格、报告导出）中仍未遇到硬阻塞，继续保留为 M1+ 参考。

## 6. 已知限制（M1）

- **xlsx/Excel 导入未支持**：M1 仅 CSV（UTF-8/GBK 编码自动探测）；Excel（openpyxl）随 M2 按需引入。
- **采购订单业务逻辑未实装**：`purchase_order`/`purchase_order_line` 表结构已建（迁移 0004），但无 UI、无导入、无业务流转（随 B 的 replenishment 里程碑落地；spec 明确不做）。
- **补货参数恒空**：`ReplenishmentRecord` 构造为空列表（本地 `supplier_sku` 无补货参数字段，见 §5 第 3 条）。
- **主数据维护缺口**：导入向导仅支持"主数据（SKU）"与"库存事件"两种类型；仓库/库位/批次/供应商无导入与 UI 维护入口。事件导入校验仓库存在性（`WAREHOUSE_NOT_FOUND` 行级错误并给修复建议），完整事件导入演示需先经脚本预置仓库（见 §3.2 第 3 步）。
- **货品/库存流水导航页仍为占位**：M0 占位保留；导入结果经导入入口页回显汇总（批次/入库/跳过/错误数），数据核对经分析页与报告页。
- **错误修复闭环为"修源重导"**：M1 不做错误行内编辑，修正 CSV 后重新导入；同 `file_hash` 的重复导入会被提示"已导入过"（不静默重复），文件内容变化后才会形成新批次。
- **重复 SKU 不支持覆盖更新**：主数据导入为 create-only，重复 `sku_id` 记错误行（`SKU_DUPLICATE`）。
- **备份无自动调度**：AUTO 类型仅在恢复前自动登记安全备份（§35.9）；定期自动备份随后续里程碑。
- **负库存不阻断**：按事件事实来源原则，出库致负的事件先落库为真，负余额维度组合记 Warning 展示（不回滚事件、不阻断导入）。
- **引擎仍为 FakeEngine 默认**：`WORKBENCH_ENGINE=local` 可切真实引擎，但 B 的真实 calculators 未交付前结果为占位；分析结果默认来自冻结 fixture。
- **同步与控制 API 仍 stub**（M3）；登录认证 M2（M0 限制原样延续）。
- **平台 CI 双绿待 PR 时确认**：本批为本地命令记录（本地无 PG，云端迁移测试 skip）；platform-ci.yml 已含 M1 新测试与两批 mypy，PR 推送后由 PG service 容器执行云端真实校验。

## 7. 回滚方式

- **本地 SQLite（M1 迁移链）**：在 `local-data/` 目录执行
  ```powershell
  uv run alembic downgrade 0002_analysis_m0   # 摘除 M1 全部 17 表（回 M0 终态，db_schema_version 回 local-0002）
  uv run alembic downgrade base                # 全部回滚（local_meta 一并删除）
  # 指定数据目录：uv run alembic -x data_dir=<目录> downgrade <目标>
  ```
  注意：downgrade 会删除对应表数据，执行前先备份（备份页"手动备份"或 `BackupService`）。
- **数据目录**（`%LOCALAPPDATA%\WarehouseWorkbench\data`）属用户数据，git 不管理；`reports/`、`backups/` 与其同级，迁移 downgrade 不经手备份文件——恢复入口（备份页/`verify_backup_restore.py` 演练路径）始终可用。
- **git 分支**：M1 无对外发布物（无 wheel/安装包）。合并前回滚 = 丢弃 `feature/a-m1-local-business-loop`；合并后回滚 = `git revert <merge commit>`，不使用 force push。
- 首个需要正式回滚方案的发布物仍是 engine wheel（B）与工作台安装包（A，M6）。

## 8. 后续待办（转入下一批，不静默降级）

1. Task 11：`feature/a-m1-local-business-loop` → `master` 提 PR，描述附本文件 + 测试结果 + 各验收场景证据；合并前确认 platform-ci / engine-ci 双绿。
2. B 侧回应 §5 反馈：调拨方向口径、replenishment 字段来源、快照粒度扩展提案（若采纳，按"只加不改"提契约版本）。
3. 仓库/库位/批次/供应商的主数据导入与维护 UI（M1 缺口，§6 第 4 条；建议 M2 与 xlsx 导入一并排期）。
4. README「快速开始」文档修正（m0-handover-a.md §8 第 4 条遗留：仓库根直接执行 `uv run --package …` 的写法不可用，正确命令须 cd 到应用目录）。

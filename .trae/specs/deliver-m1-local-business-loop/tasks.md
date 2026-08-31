# Tasks

- [x] Task 1: 分支与 M0 基线确认
  - [x] 1.1 从最新 `master`（含 PR #2 合并）拉取 `feature/a-m1-local-business-loop` 分支
  - [x] 1.2 本地全量 `uv run pytest` 确认 M0 基线（114 passed, 1 skipped）无回归

- [x] Task 2: 迁移 0003_master_data（主数据组）
  - [x] 2.1 迁移脚本 + downgrade：sku、barcode、warehouse、location、supplier、supplier_sku、lot 七表，字段/约束对齐主基线 §35.4（`sku_id UNIQUE`、`barcode UNIQUE`、`(warehouse_id, location_id) UNIQUE`、`(supplier_id, sku_id) UNIQUE`、`(sku_id, lot_id) UNIQUE`、`expiry_date >= production_date` CHECK、supplier 参数非负 CHECK）
  - [x] 2.2 ORM 模型（元数据分组"主数据"）+ Repository（SKU/仓库/供应商基础 CRUD，条码唯一映射校验）
  - [x] 2.3 测试：空库迁移/回滚、UNIQUE 冲突、CHECK 违例（生产日期晚于有效期拒绝）、SKU 往返

- [x] Task 3: 迁移 0004_inventory_events（事件组与余额投影）
  - [x] 3.1 迁移脚本 + downgrade：inventory_event、inventory_event_line、stock_snapshot、purchase_order、purchase_order_line、inventory_balance 六表（`event_id UNIQUE`、数量 > 0 CHECK、`(sku_id, warehouse_id, occurred_at)` 索引、快照五元组 UNIQUE、PO 状态枚举 CHECK）
  - [x] 3.2 ORM 模型（"库存事实"分组）+ InventoryEventRepository（幂等 upsert：重复 event_id 跳过并计数）
  - [x] 3.3 余额投影重建服务：按 occurred_at 顺序回放事件计算 on_hand/available/reserved；`inventory_balance` 可清空重建且结果一致
  - [x] 3.4 测试：事件幂等重放、出库负库存告警（记 Warning 不阻断导入，沿事件事实来源原则）、投影重建幂等性、快照存取

- [x] Task 4: 迁移 0005_import（导入治理）
  - [x] 4.1 迁移脚本 + downgrade：import_batch（file_hash、状态枚举、row_count/error_count）、import_error（(batch_id, row_no) 索引、raw_value、suggestion、resolved_at）
  - [x] 4.2 ImportBatchRepository + ImportErrorRepository
  - [x] 4.3 测试：批次状态流转（RUNNING→COMPLETED/FAILED）、错误行定位字段完整性

- [x] Task 5: CSV 导入服务与字段映射
  - [x] 5.1 application 导入用例：读取 CSV（UTF-8/GBK 编码探测）→ 字段映射（列名→契约字段）→ 逐行校验（数量>0、SKU 存在性、日期格式、Decimal 解析）→ 合法行批量入库 + 非法行进 import_error（错误码可定位行/字段）
  - [x] 5.2 重复导入检测：file_hash（SHA-256）命中已完成批次时返回提示，不静默重复入库
  - [x] 5.3 事件导入完成后触发余额投影重建
  - [x] 5.4 测试：混合质量 CSV（95 合法/5 非法）、重复 hash 阻断、GBK 编码样本、错误隔离不影响合法行入库

- [x] Task 6: 导入向导 UI（PySide6）
  - [x] 6.1 presentation 导入向导（QWizard 五步：文件选择 → 字段映射 → 预览前 N 行 → 校验结果 → 完成/错误页）
  - [x] 6.2 错误页：行号/字段/错误码/原始值/修复建议表格；"修复后重导"引导（M1 不做行内编辑，重新选文件）
  - [x] 6.3 左侧导航"导入"入口接通向导；导入完成刷新货品/流水页
  - [x] 6.4 pytest-qt（offscreen）测试：向导步骤流转、错误表格内容、字段映射缺列时阻断提示

- [x] Task 7: EngineDataset 适配器与分析闭环
  - [x] 7.1 infrastructure/dataset_adapter：从本地库读 sku/事件（期间过滤）/快照 → 构造 `contracts.EngineDataset`（金额用 Decimal）
  - [x] 7.2 application 分析用例改造：数据源切换为适配器（组合根可配 fixture 兜底，测试用）；validate → analyze(progress) → 原样持久化沿 M0 路径不变
  - [x] 7.3 presentation 历史运行列表页：analysis_run 列表（run_id、期间、状态、版本），双击重查结果（M0 已有结果页复用）
  - [x] 7.4 测试：本地数据 → FakeEngine 全链路、空库时给出"无数据"提示而非报错、历史列表数据正确

- [x] Task 8: 报告导出与迁移 0006_report_backup
  - [x] 8.1 迁移：report_artifact（(run_id, format) UNIQUE、sha256）、backup_record（类型、db_schema_version、sha256、size、status、verified_at）
  - [x] 8.2 infrastructure/report：HTML（Jinja2 或字符串模板，指标+Warning 分节，中文表头）与 CSV（UTF-8 BOM 方便 Excel）两种导出器；产物写入 `%LOCALAPPDATA%\WarehouseWorkbench\reports\`，SHA-256 登记 report_artifact
  - [x] 8.3 presentation 报告页：按 run 导出 HTML/CSV、打开所在目录
  - [x] 8.4 测试：导出文件内容包含 run_id/版本/指标、重复导出同 format 的 UNIQUE 处理、CSV 可被重新解析

- [x] Task 9: 备份恢复与完整性校验
  - [x] 9.1 infrastructure/backup：`VACUUM INTO` 全量备份 + SHA-256 + backup_record（status=VERIFIED 需读回复验通过）
  - [x] 9.2 恢复：恢复到临时库 → 校验（alembic 版本一致 + 关键表行数守恒）→ 通过才原子切换数据目录文件；失败保留当前库并记录
  - [x] 9.3 presentation 备份页：手动备份、备份列表（时间/大小/状态）、恢复按钮（二次确认对话框）
  - [x] 9.4 `scripts/verify_backup_restore.py`：对测试库执行完整备份→恢复→比对演练（m0-handover-a.md 承诺随 M1 交付）
  - [x] 9.5 测试：备份校验值复验、损坏备份恢复失败且当前库不变、恢复成功后行数守恒

- [x] Task 10: 文档、verify_schema 扩展与全量回归
  - [x] 10.1 `docs/data-dictionary.md`/`er-diagram.md` 扩展 M1 全部 17 张表（只加不改，与迁移 DDL 对齐）
  - [x] 10.2 `scripts/verify_schema.py` 扩展新表存在性/关键约束检查
  - [x] 10.3 `docs/m1-handover-a.md`：安装/启动命令、导入演示路径、备份恢复演练记录、已知限制（xlsx 未支持、PO 业务逻辑未实装）、对 B 的协作说明（EngineDataset 实测反馈）
  - [x] 10.4 全量 `uv run pytest` + ruff + mypy + pnpm lint/typecheck 全绿；CI 双绿

- [x] Task 11: PR 提交
  - [x] 11.1 `feature/a-m1-local-business-loop` → `master`，描述附测试结果与各验收场景证据（PR #3）

# Task Dependencies

- Task 2/3/4（迁移组）依赖 Task 1；三者按序（事件表 FK 依赖主数据表，导入表独立可并行于 2/3 收尾后）
- Task 5 依赖 Task 2/3/4；Task 6 依赖 Task 5
- Task 7 依赖 Task 3（事件可读）；可与 Task 5/6 并行
- Task 8 依赖 Task 7（分析结果存在）+ 迁移 0006
- Task 9 依赖迁移链完成（Task 8 的 0006 含 backup_record）
- Task 10/11 收尾依赖全部

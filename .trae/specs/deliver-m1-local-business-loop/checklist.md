# M1 本地业务闭环 验收 Checklist

## 分支与基线

- [x] `feature/a-m1-local-business-loop` 基于合并 PR #2 后的最新 `master` 创建
- [x] M0 测试基线（114 passed, 1 skipped）无回归；B 的 47 个引擎测试不受影响

## 迁移与本地库

- [x] `0003_master_data`：7 张主数据表字段/约束与主基线 §35.4 一致；upgrade/downgrade 均有测试
- [x] `0004_inventory_events`：6 张事件组表落库；`event_id UNIQUE` 幂等、数量>0 CHECK、快照五元组 UNIQUE
- [x] `0005_import`：import_batch/import_error 落库；(batch_id, row_no) 可定位错误行
- [x] `0006_report_backup`：report_artifact (run_id, format) UNIQUE；backup_record 含 sha256/status/verified_at
- [x] 全部 revision ID 用下划线命名（无 `-`）；迁移后 `alembic_version` 链完整可回滚
- [x] `inventory_balance` 可清空重建且结果一致（投影可再生）
- [x] 重复 `event_id` 导入被幂等跳过并计数，不报错不重复入账

## 导入向导

- [x] CSV（UTF-8/GBK）五步向导：文件选择 → 字段映射 → 预览 → 校验 → 提交
- [x] 混合质量文件：合法行入库、非法行进 import_error（row_no/field_name/error_code/raw_value/suggestion 齐全）
- [x] 错误页表格可按行定位并展示修复建议
- [x] 相同 file_hash 重复导入有提示，不静默重复入库
- [x] 导入完成后余额投影自动重建

## EngineDataset 适配与分析闭环

- [x] 适配器从本地库构造 EngineDataset（Decimal 金额、期间过滤），通过 validate_dataset
- [x] 校验失败展示错误列表且不调用 analyze（M0 行为保持）
- [x] UI 无引擎分支逻辑（`WORKBENCH_ENGINE` 仅组合根读取）
- [x] 历史运行列表可按 run_id 重查结果
- [x] 空库发起分析给出"无数据"提示，不抛异常

## 报告导出

- [x] HTML 与 CSV 导出可用；产物含 run_id、engine/formula 版本、指标、Warning
- [x] report_artifact 登记 sha256 与文件路径；CSV 为 UTF-8 BOM
- [x] 重复导出同 (run_id, format) 被 UNIQUE 约束正确处理

## 备份与恢复

- [x] 手动备份生成文件 + SHA-256；status=VERIFIED 需读回复验通过
- [x] 恢复先到临时库校验（schema 版本 + 行数守恒）再切换；失败时当前库零改动
- [x] 恢复操作有二次确认
- [x] `scripts/verify_backup_restore.py` 完整演练通过

## 质量门禁

- [x] 全量 `uv run pytest` 通过（新增测试 + M0 基线无回归）
- [x] ruff / mypy / `pnpm -r lint` / `pnpm -r typecheck` 全绿
- [x] Platform CI 与 Engine CI 双绿
- [x] `verify_schema.py` 扩展后全项通过

## 文档

- [x] data-dictionary / er-diagram 扩展 M1 全部表（只加不改，与 DDL 对齐）
- [x] `docs/m1-handover-a.md` 含演示路径、演练记录、已知限制、对 B 的协作说明
- [x] PR #描述附测试结果与验收证据

# Tasks

- [x] Task 1: 工作台启动自动种子基础仓库
  - [ ] 1.1 在 `main.py` 的 `create_session_factory()` 迁移完成后，调用仓库种子逻辑：检查 WH-01 是否存在，不存在则创建
  - [ ] 1.2 测试：首次启动（空库）后 warehouse 表有 WH-01 行；已存在时不重复创建

- [ ] Task 2: 导入向导前置依赖检测
  - [ ] 2.1 在 `import_wizard.py` 的提交步骤（`ExecutePage`）前，当导入类型为"库存事件"时检测 SKU 表和 warehouse 表是否有数据
  - [ ] 2.2 SKU 表为空时显示提示对话框"请先导入 SKU 主数据后再导入库存事件"并阻断；仓库表为空时同理提示
  - [ ] 2.3 测试：空库时导入库存事件被阻断并显示正确提示；有数据时正常放行

- [x] Task 3: 错误码分布统计展示
  - [ ] 3.1 在 `import_manager.py` 的 `run_import()` 返回值中新增错误码分布字典（`error_summary: dict[str, int]`）
  - [ ] 3.2 在 `import_wizard.py` 的错误汇总页展示错误码分布（如 `SKU_NOT_FOUND: 23`、`WAREHOUSE_NOT_FOUND: 5`）
  - [ ] 3.3 测试：导入有错误行时错误页展示错误码分布统计

- [x] Task 4: 全量回归测试
  - [ ] 4.1 运行 `uv run pytest -q` 确认无回归
  - [ ] 4.2 运行 `uv run ruff check .` 确认无 lint 错误

# Task Dependencies

- Task 2/3 依赖 Task 1（种子仓库确保仓库不缺失）
- Task 4 依赖 Task 1-3

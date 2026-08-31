# 修复库存事件导入全部校验失败 Spec

## Why

用户导入库存事件 CSV 时全部 23 行校验失败（SKU_NOT_FOUND / WAREHOUSE_NOT_FOUND），因为本地库的 SKU 表和仓库表为空。导入向导未检测前置依赖、错误页未展示具体错误码分布，用户无法定位原因。工作台首次启动也未自动种子基础仓库数据。

## What Changes

- 工作台首次启动时自动种子仓库 WH-01（如不存在），避免仓库外键校验失败
- 导入向导在"提交"步骤前检测库存事件导入的前置依赖（SKU/仓库是否存在），缺失时阻断并提示用户先导入主数据
- 错误汇总页展示错误码分布统计（按 error_code 分组计数），而非仅显示"全部行校验未通过"

## Impact

- Affected specs: `deliver-m1-local-business-loop`（导入向导与错误隔离）
- Affected code:
  - `apps/workbench-desktop/app/main.py`（启动时种子仓库）
  - `apps/workbench-desktop/app/presentation/import_wizard.py`（前置依赖检测 + 错误码统计）
  - `apps/workbench-desktop/app/application/import_manager.py`（返回错误码分布统计）

## ADDED Requirements

### Requirement: 工作台启动自动种子基础仓库

系统 SHALL 在工作台首次启动（本地库初始化后）自动创建默认仓库 WH-01（如不存在），确保库存事件导入的仓库外键校验不会因仓库缺失而全部失败。

#### Scenario: 首次启动种子仓库

- **WHEN** 工作台首次启动且本地 warehouse 表为空
- **THEN** 自动创建 WH-01 仓库，后续导入库存事件时仓库存在性检查通过

### Requirement: 导入前置依赖检测

系统 SHALL 在库存事件导入的"提交"步骤前检测 SKU 和仓库是否存在，若为空则阻断导入并提示用户先导入主数据。

#### Scenario: SKU 主数据未导入时阻断

- **WHEN** 用户选择导入库存事件，但 SKU 表为空
- **THEN** 向导显示提示"请先导入 SKU 主数据后再导入库存事件"，不创建批次

#### Scenario: 主数据已导入时放行

- **WHEN** 用户选择导入库存事件，且 SKU 表和仓库表均有数据
- **THEN** 正常进入提交步骤

### Requirement: 错误码分布统计

系统 SHALL 在导入完成后展示错误码分布统计（按 error_code 分组计数），帮助用户定位校验失败的具体原因。

#### Scenario: 全部行失败时展示错误码分布

- **WHEN** 导入完成后有错误行
- **THEN** 错误页展示每个错误码的计数（如 SKU_NOT_FOUND: 23），而非仅显示"全部行校验未通过"

# Tasks：开发者 B M1 标准化与 KPI 引擎

依据：`开发需求-B引擎Skill.md` §7 M1、`docs/formula-spec.md` 0.1.0（已冻结）、`docs/m0-handover-b.md` §5 缺口清单（指标层黄金数值、UNIT_COST_MISSING 实现、性能阈值、wheel 交付）。

- [x] Task 0: 分支创建与 M0 冻结签字归档
  - [x] 0.1 `git fetch origin` 后从 `origin/master` 创建并切换到 `feature/b-m1-kpi-engine`；未提交改动（formula-spec.md、README.md、docs/log.txt）随工作区带过去，不回滚
  - [x] 0.2 仅提交 `docs/formula-spec.md` 的冻结签字变更（message 注明关闭 M0 Task 2.5）；README.md、docs/log.txt 保持未提交（提交 c62917b）
  - [x] 0.3 在 `.trae/specs/deliver-m0-engine-baseline/tasks.md` 勾选 Task 2.5 并补注完成方式

- [x] Task 1: 校验与重放语义补全（先写失败测试）
  - [x] 1.1 事件归属与重放顺序：期间左闭右开、`move_date` 折算、同 SKU 按 `(move_date, occurred_at, event_id)` 升序（tests/engine/ 先写断言）
  - [x] 1.2 冲销引用校验：引用不存在 `event_id` → 阻断 `DATA_VALIDATION_FAILED`；引用存在 → 重放时撤销原事件库存影响
  - [x] 1.3 盘点事件：`on_hand_qty` 合法性校验 + 重放至盘点日替换余额（差额不计净入库）
  - [x] 1.4 日期逻辑异常清单化：可容忍项（乱序，按重放排序）与阻断项（结构/引用错误）分别出测试

- [x] Task 2: KPI/COGS 计算器实现（依赖 Task 1 的重放内核，可并行开发子公式）
  - [x] 2.1 重放内核：`opening_qty` / `closing_qty`（F-KPI-001/002，含盘点替换、历史重放、仓库分桶求和）
  - [x] 2.2 出库量与动销率（F-KPI-003/004，退货冲减出库量、调拨不计入）
  - [x] 2.3 COGS 移动加权平均（F-COGS-001，负库存附加处理按 §3.6）
  - [x] 2.4 库存价值与 `UNIT_COST_MISSING` 回退（F-KPI-005，Warning fields 含 sku_id）
  - [x] 2.5 周转率/周转天数/覆盖天数（F-KPI-006/007/008，样本不足降级按 §3.8）
  - [x] 2.6 输出公共字段与舍入：formula_id/formula_version/期间/sample_count；金额 HALF_UP 0.01、数量保输入 scale、比例 6 位小数

- [x] Task 3: analyze 集成与确定性（依赖 Task 1、2）
  - [x] 3.1 `analyze` 走真实 KPI 路径，移除 KPI 部分 `ANALYSIS_PLACEHOLDER`；其余四类维持占位
  - [x] 3.2 数据质量报告：Warning 按码汇总（计数 + 明细）随 AnalysisResult 返回
  - [x] 3.3 确定性测试：同一输入两次执行序列化结果逐字节一致；Hypothesis 守恒属性测试扩展到期初+本期净变动=期末

- [x] Task 4: 黄金数据指标层扩展（依赖 Task 3）
  - [x] 4.1 golden input/expected 从校验层扩展到指标数值层（每个 F-KPI/F-COGS 至少一个数值断言，容差按 §10 声明）
  - [x] 4.2 边界用例补充：空历史、含盘点、退货与冲销、期末为 0/负、UNIT_COST_MISSING 回退
  - [x] 4.3 edge fixtures 同步更新（不破坏 M0 已有 7 份的校验层断言）

- [x] Task 5: wheel 交付与性能基线（依赖 Task 3；与 Task 4 并行）
  - [x] 5.1 engine 版本升 0.2.0，`uv build` 产出 wheel + `SHA256SUMS`，验证独立环境 pip 安装后公开入口可用
  - [x] 5.2 perf_bench 跑 1 万/10 万行记录基线数据，设定 M1 阈值并注明测量环境（100 万行入口保留，阈值 M3 再定）

- [x] Task 6: 文档与 Skill 对齐（依赖 Task 3-5）
  - [x] 6.1 `skills/kpi/SKILL.md` 公式步骤占位转实现引用（F-KPI/F-COGS 编号 + engine 0.2.0）
  - [x] 6.2 `skills/manifest.json` engine 版本范围、`CHANGELOG.md` 0.2.0 条目
  - [x] 6.3 新增 `docs/m1-handover-b.md`：wheel 安装命令、公开入口、性能基线与阈值、已知限制、回滚版本（上一 wheel + 兼容矩阵）

- [x] Task 7: 验收与 PR（依赖全部）
  - [x] 7.1 全量检查：pytest、ruff、mypy、Schema 漂移门禁全绿；按 §26.2 PR 模板提 `feature/b-m1-kpi-engine` → `master`

# Task Dependencies

- Task 0 → 全部（分支先建立）
- Task 1 → Task 2（计算依赖重放内核）、Task 3
- Task 2 → Task 3 → Task 4、Task 5 → Task 6 → Task 7
- Task 2 的五个子公式在重放内核（Task 1）就绪后可并行开发
- Task 4 与 Task 5 无相互依赖，可并行

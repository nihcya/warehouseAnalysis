# Checklist：开发者 B M1 标准化与 KPI 引擎

## 分支与 M0 收尾
- [x] `feature/b-m1-kpi-engine` 分支从 `origin/master`（含 A 的平台基线）创建，本地 master 未被直接提交
- [x] formula-spec.md 冻结签字已提交（c62917b），M0 tasks.md Task 2.5 已勾选
- [x] 用户未提交的 README.md、docs/log.txt 改动原样保留在工作区，未被回滚或误提交

## 校验与重放
- [x] 期间归属（左闭右开、move_date 折算）与重放顺序 `(move_date, occurred_at, event_id)` 有测试覆盖
- [x] 冲销引用不存在事件返回阻断 `DATA_VALIDATION_FAILED` 且 details 定位事件
- [x] 盘点替换余额、差额不计净入库有测试覆盖
- [x] 负库存照常计算 + 非阻断 `NEGATIVE_BALANCE`（含 sku_id、warehouse_id、首个负余额日期与数值）

## KPI/COGS 实现
- [x] F-KPI-001~008、F-COGS-001 全部实现且每个指标带 formula_id / formula_version / 期间 / sample_count
- [x] COGS 按移动加权平均，与黄金数据手工核算值容差内一致（v0.1.0=143.40、v0.2.0=38.50 手工推导冻结）
- [x] 库存价值 UNIT_COST_MISSING 回退 + Warning，SKU 不被静默丢弃（回退快照 27.00 / 双缺失 null 不计合计）
- [x] 舍入规则：金额 HALF_UP 0.01、数量保输入 scale、比例 6 位小数；中间计算不舍入

## analyze 集成
- [x] KPI 路径返回真实结果，无 ANALYSIS_PLACEHOLDER；其余四类维持占位并保留占位 Warning
- [x] 数据质量报告（Warning 码计数 + 明细）随 AnalysisResult 返回（AnalysisResult.data_quality）
- [x] 确定性：同一输入两次 analyze 序列化逐字节一致
- [x] 守恒属性测试扩展：opening + 本期净变动 = closing（Hypothesis）

## 黄金数据
- [x] expected 扩展到指标数值层，每个 F-KPI/F-COGS 至少一个容差断言（v0.1.0 扩展 + v0.2.0 新增，9 指标全覆盖）
- [x] 边界用例齐全：空历史、含盘点、退货与冲销、期末为 0/负、UNIT_COST_MISSING
- [x] M0 已有 7 份 edge fixture 的校验层断言未被破坏（全部保留并新增指标层断言）

## 交付物
- [x] engine 0.2.0 wheel + SHA256SUMS 构建成功，独立环境 pip 安装后公开入口可用（12 项检查 PASS）
- [x] perf_bench 1 万/10 万行基线数据与 M1 阈值记录于 m1-handover-b.md（含测量环境与 SHA256）
- [x] kpi SKILL.md、manifest.json、CHANGELOG.md 与 engine 0.2.0 对齐
- [x] docs/m1-handover-b.md 含安装命令、公开入口、已知限制、回滚版本

## 验收
- [x] pytest / ruff / mypy / Schema 漂移门禁全绿（204 passed 1 skipped / ruff 全过 / mypy 19 文件无问题 / Schema 重导出无漂移）
- [x] 未修改 A 侧代码（services/、apps/、local-data/ 的三个提交均未触碰；openapi.json CRLF 仅为 worktree 检出环境问题，已恢复 LF，无内容变更）
- [x] PR 按 §26.2 模板提交，contracts 若有变更则为非破坏 minor（schema_version 1.1）并重导出 Schema —— contracts 变更为新增可选字段（reversal_of、ResultMetric.reason、AnalysisResult.data_quality），非破坏，Schema 已重导出

# 仓库品类分析决策工具：开发需求 B（核心引擎、Skill 与数据评测）

**文档版本**：V1.0  
**适用角色**：开发者 B（标准化、Warehouse Engine、Skill、行业基准和评测）  
**上位基线**：`仓库品类分析决策工具-开发规划与协作需求文档.md` V2.0  
**文档性质**：B 的执行需求和验收清单，不得改变上位基线的数据库边界、API 语义和 UI 规则

## 1. B 的目标

- 交付可安装、可版本化、可重复运行的 `warehouse-engine` Python wheel。
- 将库存、采购、批次和快照转换为可解释的 KPI、ABC、库龄、呆滞、补货、预测和行业基准结果。
- 为每个 Skill 提供输入/输出 Schema、公式版本、Warning、依赖、权限和兼容矩阵。
- 用黄金数据、边界数据、属性测试和性能基准证明结果可用，避免 A 在 UI 中重新实现公式。
- 让 A 只消费公开接口和版本化包，不读取 B 的内部模块、DataFrame 或实验代码。

## 2. 不在 B 范围内

- 不实现 PySide6 页面、Next.js 页面、FastAPI 路由、SQLAlchemy Model、Alembic 迁移和 SQLite 查询。
- 不直接读取商户数据库、客户文件、环境变量、网络、用户目录或云端模型 API。
- 不把实验模型直接设为默认采购依据；实验内容必须位于 `experimental/` 并标记非默认。
- 不开发微信小程序页面；只提供小程序事件需要的标准化规则、Schema 和 Mock 样例。
- 不把自然语言结论作为唯一输出；所有结论必须建立在结构化指标、来源期间和 Warning 之上。

## 3. B 负责的模块和目录

```text
packages/warehouse-engine/src/warehouse_engine/
├── contracts.py                 # 引用公共 contracts，不复制 DTO
├── validation/                  # 输入校验、单位、日期和边界规则
├── calculators/
│   ├── inventory_kpi.py         # 动销、周转、库存价值等
│   ├── abc_aging.py             # ABC、库龄、呆滞
│   ├── replenishment.py         # 安全库存、补货点和建议量
│   ├── forecasting.py           # 预测模型和误差评估
│   └── benchmark_compare.py     # 行业基准比较
├── skills/                      # Skill 编排和结果组合
├── result.py                    # 结构化结果、Warning、解释字段
├── errors.py                    # 错误码和异常映射
└── engine.py                    # WarehouseEngine 公共实现

skills/
├── kpi/SKILL.md
├── abc-aging/SKILL.md
├── replenishment/SKILL.md
├── forecasting/SKILL.md
└── benchmark/SKILL.md
```

允许依赖 `contracts-python`、锁定的 pandas/numpy/scipy/statsmodels 和标准库；禁止依赖 UI、HTTP、ORM、SQLite 和 A 的内部路径。

## 4. 公共接口和数据契约

### 4.1 Engine 接口

```python
class WarehouseEngine(Protocol):
    def validate_dataset(self, request: AnalysisRequest) -> ValidationReport: ...
    def analyze(self, request: AnalysisRequest, progress: ProgressCallback | None = None) -> AnalysisResult: ...
    def list_capabilities(self) -> list[CapabilityDescriptor]: ...
```

`AnalysisRequest` 至少包含：`schema_version`、`run_id`、`start_date`、`end_date`、`warehouse_ids`、筛选条件、标准化数据集和参数。`AnalysisResult` 至少包含：`schema_version`、`run_id`、`engine_version`、`formula_version`、指标、警告、摘要和输入摘要。

### 4.2 序列化规则

- 跨模块使用 Pydantic 模型或 JSON Schema；禁止把可变 DataFrame 作为公开协议。
- 日期使用 `YYYY-MM-DD`；时间使用 UTC ISO 8601；数量和金额使用 Decimal 语义，禁止 float 作为金额真值。
- 每个结果携带 `run_id`、数据期间、公式版本、引擎版本和输入摘要，保证可追溯和历史重开。
- Warning 必须含 `code`、`severity`、`message`、`fields` 和是否阻断；UI 依据 `code`，不依据中文文案判断。
- 不兼容的字段删除、类型改变或单位改变必须提升 `schema_version`，并更新兼容矩阵和迁移说明。

### 4.3 标准化输入字段

| 字段 | 类型/单位 | 空值规则 | 说明 |
|---|---|---|---|
| `sku_id` | 字符串 | 必填 | 本地 SKU 标识 |
| `occurred_at` | UTC 时间 | 必填 | 库存事件发生时间 |
| `move_type` | 枚举 | 必填 | 入库、出库、退货、报废、调拨、盘点、冲销 |
| `quantity` | 基础单位 Decimal | 必填且大于 0 | 事件数量，方向由 `move_type` 决定 |
| `unit_cost` | Decimal + 币种 | 采购/入库可选 | 金额精度由 contracts 冻结 |
| `warehouse_id` | 字符串 | 必填 | 仓库范围 |
| `lot_id` | 字符串 | 可选 | 批次分析需要时必填 |
| `source` | 枚举 | 必填 | IMPORT、DESKTOP、MINI_PROGRAM、ADJUSTMENT |
| `lead_time_days` | 非负 Decimal | 补货需要 | 供应商提前期 |
| `on_hand_qty` | 基础单位 Decimal | 快照可选 | 快照时点库存 |

B 必须提供字段映射、单位换算、空值处理、非法值和错误码的正反例，A 只实现适配，不自行猜测。

## 5. 计算能力要求

### 5.1 KPI

- 交付动销、出库量、库存量、库存价值、周转率、周转天数、缺货率和覆盖天数等结构化指标。
- 每个指标记录公式标识、公式版本、数据期间、聚合粒度、单位、样本数和 Warning。
- 明确期初/期末库存、COGS、退货、盘点和负库存的处理口径；未冻结的口径不得进入默认结果。

### 5.2 ABC、库龄和呆滞

- ABC 分层的排序指标、累计占比、边界归属和并列值规则必须写入 `formula_version`。
- 库龄按事件或快照口径计算，区间边界统一使用左闭右开，缺失日期产生 Warning。
- 呆滞判断至少包含观察窗口、无出库天数、最低库存阈值和排除条件。

### 5.3 补货和预测

- 补货结果包含安全库存、补货点、建议量、在途量、提前期、服务水平假设和限制原因。
- 预测输出模型名称、训练窗口、预测窗口、输入样本数、误差指标和是否降级到基线模型。
- 任何数据不足、季节性不稳定或异常值处理都必须产生可读 Warning，不隐藏为“正常结果”。

### 5.4 行业基准

- 基准记录来源、地区、行业、样本范围、更新时间、版本、指标单位和适用限制。
- 不允许把未经来源说明的经验数字作为默认基准；无匹配基准时返回 `BENCHMARK_UNAVAILABLE`。

## 6. Skill 规范

每个 Skill 的 `SKILL.md` 必须包含：

- 名称、版本、用途、输入 Schema、输出 Schema、公式/计算步骤和示例。
- 依赖的 engine 版本范围、配置参数、默认值、权限和资源限制。
- 可能的错误码、Warning、数据不足行为和降级策略。
- 黄金数据、边界数据、性能基线、变更记录和兼容矩阵。

Skill 只编排参数校验、计算顺序和结果组合，不直接写 `analysis_result`，不执行 Shell，不访问用户文件，不下载任意代码。

## 7. 开发顺序

### M0：契约和基线（第 1 周）

1. 与 A 冻结 `AnalysisRequest/Result`、标准化列、错误码、数量/金额精度和版本策略。
2. 建立 engine 包、Skill 目录、FakeEngine 和最小 JSON fixture。
3. 建立 `pytest`、Ruff、mypy、Hypothesis 和性能脚本的 CI 入口。

### M1：标准化与 KPI（第 2-4 周）

1. 实现字段、单位、日期、重复事件和负库存校验。
2. 实现 KPI 和数据质量报告，输出确定性结果。
3. 提交黄金结果和 A 可消费的版本化 wheel。

### M2：分析 Skill（第 4-6 周）

1. 实现 ABC、库龄、呆滞、基准比较。
2. 实现补货、预测和误差评估；实验模型与默认模型隔离。
3. 与 A 的 FakeEngine/真实 Engine 进行“导入→分析→报告”联调。

### M3：稳定性与发布（第 7-12 周）

1. 完成黄金/边界/属性/性能/重复运行测试。
2. 生成 engine wheel、SHA-256、依赖清单、SBOM 片段和变更日志。
3. 在 staging 使用脱敏数据确认版本兼容、回滚和结果可追溯。

## 8. 测试、评测与质量门槛

### 8.1 测试层级

- 单元测试：每个计算器的正常、空集、零需求、负库存、重复事件、边界日期和非法单位。
- 黄金数据：固定输入、预期数值、分类、排序、Warning 和版本；比较时使用逐字段容差。
- 属性测试：库存守恒、数量非负、分类互斥/完备、相同输入结果一致。
- 兼容测试：新增字段、字段顺序、可选字段和旧 `schema_version` 的兼容行为。
- 性能测试：1 万、10 万、100 万流水的耗时、内存、CPU、输出大小和失败率。
- 安全测试：Skill 无任意文件/命令/网络访问，依赖无高危漏洞，包签名和哈希可验证。

### 8.2 数值验收规则

- 每类指标在 fixture 中写明绝对误差和相对误差容忍度，不能笼统要求“与 Excel 误差为 0”。
- 预测评估必须写明数据集、时间切分、基线模型、MAPE/MAE/RMSE 等指标及适用条件；没有足够样本时不判定达标。
- 任何规则变更必须提升 `formula_version`，重新生成黄金结果并说明对旧报告的影响。

### 8.3 B 完成定义

- `uv run pytest packages/warehouse-engine tests/engine` 全绿，Ruff 和 mypy 通过。
- 同一输入、同一 contracts 和 formula 版本重复运行结果字节级稳定或差异在声明容差内。
- 所有指标、Warning、单位、期间和版本字段齐全；异常数据不会静默丢弃。
- engine wheel 可在没有工作台、API、数据库和客户文件的环境独立安装运行。
- A 能通过公开接口完成真实 wheel 替换 FakeEngine，并保留历史结果可重开。

## 9. 交给 A 的交接包

每个可消费版本必须包含：

```text
dist/warehouse_engine-<version>-py3-none-any.whl
dist/SHA256SUMS
contracts/analysis-request.schema.json
contracts/analysis-result.schema.json
fixtures/golden/<dataset-version>/input.json
fixtures/golden/<dataset-version>/expected.json
skills/*/SKILL.md
CHANGELOG.md
compatibility-matrix.md
benchmark/<dataset-version>.json
```

交接说明必须写明：安装命令、公开入口、依赖版本、支持的 Schema、公式版本、Warning 码、已知限制、性能环境和回滚版本。

## 10. B 接收 A 的内容

- 标准化 JSON/fixture、字段映射、单位和精度规则、数据期间和错误码。
- A 生成的 `EngineDataset` 示例；B 不直接打开 SQLite 或依赖 A 的 ORM。
- A 的集成测试命令、Python 版本和预期序列化结果。
- 发现字段、单位或库存语义问题时，提交 contracts Issue；不在 B 内部偷偷兼容或修改 A 数据库。

## 11. 禁止耦合和变更流程

- A 不得在 UI、Repository 或报告模板中复制公式；B 不得为了适配 UI 加入展示格式化。
- B 不得修改 API、数据库迁移或 Web 路由；需要字段时先更新 contracts 和兼容矩阵。
- 任何删除/改类型/改单位的公共字段必须由 A、B 共同评审，增加 `schema_version`，补正反例和迁移说明。
- 版本发布顺序为：fixture/Schema → engine/Skill → A 适配层 → UI/报告 → staging → release。

**B 的发布门槛：黄金数据、边界数据、属性测试、性能报告、兼容矩阵和可验证 wheel 未归档前，不得把引擎标为生产版本。**

"""引擎版本常量（M0 基线）。

- ENGINE_VERSION：引擎实现版本，随包版本演进；
- FORMULA_VERSION：公式口径版本，任何口径变更必须提升此版本并重新生成黄金数据。
"""

#: 引擎版本
ENGINE_VERSION = "0.2.0"

#: 公式口径版本（口径冻结于 docs/formula-spec.md）
FORMULA_VERSION = "0.1.0"

"""Schema 同步测试：contracts-schema 文件必须与 Pydantic 模型导出一致（防漂移）。

不一致时说明模型已变更但 Schema 未重新导出，
请运行 ``uv run python scripts/export_schemas.py`` 后提交。
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts import AnalysisRequest, AnalysisResult

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "packages" / "contracts-schema"

DRIFT_HINT = (
    "JSON Schema 与 Pydantic 模型不一致（Schema 漂移）："
    "请运行 `uv run python scripts/export_schemas.py` 重新导出并提交。"
)


def test_request_schema_in_sync() -> None:
    on_disk = json.loads(
        (SCHEMA_DIR / "analysis-request.schema.json").read_text(encoding="utf-8")
    )
    assert on_disk == AnalysisRequest.model_json_schema(), DRIFT_HINT


def test_result_schema_in_sync() -> None:
    on_disk = json.loads(
        (SCHEMA_DIR / "analysis-result.schema.json").read_text(encoding="utf-8")
    )
    assert on_disk == AnalysisResult.model_json_schema(), DRIFT_HINT

"""fixture Schema 测试：golden 与全部 edge fixture 的 request/dataset 符合契约 Schema。

- request 部分对 analysis-request.schema.json（contracts-schema 导出文件）校验；
- dataset 部分对 EngineDataset.model_json_schema()（模型导出）校验。

注意：Schema 校验只覆盖结构与类型；精度、重复事件、期间等业务规则由
validate_dataset 负责（见 tests/engine/test_edge_cases.py）。
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest
from contracts import EngineDataset

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
REQUEST_SCHEMA = json.loads(
    (REPO_ROOT / "packages" / "contracts-schema" / "analysis-request.schema.json").read_text(
        encoding="utf-8"
    )
)
DATASET_SCHEMA = EngineDataset.model_json_schema()


def _all_fixture_files() -> list[Path]:
    golden = FIXTURES_DIR / "golden" / "v0.1.0" / "input.json"
    edges = sorted((FIXTURES_DIR / "edge").glob("*.json"))
    return [golden, *edges]


@pytest.mark.parametrize(
    "fixture_path",
    _all_fixture_files(),
    ids=lambda path: path.name,
)
def test_fixture_request_and_dataset_match_schemas(fixture_path: Path) -> None:
    payload: dict = json.loads(fixture_path.read_text(encoding="utf-8"))
    jsonschema.validate(instance=payload["request"], schema=REQUEST_SCHEMA)
    jsonschema.validate(instance=payload["dataset"], schema=DATASET_SCHEMA)

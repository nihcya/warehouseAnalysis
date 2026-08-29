"""导出契约 JSON Schema 到 packages/contracts-schema/。

用法：``uv run python scripts/export_schemas.py``

生成 analysis-request.schema.json 与 analysis-result.schema.json，
两者均由 Pydantic 模型直接导出，保证与 contracts-python 定义一致。
"""

from __future__ import annotations

import json
from pathlib import Path

from contracts import AnalysisRequest, AnalysisResult
from pydantic import BaseModel

# 仓库根目录（本脚本位于 <仓库根>/scripts/ 下）
REPO_ROOT = Path(__file__).resolve().parents[1]

# 输出目录：contracts-schema 为普通目录（非 Python 包），仅存放生成的 JSON Schema
OUTPUT_DIR = REPO_ROOT / "packages" / "contracts-schema"


def export_schema(filename: str, model: type[BaseModel]) -> Path:
    """将模型的 JSON Schema 以 UTF-8 写入目标文件，返回写出路径。"""
    schema = model.model_json_schema()
    path = OUTPUT_DIR / filename
    path.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> None:
    """导出两个契约 Schema 并打印写出路径。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    targets: list[tuple[str, type[BaseModel]]] = [
        ("analysis-request.schema.json", AnalysisRequest),
        ("analysis-result.schema.json", AnalysisResult),
    ]
    for filename, model in targets:
        path = export_schema(filename, model)
        print(f"已写出：{path}")


if __name__ == "__main__":
    main()

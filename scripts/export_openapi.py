"""导出 control-plane 的 OpenAPI 文档到 packages/contracts-schema/openapi.json。

用法：``uv run python scripts/export_openapi.py``

快照作为 /api/v1 唯一接口来源提交入库；CI 通过“重导出 + diff”
检查漂移（与 services/control-plane/tests/test_openapi_sync.py 同一口径）。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = REPO_ROOT / "services" / "control-plane"

# 服务不以可分发包安装，需把服务目录加入 sys.path 后再导入 app 包
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.main import export_openapi_json

OUTPUT_PATH = REPO_ROOT / "packages" / "contracts-schema" / "openapi.json"


def main() -> None:
    """导出 OpenAPI 快照并打印写出路径。"""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_bytes(export_openapi_json())
    print(f"已写出：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()

"""OpenAPI 快照一致性测试：packages/contracts-schema/openapi.json 必须与导出一致。

不一致说明路由已变更但快照未重新导出（OpenAPI 漂移），
请运行 ``uv run python scripts/export_openapi.py`` 后提交。
"""

from __future__ import annotations

from pathlib import Path

from app.main import export_openapi_json

SNAPSHOT = (
    Path(__file__).resolve().parents[3] / "packages" / "contracts-schema" / "openapi.json"
)

DRIFT_HINT = (
    "openapi.json 与 control-plane 当前路由不一致（OpenAPI 漂移）："
    "请运行 `uv run python scripts/export_openapi.py` 重新导出并提交。"
)


def test_openapi_snapshot_matches_export() -> None:
    """快照必须与 create_app().openapi() 的导出逐字节一致。"""
    assert SNAPSHOT.exists(), f"缺少 OpenAPI 快照：{SNAPSHOT}；请先运行导出脚本。"
    assert SNAPSHOT.read_bytes() == export_openapi_json(), DRIFT_HINT

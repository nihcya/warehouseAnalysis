"""同步信封契约测试：正例通过 Schema 校验，反例定位到具体字段。

对应 spec「同步信封契约」：
- 正例：主基线 §21.6 样例（手写样例 + Schema examples 字段中的样例）通过 jsonschema 校验；
- 反例：缺 ciphertext / 加密字段为空字符串、algorithm 非 AES-256-GCM、
  event_id 格式不匹配、出现未定义字段（additionalProperties=false），
  每个反例断言错误信息定位到具体字段名；
- 时间顺序：created_at < expires_at 无法用 JSON Schema（draft 2020-12）
  表达跨字段比较，属于应用层规则，由本文件的 validate_envelope_timing 校验
  （合法时间序返回空列表，时间倒置/相等返回非空错误列表）。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "packages" / "contracts-schema" / "sync-envelope.schema.json"
SCHEMA: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _doc_envelope() -> dict[str, Any]:
    """主基线 §21.6 同步信封样例（占位值替换为符合格式的合法值）。"""
    return {
        "event_id": "evt_01J8Z6T9KQ3M5V7WX",
        "merchant_id": "mch_01J8Z6T9KQ3M5V7WX",
        "target_device_id": "dev_01J8Z6T9KQ3M5V7WX",
        "idempotency_key": "wx_req_20260901_0001",
        "algorithm": "AES-256-GCM",
        "nonce": "Q5w8e2r9tY1uI3oP5u7A",
        "ciphertext": "kJ8n7m6Q5z4X3c2V1b0Na9s8d7f6g5h4j3k2l1z6x",
        "created_at": "2026-09-01T08:01:00Z",
        "expires_at": "2026-09-01T08:31:00Z",
    }


def _assert_rejected_with_field(envelope: dict[str, Any], field_name: str) -> None:
    """断言 envelope 被 Schema 拒绝，且错误信息定位到 field_name。

    required / additionalProperties 错误的字段名出现在 message 中
    （如 "'ciphertext' is a required property"）；属性级错误
    （pattern / const）的字段名出现在 json_path 中（如 "$['event_id']"）。
    """
    with pytest.raises(jsonschema.ValidationError) as excinfo:
        jsonschema.validate(instance=envelope, schema=SCHEMA)
    located = f"{excinfo.value.message} {excinfo.value.json_path}"
    assert field_name in located, f"错误未定位到字段 {field_name}：{excinfo.value.message}"


def validate_envelope_timing(envelope: dict[str, Any]) -> list[str]:
    """应用层时间顺序校验：expires_at 必须晚于 created_at，返回错误列表。

    JSON Schema draft 2020-12 无法表达跨字段比较，该规则属于应用层校验；
    缺字段/格式非法已由 Schema 层负责，此处不重复报错。
    """
    try:
        created_at = datetime.fromisoformat(envelope["created_at"])
        expires_at = datetime.fromisoformat(envelope["expires_at"])
    except (KeyError, ValueError):
        return []
    if expires_at <= created_at:
        message = (
            "expires_at 必须晚于 created_at："
            f"created_at={envelope['created_at']}，expires_at={envelope['expires_at']}"
        )
        return [message]
    return []


def test_doc_envelope_passes_validation() -> None:
    """正例：§21.6 手写样例通过 Schema 校验。"""
    jsonschema.validate(instance=_doc_envelope(), schema=SCHEMA)


@pytest.mark.parametrize(
    "example",
    SCHEMA["examples"],
    ids=[f"schema-example-{index}" for index in range(len(SCHEMA["examples"]))],
)
def test_schema_examples_pass_validation(example: dict[str, Any]) -> None:
    """正例：Schema examples 字段中的样例通过校验。"""
    jsonschema.validate(instance=example, schema=SCHEMA)


def test_missing_ciphertext_rejected() -> None:
    """反例：缺 ciphertext，required 错误定位到 ciphertext。"""
    envelope = _doc_envelope()
    del envelope["ciphertext"]
    _assert_rejected_with_field(envelope, "ciphertext")


def test_empty_ciphertext_rejected() -> None:
    """反例：ciphertext 为空字符串，pattern 错误定位到 ciphertext。"""
    envelope = _doc_envelope()
    envelope["ciphertext"] = ""
    _assert_rejected_with_field(envelope, "ciphertext")


def test_invalid_algorithm_rejected() -> None:
    """反例：algorithm 为 AES-128-CBC，const 错误定位到 algorithm。"""
    envelope = _doc_envelope()
    envelope["algorithm"] = "AES-128-CBC"
    _assert_rejected_with_field(envelope, "algorithm")


def test_invalid_event_id_format_rejected() -> None:
    """反例：event_id 带空格不符合 evt_ 前缀 pattern，错误定位到 event_id。"""
    envelope = _doc_envelope()
    envelope["event_id"] = "evt 123"
    _assert_rejected_with_field(envelope, "event_id")


def test_undefined_field_rejected() -> None:
    """反例：additionalProperties=false，未定义字段 extra_field 被拒并定位。"""
    envelope = _doc_envelope()
    envelope["extra_field"] = "oops"
    _assert_rejected_with_field(envelope, "extra_field")


def test_validate_envelope_timing_valid_returns_empty() -> None:
    """时间顺序合法（created_at < expires_at）返回空错误列表。"""
    assert validate_envelope_timing(_doc_envelope()) == []


def test_validate_envelope_timing_inverted_returns_error() -> None:
    """时间倒置（expires_at 早于 created_at）返回非空错误，信息定位到两个字段。"""
    envelope = _doc_envelope()
    envelope["created_at"] = "2026-09-01T08:31:00Z"
    envelope["expires_at"] = "2026-09-01T08:01:00Z"
    errors = validate_envelope_timing(envelope)
    assert errors
    assert "expires_at" in errors[0]
    assert "created_at" in errors[0]


def test_validate_envelope_timing_equal_returns_error() -> None:
    """时间相等（expires_at == created_at）同样返回非空错误。"""
    envelope = _doc_envelope()
    envelope["expires_at"] = envelope["created_at"]
    assert validate_envelope_timing(envelope)

"""控制平面标识生成（M2）。

统一 ``<prefix>_<hex>`` 形式：可读性前缀 + uuid4 十六进制，便于日志与审计定位。
标识只用于标识，不含业务语义，不可推导、不可枚举。
"""

from __future__ import annotations

import uuid

#: 各实体的标识前缀（与云端表主键一一对应）
PREFIX_TENANT = "tnt"
PREFIX_ACCOUNT = "acc"
PREFIX_SESSION = "ses"
PREFIX_DEVICE = "dev"
PREFIX_LICENSE = "lic"
PREFIX_PRODUCT_PROFILE = "ppf"
PREFIX_FEATURE_GRANT = "fgr"
PREFIX_AUDIT = "aud"
PREFIX_CONFIG_VERSION = "cfv"
PREFIX_TASK = "tsk"
PREFIX_TASK_RUN = "run"
PREFIX_SYNC_ENVELOPE = "env"
PREFIX_SYNC_EVENT = "evt"


def new_id(prefix: str) -> str:
    """生成 ``<prefix>_<uuid4 hex>`` 形式的标识。"""
    return f"{prefix}_{uuid.uuid4().hex}"

"""行业类型与功能授权（M2）。

- ``product_profile``：零售 / 批发 / 制造等行业类型，决定默认配置与能力集，
  一套代码服务多行业（主基线 §2.3、DECISIONS.md D-010）；
- ``feature_grant``：商户级功能开关，来源可以是许可证默认或开发者显式授权。

两者都是只读配置性实体：本模块不提供状态机，状态变更由开发者端（后续里程碑）
经审计后写入。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class ProductProfileStatus(str, Enum):
    """行业类型状态。"""

    ACTIVE = "ACTIVE"
    RETIRED = "RETIRED"  # 已下线（不再对新商户开放，存量为历史数据保留）


@dataclass
class ProductProfile:
    """行业类型实体。"""

    product_profile_id: str
    code: str
    name: str
    status: ProductProfileStatus = ProductProfileStatus.ACTIVE
    default_config_version: str | None = None


@dataclass
class FeatureGrant:
    """功能授权：``(tenant_id, feature_code)`` 唯一。"""

    feature_grant_id: str
    tenant_id: str
    feature_code: str
    enabled: bool = True
    source: str = "LICENSE"
    expires_at: datetime | None = None

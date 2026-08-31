"""设备指纹生成：基于 hostname + MAC 地址哈希生成稳定标识。

同一台机器多次调用结果一致；跨机器不同。
"""

from __future__ import annotations

import hashlib
import socket
import uuid

#: 指纹长度（SHA-256 取前 32 字符）
FINGERPRINT_LENGTH = 32


def generate_device_fingerprint() -> str:
    """生成稳定设备指纹：hostname + MAC 地址 SHA-256 取前 32 字符。"""
    hostname = socket.gethostname()
    mac = uuid.getnode()
    raw = f"{hostname}|{mac}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return digest[:FINGERPRINT_LENGTH]


def get_device_name() -> str:
    """返回 hostname 作为设备名。"""
    return socket.gethostname()

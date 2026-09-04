"""同步信封对称加密（Fernet，M3）。

- 小程序事件明文只存在于加密前的内存：落库与传输均为密文
  （主基线 §35.5 / SECURITY.md：密钥经环境变量注入，不写入代码）；
- 密钥为 Fernet 规范的 urlsafe base64 32 字节（``Fernet.generate_key()`` 生成），
  云端（``SYNC_ENCRYPTION_KEY``）与工作台必须持有同一密钥；
- 云端只加密（dev 注入工具）；解密供工作台侧与联调测试复用同一口径。
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet


def encrypt_json(payload: dict[str, Any], key: str) -> str:
    """把事件载荷规范化序列化后加密为 Fernet 密文（token 字符串）。"""
    fernet = Fernet(key.encode("utf-8"))
    plain = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return fernet.encrypt(plain).decode("utf-8")


def decrypt_json(ciphertext: str, key: str) -> dict[str, Any]:
    """解密 Fernet 密文并反序列化为事件载荷（密钥不符或密文损坏抛 InvalidToken）。"""
    fernet = Fernet(key.encode("utf-8"))
    plain = fernet.decrypt(ciphertext.encode("utf-8"))
    return dict(json.loads(plain.decode("utf-8")))

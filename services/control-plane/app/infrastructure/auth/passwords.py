"""密码哈希：Argon2id（M2 实装）。

SECURITY.md 冻结「商户密码：Argon2id 哈希」；主基线 §35.5 要求
账号密码只保存 Argon2id 哈希，明码不得落库、不得写入日志。

- 只暴露 ``hash_password`` / ``verify_password`` 两个入口，调用方不接触算法参数；
- 校验失败与哈希损坏一律返回 ``False``，不抛异常、不回传原因
  （避免把"账号不存在/密码错误/哈希格式错误"区分出来造成账号枚举）；
- 哈希值自带参数与盐，可直接存 ``account.password_hash``。
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

#: 进程级单例：argon2-cffi 的 PasswordHasher 线程安全，可复用
_HASHER = PasswordHasher()


def hash_password(password: str) -> str:
    """生成 Argon2id 哈希（含参数与盐，可直接入库）。"""
    try:
        return _HASHER.hash(password)
    except HashingError as exc:  # pragma: no cover —— 仅在内存/参数异常时触发
        raise RuntimeError("密码哈希失败。") from exc


def verify_password(password: str, password_hash: str) -> bool:
    """校验密码；任何不匹配或哈希不可解析都返回 False（不泄露具体原因）。"""
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False

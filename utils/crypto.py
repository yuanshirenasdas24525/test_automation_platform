"""对称加密工具 —— 平台内部敏感字段（Git PAT / SSH key 等）落库前加密。

设计：
- AES-256-GCM（cryptography 库），自带 auth tag 抗篡改
- 主密钥从 env ``PLATFORM_SECRET_KEY`` 读：
    - 32 字节原始 base64 字符串（urlsafe / 标准都接受）
    - 或任意字符串 → 用 SHA-256 派生 32 字节 key（弱场景兜底，会 warn）
- 没设 env：用一个**固定的 dev 默认 key**，启动期 warn。
  生产部署 **必须** 在 docker-compose / k8s secret 里注入 ``PLATFORM_SECRET_KEY``。
- 加密输出格式 base64(nonce_12B || ciphertext || tag_16B)，前端 / 数据库都按字符串走。

不直接处理 Fernet —— Fernet 是 AES-128-CBC + HMAC，跟我们文档里的"AES-256"对不上；
GCM 模式更现代、单独 tag 校验、性能也更好。

用法：
    from utils.crypto import encrypt_secret, decrypt_secret
    blob = encrypt_secret("github_pat_xxx")          # → 落 DB
    plain = decrypt_secret(blob)                     # → worker 调 git 前还原
"""
from __future__ import annotations

import base64
import hashlib
import os
import warnings

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


# ---------------------------------------------------------------------------
# 主密钥解析
# ---------------------------------------------------------------------------
_DEV_DEFAULT_KEY_HEX = (
    # 32 字节，固定，仅 dev / CI / 本地起服务用。生产覆盖。
    "0b" * 32
)


def _resolve_key() -> bytes:
    """返回 32 字节主密钥；env 缺失时 fallback 到 dev key + warn。"""
    raw = os.getenv("PLATFORM_SECRET_KEY")
    if not raw:
        warnings.warn(
            "PLATFORM_SECRET_KEY 未设置，正在使用内置 dev 密钥。"
            "生产环境必须设置该环境变量（32 字节 base64 或任意长字符串）。",
            stacklevel=2,
        )
        return bytes.fromhex(_DEV_DEFAULT_KEY_HEX)

    # 1) 尝试当 base64 解（urlsafe + 标准两种 padding 都试）
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            padded = raw + "=" * (-len(raw) % 4)
            decoded = decoder(padded)
            if len(decoded) == 32:
                return decoded
        except (ValueError, base64.binascii.Error):
            continue

    # 2) 当 hex 解
    try:
        decoded = bytes.fromhex(raw)
        if len(decoded) == 32:
            return decoded
    except ValueError:
        pass

    # 3) 普通字符串 → SHA-256 派生
    warnings.warn(
        "PLATFORM_SECRET_KEY 不是 32 字节 base64/hex，回退到 SHA-256 派生。"
        "建议生产配置：`openssl rand -base64 32` 生成强密钥。",
        stacklevel=2,
    )
    return hashlib.sha256(raw.encode("utf-8")).digest()


# 进程内缓存 key + AESGCM 实例；env 改了重启进程即可
_KEY: bytes | None = None
_CIPHER: AESGCM | None = None


def _cipher() -> AESGCM:
    global _KEY, _CIPHER
    if _CIPHER is None:
        _KEY = _resolve_key()
        _CIPHER = AESGCM(_KEY)
    return _CIPHER


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------
_NONCE_BYTES = 12  # GCM 推荐 96 bit


def encrypt_secret(plaintext: str) -> str:
    """加密敏感字符串。返回 base64(urlsafe) 字符串，存 DB 用。"""
    if plaintext is None:
        raise ValueError("encrypt_secret: plaintext 不能为 None")
    nonce = os.urandom(_NONCE_BYTES)
    ct = _cipher().encrypt(nonce, plaintext.encode("utf-8"), associated_data=None)
    return base64.urlsafe_b64encode(nonce + ct).decode("ascii")


def decrypt_secret(token: str) -> str:
    """解密 encrypt_secret 产物。token 损坏 / 主密钥换了会抛 InvalidTag。"""
    if not token:
        raise ValueError("decrypt_secret: 空 token")
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    if len(raw) < _NONCE_BYTES + 16:
        raise ValueError("decrypt_secret: token 长度异常")
    nonce, ct = raw[:_NONCE_BYTES], raw[_NONCE_BYTES:]
    plain = _cipher().decrypt(nonce, ct, associated_data=None)
    return plain.decode("utf-8")


# ---------------------------------------------------------------------------
# self-test —— python utils/crypto.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    samples = [
        "github_pat_11ABCXYZ_xxxxxxxxxxxxxxxxxxxxxx",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nfake-multi-line\n-----END-----",
        "",  # 空串也得能跑（虽然实际不会用空串）
        "中文密钥也能 ✅",
    ]

    failures = []
    for s in samples:
        if s == "":
            # 空串单独跑——decrypt_secret 拒空但 encrypt_secret 允许
            blob = encrypt_secret(s)
            got = decrypt_secret(blob)
            if got != s:
                failures.append(f"empty roundtrip mismatch: {got!r}")
            continue
        blob = encrypt_secret(s)
        got = decrypt_secret(blob)
        if got != s:
            failures.append(f"mismatch: in={s!r} out={got!r}")
            continue
        # 同样明文 → 两次 nonce 必须不同
        blob2 = encrypt_secret(s)
        if blob == blob2:
            failures.append(f"nonce reused: {blob}")

    # 篡改检测
    blob = encrypt_secret("hello")
    bad = blob[:-2] + ("A" if blob[-1] != "A" else "B") + "="
    try:
        decrypt_secret(bad)
        failures.append("tamper not detected")
    except Exception:  # noqa: BLE001
        pass

    if failures:
        print("FAIL:")
        for f in failures:
            print("  ", f)
        sys.exit(1)
    print("crypto self-test ok")

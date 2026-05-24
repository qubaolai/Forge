"""加密 provider_keys 表中已有的明文 Key。

用法:
    1. 先生成密钥: python3 -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
    2. 将密钥写入 .env: FORGE_ENCRYPTION_KEY=xxxxx
    3. 执行加密: APP_ENV=dev poetry run python scripts/encrypt_existing_keys.py

幂等: 已是密文的 key 自动跳过（通过检查能否成功 decrypt）。
"""

from __future__ import annotations

import asyncio
import logging
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("encrypt_keys")


def _is_already_encrypted(text: str) -> bool:
    """简单判断：AES-GCM 密文是 Base64 编码，以 nonce(12) + ciphertext 形式存储。
    明文 API Key 通常以 sk- / fk- / api- 开头。"""
    return not any(text.startswith(p) for p in ("sk-", "fk-", "api-", "ak-", "cm-"))


async def encrypt_keys() -> None:
    from forge.config.settings import get_settings
    from forge.infrastructure.database import database as db_module
    from forge.infrastructure.database.orm.provider_key_orm import ProviderKeyOrm
    from forge.core.crypto import encrypt, decrypt

    settings = get_settings()
    db_module.init_engine()
    await db_module.ping()

    session_factory = db_module.get_session_factory()
    async with session_factory() as db:
        from sqlalchemy import select

        res = await db.execute(select(ProviderKeyOrm))
        all_keys = list(res.scalars().all())

        updated = 0
        skipped = 0
        failed = 0

        for key_row in all_keys:
            current = key_row.key_ciphertext
            if not current:
                skipped += 1
                continue

            # 已经是密文则跳过
            if _is_already_encrypted(current):
                # 再验证一下能解吗
                try:
                    decrypt(current)
                    logger.info("  跳过 (已是密文): fingerprint=%s", key_row.key_fingerprint)
                    skipped += 1
                    continue
                except Exception:
                    logger.info("  密文无法解密，将用当前值重新加密: fingerprint=%s", key_row.key_fingerprint)

            try:
                key_row.key_ciphertext = encrypt(current)
                updated += 1
                logger.info("  已加密: fingerprint=%s → OK", key_row.key_fingerprint)
            except Exception as e:
                failed += 1
                logger.error("  加密失败: fingerprint=%s err=%s", key_row.key_fingerprint, e)

        if updated or skipped or failed:
            await db.commit()
            logger.info(
                "加密完成: 已加密=%d 已跳过=%d 失败=%d 总计=%d",
                updated, skipped, failed, len(all_keys),
            )
        else:
            logger.info("无 Key 需要加密")

    await db_module.dispose_engine()


if __name__ == "__main__":
    # 确保项目根在 sys.path 中
    import sys
    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    src = os.path.join(server_dir, "src")
    if src not in sys.path:
        sys.path.insert(0, src)

    from forge.config.settings import get_settings
    get_settings()

    if not os.environ.get("FORGE_ENCRYPTION_KEY", "").strip():
        logger.error(
            "FORGE_ENCRYPTION_KEY 未设置！\n"
            "先在 server/.env 中添加 FORGE_ENCRYPTION_KEY=xxx\n"
            "生成密钥命令:\n"
            "  python3 -c \"import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())\""
        )
        exit(1)

    asyncio.run(encrypt_keys())

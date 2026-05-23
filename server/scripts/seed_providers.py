"""初始化 model_providers 表数据。

用法:
    APP_ENV=dev poetry run python scripts/seed_providers.py

从 sys_config.yaml 的 llm.providers 段读取配置，写入 model_providers 表。
已存在的 provider（按 name 判断）会跳过。
"""

from __future__ import annotations

import asyncio
import os
import sys

server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if server_dir not in sys.path:
    sys.path.insert(0, server_dir)
src = os.path.join(server_dir, "src")
if src not in sys.path:
    sys.path.insert(0, src)


async def _main() -> None:
    from config.settings import get_settings

    settings = get_settings()
    providers_cfg = settings.llm.providers  # type: ignore[attr-defined]

    from forge.infrastructure.database.database import dispose_engine, init_engine
    from forge.infrastructure.database.repositories.model_provider_repo import (
        ProviderRepository,
    )

    init_engine()
    from forge.infrastructure.database.database import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        repo = ProviderRepository(db)
        for name, cfg in providers_cfg.items():
            existing = await repo.get_by_name(name)
            if existing:
                print(f"跳过 (已存在): {name}")
                continue

            api_keys = cfg.get("api_keys") or []
            api_key = api_keys[0] if api_keys else ""
            base_url = cfg.get("base_url") or None
            impl = cfg.get("impl") or name

            await repo.create(
                name=name,
                impl=impl,
                base_url=base_url,
                api_key=api_key,
                is_enabled=bool(api_key),
                priority=0,
            )
            print(f"创建: {name} (impl={impl}, enabled={bool(api_key)})")
        await db.commit()

    await dispose_engine()
    print("完成。")


if __name__ == "__main__":
    asyncio.run(_main())

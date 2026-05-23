"""数据库初始化脚本。

用法:
    # 开发环境（创建所有表）
    APP_ENV=dev poetry run python scripts/init_db.py

    # 生产环境（打印 SQL 不执行）
    APP_ENV=prod poetry run python scripts/init_db.py --dry-run

    # 指定环境
    APP_ENV=test poetry run python scripts/init_db.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


async def _main(dry_run: bool = False) -> None:
    # 确保项目根在 path 中
    server_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if server_dir not in sys.path:
        sys.path.insert(0, server_dir)
    src = os.path.join(server_dir, "src")
    if src not in sys.path:
        sys.path.insert(0, src)

    # 强制导入所有 ORM 模型
    from forge.infrastructure.database.orm.base import Base
    import forge.infrastructure.database.orm  # noqa: F401 — 触发模型注册

    from config.settings import get_settings

    settings = get_settings()
    print(f"数据库驱动: {settings.db.driver}")
    print(f"数据库 URL: {settings.db.safe_url}")
    print(f"表数量: {len(Base.metadata.tables)}")
    for table_name in sorted(Base.metadata.tables.keys()):
        print(f"  - {table_name}")

    if dry_run:
        print("\n[Dry-run] 仅输出表名，不执行建表。")
        print("如需生成 DDL SQL，请使用 scripts/schema.sql 手工执行。")
        return

    from forge.infrastructure.database.database import init_engine, dispose_engine

    init_engine()

    # 幂等建表（SQLAlchemy create_all 对已存在的表会跳过）
    from forge.infrastructure.database.orm.base import Base
    from forge.infrastructure.database.database import get_engine

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print(f"\n建表完成。{len(Base.metadata.tables)} 张表已就绪。")

    await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Forge 数据库初始化")
    parser.add_argument("--dry-run", action="store_true", help="仅输出表名，不执行建表")
    args = parser.parse_args()
    asyncio.run(_main(dry_run=args.dry_run))

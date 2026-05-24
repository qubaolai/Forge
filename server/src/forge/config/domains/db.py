"""数据存储层配置: SQLite/MySQL + Redis + Celery."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DBSettings(BaseSettings):
    """数据库连接配置.

    单机模式默认 SQLite (`sqlite+aiosqlite`), 路径未显式配置时落
    `forge.config.paths.kb_db_path()`.
    保留 MySQL 字段以兼容旧部署.
    """
    model_config = SettingsConfigDict(extra="ignore")

    driver: str = "sqlite+aiosqlite"
    host: str = ""
    port: int = 3306
    user: str = ""
    password: str = ""
    database: str = ""
    path: str = ""
    echo: bool = False
    charset: str = "utf8mb4"

    pool_size: int = 5
    max_overflow: int = 10
    pool_recycle: int = 3600
    pool_pre_ping: bool = True

    @property
    def is_sqlite(self) -> bool:
        return self.driver.startswith("sqlite")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        if self.is_sqlite:
            db_path = self._sqlite_path()
            if db_path == ":memory:":
                return "sqlite+aiosqlite:///:memory:"
            return f"sqlite+aiosqlite:///{db_path}"

        pwd = quote_plus(self.password)
        user = quote_plus(self.user)
        return (
            f"{self.driver}://{user}:{pwd}@{self.host}:{self.port}"
            f"/{self.database}?charset={self.charset}"
        )

    @property
    def safe_url(self) -> str:
        """脱敏连接串, 给日志/异常信息用."""
        if self.is_sqlite:
            db_path = self._sqlite_path()
            return f"sqlite+aiosqlite:///{db_path}"
        return (
            f"{self.driver}://{self.user}:***@{self.host}:{self.port}"
            f"/{self.database}?charset={self.charset}"
        )

    def _sqlite_path(self) -> str:
        raw = self.path or self.database
        if raw:
            if raw == ":memory:":
                return raw
            p = Path(raw).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            return str(p)
        from forge.config.paths import kb_db_path

        return str(kb_db_path())


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    host: str = "127.0.0.1"
    port: int = 6379
    username: str = ""
    password: str = ""
    database: int = 0
    pool_size: int = 10

    @computed_field  # type: ignore[prop-decorator]
    @property
    def url(self) -> str:
        """redis:// 连接串. 给 Celery 等需要 URL 形式的组件用."""
        auth = ""
        if self.password:
            user = self.username or ""
            auth = f"{quote_plus(user)}:{quote_plus(self.password)}@"
        return f"redis://{auth}{self.host}:{self.port}/{self.database}"


class CelerySettings(BaseSettings):
    """Celery 任务队列配置.

    broker_url / backend_url 留空表示走 Redis 配置 (与 RedisSettings.url 共用).
    enabled=False 时整套队列降级到 NullTaskQueue.
    """
    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = True
    broker_url: str = ""
    backend_url: str = ""
    task_modules: list[str] = [
        "forge.memory.tasks",
        "forge.observability.cost.tasks",
    ]

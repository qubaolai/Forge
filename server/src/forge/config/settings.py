"""
配置管理唯一入口。

用法:
    from forge.config.settings import get_settings
    s = get_settings()

    # LLM (支持运行时按 provider + model 切换)
    provider, model, call_cfg = s.llm.resolve(provider="dashscope", model="qwen-plus")

    # 工具模型: 业务层通过 LLMRequest(task_type="utility", model_profile="fast") 走 LLMGateway 即可

    # Embedding / Reranker 模型由数据库系统绑定选择。

环境配置选择 (优先级从高到低):
    1. init_settings(path) 显式传入
    2. APP_CONFIG=/absolute/path/to/config.yaml  完整路径
    3. APP_ENV=prod / dev / test  → <server_root>/config/sys_config.{env}.yaml
    4. 默认 <server_root>/config/sys_config.yaml
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import yaml

from forge.config._env import expand_env, load_env_files
from forge.config.domains.agent_profiles import AgentProfilesConfig
from forge.config.domains.app import AppConfig, MiddlewareConfig
from forge.config.domains.context import (
    ContextDigestSettings,
    ContextSemanticRecallSettings,
    ContextSettings,
)
from forge.config.domains.db import CelerySettings, DBSettings, RedisSettings
from forge.config.domains.llm import LLMConfig, UtilityLLMConfig
from forge.config.domains.memory import (
    MemorySettings,
    MemorySummarizerSettings,
    MemoryTriggerSettings,
)
from forge.config.domains.observability import ObservabilityConfig
from forge.config.domains.quota import UserQuotaSettings
from forge.config.domains.retrieval import (
    BM25StoreConfig,
    ComponentConfig,
    IngestConfig,
    RetrievalConfig,
)

logger = logging.getLogger(__name__)

SERVER_ROOT = Path(__file__).resolve().parents[3]
ROOT_DIR = SERVER_ROOT
CONFIG_DIR = SERVER_ROOT / "config"
CONFIG_FILE = CONFIG_DIR / "sys_config.yaml"

__all__ = [
    "Settings",
    "get_settings",
    "init_settings",
    "reset_settings",
    "ComponentConfig",
]



# ======================================================================
# Settings: 全局配置对象
# ======================================================================
class Settings:
    """全局配置对象, 通过 get_settings() 获取单例.

    各组件按领域平铺组织; 子模型定义在 forge/config/domains/ 各文件中.
    """

    def __init__(self, config: dict) -> None:
        self.app = AppConfig(**config.get("app", {}))
        self.middleware = MiddlewareConfig(**config.get("middleware", {}))
        self.observability = ObservabilityConfig(**config.get("observability", {}))
        self.db = DBSettings(**config.get("db", {}))
        self.redis = RedisSettings(**config.get("redis", {}))
        self.celery = CelerySettings(**config.get("celery", {}))
        self.user_quota = UserQuotaSettings(**(config.get("user_quota") or {}))

        mem_cfg = config.get("memory", {}) or {}
        self.memory = MemorySettings(
            enabled=mem_cfg.get("enabled", True),
            summarizer=MemorySummarizerSettings(**(mem_cfg.get("summarizer") or {})),
            trigger=MemoryTriggerSettings(**(mem_cfg.get("trigger") or {})),
        )

        ctx_cfg = config.get("context", {}) or {}
        self.context = ContextSettings(
            digest=ContextDigestSettings(**(ctx_cfg.get("digest") or {})),
            semantic_recall=ContextSemanticRecallSettings(
                **(ctx_cfg.get("semantic_recall") or {})
            ),
        )

        self.llm = LLMConfig(**config["llm"])

        utility_cfg = config.get("utility_llm", {}) or {}
        self.utility_llm = UtilityLLMConfig(**utility_cfg)

        self.ingest = IngestConfig(**config.get("ingest", {}))
        self.vector_store = ComponentConfig(**config["vector_store"])
        self.bm25_store = BM25StoreConfig(**config["bm25_store"])
        self.retrieval = RetrievalConfig(**config.get("retrieval", {}))
        # 统一 agent_mode 治理: 工具白名单 / 模型档位 / 步数上限均移到 agent_profiles 段.
        self.agent_profiles = AgentProfilesConfig(**(config.get("agent_profiles") or {}))

# ======================================================================
# 单例入口
# ======================================================================
ENV_CONFIG_KEY = "APP_CONFIG"
ENV_APP_ENV_KEY = "APP_ENV"

_settings: Settings | None = None
_settings_lock = threading.Lock()


def _resolve_config_path(explicit: Path | str | None = None) -> Path:
    """按优先级决定配置文件路径.

    1. explicit 参数
    2. APP_CONFIG 环境变量 (完整路径)
    3. APP_ENV 环境变量 → <server_root>/config/sys_config.{env}.yaml
    4. 默认 <server_root>/config/sys_config.yaml
    """
    if explicit is not None:
        return Path(explicit)
    if env_path := os.environ.get(ENV_CONFIG_KEY):
        return Path(env_path)
    if app_env := os.environ.get(ENV_APP_ENV_KEY):
        return CONFIG_DIR / f"sys_config.{app_env}.yaml"
    return CONFIG_FILE


def _load_yaml_with_env(path: Path) -> dict:
    app_env = os.environ.get(ENV_APP_ENV_KEY)
    load_env_files(ROOT_DIR, app_env=app_env)
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return expand_env(raw)


def init_settings(config_file: Path | str | None = None) -> Settings:
    """显式初始化配置单例. 应用启动时调一次.

    重复调用会抛错, 防止运行中途无意改配置.
    要切配置必须先 reset_settings() 清空再 init.
    """
    global _settings
    with _settings_lock:
        if _settings is not None:
            raise RuntimeError(
                "Settings 已初始化, 不能重复 init. 如确需重新加载, 先调 reset_settings()."
            )
        path = _resolve_config_path(config_file)
        if not path.exists():
            raise FileNotFoundError(f"配置文件不存在: {path}")
        raw = _load_yaml_with_env(path)
        _settings = Settings(raw)
        logger.info("配置加载完成: %s (APP_ENV=%s)", path, os.environ.get(ENV_APP_ENV_KEY, "-"))
    return _settings


def get_settings(config_file: Path | str | None = None) -> Settings:
    """获取配置单例. 首次调用时加载, 后续直接返回缓存."""
    global _settings
    if _settings is None:
        with _settings_lock:
            if _settings is None:
                path = _resolve_config_path(config_file)
                if not path.exists():
                    raise FileNotFoundError(f"配置文件不存在: {path}")
                raw = _load_yaml_with_env(path)
                _settings = Settings(raw)
                logger.info(
                    "配置加载完成: %s (APP_ENV=%s)", path, os.environ.get(ENV_APP_ENV_KEY, "-")
                )
    return _settings


def reset_settings() -> None:
    """清空已加载的配置单例.

    用途: 测试隔离 / 热重载. 不会清空下游缓存 (如 ComponentManager).
    """
    global _settings
    with _settings_lock:
        _settings = None
    logger.info("Settings 已重置")

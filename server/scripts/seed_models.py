"""模型数据初始化脚本 — 幂等，可重复执行。

用法:
    cd server
    APP_ENV=dev poetry run python scripts/seed_models.py

功能:
    1. 插入默认供应商 (dashscope / openai / deepseek / anthropic)
    2. 插入已知模型及其能力元数据 (脚本内置静态配置)
    3. 插入 embedding / reranker 模型
    4. 幂等: 已存在的 supplier/model 跳过 (按 name 去重)

注意:
    - API Key 不在此脚本维护，请在管理端或 SQL 中手工录入 provider_keys 表。
    - AES 加密 key 方法见 forge.utils.crypto。
"""

from __future__ import annotations

import asyncio
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_models")

# 模型能力元数据（脚本内置静态配置）
_CAPABILITIES: dict[str, dict] = {
    # Anthropic
    "claude-sonnet-4-6": {"context_window": 200000, "supports_tools": True, "supports_images": True, "thinking": {"type": "enabled"}},
    "claude-sonnet-4-5": {"context_window": 200000, "supports_tools": True, "supports_images": True, "thinking": {"type": "enabled"}},
    "claude-haiku-4-5":  {"context_window": 200000, "supports_tools": True, "supports_images": True, "thinking": {"type": "enabled"}},
    "claude-opus-4-7":   {"context_window": 200000, "supports_tools": True, "supports_images": True, "thinking": {"type": "enabled"}},
    # OpenAI
    "gpt-4o":            {"context_window": 128000, "supports_tools": True, "supports_images": True,  "thinking": None},
    "gpt-4o-mini":       {"context_window": 128000, "supports_tools": True, "supports_images": True,  "thinking": None},
    # DeepSeek
    "deepseek-v4-pro":   {"context_window": 1000000, "supports_tools": True, "supports_images": False, "thinking": {"type": "reasoning_effort", "options": ["high", "max"], "default": "high"}},
    "deepseek-v4-flash": {"context_window": 1000000, "supports_tools": True, "supports_images": False, "thinking": {"type": "reasoning_effort", "options": ["high", "max"], "default": "high"}},
    # DashScope (Qwen)
    "qwen3-max-preview": {"context_window": 32768,  "supports_tools": True, "supports_images": False, "thinking": None},
    "qwen-plus":         {"context_window": 131072, "supports_tools": True, "supports_images": False, "thinking": None},
    "qwen3.6-plus":      {"context_window": 131072, "supports_tools": True, "supports_images": False, "thinking": None},
    "qwen3.5-flash":     {"context_window": 8192,   "supports_tools": True, "supports_images": False, "thinking": None},
    "qwen3.7-max":       {"context_window": 131072, "supports_tools": True, "supports_images": False, "thinking": None},
}

# 供应商定义
_PROVIDERS = [
    {"provider_id": "prov_dashscope", "name": "dashscope", "impl": None, "priority": 10,
     "routing_config": {"fallback_chain": [{"provider": "openai", "model": "gpt-4o-mini"}]}},
    {"provider_id": "prov_openai",    "name": "openai",    "impl": None, "priority": 20},
    {"provider_id": "prov_deepseek",  "name": "deepseek",  "impl": None, "priority": 30},
    {"provider_id": "prov_anthropic", "name": "anthropic", "impl": None, "priority": 40},
]

# 文本模型列表 → 依附的供应商 name
_TEXT_MODELS: list[tuple[str, str, str, str, dict | None]] = [
    # (provider_name, model_name, display_name, cost_tier, extra_params)
    ("dashscope",  "qwen3-max-preview", "通义千问 Max",           "mid",       {"temperature": 0.7}),
    ("dashscope",  "qwen-plus",         "通义千问 Plus",          "cheap",     {"temperature": 0.7}),
    ("dashscope",  "qwen3.6-plus",      "通义千问 qwen3.6-plus",  "mid",       {"temperature": 0.7}),
    ("dashscope",  "qwen3.5-flash",     "通义千问 qwen3.5-flash", "cheap",     {"temperature": 0.7}),
    ("openai",     "gpt-4o",            "GPT-4o",                 "expensive", {"temperature": 0.7}),
    ("openai",     "gpt-4o-mini",       "GPT-4o mini",            "cheap",     {"temperature": 0.7}),
    ("deepseek",   "deepseek-v4-pro",   "DeepSeek V4 PRO",        "expensive", {"temperature": 0.7}),
    ("deepseek",   "deepseek-v4-flash", "DeepSeek V4 flash",      "mid",       {"temperature": 0.7}),
    ("anthropic",  "claude-sonnet-4-5", "Claude Sonnet 4.5",      "expensive", {"temperature": 1.0, "max_tokens": 4096}),
    ("anthropic",  "claude-haiku-4-5",  "Claude Haiku 4.5",       "mid",       {"temperature": 1.0, "max_tokens": 4096}),
    ("anthropic",  "claude-sonnet-4-6", "Claude Sonnet 4.6",      "expensive", {"temperature": 1.0, "max_tokens": 4096}),
    ("anthropic",  "claude-opus-4-7",   "Claude Opus 4.7",        "expensive", {"temperature": 1.0, "max_tokens": 4096}),
]

# 非文本模型 (embedding / reranker)
_OTHER_MODELS = [
    ("dashscope", "text-embedding-v3", "通义千问 Embedding V3", "embedding",
     {"dimension": 1024, "batch_size": 10, "max_retries": 3, "retry_backoff": 1.0},
     "cheap", True),
    ("dashscope", "gte-rerank", "通义千问 GTE Rerank", "reranker",
     {"timeout": 5.0, "truncation": {"strategy": "tail", "max_doc_chars": 4000, "monitor_threshold": 0.1}},
     "cheap", True),
]


async def seed() -> None:
    """幂等初始化数据库中的供应商和模型数据。"""
    from config.settings import get_settings
    from forge.infrastructure.database import database as db_module
    from forge.infrastructure.database.orm.model_provider_orm import ProviderOrm
    from forge.infrastructure.database.orm.model_orm import ModelOrm
    from forge.infrastructure.database.repositories.model_repo import ModelRepository
    from forge.utils.id_generator import new_id

    settings = get_settings()
    db_module.init_engine()
    await db_module.ping()

    # 确保表已创建
    await db_module.bootstrap_schema()
    logger.info("数据库表已就绪 (幂等 create_all)")

    session_factory = db_module.get_session_factory()

    async with session_factory() as db:
        # ---- 1. 供应商 ----
        logger.info("写入供应商...")
        for pdata in _PROVIDERS:
            existing = await db.execute(
                __import__("sqlalchemy").select(ProviderOrm).where(ProviderOrm.name == pdata["name"])
            )
            existing = existing.scalar_one_or_none()
            if existing:
                logger.info("  跳过 (已存在): %s", pdata["name"])
                continue
            prov = ProviderOrm(
                provider_id=pdata["provider_id"],
                name=pdata["name"],
                impl=pdata.get("impl"),
                is_enabled=1,
                priority=pdata.get("priority", 0),
                routing_config=pdata.get("routing_config") or {},
            )
            db.add(prov)
            logger.info("  新增供应商: %s", pdata["name"])

        await db.flush()

        # 加载 provider id 映射
        res = await db.execute(
            __import__("sqlalchemy").select(ProviderOrm)
        )
        providers = {p.name: p for p in res.scalars().all()}

        # ---- 2. 文本模型 ----
        logger.info("写入文本模型...")
        model_repo = ModelRepository(db)
        for provider_name, model_name, display_name, cost_tier, extra in _TEXT_MODELS:
            prov = providers.get(provider_name)
            if not prov:
                logger.warning("  供应商不存在, 跳过: %s:%s", provider_name, model_name)
                continue
            cap = _CAPABILITIES.get(model_name, {})
            thinking_raw = cap.get("thinking")
            thinking_type = None
            thinking_options = None
            thinking_default = None
            if thinking_raw:
                thinking_type = thinking_raw["type"]
                thinking_options = thinking_raw.get("options")
                thinking_default = thinking_raw.get("default")

            model_data = {
                "name": model_name,
                "display_name": display_name,
                "model_type": "text",
                "context_window": cap.get("context_window", 128000),
                "max_output_tokens": (extra or {}).get("max_tokens", 4096) if extra else 4096,
                "supports_tools": cap.get("supports_tools", True),
                "supports_images": cap.get("supports_images", False),
                "supports_thinking": thinking_raw is not None,
                "thinking_type": thinking_type,
                "thinking_options": thinking_options,
                "thinking_default": thinking_default,
                "extra_params": extra,
                "cost_tier": cost_tier,
                "is_default": (model_name == "qwen-plus" or model_name == "gpt-4o"
                               or model_name == "deepseek-v4-pro" or model_name == "claude-sonnet-4-5"),
            }
            model, _ = await model_repo.sync_upsert(prov.id, model_data)
            model.cost_tier = model_data.get("cost_tier", model.cost_tier)
            if model_data.get("is_default"):
                model.is_default = True
        logger.info("  文本模型写入完成")

        # ---- 3. Embedding / Reranker 模型 ----
        logger.info("写入 Embedding / Reranker 模型...")
        for provider_name, model_name, display_name, model_type, extra, cost_tier, is_default in _OTHER_MODELS:
            prov = providers.get(provider_name)
            if not prov:
                logger.warning("  供应商不存在, 跳过: %s:%s", provider_name, model_name)
                continue
            model_data = {
                "name": model_name,
                "display_name": display_name,
                "model_type": model_type,
                "context_window": 0,
                "max_output_tokens": 0,
                "supports_tools": False,
                "supports_images": False,
                "supports_thinking": False,
                "extra_params": extra,
                "cost_tier": cost_tier,
                "is_default": is_default,
            }
            model, _ = await model_repo.sync_upsert(prov.id, model_data)
            model.cost_tier = model_data.get("cost_tier", model.cost_tier)
            if model_data.get("is_default"):
                model.is_default = True
        logger.info("  Embedding / Reranker 模型写入完成")

        await db.commit()

    logger.info("初始化完成！")
    logger.info("")
    logger.info("下一步：")
    logger.info("  1. 在 provider_keys 表中添加各供应商的 API Key（加密存储）")
    logger.info("  2. 启动服务，检查日志确认 ModelConfigCache 加载成功")
    logger.info("  3. 调用 API 测试模型可用性")
    await db_module.dispose_engine()


if __name__ == "__main__":
    asyncio.run(seed())

"""pytest 全局配置 — 必须在任何 settings/forge 模块 import 之前生效.

C1 修复 (fix-12): server/.env 中的开发机私有变量 (例如
``LLM_DEFAULT_MODEL=qwen3.6-plus``) 会被 ``load_env_files()`` 在
``APP_ENV=test`` 时覆盖 sys_config.test.yaml 的默认值，导致
``get_settings()`` 在 collection 阶段抛 ValidationError，整批测试
卡在 collection。

本文件做两件事：
- ``os.environ['APP_ENV'] = 'test'``：保证 settings 走 test 配置链
- 清理可能污染 test provider 的 LLM_* 变量，回退到 yaml 默认值
"""

from __future__ import annotations

import os

# 必须在任何 forge.* 模块被 import 之前执行（pytest 加载 conftest 早于测试模块）
os.environ.setdefault("APP_ENV", "test")

# 强制为测试场景指定与 sys_config.test.yaml 中 provider 配置一致的默认模型，
# 抢占 ``load_env_files()`` 对 .env 的 setdefault 路径。
# 不能用 pop —— load_env_files 之后还会从 .env 重新 setdefault 进来。
os.environ["LLM_DEFAULT_PROVIDER"] = "dashscope"
os.environ["LLM_DEFAULT_MODEL"] = "qwen-plus"
os.environ["LLM_FALLBACK_CHAIN"] = ""

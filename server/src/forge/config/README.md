# `forge.config` — 配置加载与域模型

把 YAML / 环境变量收敛成强类型的 `Settings`（pydantic），并以「配置即治理」的方式驱动 agent_profiles、模型档位、路径等。

## 设计理念

1. **强类型 + 启动校验**：配置全部映射成 pydantic 域模型（`domains/`），`extra="forbid"` 防止写错字段悄悄失效；`agent_profiles` 启动期 7 项强校验。
2. **明确的优先级**：`init_settings(path)` > `APP_CONFIG` > `APP_ENV` > 默认路径。
3. **单机优先**：默认 SQLite + 本地路径，零外部依赖即可跑通；MySQL/Redis 等为可选增强。
4. **路径集中**：所有运行态文件路径由 `paths.py` 统一推导（与 Claude Code 的路径编码兼容），不在业务里散落硬编码。

## 模块速览

```
config/
├── settings.py     ← Settings 根模型 + get_settings / init_settings / reset_settings
├── paths.py        ← 运行态路径推导 (ASSISTANT_HOME / data_dir / kb_db_path ...)
├── _env.py         ← .env 加载
└── domains/        ← 分域配置模型 (llm / db / context / memory / agent_profiles / ...)
```

## 如何使用

```python
from forge.config.settings import get_settings
settings = get_settings()          # 全局单例
settings.llm....                   # 分域访问
settings.agent_profiles.profiles["chat"].tools_allowed
```

测试中用 `init_settings(path)` 或 `reset_settings()` 控制配置。

## 如何扩展

- **加一个配置项**：在对应 `domains/<域>.py` 的 pydantic 模型加字段（带默认值，保证向后兼容），在 `sys_config.*.yaml` 配置。
- **调整 chat agent profile**：在 `sys_config.*.yaml` 的 `agent_profiles.profiles.chat` 修改 `tools_allowed` / `model_profile` / `max_steps`。
- **加一个新配置域**：在 `domains/` 建模型并挂到 `Settings` 根模型。

## 边界与注意

- `agent_profiles` 的 3 项校验：工具已注册 / 模板存在 / `model_profile` 已定义。任一不过拒绝启动。
- `db.py` 保留 MySQL 字段是为兼容旧部署；单机默认 SQLite。

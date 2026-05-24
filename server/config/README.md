# server/config 说明

本目录是 Forge 的**运行时可编辑配置资源目录**，不承载 Python 业务代码。

## 文件职责

- `sys_config.yaml.example`：配置注释真源（全字段说明）
- `sys_config.dev.yaml`：开发环境配置（只写环境差异）
- `sys_config.test.yaml`：测试环境配置（只写环境差异）

## 维护规则

1. 新增或重命名配置项时，先更新 `sys_config.yaml.example` 注释，再改代码。
2. `dev/test` 不重复全量注释，避免三份注释漂移。
3. 运行时优先级：`APP_CONFIG` > `APP_ENV` > 默认配置路径。
4. 敏感信息通过环境变量注入，不写入仓库明文。

# `forge.utils` — 通用工具

无业务语义的纯工具函数。和 `core` 的区别：`core` 是领域原语（消息/异常/响应），`utils` 是与领域无关的技术小工具。

## 设计理念

1. **零依赖、纯函数**：不依赖任何业务模块，任何地方都能安全 import。
2. **小而稳**：只放确实跨模块复用、且无歧义的工具，避免变成杂物抽屉。

## 模块速览

```
utils/
├── snowflake.py     ← new_snowflake_id: 分布式有序 ID
└── id_generator.py  ← 其他 ID 生成
```

## 如何使用

```python
from forge.utils.snowflake import new_snowflake_id
mid = new_snowflake_id()   # 有序、可排序的全局 ID (消息/run 等主键)
```

## 如何扩展

- 新增纯工具函数时先自问：它有业务语义吗？有 → 放对应业务模块或 `core`；没有且跨模块复用 → 才放这里。

## 边界与注意

- 不要让 `utils` 依赖业务模块，否则破坏「叶子依赖」定位。

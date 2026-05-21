"""ReAct 提示词指针.

实际模板内容在 <repo_root>/prompts/react/system.j2,
通过 forge.prompts.get_registry().render("react/system") 加载.

本文件保留, 后续若 ReAct 需要多个变体 / 辅助渲染函数 (如 tools 元数据格式化)
可以加在这里.
"""

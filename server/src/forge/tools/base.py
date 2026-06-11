"""Tool 抽象基类.

一个 Tool 必须提供:
    - name:        唯一标识 (与 OpenAI function calling name 对齐)
    - description: 描述, LLM 用它判断何时调用
    - parameters:  JSON Schema dict, 描述参数结构
    - run / arun:  执行函数, 二选一; 接收 dict, 返回任何可 JSON 序列化的结果

元数据扩展 (类属性, 子类按需覆盖):
    - parallelism_safe:   同 step 内能否并行
    - dangerous:          危险工具 → 走 audit log
    - audit_payload_fields: audit 写入时保留哪些参数字段 (其余 redact)
    - timeout_sec:        软上限 (具体语义由工具自己解释)

设计原则:
    - 同步工具实现 run (CPU bound / 纯计算); 异步工具实现 arun (IO bound,
      尤其是要复用全局连接池如 DB / HTTP client 的). 两者只需选其一.
    - ToolExecutor.aexecute 会优先调用子类覆写的 arun, 否则把 run 扔到
      asyncio.to_thread 跑, 不阻塞 event loop.
    - 异常向上传播, 由 ToolExecutor 捕获并包装成 ToolError.
    - 工具结果序列化由调用方负责; Tool 自己不 json.dumps.
"""

from __future__ import annotations

from abc import ABC
from typing import Any


class Tool(ABC):
    """工具基类."""

    name: str
    description: str
    parameters: dict[str, Any]

    # ──────── 执行特性 ────────

    # 是否允许在同一 LLM response 内的多个 tool_calls 中并行执行.
    # 默认 True: 纯查询类工具 (read_file / calculator / current_time / 检索类) 都是.
    # 设为 False 的情况:
    #     - 有副作用且互相影响 (write_file / edit_file / shell / git 写)
    #     - 依赖外部互斥资源 (eg 抢锁 / 限流敏感)
    # ReActAgent 在一个 step 内会把连续的 safe 工具组并行 (asyncio.gather),
    # unsafe 工具严格串行, 整体保留 LLM 给的顺序.
    parallelism_safe: bool = True

    # ──────── 安全 / 审计 ────────

    # 是否记入审计日志 (audit.jsonl). 一般有副作用 / 外部可达的工具置 True.
    dangerous: bool = False

    # audit 写入时保留哪些参数字段 (其余字段会被 ToolExecutor 截断 / redact).
    # 留空表示"工具自行裁剪", ToolExecutor 走 _summarize_args 内置规则.
    audit_payload_fields: tuple[str, ...] = ()

    # 软超时 (秒). None = 工具自己决定; 工具可在 run/arun 内读取并应用.
    timeout_sec: float | None = None

    # ──────── 入口 ────────

    def run(self, args: dict[str, Any]) -> Any:
        """同步执行. 子类未覆写时抛错; 子类必须实现 run 或 arun 之一."""
        raise NotImplementedError(
            f"Tool {self.name!r} 未实现 run(); 若是 IO bound 工具请实现 arun()"
        )

    async def arun(self, args: dict[str, Any]) -> Any:
        """异步执行. 子类未覆写时抛错; 子类必须实现 run 或 arun 之一.

        IO bound 工具 (DB / HTTP / 外部服务) 应实现 arun, 这样能直接复用
        调用方所在 event loop 的连接池, 避免 asyncio.run() 起临时 loop
        造成跨 loop Future 错误.
        """
        raise NotImplementedError(
            f"Tool {self.name!r} 未实现 arun(); 若是 CPU bound 工具请实现 run()"
        )

    def openai_schema(self) -> dict:
        """转成 OpenAI function calling 接受的 schema dict."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

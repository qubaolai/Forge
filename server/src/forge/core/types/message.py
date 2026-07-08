"""对话消息数据结构.

约定与 OpenAI Chat Completions 兼容:
    role: system | user | assistant | tool
    content: str (tool 消息时含 tool 结果)
    tool_calls: 模型请求调用的工具列表 (assistant 才有)
    tool_call_id: tool 响应消息关联的 call id (tool 才有)

与 llm.providers.base.ChatMessage 的关系:
    底层 ChatMessage 是最小集 (role + content), 供单纯 chat 使用.
    Message (本文件) 是 agent 层增强版, 支持 tool calling.
    转换通过 to_chat_message() / from_chat_message() 完成.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCall:
    """模型请求的一次工具调用."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None  # tool 消息时 = tool name (调试用)
    # provider 特有字段, 中性数据载体. 是否塞到请求 payload 由具体 provider 决定
    # (例: DeepSeek thinking 模式要求 assistant 消息回灌 reasoning_content).
    extra_content: str | None = None
    # 服务端内部元数据, 不进入 provider payload。用于记录工具耗时、截断等观测信息。
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_openai_dict(self) -> dict:
        """转成 OpenAI chat.completions 接收的 dict 格式.

        注意: 不包含 reasoning_content 等 provider 特有字段, 由
        provider 子类在 _postprocess_message_dict 钩子里追加, 避免污染
        通用兼容模型 (openai/qwen 等).
        """
        d: dict = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": __import__("json").dumps(
                            tc.arguments,
                            ensure_ascii=False,
                        ),
                    },
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d

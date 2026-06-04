"""OllamaLLM 注册与构造测试。"""

from __future__ import annotations

from forge.llm.providers.ollama import OllamaLLM
from forge.llm.registry import build_llm_client, list_providers


def test_ollama_registered() -> None:
    assert "ollama" in list_providers()


def test_build_ollama_client_with_base_url() -> None:
    client = build_llm_client(
        "ollama", "ollama", {"base_url": "http://localhost:11434/v1"}
    )
    assert isinstance(client, OllamaLLM)
    assert client.provider_name == "ollama"


def test_default_base_url() -> None:
    """provider 行未填 base_url 时落到本地默认值。"""
    assert OllamaLLM.DEFAULT_BASE_URL == "http://localhost:11434/v1"

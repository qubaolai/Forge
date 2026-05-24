from __future__ import annotations

from forge.llm.client_pool import LLMClientPool


class _FakeClient:
    def __init__(self, impl: str, api_key: str, options: dict) -> None:
        self.impl = impl
        self.api_key = api_key
        self.options = options


def _factory(impl: str, api_key: str, options: dict):
    return _FakeClient(impl, api_key, options)


def test_smooth_weighted_round_robin_sequence() -> None:
    pool = LLMClientPool(_factory)
    pool.register_key("openai", "sk-a", weight=3)
    pool.register_key("openai", "sk-b", weight=1)

    selected = [
        pool.get_by_impl_with_key("openai")[1]  # type: ignore[index]
        for _ in range(4)
    ]

    assert selected == ["sk-a", "sk-a", "sk-b", "sk-a"]


def test_cooldown_key_is_not_selected() -> None:
    pool = LLMClientPool(_factory)
    pool.register_key("openai", "sk-a", weight=10)
    pool.register_key("openai", "sk-b", weight=1)

    pool.mark_cooldown("openai", "sk-a", seconds=60)
    selected = pool.get_by_impl_with_key("openai")

    assert selected is not None
    assert selected[1] == "sk-b"


def test_invalid_weight_is_normalized_to_one() -> None:
    pool = LLMClientPool(_factory)
    pool.register_key("openai", "sk-a", weight=0)

    stats = pool.stats()

    assert stats[0]["weight"] == 1

"""context_mgmt.memory_factory.get_memory_store 单测.

覆盖三条分支:
    1. memory.enabled=False -> NullMemoryStore
    2. memory.enabled=True 但 DB 未初始化 -> NullMemoryStore (软降级)
    3. memory.enabled=True 且 DB 就绪 -> CompositeMemoryStore
    4. 缓存: 二次调用拿同一实例
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from forge.context_mgmt.memory_factory import get_memory_store, reset_memory_store
from forge.memory.composite import CompositeMemoryStore
from forge.memory.null import NullMemoryStore


def _settings(enabled: bool) -> MagicMock:
    s = MagicMock()
    s.memory.enabled = enabled
    return s


def setup_function(_):
    reset_memory_store()


def teardown_function(_):
    reset_memory_store()


def test_disabled_returns_null() -> None:
    with patch("forge.context_mgmt.memory_factory.get_settings", return_value=_settings(False)):
        store = get_memory_store()
    assert isinstance(store, NullMemoryStore)


def test_db_not_initialized_returns_null() -> None:
    with (
        patch("forge.context_mgmt.memory_factory.get_settings", return_value=_settings(True)),
        patch(
            "forge.infrastructure.database.database.get_session_factory",
            side_effect=RuntimeError("Engine 尚未初始化"),
        ),
    ):
        store = get_memory_store()
    assert isinstance(store, NullMemoryStore)


def test_db_ready_returns_composite() -> None:
    fake_factory = MagicMock()
    with (
        patch("forge.context_mgmt.memory_factory.get_settings", return_value=_settings(True)),
        patch(
            "forge.infrastructure.database.database.get_session_factory",
            return_value=fake_factory,
        ),
    ):
        store = get_memory_store()
    assert isinstance(store, CompositeMemoryStore)


def test_singleton_cached() -> None:
    with patch("forge.context_mgmt.memory_factory.get_settings", return_value=_settings(False)):
        a = get_memory_store()
        b = get_memory_store()
    assert a is b

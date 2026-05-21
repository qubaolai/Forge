"""Storage 装配工厂单测 (S6.5 M3).

覆盖:
1. 7 个 Protocol 的 isinstance check (local 模式装配后结构化匹配).
2. deployment_mode=local 时 make_* 工厂正常返回.
3. deployment_mode=sandbox/cloud 时抛 NotImplementedError + 错误信息含 "S6.5".
4. get_deployment_mode 默认 local + env 覆盖.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from forge.infrastructure.storage import (
    AuditStore,
    CostStore,
    KbDocumentStore,
    KnowledgeBaseStore,
    MessageStore,
    SessionStore,
    SummaryStore,
    get_deployment_mode,
    make_audit_store,
    make_cost_store,
    make_kb_document_store,
    make_knowledge_base_store,
    make_message_store,
    make_session_store,
    make_summary_store,
)
from forge.infrastructure.storage.data_protocols import ALL_PROTOCOLS


def test_deployment_mode_defaults_to_local(monkeypatch) -> None:
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    assert get_deployment_mode() == "local"


def test_deployment_mode_env_override(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_MODE", "sandbox")
    assert get_deployment_mode() == "sandbox"


def test_deployment_mode_normalizes_case_and_spaces(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_MODE", "  LOCAL  ")
    assert get_deployment_mode() == "local"


def test_all_protocols_enumerated() -> None:
    """ALL_PROTOCOLS 应含 S6.5 计划的 7 个 Protocol."""
    expected = {
        SessionStore,
        MessageStore,
        CostStore,
        AuditStore,
        SummaryStore,
        KnowledgeBaseStore,
        KbDocumentStore,
    }
    assert set(ALL_PROTOCOLS) == expected


@pytest.mark.parametrize(
    "store_name,make_fn,protocol",
    [
        ("SessionStore", lambda: make_session_store(MagicMock()), SessionStore),
        ("MessageStore", lambda: make_message_store(MagicMock()), MessageStore),
        (
            "KnowledgeBaseStore",
            lambda: make_knowledge_base_store(MagicMock()),
            KnowledgeBaseStore,
        ),
        (
            "KbDocumentStore",
            lambda: make_kb_document_store(MagicMock()),
            KbDocumentStore,
        ),
    ],
)
def test_local_mode_db_factory_returns_protocol_compliant(
    store_name: str,
    make_fn,
    protocol: type,
    monkeypatch,
) -> None:
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    impl = make_fn()
    assert isinstance(impl, protocol), f"{store_name} 装配出的实例不满足 Protocol"


def test_local_mode_cost_audit_factories_return_protocol_compliant(monkeypatch) -> None:
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    assert isinstance(make_cost_store(), CostStore)
    assert isinstance(make_audit_store(), AuditStore)


def test_local_mode_summary_store_factory_returns_protocol_compliant(monkeypatch) -> None:
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    factory_mock = MagicMock()
    assert isinstance(make_summary_store(factory_mock), SummaryStore)


@pytest.mark.parametrize("mode", ["sandbox", "cloud", "k8s", "anything"])
def test_non_local_mode_make_factory_raises(monkeypatch, mode: str) -> None:
    """非 local 模式下任何 make_* 都应 raise NotImplementedError 且提示 S6.5."""
    monkeypatch.setenv("DEPLOYMENT_MODE", mode)

    with pytest.raises(NotImplementedError) as excinfo:
        make_message_store(MagicMock())
    msg = str(excinfo.value)
    assert "S6.5" in msg
    assert mode in msg

    with pytest.raises(NotImplementedError):
        make_session_store(MagicMock())
    with pytest.raises(NotImplementedError):
        make_cost_store()
    with pytest.raises(NotImplementedError):
        make_audit_store()
    with pytest.raises(NotImplementedError):
        make_summary_store(MagicMock())
    with pytest.raises(NotImplementedError):
        make_knowledge_base_store(MagicMock())
    with pytest.raises(NotImplementedError):
        make_kb_document_store(MagicMock())

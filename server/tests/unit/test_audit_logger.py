"""AuditLogger 单元测试.

覆盖:
    - 成功调用记 INFO 级日志, 含 provider/model/tokens/cost/latency
    - 失败调用记 WARNING 级日志, 含 error
    - trace_id / user_id 从 ContextVar 自动取
    - 显式参数覆盖 ContextVar
    - extra 字段透传 (JSONFormatter 能展开)
"""

from __future__ import annotations

import logging

from forge.api.middleware.tracing import user_id_scope
from forge.guardrails.compliance.audit_logger import (
    AuditLogger,
    LLMCallAuditRecord,
)


def _logger_caplog(caplog):
    caplog.set_level(logging.DEBUG, logger="llm.audit")
    return caplog


def test_audit_success_emits_info(caplog):
    _logger_caplog(caplog)
    AuditLogger().log(
        LLMCallAuditRecord(
            provider="openai",
            model="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            estimated_cost_usd=0.001,
            latency_ms=120.5,
            api_key_fingerprint="sk-abc***",
        )
    )
    records = [r for r in caplog.records if r.name == "llm.audit"]
    assert len(records) == 1
    r = records[0]
    assert r.levelno == logging.INFO
    assert "openai" in r.getMessage()
    assert "gpt-4o" in r.getMessage()
    # extra 字段透传
    assert getattr(r, "audit_provider", None) == "openai"
    assert getattr(r, "audit_prompt_tokens", None) == 100
    assert getattr(r, "audit_event", None) == "llm.call"


def test_audit_failure_emits_warning(caplog):
    _logger_caplog(caplog)
    AuditLogger().log(
        LLMCallAuditRecord(
            provider="openai",
            model="gpt-4o",
            error="ConnectionError",
        )
    )
    records = [r for r in caplog.records if r.name == "llm.audit"]
    assert records[-1].levelno == logging.WARNING
    assert "ConnectionError" in getattr(records[-1], "audit_error", "")


def test_audit_picks_user_id_from_context(caplog):
    _logger_caplog(caplog)
    with user_id_scope("u-from-ctx"):
        AuditLogger().log(LLMCallAuditRecord(provider="openai", model="gpt-4o"))
    records = [r for r in caplog.records if r.name == "llm.audit"]
    assert records[-1].user_id == "u-from-ctx"


def test_audit_explicit_user_overrides_context(caplog):
    _logger_caplog(caplog)
    with user_id_scope("ctx-user"):
        AuditLogger().log(LLMCallAuditRecord(provider="openai", model="gpt-4o", user_id="explicit"))
    records = [r for r in caplog.records if r.name == "llm.audit"]
    assert records[-1].user_id == "explicit"


def test_audit_circuit_breaker_skipped_marker(caplog):
    _logger_caplog(caplog)
    AuditLogger().log(
        LLMCallAuditRecord(
            provider="openai",
            model="gpt-4o",
            circuit_breaker_skipped=True,
            error="circuit_breaker_open",
        )
    )
    records = [r for r in caplog.records if r.name == "llm.audit"]
    r = records[-1]
    assert getattr(r, "audit_circuit_breaker_skipped", None) is True

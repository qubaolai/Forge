"""抽象契约必须通过 ABC 强制实现."""

from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path

import pytest

from forge.agents.lifecycle import AgentLifecycle, NoopLifecycle, StepContext, StepDecision
from forge.chat.guards.base import LoopGuard
from forge.chat.runner import AgentRunner
from forge.context_mgmt.protocols import (
    BudgetPolicy,
    CompactionStrategy,
    CompactionTrigger,
    ContentProvider,
    HistoryFilter,
    TokenMeter,
    ToolResultPolicy,
)
from forge.infrastructure.event_bus.base import EventBus
from forge.infrastructure.queue.base import TaskQueue
from forge.infrastructure.storage.data_protocols import (
    AuditStore,
    CostStore,
    KbDocumentStore,
    KnowledgeBaseStore,
    MessageStore,
    SessionStore,
    SummaryStore,
)
from forge.llm.caching.exact_cache import ExactCacheBackend
from forge.llm.client_pool import KeySelectionStrategy
from forge.llm.contracts import ToolCallingLLM
from forge.llm.inbound_rate_limiter import InboundRateLimiter
from forge.llm.pipeline.base import PostMiddleware, PreMiddleware
from forge.llm.pipeline.dedup import IdempotencyStore
from forge.llm.resilience.circuit_breaker import CircuitBreakerStrategy
from forge.llm.resilience.retry import RetryPolicy
from forge.llm.token_counter import TokenCounter
from forge.memory.base import MemoryStore
from forge.memory.policies.conflict import ConflictResolver
from forge.memory.policies.forgetting import ForgettingPolicy
from forge.observability.tracing.tracer import Span
from forge.retrieval.parsers.dispatcher import ParserDispatcher
from forge.retrieval.parsers.parser_base import BaseParser
from forge.retrieval.parsers.text.md_parser import MdParser
from forge.retrieval.parsers.text.txt_parser import TxtParser
from forge.retrieval.parsers.word.word_parser import WordParser
from forge.retrieval.recall.mock_extended import QueryRewriter

ABSTRACT_BASES = (
    AgentLifecycle,
    AgentRunner,
    AuditStore,
    BudgetPolicy,
    CircuitBreakerStrategy,
    CompactionStrategy,
    CompactionTrigger,
    ConflictResolver,
    ContentProvider,
    CostStore,
    EventBus,
    ExactCacheBackend,
    ForgettingPolicy,
    HistoryFilter,
    IdempotencyStore,
    InboundRateLimiter,
    KbDocumentStore,
    KeySelectionStrategy,
    KnowledgeBaseStore,
    LoopGuard,
    MemoryStore,
    MessageStore,
    PostMiddleware,
    PreMiddleware,
    QueryRewriter,
    RetryPolicy,
    SessionStore,
    Span,
    SummaryStore,
    TaskQueue,
    TokenCounter,
    TokenMeter,
    ToolCallingLLM,
    ToolResultPolicy,
)


@pytest.mark.parametrize("base", ABSTRACT_BASES)
def test_contract_base_is_abstract(base: type) -> None:
    assert inspect.isabstract(base), base.__name__


def test_incomplete_lifecycle_cannot_be_instantiated() -> None:
    class IncompleteLifecycle(AgentLifecycle):
        pass

    with pytest.raises(TypeError):
        IncompleteLifecycle()


def test_selective_lifecycle_can_extend_noop() -> None:
    class SelectiveLifecycle(NoopLifecycle):
        async def before_step(self, step: StepContext) -> StepDecision | None:
            return StepDecision(force_text_only=True)

    assert not inspect.isabstract(SelectiveLifecycle)


def test_builtin_parsers_share_base_class() -> None:
    assert issubclass(WordParser, BaseParser)
    assert issubclass(TxtParser, BaseParser)
    assert issubclass(MdParser, BaseParser)
    if importlib.util.find_spec("pypdf") is not None:
        from forge.retrieval.parsers.pdf.pdf_parser import PdfParser

        assert issubclass(PdfParser, BaseParser)

    parser = TxtParser()
    dispatcher = ParserDispatcher()
    dispatcher.register(parser, [".txt"])

    assert dispatcher.get(Path("sample.txt")) is parser

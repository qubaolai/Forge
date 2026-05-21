"""tracer span 携带 workflow_id / workspace_id 标签 (S6 P3 M4.3).

跑一遍 ContextBuilder.build, 验证 span "context.build" 的属性里出现这两个 ID.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

import pytest

from forge.context.base import AgentContextConfig, BuildRequest
from forge.context.builder import CompositeContextBuilder
from forge.llm.token_counter import HeuristicCounter
from forge.memory.null import NullMemoryStore
from forge.observability.tracing import tracer as tracer_mod


@dataclass
class _Span:
    attrs: dict
    error: BaseException | None = None

    def set(self, key: str, value) -> None:  # noqa: ANN001
        self.attrs[key] = value

    def set_error(self, exc: BaseException) -> None:
        self.error = exc


class _RecordingTracer:
    def __init__(self) -> None:
        self.spans: list[tuple[str, _Span]] = []

    @contextmanager
    def span(self, name: str, **attrs):  # noqa: ANN001, ANN201
        s = _Span(attrs=dict(attrs))
        try:
            yield s
        except Exception as exc:  # noqa: BLE001
            s.set_error(exc)
            raise
        finally:
            self.spans.append((name, s))


class _EmptyRepo:
    async def load_recent(self, session_id: str, limit: int):  # noqa: ANN001
        return []


@pytest.fixture
def recording_tracer(monkeypatch):
    rec = _RecordingTracer()
    monkeypatch.setattr(tracer_mod, "_tracer", rec)
    return rec


@pytest.mark.asyncio
async def test_tracer_spans_carry_workflow_and_workspace_id(recording_tracer):
    builder = CompositeContextBuilder(_EmptyRepo(), NullMemoryStore(), HeuristicCounter())

    await builder.build(
        BuildRequest(
            user_id="u1",
            session_id="s1",
            current_user_message="当前问题",
            agent=AgentContextConfig(
                system_prompt="你是助手",
                context_window=4096,
                history_limit=30,
            ),
            workspace_id="ws_trace",
            workflow_id="wf_trace",
        )
    )

    assert recording_tracer.spans, "应至少抛出一个 span"
    name, s = recording_tracer.spans[0]
    assert name == "context.build"
    assert s.attrs.get("workspace_id") == "ws_trace"
    assert s.attrs.get("workflow_id") == "wf_trace"

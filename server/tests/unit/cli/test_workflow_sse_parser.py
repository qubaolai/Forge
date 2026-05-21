from __future__ import annotations

from forge.cli.workflow import (
    _iter_sse_events,
    attach_workflow_events,
    start_workflow_once,
)


def test_sse_parser_handles_multiple_frames_in_one_chunk():
    chunk = (
        b'data: {"id":"evt_1","type":"workflow.started"}\n\n'
        b'data: {"id":"evt_2","type":"phase.started"}\n\n'
    )
    events = list(_iter_sse_events([chunk]))
    assert [e["id"] for e in events] == ["evt_1", "evt_2"]


def test_sse_parser_streams_frame_by_frame():
    """生成器边解析边 yield, 而不是攒完整 list."""
    chunks = [
        b'data: {"id":"evt_1","type":"workflow.started"}\n\n',
        b'data: {"id":"evt_2","type":"phase.started"}\n\n',
        b'data: {"id":"evt_3","type":"workflow.completed"}\n\n',
    ]
    iterator = _iter_sse_events(iter(chunks))
    first = next(iterator)
    assert first["id"] == "evt_1"
    second = next(iterator)
    assert second["id"] == "evt_2"
    third = next(iterator)
    assert third["id"] == "evt_3"


def test_sse_parser_handles_split_across_chunks():
    """frame 跨 chunk 边界仍能正确组装."""
    chunks = [
        b'data: {"id":"evt_',
        b'1","type":"workflow.started"}',
        b'\n\ndata: {"id":"evt_2","type":"phase.started"}\n\n',
    ]
    events = list(_iter_sse_events(chunks))
    assert [e["id"] for e in events] == ["evt_1", "evt_2"]


def test_workflow_start_sends_client_type_header(monkeypatch):
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"code":0,"data":{"workflow_id":"wf_1"}}'

    def _fake_urlopen(req, timeout):  # noqa: ARG001
        captured["client_type"] = req.get_header("X-client-type")
        return _Resp()

    monkeypatch.setattr("forge.cli.workflow.urllib_request.urlopen", _fake_urlopen)
    state = start_workflow_once(
        base_url="http://127.0.0.1:8553",
        token=None,
        message="x",
    )
    assert state["workflow_id"] == "wf_1"
    assert captured["client_type"] == "cli"


def test_workflow_attach_sends_client_type_header(monkeypatch):
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def __iter__(self):
            return iter([b'data: {"id":"evt_1","type":"workflow.started"}\n\n'])

    def _fake_urlopen(req, timeout):  # noqa: ARG001
        captured["client_type"] = req.get_header("X-client-type")
        return _Resp()

    monkeypatch.setattr("forge.cli.workflow.urllib_request.urlopen", _fake_urlopen)
    events = attach_workflow_events(
        base_url="http://127.0.0.1:8553",
        token=None,
        workflow_id="wf_1",
    )
    assert events[0]["id"] == "evt_1"
    assert captured["client_type"] == "cli"

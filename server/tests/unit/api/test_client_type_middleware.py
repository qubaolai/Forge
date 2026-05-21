"""client_type 中间件与 SSE 透传测试 (S6.5 M4)."""

from __future__ import annotations

import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from forge.api.middleware.client_type import (
    _CLIENT_TYPE,
    CLIENT_TYPE_HEADER,
    ClientTypeMiddleware,
    current_client_type,
    set_client_type,
)


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(ClientTypeMiddleware)

    @app.get("/echo")
    async def echo(request: Request):
        return {
            "state": request.state.client_type,
            "context": current_client_type(),
        }

    @app.get("/stream")
    async def stream(request: Request):
        client_type = request.state.client_type

        async def events():
            # 与 chat/workflow SSE 生成器一致: BaseHTTPMiddleware 边界后显式重设.
            set_client_type(client_type)
            yield (
                "data: "
                + json.dumps({"client_type": _CLIENT_TYPE.get()}, ensure_ascii=False)
                + "\n\n"
            )

        return StreamingResponse(events(), media_type="text/event-stream")

    return app


def test_client_type_header_recognized() -> None:
    client = TestClient(_app())
    resp = client.get("/echo", headers={CLIENT_TYPE_HEADER: "web"})
    assert resp.status_code == 200
    assert resp.json() == {"state": "web", "context": "web"}
    assert resp.headers[CLIENT_TYPE_HEADER] == "web"


def test_client_type_defaults_to_cli() -> None:
    client = TestClient(_app())
    resp = client.get("/echo", headers={"user-agent": "assistant-cli-test"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "cli"


def test_client_type_contextvar_survives_sse_stream() -> None:
    client = TestClient(_app())
    with client.stream("GET", "/stream", headers={CLIENT_TYPE_HEADER: "web"}) as resp:
        payload = "".join(resp.iter_text())
    assert resp.status_code == 200
    assert 'data: {"client_type": "web"}' in payload

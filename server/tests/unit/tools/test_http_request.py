"""http_request 工具单测."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from forge.tools.builtin.http.http_request import HttpRequest, _check_internal


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "::1",
        "fe80::1",
    ],
)
def test_ssrf_blocks_internal(host: str) -> None:
    assert _check_internal(host) is not None


@pytest.mark.parametrize("host", ["8.8.8.8", "1.1.1.1"])
def test_ssrf_allows_public_ip(host: str) -> None:
    assert _check_internal(host) is None


def test_http_request_blocks_localhost_by_default() -> None:
    out = HttpRequest().run({"url": "http://localhost:1/x"})
    assert out["ok"] is False
    assert out["blocked"] is True


def test_http_request_invalid_scheme() -> None:
    out = HttpRequest().run({"url": "ftp://example.com/x"})
    assert out["ok"] is False
    assert "scheme" in out["error"]


class _PingHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok": true, "echo": "hi"}')

    def log_message(self, *_a, **_kw) -> None:
        pass


def test_http_request_against_local_server_with_allow_internal() -> None:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _PingHandler)
    except PermissionError:
        pytest.skip("当前运行环境不允许绑定本地端口")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        out = HttpRequest().run(
            {
                "url": f"http://127.0.0.1:{port}/test",
                "allow_internal": True,
                "timeout_seconds": 3,
            }
        )
        assert out["ok"] is True
        assert out["status_code"] == 200
        assert out["json"] == {"ok": True, "echo": "hi"}
    finally:
        server.shutdown()
        server.server_close()

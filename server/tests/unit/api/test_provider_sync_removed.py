"""验证 Provider 动态同步能力已下线。"""

from __future__ import annotations

import inspect

from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge.api.routes.v1 import providers


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(providers.router)
    return app


def test_provider_sync_route_removed_returns_404() -> None:
    """`POST /providers/{name}/sync` 已删除，应返回 404。"""
    client = TestClient(_app())
    resp = client.post("/providers/openai/sync")
    assert resp.status_code == 404


def test_lifespan_has_no_dynamic_model_sync_symbols() -> None:
    """启动流程不再包含动态模型同步符号。"""
    from forge.api import lifespan as lifespan_module

    source = inspect.getsource(lifespan_module.lifespan)
    assert "model_sync_service" not in source
    assert "MODEL_SYNC_INTERVAL_MIN" not in source
    assert "_sync_task" not in source

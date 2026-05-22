"""M3: runs / artifacts 路由测试。"""

from __future__ import annotations

from config.settings import reset_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

from forge.adaptive.models import Artifact, ArtifactKind
from forge.adaptive.store import AdaptiveRunStore
from forge.api.routes.v1 import artifacts, runs


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(runs.router)
    app.include_router(artifacts.router)
    return app


def test_runs_create_list_get_abort_and_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "assistant_home"))
    reset_settings()

    client = TestClient(_app())
    workspace = str(tmp_path / "workspace")

    created = client.post(
        "/runs",
        json={
            "goal": "修复支付回调重复入库问题",
            "task_options": {"workspace_path": workspace},
        },
    )
    assert created.status_code == 200
    run = created.json()["data"]
    run_id = run["run_id"]
    assert run["status"] == "created"

    listed = client.get("/runs", params={"workspace_path": workspace})
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] >= 1

    fetched = client.get(f"/runs/{run_id}", params={"workspace_path": workspace})
    assert fetched.status_code == 200
    assert fetched.json()["data"]["run_id"] == run_id

    with client.stream(
        "GET",
        f"/runs/{run_id}/events",
        params={"workspace_path": workspace},
    ) as resp:
        payload = "".join(resp.iter_text())
    assert resp.status_code == 200
    assert "run.created" in payload

    aborted = client.post(f"/runs/{run_id}/abort", params={"workspace_path": workspace})
    assert aborted.status_code == 200
    assert aborted.json()["data"]["status"] == "aborted"


def test_artifacts_get_and_list(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "assistant_home"))
    reset_settings()

    workspace = str(tmp_path / "workspace")
    store = AdaptiveRunStore(workspace_path=workspace)
    artifact = Artifact(
        artifact_id="art_demo",
        run_id="run_demo",
        task_id="t1",
        kind=ArtifactKind.PATCH_SET,
        payload={"changed_files": ["server/src/a.py"]},
    )
    # 先建 run 目录，避免孤儿 artifact 目录。
    import asyncio

    from forge.adaptive.models import AdaptiveRun

    asyncio.run(store.save_run(AdaptiveRun(run_id="run_demo", workspace_path=workspace, goal="g")))
    asyncio.run(store.save_artifact(artifact))

    client = TestClient(_app())
    fetched = client.get("/artifacts/art_demo", params={"workspace_path": workspace})
    assert fetched.status_code == 200
    assert fetched.json()["data"]["artifact_id"] == "art_demo"

    listed = client.get(
        "/artifacts",
        params={"workspace_path": workspace, "run_id": "run_demo", "kind": "patch_set"},
    )
    assert listed.status_code == 200
    assert listed.json()["data"]["total"] == 1
    assert listed.json()["data"]["items"][0]["artifact_id"] == "art_demo"

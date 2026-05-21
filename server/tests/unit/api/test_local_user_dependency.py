from __future__ import annotations

import pytest

from forge.api.dependencies import _get_local_user
from forge.core.exceptions import Unauthorized


@pytest.mark.asyncio
async def test_get_local_user_without_local_token_file(monkeypatch, tmp_path):
    monkeypatch.setenv("ASSISTANT_HOME", str(tmp_path / "home"))

    user = await _get_local_user(None)
    assert user.id == "local"
    assert user.role == "owner"


@pytest.mark.asyncio
async def test_get_local_user_with_local_token_file(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("ASSISTANT_HOME", str(home))
    home.mkdir(parents=True, exist_ok=True)
    (home / "local_token").write_text("abc123", encoding="utf-8")

    user = await _get_local_user("abc123")
    assert user.id == "local"

    with pytest.raises(Unauthorized):
        await _get_local_user("bad")

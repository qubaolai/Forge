"""deployment_mode 配置校验 (S6.5 M4)."""

from __future__ import annotations

import pytest
from config.settings import get_settings, reset_settings


@pytest.fixture(autouse=True)
def _settings_isolation(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.delenv("APP_CONFIG", raising=False)
    monkeypatch.delenv("DEPLOYMENT_MODE", raising=False)
    reset_settings()
    yield
    reset_settings()


def test_deployment_mode_defaults_to_local() -> None:
    assert get_settings().deployment_mode == "local"


def test_deployment_mode_env_override(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_MODE", "sandbox")
    reset_settings()
    assert get_settings().deployment_mode == "sandbox"


def test_deployment_mode_rejects_invalid_value(monkeypatch) -> None:
    monkeypatch.setenv("DEPLOYMENT_MODE", "k8s")
    reset_settings()
    with pytest.raises(ValueError, match="deployment_mode"):
        get_settings()

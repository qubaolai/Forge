from __future__ import annotations

import pytest
from pydantic import ValidationError

from forge.api.schemas.admin import validate_model_config
from forge.retrieval.bound_model_resolver import BoundModelResolver


def test_embedding_model_config_requires_explicit_capabilities() -> None:
    with pytest.raises(ValidationError):
        validate_model_config("embedding", {})


def test_embedding_model_config_accepts_values_within_capabilities() -> None:
    config = validate_model_config("embedding", {
        "dimension": 1024,
        "batch_size": 10,
        "supported_dimensions": [2048, 1024, 768],
        "max_batch_size": 10,
    })

    assert config["dimension"] == 1024
    assert config["batch_size"] == 10
    assert config["supported_dimensions"] == [2048, 1024, 768]
    assert config["max_batch_size"] == 10


def test_embedding_model_capabilities_reach_runtime_config() -> None:
    config = validate_model_config("embedding", {
        "dimension": 1024,
        "batch_size": 10,
        "supported_dimensions": [2048, 1024, 768],
        "max_batch_size": 10,
    })

    runtime = BoundModelResolver._runtime_config(
        "text-embedding-model",
        "embedding",
        config,
        "sk-test",
    )

    assert runtime["model"] == "text-embedding-model"
    assert runtime["supported_dimensions"] == [2048, 1024, 768]
    assert runtime["max_batch_size"] == 10


@pytest.mark.parametrize(
    "config",
    [
        {
            "dimension": 512,
            "batch_size": 10,
            "supported_dimensions": [1024],
            "max_batch_size": 10,
        },
        {
            "dimension": 1024,
            "batch_size": 11,
            "supported_dimensions": [1024],
            "max_batch_size": 10,
        },
    ],
)
def test_embedding_model_config_rejects_values_outside_capabilities(config: dict) -> None:
    with pytest.raises(ValidationError):
        validate_model_config("embedding", config)

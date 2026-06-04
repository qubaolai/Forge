"""DashScopeEmbedder 配置来源与错误可观测性测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from forge.retrieval.embedders.dashscope_embedder import DashScopeEmbedder


def _config(**overrides) -> dict:
    return {
        "api_key": "sk-test",
        "model": "text-embedding-v3",
        "dimension": 1024,
        "batch_size": 10,
        "supported_dimensions": [1024],
        "max_batch_size": 10,
        "max_retries": 0,
        "retry_backoff": 0,
        **overrides,
    }


@pytest.mark.parametrize(
    "model",
    ["tongyi-embedding-vision-plus-2026-03-06", "multimodal-embedding-v1", "Foo-Vision-Bar"],
)
def test_rejects_multimodal_vision_models(model: str) -> None:
    """多模态/视觉向量模型走文本 TextEmbedding 接口必失败, 应在构造期 fail-fast。"""
    with pytest.raises(ValueError, match=r"不支持多模态 / 视觉向量模型"):
        DashScopeEmbedder(_config(model=model))


@pytest.mark.parametrize(
    "field",
    ["model", "dimension", "batch_size", "supported_dimensions", "max_batch_size"],
)
def test_requires_model_config_fields(field: str) -> None:
    config = _config()
    config.pop(field)

    with pytest.raises(ValueError, match=rf"config\['{field}'\]"):
        DashScopeEmbedder(config)


def test_uses_model_config_without_provider_side_capability_table() -> None:
    embedder = DashScopeEmbedder(
        _config(
            model="custom-text-embedding",
            dimension=1536,
            batch_size=25,
            supported_dimensions=[1536],
            max_batch_size=25,
        )
    )

    assert embedder.model_name == "custom-text-embedding"
    assert embedder.dimension == 1536


def test_validates_values_against_model_config_capabilities() -> None:
    with pytest.raises(ValueError, match="不在 supported_dimensions"):
        DashScopeEmbedder(_config(dimension=768))

    with pytest.raises(ValueError, match="超过 max_batch_size"):
        DashScopeEmbedder(_config(batch_size=11))


def test_remote_error_includes_model_and_text_type(monkeypatch) -> None:
    response = SimpleNamespace(
        status_code=400,
        code="InvalidParameter",
        message="url error, please check url!",
    )
    monkeypatch.setattr(
        "forge.retrieval.embedders.dashscope_embedder.TextEmbedding.call",
        lambda **kwargs: response,
    )
    embedder = DashScopeEmbedder(_config())

    with pytest.raises(
        RuntimeError,
        match=r"model=text-embedding-v3, text_type=document",
    ):
        embedder.embed_documents(["hello"])

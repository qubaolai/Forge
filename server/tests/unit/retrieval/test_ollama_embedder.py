"""OllamaEmbedder 配置校验、分批、维度校验、重试测试。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from forge.retrieval.embedders.ollama_embedder import OllamaEmbedder


def _config(**overrides) -> dict:
    return {
        "model": "nomic-embed-text",
        "dimension": 768,
        "batch_size": 2,
        "supported_dimensions": [768],
        "max_batch_size": 10,
        "max_retries": 0,
        "retry_backoff": 0,
        **overrides,
    }


def _items(count: int, dim: int) -> list[SimpleNamespace]:
    return [SimpleNamespace(index=i, embedding=[float(i)] * dim) for i in range(count)]


@pytest.mark.parametrize(
    "field",
    ["model", "dimension", "batch_size", "supported_dimensions", "max_batch_size"],
)
def test_requires_model_config_fields(field: str) -> None:
    config = _config()
    config.pop(field)
    with pytest.raises(ValueError, match=rf"config\['{field}'\]"):
        OllamaEmbedder(config)


def test_validates_values_against_model_config() -> None:
    with pytest.raises(ValueError, match="不在 supported_dimensions"):
        OllamaEmbedder(_config(dimension=512))
    with pytest.raises(ValueError, match="超过 max_batch_size"):
        OllamaEmbedder(_config(batch_size=11))


def test_default_placeholder_key_and_base_url() -> None:
    """不传 api_key / base_url 也能构造 (占位 key + 本地默认 base_url)。"""
    embedder = OllamaEmbedder(_config())
    assert embedder.model_name == "nomic-embed-text"
    assert embedder.dimension == 768


def test_embed_documents_batches_and_orders(monkeypatch) -> None:
    embedder = OllamaEmbedder(_config(batch_size=2))
    calls: list[list[str]] = []

    def fake_create(*, model: str, input: list[str]):
        calls.append(list(input))
        # 故意乱序返回, 验证按 index 排序
        return SimpleNamespace(data=list(reversed(_items(len(input), 768))))

    monkeypatch.setattr(embedder._client.embeddings, "create", fake_create)
    vectors = embedder.embed_documents(["a", "b", "c"])

    assert len(vectors) == 3
    # 按 batch_size=2 分两批: [a, b], [c]
    assert calls == [["a", "b"], ["c"]]
    # 排序后首个向量 index=0
    assert vectors[0] == [0.0] * 768


def test_count_mismatch_raises(monkeypatch) -> None:
    embedder = OllamaEmbedder(_config())
    monkeypatch.setattr(
        embedder._client.embeddings,
        "create",
        lambda *, model, input: SimpleNamespace(data=_items(1, 768)),
    )
    with pytest.raises(RuntimeError, match=r"数量不一致.*model=nomic-embed-text"):
        embedder.embed_documents(["a", "b"])


def test_dimension_mismatch_raises(monkeypatch) -> None:
    embedder = OllamaEmbedder(_config())
    monkeypatch.setattr(
        embedder._client.embeddings,
        "create",
        lambda *, model, input: SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1] * 512)]),
    )
    with pytest.raises(RuntimeError, match=r"维度 512 与配置 768 不一致"):
        embedder.embed_query("hi")


def test_retries_on_connection_error(monkeypatch) -> None:
    import httpx
    from openai import APIConnectionError

    embedder = OllamaEmbedder(_config(max_retries=1, retry_backoff=0))
    state = {"n": 0}

    def fake_create(*, model: str, input: list[str]):
        state["n"] += 1
        if state["n"] == 1:
            raise APIConnectionError(
                request=httpx.Request("POST", "http://localhost:11434/v1/embeddings")
            )
        return SimpleNamespace(data=_items(len(input), 768))

    monkeypatch.setattr(embedder._client.embeddings, "create", fake_create)
    vectors = embedder.embed_documents(["a"])

    assert state["n"] == 2  # 第一次失败, 第二次成功
    assert len(vectors) == 1

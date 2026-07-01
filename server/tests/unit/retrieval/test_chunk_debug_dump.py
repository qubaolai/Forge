from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

import pytest

from forge.core.types import Chunk, Element
from forge.retrieval.chunkers import ChunkConfig, select_chunker
from forge.retrieval.parsers.dispatcher import default_dispatcher


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


def _maybe_truncate(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}\n[debug dump truncated: {len(text)} chars]"


def _format_element(index: int, element: Element, *, max_chars: int) -> str:
    metadata = asdict(element.metadata)
    metadata = {key: value for key, value in metadata.items() if value is not None}
    lines = [
        f"## Element {index:04d}",
        f"- type: {element.type.value}",
        f"- level: {element.level}",
        f"- chars: {len(element.content)}",
        f"- metadata: {metadata}",
        "",
        "```text",
        _maybe_truncate(element.content, max_chars),
        "```",
    ]
    return "\n".join(lines)


def _format_chunk(index: int, chunk: Chunk, *, max_chars: int) -> str:
    metadata = asdict(chunk.metadata)
    metadata = {key: value for key, value in metadata.items() if value not in (None, {})}
    lines = [
        f"## Chunk {index:04d}",
        f"- id: {chunk.chunk_id}",
        f"- type: {chunk.chunk_type.value}",
        f"- source_type: {chunk.source_type}",
        f"- parent_id: {chunk.parent_id}",
        f"- header_path: {chunk.header_path}",
        f"- chars: {len(chunk.content)}",
        f"- metadata: {metadata}",
        "",
        "```text",
        _maybe_truncate(chunk.content, max_chars),
        "```",
    ]
    return "\n".join(lines)


def _build_chunk_dump(file_path: Path) -> str:
    dispatcher = default_dispatcher()
    parser = dispatcher.get(file_path)
    assert parser is not None, (
        f"unsupported file extension: {file_path.suffix}; "
        f"supported={dispatcher.supported_extensions()}"
    )

    elements = parser.parse(file_path)
    assert elements, "parser returned no elements"

    config = ChunkConfig(
        parent_target_max=_env_int("RAG_CHUNK_DEBUG_PARENT_MAX", 2000),
        sliding_parent_chars=_env_int("RAG_CHUNK_DEBUG_SLIDING_PARENT_CHARS", 1500),
        child_target_chars=_env_int("RAG_CHUNK_DEBUG_CHILD_SIZE", 400),
        child_overlap_chars=_env_int("RAG_CHUNK_DEBUG_CHILD_OVERLAP", 80),
        table_context_max_chars=_env_int("RAG_CHUNK_DEBUG_TABLE_CONTEXT", 300),
    )
    chunker = select_chunker(elements, config)
    chunks = chunker.chunk(
        elements,
        doc_id=os.getenv("RAG_CHUNK_DEBUG_DOC_ID", "debug-doc"),
        doc_version="debug",
    )
    assert chunks, "chunker returned no chunks"

    max_chars = _env_int("RAG_CHUNK_DEBUG_MAX_CHARS", 0)
    parent_count = sum(1 for chunk in chunks if chunk.chunk_type.value == "parent")
    child_count = sum(1 for chunk in chunks if chunk.chunk_type.value == "child")
    parts = [
        "# RAG Chunk Debug Dump",
        "",
        f"- file: {file_path}",
        f"- parser: {type(parser).__name__}",
        f"- chunker: {type(chunker).__name__}",
        f"- elements: {len(elements)}",
        f"- chunks: {len(chunks)}",
        f"- parents: {parent_count}",
        f"- children: {child_count}",
        f"- config: {config}",
        "",
        "# Elements",
        "",
        *(_format_element(i, element, max_chars=max_chars) for i, element in enumerate(elements)),
        "",
        "# Chunks",
        "",
        *(_format_chunk(i, chunk, max_chars=max_chars) for i, chunk in enumerate(chunks)),
    ]
    return "\n\n".join(parts)


def test_debug_dump_document_chunks() -> None:
    """调试真实文档分块结果.

    用法:
        RAG_CHUNK_DEBUG_FILE=/abs/path/to/doc.pdf \
        RAG_CHUNK_DEBUG_OUT=/tmp/rag_chunks.md \
        pytest -q -s server/tests/unit/retrieval/test_chunk_debug_dump.py
    """
    raw_file = os.getenv("RAG_CHUNK_DEBUG_FILE")
    if not raw_file:
        pytest.skip("set RAG_CHUNK_DEBUG_FILE=/abs/path/to/document to dump chunks")

    file_path = Path(raw_file).expanduser().resolve()
    assert file_path.exists(), f"file not found: {file_path}"

    dump = _build_chunk_dump(file_path)
    out_path = os.getenv("RAG_CHUNK_DEBUG_OUT")
    if out_path:
        Path(out_path).expanduser().write_text(dump, encoding="utf-8")
    print(f"\n{dump}")

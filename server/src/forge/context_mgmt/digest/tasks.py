"""会话 digest 异步任务 (context 子系统自有写路径).

不阻塞响应: 由 hooks 在 turn.completed 时入队, 这里扫描该 session 中超长的历史
消息, 计算 digest 并 upsert 到 message_digests。下一轮 HistoryProvider 即可命中无损
digest 替代廉价截断。

幂等: 以 message_id + source_hash 去重 (source_hash 匹配且 status=done 则跳过),
天然容忍每轮入队的重复触发。

celery 软依赖: 没装 celery 时, import 仍可工作 (兜底装饰器), 本地队列走 run_digest_task。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, cast

logger = logging.getLogger(__name__)

# prose 段短于此字符数时直接保留 (截断) 原文, 不调 LLM (省成本)
_PROSE_VERBATIM_MAX_CHARS = 600
# 单条消息扫描的最近条数上限
_SCAN_LIMIT = 50


# ---------------------------------------------------------------------------
# celery 装饰器: 软依赖
# ---------------------------------------------------------------------------
try:
    from celery import shared_task
except ImportError:  # pragma: no cover

    def shared_task(*args: Any, **kwargs: Any):
        def decorator(fn):
            return fn

        if args and callable(args[0]):
            return args[0]
        return decorator


# ---------------------------------------------------------------------------
# 本地/可复用入口 (async)
# ---------------------------------------------------------------------------
async def run_digest_task(session_id: str) -> None:
    """扫描 session 近期超长消息, 计算并 upsert digest。

    现算长度判定 (chat_messages 无独立 token 列, usage 是整轮用量而非单条),
    不依赖任何 DB token 列。
    """
    from forge.config.settings import get_settings
    from forge.context_mgmt.digest.store import DigestStore
    from forge.context_mgmt.meter.token_meter import get_token_meter
    from forge.core.types.message import Message, Role
    from forge.infrastructure.database.database import get_session_factory, init_engine
    from forge.infrastructure.database.repositories.chat_message_repo import (
        ChatMessageRepository,
    )
    from forge.observability.tracing.tracer import span

    try:
        cfg = get_settings().context.digest
    except Exception:  # noqa: BLE001
        return
    if not cfg.enabled:
        return
    min_tokens = max(1, cfg.min_tokens)

    init_engine()  # 幂等, worker 进程也安全
    factory = get_session_factory()

    async with factory() as db:
        repo = ChatMessageRepository(db)
        rows = await repo.load_recent(session_id, limit=_SCAN_LIMIT)
    if not rows:
        return

    meter = get_token_meter()
    store = DigestStore(factory)

    # 1. 粗筛候选: 优先用落库的 token_count (免 tiktoken); 缺列时用字节数下界粗筛
    #    (tiktoken token 数 <= UTF-8 字节数) 跳过短消息, 再对剩余做精确计数。
    candidates: list[tuple] = []  # (row, content, approx_tokens, source_hash)
    for row in rows:
        if row.role not in ("user", "assistant"):
            continue
        content = row.content or ""
        if not content:
            continue
        tc = getattr(row, "token_count", None)
        if tc is not None:
            approx_tokens = tc
        else:
            if len(content.encode("utf-8")) <= min_tokens:
                continue
            approx_tokens = meter.count_messages(
                [Message(role=cast(Role, row.role), content=content)]
            )
        if approx_tokens <= min_tokens:
            continue
        source_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        candidates.append((row, content, approx_tokens, source_hash))
    if not candidates:
        return

    # 2. 一次性查已有 digest 元信息, 过滤掉 source_hash 未变更的 (幂等去重)
    metas = await store.batch_get_meta([row.id for row, *_ in candidates])
    todo = [
        c for c in candidates
        if metas.get(c[0].id) != (c[3], "done")
    ]
    if not todo:
        return

    # 3. 计算 + upsert; summarizer 惰性构造一次 (失败也只构造一次, 不每条重试)
    summarizer = None
    summarizer_built = False

    for row, content, approx_tokens, source_hash in todo:
        if not summarizer_built:
            summarizer = _build_summarizer()
            summarizer_built = True

        try:
            with span(
                "context.digest.compute",
                session_id=session_id,
                message_id=row.id,
                approx_tokens=approx_tokens,
            ) as s:
                segments, model_used = await _compute_segments(content, summarizer)
                s.set("segments", len(segments))
        except Exception as exc:  # noqa: BLE001
            logger.warning("digest 计算失败 message=%s: %s", row.id, exc)
            continue

        try:
            await store.upsert(
                message_id=row.id,
                session_id=row.session_id or session_id,
                segments=segments,
                total_tokens=approx_tokens,
                source_hash=source_hash,
                model=model_used,
                status="done",
            )
            logger.info(
                "digest 写入 message=%s session=%s segments=%d tokens=%d",
                row.id, session_id, len(segments), approx_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("digest upsert 失败 message=%s: %s", row.id, exc)


# ---------------------------------------------------------------------------
# 内部: 分段计算
# ---------------------------------------------------------------------------
def _build_summarizer():
    """构造 ProseSummarizer; 失败返回 None (prose 全走截断降级)。"""
    try:
        from forge.config.settings import get_settings
        from forge.context_mgmt.digest.prose_summarizer import ProseSummarizer
        from forge.llm import get_llm_gateway

        gateway = get_llm_gateway(get_settings())
        return ProseSummarizer(gateway)
    except Exception as exc:  # noqa: BLE001
        logger.warning("ProseSummarizer 初始化失败, prose 段走截断降级: %s", exc)
        return None


async def _compute_segments(content: str, summarizer) -> tuple[list, str | None]:
    """切分 + 计算每段 digest. 返回 (segments, 使用的模型名 | None)。

    prose 段再按 markdown 标题/段落做语义子分段 (split_prose_sections), 逐子段摘要,
    每子段带 anchor (标题/首句 + 行号) —— 与同步骨架共用 segmenter / section_anchor,
    使长文章也能被 read_message 按 line_range 定向回读。
    """
    from forge.context_mgmt.digest.code_skeleton import build_code_segment
    from forge.context_mgmt.digest.prose_skeleton import section_anchor
    from forge.context_mgmt.digest.segmenter import split_prose_sections, split_segments
    from forge.context_mgmt.digest.types import Segment

    raw_segments = split_segments(content)
    segments: list[Segment] = []
    model_used: str | None = None

    for raw in raw_segments:
        if raw.kind == "code":
            segments.append(build_code_segment(raw))
            continue
        # prose: 语义子分段 → 逐段处理 (短段截断保留, 长段 LLM 摘要, 失败回退截断)
        for sec in split_prose_sections(raw.text, raw.start_line):
            text = sec.text.strip()
            if len(text) <= _PROSE_VERBATIM_MAX_CHARS or summarizer is None:
                digest_text = _truncate(text)
            else:
                summary = await summarizer.summarize(text)
                if summary:
                    digest_text = summary
                    model_used = "utility-fast"
                else:
                    digest_text = _truncate(text)
            segments.append(
                Segment(
                    kind="prose",
                    start_line=sec.start_line,
                    end_line=sec.end_line,
                    anchor=section_anchor(sec),
                    digest_text=digest_text,
                )
            )
    return segments, model_used


def _truncate(text: str, limit: int = _PROSE_VERBATIM_MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + " …(截断, 用 read_message 回读)"


# ---------------------------------------------------------------------------
# 任务入口 (sync, Celery 标准签名)
# ---------------------------------------------------------------------------
@shared_task(name="context.digest", bind=True, max_retries=2, default_retry_delay=30)
def digest_task(self: Any, session_id: str) -> None:
    """生成 + 持久化 session_id 中超长消息的 digest。"""
    import asyncio

    logger.info("digest 任务开始 session=%s 第 %s 次尝试", session_id, self.request.retries)
    asyncio.run(run_digest_task(session_id))
    logger.info("digest 任务完成 session=%s", session_id)

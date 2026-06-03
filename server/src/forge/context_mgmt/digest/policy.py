"""DigestPolicy: 读时把单条超长消息引用化折叠.

这是「核心止血点」: 在 HistoryProvider 把历史交给 MessageAssembler 之前,
对每条消息做单条上限判定 ——

    meter.count_messages([m]) > cap  时:
        - 缓存命中 (lookup[id] 有 DigestRecord)  -> 替换为「[ref:msg:<id>] + 无损 digest 文本」(阶段 2)
        - 缓存未命中 (刚产生 / 异步未算完)        -> **同步结构化兜底**: 复用 segmenter +
          code_skeleton + prose_skeleton 现场切出带 anchor+行号的骨架占位, 标记 digest_pending。
          下一轮异步 digest 算完后无缝升级为 LLM 摘要版。

「同步结构化兜底」相比旧版「首尾截断」的关键收益: 即使最新巨型消息无缓存,
也能即时折叠为 **有界 + 可寻址 + 可回读** 的占位 —— 单条 cap 永远优先且与新旧无关,
故单条消息独自爆上下文窗口的情况被彻底兜住 (无需为「最近 N 轮」开 verbatim 豁免)。

折叠后单条体积骤降, 既不再独吞 dialogue 预算, 也不会在
MessageAssembler._trim_history_by_budget 里触发 break 把更早历史全部丢弃,
与「累计预算」形成「单条 cap + 累计 budget」双闸。

无状态, 同一实例可并发使用。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from forge.context_mgmt.digest.code_skeleton import build_code_segment
from forge.context_mgmt.digest.prose_skeleton import build_prose_segments
from forge.context_mgmt.digest.segmenter import split_segments
from forge.context_mgmt.digest.types import DigestLookup, DigestRecord, Segment
from forge.context_mgmt.protocols import TokenMeter
from forge.context_mgmt.types import HistoryMessage


@dataclass
class DigestApplyResult:
    """DigestPolicy.apply 的返回值.

    messages:    处理后的历史消息 (超 cap 的已被替换为占位)。
    substituted: 命中缓存、用无损 digest 替换的条数 (阶段 2 才 > 0)。
    pending:     未命中缓存、走结构化骨架兜底的条数。
    message_tokens: 与 messages 等长的「每条折叠后 token 数」, 供 MessageAssembler
                    裁剪/聚合时免重复 tiktoken (未折叠的用落库携带值, 折叠的算占位符体积)。
    """

    messages: list[HistoryMessage]
    substituted: int = 0
    pending: int = 0
    message_tokens: list[int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.message_tokens is None:
            self.message_tokens = []

    @property
    def degraded_flags(self) -> list[str]:
        """供 ContentChunk.degraded 透出, 最终并入 snapshot.degraded / context_meta.

        注意: digest_substituted 是无损路径, 不算降级, 故不进 degraded
        (走 info_flags 单独通道展示); 仅 digest_pending (有损截断) 计入降级。
        """
        return ["digest_pending"] if self.pending else []

    @property
    def info_flags(self) -> list[str]:
        """供 ContentChunk.info 透出: digest_substituted (无损折叠, 非降级)。"""
        return ["digest_substituted"] if self.substituted else []


class DigestPolicy:
    """读时引用化策略.

    Args:
        head_lines: 降级截断保留的开头行数。
        tail_lines: 降级截断保留的结尾行数。
        side_char_cap: 单侧 (头/尾) 摘录的字符上限 (防止超长单行炸开)。
    """

    def __init__(
        self,
        *,
        max_segments: int = 40,
        head_lines: int = 24,
        tail_lines: int = 12,
        side_char_cap: int = 2400,
    ) -> None:
        # 同步结构化兜底单条最多保留的分段数 (防超多函数/标题撑爆占位)
        self._max_segments = max(1, max_segments)
        # 以下三项仅用于 _raw_truncate 末位兜底 (结构化失败时)
        self._head_lines = max(1, head_lines)
        self._tail_lines = max(0, tail_lines)
        self._side_char_cap = max(200, side_char_cap)

    def apply(
        self,
        messages: list[HistoryMessage],
        *,
        cap: int,
        meter: TokenMeter,
        lookup: DigestLookup | None = None,
    ) -> DigestApplyResult:
        """对超过单条 cap 的消息做引用化折叠.

        cap <= 0 视为关闭, 原样返回。
        """
        if cap <= 0 or not messages:
            return DigestApplyResult(messages=messages)

        out: list[HistoryMessage] = []
        costs: list[int] = []
        substituted = 0
        pending = 0
        for hm in messages:
            content = hm.message.content or ""
            if not content:
                out.append(hm)
                costs.append(hm.token_count or 0)
                continue
            # 原文 token 数: 优先用落库携带值 (免重复 tiktoken), 缺则实时算
            approx_tokens = (
                hm.token_count
                if hm.token_count is not None
                else meter.count_messages([hm.message])
            )
            if approx_tokens <= cap:
                out.append(hm)
                costs.append(approx_tokens)
                continue

            ref = f"msg:{hm.id}"
            record = lookup.get(hm.id) if lookup else None
            if record is not None:
                placeholder = self._render_with_digest(ref, record)
                substituted += 1
            else:
                placeholder = self._fallback_fold(ref, content, approx_tokens)
                pending += 1

            # 不可变更原始 Message (可能是共享的 DB 视图), 构造新对象。
            new_message = replace(hm.message, content=placeholder)
            out.append(replace(hm, message=new_message))
            # 折叠后实际体积 (占位符) 重算 —— 折叠条数少, 成本可接受
            costs.append(meter.count_messages([new_message]))

        return DigestApplyResult(
            messages=out, substituted=substituted, pending=pending,
            message_tokens=costs,
        )

    # ------------------------------------------------------------------
    # 内部: 无损 digest 占位 (阶段 2 缓存命中走这里)
    # ------------------------------------------------------------------
    def _render_with_digest(self, ref: str, record: DigestRecord) -> str:
        return self._render_record(ref, record, pending=False)

    def _render_record(
        self, ref: str, record: DigestRecord, *, pending: bool
    ) -> str:
        """统一渲染 [ref] + digest 主体. pending=True 时标注「待精炼」。"""
        body = record.render()
        note = "已折叠为结构化骨架(待精炼)" if pending else "已折叠为以下 digest"
        header = (
            f"[ref:{ref}] (原文约 {record.total_tokens} tokens, {note}; "
            f"需原文用 read_message 按 line_range 回读)"
        )
        return f"{header}\n{body}" if body else header

    # ------------------------------------------------------------------
    # 内部: 同步结构化兜底 (缓存未命中走这里)
    # ------------------------------------------------------------------
    def _fallback_fold(self, ref: str, content: str, approx_tokens: int) -> str:
        """现场切出结构化骨架占位 (无 LLM): code 走 tree-sitter 骨架, prose 走标题/段落骨架。

        结构与缓存命中的无损版一致, 故异步 digest 算完后可无缝升级。
        极端无法结构化时 (无任何可切分段) 退回 _raw_truncate 首尾截断保底。
        """
        record = self._build_sync_record(ref, content, approx_tokens)
        if not record.segments:
            return self._raw_truncate(ref, content, approx_tokens)
        return self._render_record(ref, record, pending=True)

    def _build_sync_record(
        self, ref: str, content: str, approx_tokens: int
    ) -> DigestRecord:
        """同步切分 + 骨架, 拼成 DigestRecord (不调 LLM)。"""
        segments: list[Segment] = []
        for raw in split_segments(content):
            if raw.kind == "code":
                segments.append(build_code_segment(raw))
            else:
                segments.extend(build_prose_segments(raw))
            if len(segments) >= self._max_segments:
                segments = segments[: self._max_segments]
                break
        return DigestRecord(
            ref=ref, total_tokens=approx_tokens, segments=tuple(segments)
        )

    # ------------------------------------------------------------------
    # 内部: 末位兜底 —— 廉价首尾截断 (仅结构化失败时)
    # ------------------------------------------------------------------
    def _raw_truncate(self, ref: str, content: str, approx_tokens: int) -> str:
        # 用 split("\n") 与 segmenter / slice_text 的行号体系保持一致
        lines = content.split("\n")
        total = len(lines)

        head = lines[: self._head_lines]
        has_tail = self._tail_lines > 0 and total > self._head_lines + self._tail_lines
        tail = lines[-self._tail_lines :] if has_tail else []
        omitted = max(0, total - len(head) - len(tail))

        header = (
            f"[ref:{ref}] (原文约 {approx_tokens} tokens / 共 {total} 行; "
            f"以下为首尾摘录, 中间 {omitted} 行已折叠)"
        )
        parts = [header, self._char_cap("\n".join(head))]
        if tail:
            parts.append(f"... ({omitted} 行已折叠) ...")
            parts.append(self._char_cap("\n".join(tail)))
        return "\n".join(parts)

    def _char_cap(self, text: str) -> str:
        if len(text) <= self._side_char_cap:
            return text
        return text[: self._side_char_cap] + " …(截断)"

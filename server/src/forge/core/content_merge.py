"""内容合并工具 — 续写去重（longest suffix-prefix match）。"""


def strip_overlap(prev: str, new: str, max_overlap: int = 256) -> tuple[str, int]:
    """找 prev 的最长后缀, 它同时是 new 的前缀; 返回 (剥离后的 new, 重叠字符数).

    若 prev/new 任一为空 -> 直接返回原 new + 0.
    若没找到任何重叠 -> 返回原 new + 0.

    算法: 从可能的最长重叠 (= min(len(prev), len(new), max_overlap)) 向下试,
    命中即返回, 保证拿到的是最长匹配.
    """
    if not prev or not new:
        return new, 0
    end = min(len(prev), len(new), max_overlap)
    for k in range(end, 0, -1):
        if prev.endswith(new[:k]):
            return new[k:], k
    return new, 0


class ResumeStreamDedup:
    """续写流的实时去重器, 解决前端拼接 LLM 续写 delta 时的"割裂"问题.

    背景:
        LLM 续写时不总能严格按 anchor 接上, 会重复 prev_content 末尾的若干字符,
        或者在代码块 / 表格场景下补一个多余的换行 / 前缀.
        SSE delta 原样推到前端 -> 前端 `prev.content + delta` 拼出来就有"割裂".

    用法:
        dedup = ResumeStreamDedup(prev_content)
        async for ev in runner:
            if ev.type == "delta":
                clean = dedup.feed(ev.payload.get("content", ""))
                if clean:
                    yield AgentEvent("delta", {"content": clean})
            else:
                yield ev
        tail = dedup.flush()
        if tail:
            yield AgentEvent("delta", {"content": tail})

    设计要点:
        - 比较窗口仅取 prev_content 末尾 max_overlap 字符 (256),
          避免长 prev 拖性能, 同时限定最长重叠.
        - 维护 accumulated + emitted: emitted 单调不减, 即便后续 overlap 反复
          也不会撤回已 emit 字符 (SSE 没法回滚).
        - 保留 safety_buffer 字符不 emit (默认 32), 给后续 token 留判定窗口,
          降低 over-emit 概率; flush() 时清空尾巴.
    """

    DEFAULT_MAX_OVERLAP = 256
    DEFAULT_SAFETY_BUFFER = 32

    def __init__(
        self,
        prev_content: str,
        *,
        max_overlap: int = DEFAULT_MAX_OVERLAP,
        safety_buffer: int = DEFAULT_SAFETY_BUFFER,
    ) -> None:
        self._prev_tail = prev_content[-max_overlap:] if prev_content else ""
        self._max_overlap = max_overlap
        # 没有 prev 就没必要保留 buffer, 全部直发以保实时性
        self._safety_buffer = safety_buffer if self._prev_tail else 0
        self._accumulated = ""
        self._emitted = 0
        self._dedup_chars = 0

    @property
    def dedup_chars(self) -> int:
        """剥掉的重叠字符数 (仅做日志/指标用)."""
        return self._dedup_chars

    def feed(self, chunk: str) -> str:
        """喂进一个 raw delta chunk, 返回当下可以安全 emit 的内容 (可能为空)."""
        if not chunk:
            return ""
        if not self._prev_tail:
            self._accumulated += chunk
            self._emitted = len(self._accumulated)
            return chunk
        self._accumulated += chunk
        non_overlap = self._compute_non_overlap()
        self._dedup_chars = len(self._accumulated) - len(non_overlap)
        safe_end = max(0, len(non_overlap) - self._safety_buffer)
        if safe_end <= self._emitted:
            return ""
        out = non_overlap[self._emitted:safe_end]
        self._emitted = safe_end
        return out

    def flush(self) -> str:
        """LLM 流结束时调用, 把 safety_buffer 里剩下的非重叠字符全部 emit."""
        if not self._prev_tail:
            return ""
        non_overlap = self._compute_non_overlap()
        self._dedup_chars = len(self._accumulated) - len(non_overlap)
        if len(non_overlap) <= self._emitted:
            return ""
        out = non_overlap[self._emitted:]
        self._emitted = len(non_overlap)
        return out

    def _compute_non_overlap(self) -> str:
        prev = self._prev_tail
        new = self._accumulated
        end = min(len(prev), len(new), self._max_overlap)
        for k in range(end, 0, -1):
            if prev.endswith(new[:k]):
                return new[k:]
        return new

"""DigestPolicy 单测: 读时引用化折叠 (止血核心)."""

from __future__ import annotations

from datetime import datetime

from forge.context_mgmt.digest.policy import DigestPolicy
from forge.context_mgmt.digest.types import DigestRecord, Segment
from forge.context_mgmt.protocols import TokenMeter
from forge.context_mgmt.types import HistoryMessage
from forge.core.types.message import Message


class _CharMeter(TokenMeter):
    """以字符数充当 token 数, 让 cap 在测试中可精确控制."""

    def count_text(self, text: str) -> int:
        return len(text)

    def count_messages(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") for m in messages)


def _msg(mid: str, content: str) -> HistoryMessage:
    return HistoryMessage(
        message=Message(role="assistant", content=content),
        id=mid,
        turn_index=0,
    )


def test_under_cap_kept_verbatim():
    """未超 cap 的消息原样保留 (对象不变)."""
    meter = _CharMeter()
    hm = _msg("m1", "短消息")
    result = DigestPolicy().apply([hm], cap=100, meter=meter)
    assert result.messages[0] is hm
    assert result.pending == 0 and result.substituted == 0
    assert result.degraded_flags == []


def test_over_cap_without_cache_falls_back_and_flags_pending():
    """超 cap 且无缓存 -> 廉价截断降级 + digest_pending 标记."""
    meter = _CharMeter()
    long_content = "\n".join(f"line {i}" for i in range(200))
    hm = _msg("m42", long_content)

    result = DigestPolicy(head_lines=5, tail_lines=3).apply(
        [hm], cap=50, meter=meter
    )

    folded = result.messages[0].message.content
    assert folded != long_content                      # 已被折叠
    assert "[ref:msg:m42]" in folded                   # 引用占位
    assert "已折叠" in folded
    assert len(folded) < len(long_content)             # 体积显著下降
    assert result.pending == 1 and result.substituted == 0
    assert result.degraded_flags == ["digest_pending"]
    # 原始入参对象不应被原地修改
    assert hm.message.content == long_content


def test_over_cap_with_cache_uses_lossless_digest():
    """超 cap 且命中缓存 -> 用无损 digest 替换, 不计降级."""
    meter = _CharMeter()
    long_content = "x" * 500
    hm = _msg("m7", long_content)
    record = DigestRecord(
        ref="msg:m7",
        total_tokens=500,
        segments=(
            Segment(kind="code", start_line=1, end_line=20,
                    digest_text="def login(...): ...", anchor="def login (L1-20)"),
        ),
        generated_at=datetime.now(),
    )

    result = DigestPolicy().apply(
        [hm], cap=50, meter=meter, lookup={"m7": record}
    )

    folded = result.messages[0].message.content
    assert "[ref:msg:m7]" in folded
    assert "def login (L1-20)" in folded               # 锚点已渲染
    assert result.substituted == 1 and result.pending == 0
    assert result.degraded_flags == []                 # 无损路径不算降级


def test_cap_zero_disables_policy():
    """cap<=0 视为关闭, 原样返回整列表."""
    meter = _CharMeter()
    msgs = [_msg("a", "y" * 999), _msg("b", "z" * 999)]
    result = DigestPolicy().apply(msgs, cap=0, meter=meter)
    assert result.messages is msgs
    assert result.degraded_flags == []


def test_mixed_only_oversized_folded():
    """混合列表: 仅超 cap 的被折叠, 其余原样."""
    meter = _CharMeter()
    short = _msg("s", "ok")
    long_ = _msg("l", "q" * 300)
    result = DigestPolicy().apply([short, long_], cap=50, meter=meter)

    assert result.messages[0] is short                 # 短消息原样
    assert result.messages[1] is not long_             # 长消息被替换
    assert "[ref:msg:l]" in result.messages[1].message.content
    assert result.pending == 1

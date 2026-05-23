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

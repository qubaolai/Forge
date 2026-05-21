"""跨格式复用的文本处理工具函数."""

import hashlib


def content_hash(text: str) -> str:
    """生成文本的 MD5 哈希值.

    用于增量更新场景: 对比新旧 chunk 的 hash 以判断内容是否变化,
    从而决定是否需要重新 embedding 和入库.

    Args:
        text: 待哈希的文本.

    Returns:
        32 位小写十六进制 MD5 字符串.
    """
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def find_cut_point(text: str, target: int, search_window: int = 50) -> int:
    """在目标位置附近查找自然切点.

    按优先级搜索段落分隔、句号、换行、分号、逗号等标点, 在
    ``[target - search_window, target + search_window]`` 范围内
    返回最靠近的切点位置. 避免子块在句子中间断开.

    Args:
        text: 待切分的文本.
        target: 目标切点位置 (字符索引).
        search_window: 允许偏离目标位置的搜索半径, 默认 50 字符.

    Returns:
        实际切点位置 (字符索引). 若未找到合适的标点, 则返回 target.
    """
    if target >= len(text):
        return len(text)

    start = max(0, target - search_window)
    end = min(len(text), target + search_window)

    # 优先级: 段落分隔 > 中文句号 > 英文句号 > 换行 > 分号 > 逗号
    for punct in ["\n\n", "。\n", "。", ".", "!", "?", "\n", ";", ";", ","]:
        idx = text.rfind(punct, start, end)
        if idx != -1:
            return idx + len(punct)
    return target


def estimate_chinese_length(text: str) -> int:
    """估算文本的等效中文字数.

    中文字符按 1 字计算, 其他字符按 2 字符等效 1 个中文字计算.
    用于混合语种文档的切分字数判断, 避免纯按字符数切分时英文段落
    被切得过短.

    Args:
        text: 待估算的文本.

    Returns:
        等效中文字数 (整数).
    """
    cn_count = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other_count = len(text) - cn_count
    return cn_count + other_count // 2

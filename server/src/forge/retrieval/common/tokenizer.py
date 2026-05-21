"""分词器抽象 (横切关注点).

职责:
    - 给 BM25 入库和查询提供统一的分词接口
    - 换分词器只改这里, 不动 BM25Store
    - 工厂返回单例, 避免 jieba 词典重复加载

设计要点:
    - tokenize_to_string 是 mixin 方法, 所有实现共享 (空格连接, FTS5 入库专用)
    - 入库 tokenizer 与查询 tokenizer 必须同一个实例, 否则召回率断崖
"""

from __future__ import annotations

import logging
import re
import threading
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 抽象基类
# ----------------------------------------------------------------------
class Tokenizer(ABC):
    """分词器抽象基类."""

    def __init__(self, config: dict):
        self.config = config

    @abstractmethod
    def tokenize(self, text: str) -> list[str]:
        """分词. 返回 token 列表.

        约定:
            - 已做 lower 化 (中英混排时 'API' 和 'api' 命中同一 token)
            - 已过滤纯空白和纯标点 token
            - 不过滤停用词 (BM25 IDF 自然降权高频词)
        """

    def tokenize_to_string(self, text: str) -> str:
        """分词后空格连接, FTS5 入库/查询专用.

        FTS5 用 unicode61 tokenizer 按空白二次切分,
        我们外部用 jieba 切好后空格连接, FTS5 即可正确入库.
        """
        return " ".join(self.tokenize(text))


# ----------------------------------------------------------------------
# 装饰器自注册工厂
# ----------------------------------------------------------------------
_REGISTRY: dict[str, type[Tokenizer]] = {}
_INSTANCES: dict[str, Tokenizer] = {}
_INSTANCE_LOCK = threading.Lock()


def register_tokenizer(name: str):
    """类装饰器: 把 Tokenizer 子类登记到工厂注册表."""

    def decorator(cls: type[Tokenizer]) -> type[Tokenizer]:
        if not issubclass(cls, Tokenizer):
            raise TypeError(f"@register_tokenizer 只能装饰 Tokenizer 子类, 收到 {cls.__name__}")
        if name in _REGISTRY:
            raise ValueError(
                f"Tokenizer 重复注册: {name} "
                f"(已存在: {_REGISTRY[name].__name__}, 新增: {cls.__name__})"
            )
        _REGISTRY[name] = cls
        return cls

    return decorator


class TokenizerFactory:
    """Tokenizer 工厂. 返回单例.

    与 EmbedderFactory 不同: Tokenizer 是有状态的 (jieba 词典),
    重复创建浪费内存且冷启动慢, 因此按 (name, config_key) 缓存.
    """

    @staticmethod
    def create(name: str, config: dict | None = None) -> Tokenizer:
        if name not in _REGISTRY:
            raise ValueError(f"未注册的 tokenizer: {name!r}. 已注册: {sorted(_REGISTRY.keys())}")
        config = config or {}
        # 缓存 key: name + 关键配置 (此处简化为只按 name + user_dict)
        cache_key = f"{name}:{config.get('user_dict') or ''}"
        with _INSTANCE_LOCK:
            inst = _INSTANCES.get(cache_key)
            if inst is None:
                inst = _REGISTRY[name](config)
                _INSTANCES[cache_key] = inst
                logger.info("创建 Tokenizer 单例: %s (cache_key=%s)", name, cache_key)
            return inst

    @staticmethod
    def list_names() -> list[str]:
        return sorted(_REGISTRY.keys())


# ----------------------------------------------------------------------
# jieba 实现
# ----------------------------------------------------------------------
# 仅保留: 中文 / 英文字母数字 / 下划线
# 过滤: 纯空白、标点、控制字符
_VALID_TOKEN_RE = re.compile(r"[\u4e00-\u9fffA-Za-z0-9_]")


@register_tokenizer("jieba")
class JiebaTokenizer(Tokenizer):
    """jieba 精确模式分词.

    配置:
        user_dict: 可选自定义词典路径, None 表示不加载
    """

    def __init__(self, config: dict):
        super().__init__(config)
        # 延迟 import: 让没装 jieba 的开发环境也能 import 本模块
        import logging
        import jieba

        # jieba 在 __init__.py 里给自己的 logger 加了一个 StreamHandler(stderr)，
        # 并把级别强制设为 DEBUG，会污染启动日志。这里清除掉。
        _jlog = logging.getLogger("jieba")
        _jlog.handlers.clear()
        _jlog.setLevel(logging.WARNING)

        self._jieba = jieba

        user_dict = config.get("user_dict")
        if user_dict:
            dict_path = Path(user_dict)
            if not dict_path.exists():
                raise FileNotFoundError(f"jieba 用户词典不存在: {dict_path}")
            jieba.load_userdict(str(dict_path))
            logger.info("jieba 加载用户词典: %s", dict_path)

        # 触发 jieba 初始化, 避免首次分词时的延迟
        list(jieba.cut("初始化"))
        logger.info("JiebaTokenizer 就绪")

    def tokenize(self, text: str) -> list[str]:
        if not text:
            return []
        # 精确模式: 不切碎, 召回质量更可控
        raw = self._jieba.cut(text, cut_all=False)
        out: list[str] = []
        for tok in raw:
            tok = tok.strip()
            if not tok:
                continue
            # 过滤纯标点/空白: 至少包含一个有效字符
            if not _VALID_TOKEN_RE.search(tok):
                continue
            out.append(tok.lower())
        return out

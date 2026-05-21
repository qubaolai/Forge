"""ID 生成:`<prefix>_<nanoid>` 格式,符合规范文档约定。"""

from nanoid import generate

# nanoid 默认 21 位太长,这里取 12 位足够(碰撞概率极低)
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_SIZE = 12


def new_id(prefix: str) -> str:
    """生成业务 ID,例如 new_id("user") -> "user_x7k2a9b3c1d8"。"""
    return f"{prefix}_{generate(_ALPHABET, _SIZE)}"

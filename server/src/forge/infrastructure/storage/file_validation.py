"""上传文件类型校验 (防可执行程序 / 脚本 / 木马)。

策略 (前后端双层, 本模块是后端权威校验):
    1. 扩展名白名单: 只允许 txt/md/word/excel/pdf/常见源码; 命中黑名单 (可执行/脚本) 直接拒。
    2. magic-byte 嗅探 (无视扩展名, 防改名): 拒绝 PE/ELF/Mach-O/Java class 等可执行魔数,
       拒绝 "#!" shebang 脚本。
    3. 声明的二进制格式校验魔数匹配 (pdf/office), 防把可执行体改名成 .pdf/.docx 混入。

调用方在大小校验之后调用 validate_upload(), 失败抛 BadRequest。
"""

from __future__ import annotations

import os

from forge.core.exceptions import BadRequest

# ── 扩展名白名单 ──────────────────────────────────────────────────────────────
# 文档类
_DOC_EXTS = {".txt", ".md", ".markdown", ".rtf", ".csv", ".log"}
# Office
_OFFICE_EXTS = {".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx"}
# PDF
_PDF_EXTS = {".pdf"}
# 源代码 (纯文本, 不含可执行脚本)
_SOURCE_EXTS = {
    ".py", ".pyi", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".vue",
    ".java", ".kt", ".scala", ".go", ".rs", ".c", ".h", ".cpp", ".hpp",
    ".cc", ".cxx", ".cs", ".rb", ".php", ".swift", ".m", ".mm", ".lua",
    ".pl", ".r", ".dart", ".sql", ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".xml", ".html", ".htm", ".css", ".scss", ".less",
    ".tex", ".gradle", ".proto", ".graphql", ".tsv", ".env",
}
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(
    _DOC_EXTS | _OFFICE_EXTS | _PDF_EXTS | _SOURCE_EXTS
)

# ── 扩展名黑名单 (可执行程序 / 脚本, 纵深防御; 与白名单互斥, 优先拒绝) ──────────────
BLOCKED_EXTENSIONS: frozenset[str] = frozenset({
    # 二进制可执行 / 库
    ".exe", ".dll", ".so", ".dylib", ".com", ".msi", ".scr", ".bin",
    ".o", ".a", ".lib", ".obj", ".out", ".elf",
    # 脚本 (用户确认: 一并禁止)
    ".sh", ".bash", ".zsh", ".fish", ".bat", ".cmd", ".ps1", ".psm1",
    ".vbs", ".vbe", ".js.exe", ".wsf", ".wsh", ".reg", ".ps", ".applescript",
    # 打包 / 安装 / 字节码
    ".jar", ".war", ".apk", ".app", ".deb", ".rpm", ".dmg", ".pkg",
    ".class", ".pyc", ".pyo", ".whl", ".egg",
    # 压缩包 (可藏可执行体, 暂不放行; 如需放行另议)
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz",
})

# 可执行体魔数 (无视扩展名一律拒绝)
_EXEC_MAGICS: tuple[bytes, ...] = (
    b"MZ",            # Windows PE (.exe/.dll)
    b"\x7fELF",       # ELF (Linux 可执行/共享库)
    b"\xfe\xed\xfa\xce",  # Mach-O 32-bit
    b"\xfe\xed\xfa\xcf",  # Mach-O 64-bit
    b"\xcf\xfa\xed\xfe",  # Mach-O 64-bit (反序)
    b"\xce\xfa\xed\xfe",  # Mach-O 32-bit (反序)
    b"\xca\xfe\xba\xbe",  # Mach-O fat / Java class
    b"\xca\xfe\xd0\x0d",  # Java pack200
    b"dex\n",         # Android dex
)

# 已知二进制格式应有的魔数 (声明该扩展则必须匹配其一)
_FORMAT_MAGICS: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    # OOXML (docx/xlsx/pptx) 本质是 zip: PK\x03\x04 (空包 PK\x05\x06)
    ".docx": (b"PK\x03\x04", b"PK\x05\x06"),
    ".xlsx": (b"PK\x03\x04", b"PK\x05\x06"),
    ".pptx": (b"PK\x03\x04", b"PK\x05\x06"),
    # 旧版 Office 是 OLE 复合文档
    ".doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
    ".ppt": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",),
}

# 错误码 (沿用 files 路由 400xx 段)
_CODE_BAD_EXT = 40016
_CODE_EXEC_CONTENT = 40017
_CODE_FORMAT_MISMATCH = 40018


def _ext(filename: str) -> str:
    return os.path.splitext((filename or "").strip().lower())[1]


def validate_upload(filename: str, data: bytes) -> None:
    """校验上传文件; 不通过抛 BadRequest。

    Args:
        filename: 原始文件名 (取扩展名判断)。
        data:     文件二进制内容 (用于 magic-byte 嗅探)。
    """
    ext = _ext(filename)

    # 1) 扩展名: 先黑后白
    if ext in BLOCKED_EXTENSIONS:
        raise BadRequest(
            f"不允许上传可执行程序/脚本类型: {ext or '(无扩展名)'}", code=_CODE_BAD_EXT
        )
    if ext not in ALLOWED_EXTENSIONS:
        raise BadRequest(
            f"不支持的文件类型: {ext or '(无扩展名)'}; "
            "仅支持 txt/md/word/excel/pdf 及常见源代码文件",
            code=_CODE_BAD_EXT,
        )

    head = data[:16] if data else b""

    # 2) 可执行体魔数 (无视扩展名): 防把 .exe 改名成 .txt
    for magic in _EXEC_MAGICS:
        if head.startswith(magic):
            raise BadRequest("文件内容疑似可执行程序, 已拒绝", code=_CODE_EXEC_CONTENT)
    # shebang 脚本
    if head.startswith(b"#!"):
        raise BadRequest("文件内容疑似可执行脚本, 已拒绝", code=_CODE_EXEC_CONTENT)

    # 3) 声明的二进制格式必须匹配魔数 (防改名混入)
    expected = _FORMAT_MAGICS.get(ext)
    if expected and not any(head.startswith(m) for m in expected):
        raise BadRequest(
            f"文件内容与扩展名 {ext} 不符, 已拒绝", code=_CODE_FORMAT_MISMATCH
        )


__all__ = ["validate_upload", "ALLOWED_EXTENSIONS", "BLOCKED_EXTENSIONS"]

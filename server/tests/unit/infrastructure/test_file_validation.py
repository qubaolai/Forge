"""上传类型校验单元测试: 白名单放行 + 黑名单/魔数/格式拒绝。"""

from __future__ import annotations

import pytest

from forge.core.exceptions import BadRequest
from forge.infrastructure.storage.file_validation import validate_upload


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("note.txt", b"hello"),
        ("readme.md", b"# title"),
        ("main.py", b"print('hi')\n"),
        ("app.ts", b"export const x = 1"),
        ("data.csv", b"a,b,c"),
        ("doc.pdf", b"%PDF-1.7\n%..."),
        ("sheet.xlsx", b"PK\x03\x04rest-of-zip"),
        ("legacy.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1more"),
    ],
)
def test_allowed_pass(name, data):
    validate_upload(name, data)  # 不抛即通过


@pytest.mark.parametrize(
    ("name", "data"),
    [
        ("evil.sh", b"echo hi"),          # 脚本扩展名
        ("a.exe", b"MZ\x90\x00"),         # PE 魔数
        ("lib.dll", b"MZ..."),            # 可执行库
        ("pkg.jar", b"PK\x03\x04"),       # Java 包
        ("arc.zip", b"PK\x03\x04"),       # 压缩包黑名单
        ("run.bat", b"@echo off"),        # 批处理
        ("p.ps1", b"Write-Host"),         # PowerShell
    ],
)
def test_blocked_extension_reject(name, data):
    with pytest.raises(BadRequest):
        validate_upload(name, data)


def test_renamed_executable_rejected_by_magic():
    # 把 ELF 可执行体改名成 .txt, 仍被 magic-byte 拦下
    with pytest.raises(BadRequest):
        validate_upload("payload.txt", b"\x7fELF\x02\x01\x01")


def test_shebang_script_rejected():
    with pytest.raises(BadRequest):
        validate_upload("hack.txt", b"#!/bin/bash\nrm -rf /")


def test_format_mismatch_rejected():
    # 声明 pdf 但内容不是 %PDF-
    with pytest.raises(BadRequest):
        validate_upload("fake.pdf", b"this is not a pdf")


def test_unknown_extension_rejected():
    with pytest.raises(BadRequest):
        validate_upload("mystery.xyz", b"whatever")


def test_no_extension_rejected():
    with pytest.raises(BadRequest):
        validate_upload("noext", b"data")

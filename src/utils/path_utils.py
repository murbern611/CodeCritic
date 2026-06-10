"""
CodeCritic — 路径安全工具
=========================
防止路径遍历攻击（Path Traversal），对用户输入的文件路径进行验证和规范化。

使用 ``os.path.abspath`` + ``os.path.normpath`` 双重规范化，
配合 ``allowed_base`` 基目录限制，防御 ``../`` 等遍历攻击。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def safe_resolve_path(
    path_str: str,
    allowed_base: Optional[Path] = None,
    must_exist: bool = True,
    must_be_file: bool = False,
    must_be_dir: bool = False,
) -> Path:
    """
    安全解析并验证路径，防止路径遍历攻击（``../`` 等）。

    使用 ``os.path.abspath`` + ``os.path.normpath`` 进行双重规范化，
    再检查解析后的路径是否在 ``allowed_base`` 范围内。

    Args:
        path_str: 用户输入的原始路径字符串。
        allowed_base: 允许的基目录。默认当前工作目录，
                      解析后的路径必须在此目录下。
        must_exist: 为 ``True`` 时路径必须存在，否则抛 ``FileNotFoundError``。
        must_be_file: 为 ``True`` 时路径必须是文件。
        must_be_dir: 为 ``True`` 时路径必须是目录。

    Returns:
        经过规范化验证的绝对 Path 对象。

    Raises:
        FileNotFoundError: ``must_exist=True`` 但路径不存在。
        PermissionError: 解析后的路径不在 ``allowed_base`` 范围内。
        ValueError: 路径类型（文件/目录）不匹配。
    """
    # Step 1: os.path.normpath 去除冗余分隔符和 .. 遍历
    normalized = os.path.normpath(path_str)

    # Step 2: os.path.abspath 转为绝对路径（同时处理相对路径）
    absolute = os.path.abspath(normalized)

    # Step 3: 用 pathlib 做最终规范化（处理符号链接等）
    resolved = Path(absolute).resolve()

    # Step 4: 检查是否在允许的基目录下
    base = (allowed_base or Path.cwd()).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise PermissionError(
            f"路径越权，不允许访问基目录之外的路径: {path_str} "
            f"(resolved={resolved}, base={base})"
        )

    # Step 5: 检查是否存在
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"路径不存在: {path_str}")

    # Step 6: 检查文件/目录类型
    if must_exist:
        if must_be_file and not resolved.is_file():
            raise ValueError(f"路径不是文件: {path_str}")
        if must_be_dir and not resolved.is_dir():
            raise ValueError(f"路径不是目录: {path_str}")

    return resolved

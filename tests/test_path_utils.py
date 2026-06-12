"""
测试路径安全工具
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.utils.path_utils import safe_resolve_path


class TestSafeResolvePath:
    """测试路径安全解析"""

    def test_absolute_path_allowed(self, temp_dir: Path):
        """基础：绝对路径在 base 内应该通过"""
        target = temp_dir / "test.txt"
        target.touch()

        result = safe_resolve_path(str(target), allowed_base=temp_dir)
        assert result == target.resolve()

    def test_relative_path_resolves(self, temp_dir: Path):
        """基础：相对路径应该正常解析"""
        target = temp_dir / "test.txt"
        target.touch()

        # 模拟在 temp_dir 中执行
        original_cwd = Path.cwd()
        try:
            os.chdir(str(temp_dir))
            result = safe_resolve_path("test.txt", allowed_base=temp_dir)
            assert result == target.resolve()
        finally:
            os.chdir(str(original_cwd))

    def test_path_traversal_blocked(self, temp_dir: Path):
        """安全：路径遍历攻击应被阻止"""
        with pytest.raises(PermissionError, match="路径越权"):
            safe_resolve_path(
                "../outside.txt",
                allowed_base=temp_dir,
                must_exist=False,
            )

    def test_nonexistent_file_raises(self, temp_dir: Path):
        """边界：不存在的文件应报错"""
        with pytest.raises(FileNotFoundError, match="路径不存在"):
            safe_resolve_path(
                str(temp_dir / "nonexistent.txt"),
                allowed_base=temp_dir,
                must_exist=True,
            )

    def test_nonexistent_file_allowed(self, temp_dir: Path):
        """边界：允许不存在的路径（must_exist=False）"""
        result = safe_resolve_path(
            str(temp_dir / "new_file.txt"),
            allowed_base=temp_dir,
            must_exist=False,
        )
        assert "new_file.txt" in str(result)

    def test_directory_as_file_raises(self, temp_dir: Path):
        """边界：期望文件但传入目录应报错"""
        with pytest.raises(ValueError, match="路径不是文件"):
            safe_resolve_path(
                str(temp_dir),
                allowed_base=temp_dir.parent,
                must_be_file=True,
            )

    def test_double_dot_escape_blocked(self):
        """安全：使用 ../ 逃逸到 base 之外应被阻止"""
        import tempfile
        # 在系统临时目录下创建一个沙箱目录
        with tempfile.TemporaryDirectory() as sandbox_str:
            sandbox = Path(sandbox_str)
            secret_file = sandbox.parent / "secret.txt"
            # 尝试从 sandbox 逃逸到父目录
            escaped = sandbox / ".." / sandbox.parent.name / ".." / "secret.txt"
            with pytest.raises(PermissionError):
                safe_resolve_path(
                    str(escaped),
                    allowed_base=sandbox,
                    must_exist=False,
                )

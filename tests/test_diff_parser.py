"""
测试 Diff 解析器
"""

from __future__ import annotations

from src.diff.parser import (
    ChangedLine,
    DiffResult,
    FileDiff,
    Hunk,
    build_diff_review_prompt,
    format_diff_for_llm,
    generate_diff,
    parse_diff,
)


class TestParseDiff:
    """测试 unified diff 文本解析"""

    def test_parse_basic_diff(self, sample_git_diff: str):
        """基础：解析标准的 git diff 输出"""
        result = parse_diff(sample_git_diff)

        assert isinstance(result, DiffResult)
        assert len(result.files) == 1

        file_diff = result.files[0]
        assert file_diff.old_path == "user.py"
        assert file_diff.new_path == "user.py"
        assert not file_diff.is_new_file
        assert not file_diff.is_deleted_file

    def test_parse_hunk_count(self, sample_git_diff: str):
        """基础：正确解析 hunk 数量"""
        result = parse_diff(sample_git_diff)
        assert len(result.files[0].hunks) == 2

    def test_parse_added_lines(self, sample_git_diff: str):
        """核心：正确识别新增行"""
        result = parse_diff(sample_git_diff)
        added = result.files[0].all_added_lines

        assert len(added) == 5
        # 应该包含参数化查询行和新的执行调用
        assert any('"SELECT * FROM users WHERE id = ?"' in l.content for l in added)
        assert any("db.execute(query, [id])" in l.content for l in added)

    def test_parse_deleted_lines(self, sample_git_diff: str):
        """核心：正确识别删除行"""
        result = parse_diff(sample_git_diff)
        deleted = result.files[0].all_deleted_lines

        assert len(deleted) == 4
        assert any('f"SELECT * FROM users WHERE id = {id}"' in l.content for l in deleted)

    def test_parse_context_lines(self, sample_git_diff: str):
        """基础：正确识别上下文行"""
        result = parse_diff(sample_git_diff)
        file_diff = result.files[0]
        context_lines = [l for h in file_diff.hunks for l in h.lines if l.type == "context"]

        assert len(context_lines) > 0
        # 第一个 hunk 有 "    print("fetching user")" 作为上下文
        assert any("print" in l.content for l in context_lines)

    def test_parse_line_numbers(self, sample_git_diff: str):
        """基础：正确跟踪行号"""
        result = parse_diff(sample_git_diff)
        file_diff = result.files[0]

        # 第一个 hunk 的起始行
        assert file_diff.hunks[0].old_start == 10
        assert file_diff.hunks[0].new_start == 10

        # 第二个 hunk 的起始行
        assert file_diff.hunks[1].old_start == 20
        assert file_diff.hunks[1].new_start == 20

    def test_parse_section_names(self, sample_git_diff: str):
        """基础：正确提取函数名（git diff 的 @@ 行尾部会带函数名）"""
        result = parse_diff(sample_git_diff)
        file_diff = result.files[0]

        assert "def get_user" in file_diff.hunks[0].section
        assert "def process_items" in file_diff.hunks[1].section

    def test_empty_diff(self):
        """边界：空 diff"""
        result = parse_diff("")
        assert len(result.files) == 0
        assert result.total_files_changed == 0


class TestGenerateDiff:
    """测试从两个代码字符串生成 diff"""

    def test_generate_diff_from_strings(self, sample_code: str, sample_code_v2: str):
        """核心：从新旧版本代码生成 diff"""
        diff_text = generate_diff(sample_code, sample_code_v2)
        # difflib.unified_diff 生成 `--- old.py` 而非 `--- a/old.py`
        assert "--- old.py" in diff_text
        assert "+++ new.py" in diff_text
        assert "-" in diff_text  # 有删除行
        assert "+" in diff_text  # 有新增行

    def test_roundtrip(self, sample_code: str, sample_code_v2: str):
        """核心：生成后解析，验证结果正确"""
        diff_text = generate_diff(sample_code, sample_code_v2)
        result = parse_diff(diff_text)

        assert len(result.files) == 1
        file_diff = result.files[0]
        assert file_diff.total_changes > 0

    def test_identical_code(self):
        """边界：两段代码相同应产生空 diff"""
        code = "def foo():\n    pass\n"
        diff_text = generate_diff(code, code)
        # unified_diff 对相同内容会输出空（或只包含文件头）
        result = parse_diff(diff_text)
        assert len(result.files) == 0 or result.files[0].total_changes == 0

    def test_custom_paths(self):
        """基础：自定义文件路径"""
        diff_text = generate_diff("a=1", "a=2", old_path="src/v1.py", new_path="src/v2.py")
        assert "src/v1.py" in diff_text
        assert "src/v2.py" in diff_text


class TestFormatDiffForLLM:
    """测试格式化为 LLM prompt"""

    def test_format_includes_symbols(self, sample_git_diff: str):
        """核心：格式化输出包含 +/- 标记"""
        result = parse_diff(sample_git_diff)
        formatted = format_diff_for_llm(result)

        assert "+" in formatted
        assert "-" in formatted
        assert "新增" in formatted or "add" in formatted

    def test_format_includes_file_path(self, sample_git_diff: str):
        """基础：格式化输出包含文件名"""
        result = parse_diff(sample_git_diff)
        formatted = format_diff_for_llm(result)

        assert "user.py" in formatted

    def test_empty_diff_format(self):
        """边界：空 diff 格式化"""
        result = DiffResult()
        formatted = format_diff_for_llm(result)
        assert isinstance(formatted, str)


class TestBuildDiffReviewPrompt:
    """测试构建 diff 感知的审查 prompt"""

    def test_build_prompt_structure(self, sample_git_diff: str):
        """核心：生成的 message 列表结构正确"""
        result = parse_diff(sample_git_diff)
        messages = build_diff_review_prompt(
            result,
            agent_system_prompt="你是一个安全专家",
            agent_analysis_dimensions="1. SQL注入",
        )

        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "user"

    def test_system_prompt_preserved(self, sample_git_diff: str):
        """核心：system prompt 被保留"""
        result = parse_diff(sample_git_diff)
        messages = build_diff_review_prompt(
            result,
            agent_system_prompt="你是一个安全专家",
            agent_analysis_dimensions="1. SQL注入",
        )

        assert messages[0]["content"] == "你是一个安全专家"


class TestDataClasses:
    """测试数据结构"""

    def test_changed_line_creation(self):
        """基础：ChangedLine 创建"""
        line = ChangedLine(type="add", content="x = 1", new_line_no=10)
        assert line.type == "add"
        assert line.content == "x = 1"
        assert line.new_line_no == 10

    def test_hunk_properties(self):
        """基础：Hunk 属性计算"""
        hunk = Hunk(header="@@ -1,3 +1,3 @@", old_start=1, old_count=3, new_start=1, new_count=3, section="foo")
        hunk.lines = [
            ChangedLine(type="add", content="x = 1", new_line_no=1),
            ChangedLine(type="delete", content="x = 2", old_line_no=1),
            ChangedLine(type="context", content="y = 3", old_line_no=2, new_line_no=2),
        ]

        assert hunk.has_changes
        assert len(hunk.added_lines) == 1
        assert len(hunk.deleted_lines) == 1

    def test_file_diff_extension(self):
        """基础：正确提取文件扩展名"""
        fd = FileDiff(old_path="test.py", new_path="test.py")
        assert fd.file_extension == "py"

        fd2 = FileDiff(old_path="test", new_path="test")
        assert fd2.file_extension == ""

    def test_diff_result_empty(self):
        """边界：空的 DiffResult"""
        result = DiffResult()
        assert result.total_files_changed == 0
        assert result.total_additions == 0

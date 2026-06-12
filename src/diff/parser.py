"""
CodeCritic — Diff 解析器

职责：
  1. 解析 git unified diff 格式（或直接从两个代码字符串生成 diff）
  2. 提取变更行、上下文行、变更所在的函数/类范围
  3. 格式化为 LLM 友好的文本（标注 +/- 符号 + 行号）

使用场景：
  - PR/MR 审查：传入 git diff 输出即可
  - CLI diff 模式：python main.py diff --old old.py --new new.py
  - CI 集成：从 GitHub API 获取 PR diff 后传入

unified diff 格式示例：
    --- a/file.py
    +++ b/file.py
    @@ -10,7 +10,7 @@
     def get_user(id):
    -    return query("SELECT * FROM users WHERE id = " + id)
    +    return query("SELECT * FROM users WHERE id = ?", [id])
         ...
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# 数据结构
# ============================================================


@dataclass
class ChangedLine:
    """代码中的一行变更"""
    type: str                      # "add" | "delete" | "context"
    content: str                   # 行内容（不含 +/- 前缀）
    old_line_no: Optional[int] = None   # 旧文件中的行号
    new_line_no: Optional[int] = None   # 新文件中的行号


@dataclass
class Hunk:
    """diff 中的一个 hunk（一个连续的变更块）"""
    header: str                    # "@@ -old,new @@ context" 行
    old_start: int                 # hunk 在旧文件中的起始行
    old_count: int                 # hunk 在旧文件中占的行数
    new_start: int                 # hunk 在新文件中的起始行
    new_count: int                 # hunk 在新文件中占的行数
    section: str                   # hunk 所在的函数/类名（git 自动提取）
    lines: list[ChangedLine] = field(default_factory=list)

    @property
    def added_lines(self) -> list[ChangedLine]:
        return [l for l in self.lines if l.type == "add"]

    @property
    def deleted_lines(self) -> list[ChangedLine]:
        return [l for l in self.lines if l.type == "delete"]

    @property
    def has_changes(self) -> bool:
        return bool(self.added_lines or self.deleted_lines)


@dataclass
class FileDiff:
    """一个文件的完整 diff"""
    old_path: str                  # --- a/path
    new_path: str                  # +++ b/path
    hunks: list[Hunk] = field(default_factory=list)
    is_new_file: bool = False      # 是否新增文件（无旧文件）
    is_deleted_file: bool = False  # 是否删除文件

    @property
    def all_added_lines(self) -> list[ChangedLine]:
        return [l for h in self.hunks for l in h.added_lines]

    @property
    def all_deleted_lines(self) -> list[ChangedLine]:
        return [l for h in self.hunks for l in h.deleted_lines]

    @property
    def total_changes(self) -> int:
        return len(self.all_added_lines) + len(self.all_deleted_lines)

    @property
    def file_extension(self) -> str:
        return self.new_path.rsplit(".", 1)[-1] if "." in self.new_path else ""


@dataclass
class DiffResult:
    """完整的 diff 解析结果（可能包含多个文件）"""
    files: list[FileDiff] = field(default_factory=list)

    @property
    def total_files_changed(self) -> int:
        return len(self.files)

    @property
    def total_additions(self) -> int:
        return sum(f.total_changes for f in self.files)

    def get_file(self, path: str) -> Optional[FileDiff]:
        for f in self.files:
            if f.new_path == path or f.old_path == path:
                return f
        return None


# ============================================================
# 解析 unified diff 文本
# ============================================================

# @@ -old_start,old_count +new_start,new_count @@ section_name
_HUNK_HEADER_RE = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(?:\s+(.*))?"
)
# --- a/path
_OLD_FILE_RE = re.compile(r"^---\s+(?:a/)?(.+)")
# +++ b/path
_NEW_FILE_RE = re.compile(r"^\+\+\+\s+(?:b/)?(.+)")


def parse_diff(diff_text: str) -> DiffResult:
    """
    解析 git unified diff 文本。

    Args:
        diff_text: git diff 的输出文本

    Returns:
        DiffResult 包含所有文件的所有变更

    用法:
        diff = parse_diff(diff_text)
        for file in diff.files:
            for hunk in file.hunks:
                for line in hunk.added_lines:
                    print(f"+L{line.new_line_no}: {line.content}")
    """
    result = DiffResult()
    current_file: Optional[FileDiff] = None
    current_hunk: Optional[Hunk] = None
    old_line_no: int = 0
    new_line_no: int = 0

    for line in diff_text.splitlines():
        # --- 文件头 ---
        m = _OLD_FILE_RE.match(line)
        if m:
            old_path = m.group(1).strip()
            if current_file and current_hunk:
                current_file.hunks.append(current_hunk)
                current_hunk = None
            if current_file:
                result.files.append(current_file)
            # 检查是否已有同路径的 file diff（处理 rename 等情况）
            current_file = FileDiff(old_path=old_path, new_path=old_path)
            continue

        m = _NEW_FILE_RE.match(line)
        if m:
            new_path = m.group(1).strip()
            if current_file is None:
                # 只有 +++ 没有 ---，可能是新增文件
                current_file = FileDiff(old_path=new_path, new_path=new_path, is_new_file=True)
            else:
                current_file.new_path = new_path
            continue

        # --- hunk 头 ---
        m = _HUNK_HEADER_RE.match(line)
        if m:
            if current_file and current_hunk:
                current_file.hunks.append(current_hunk)

            old_start = int(m.group(1))
            old_count = int(m.group(2)) if m.group(2) else 1
            new_start = int(m.group(3))
            new_count = int(m.group(4)) if m.group(4) else 1
            section = (m.group(5) or "").strip()

            current_hunk = Hunk(
                header=line.strip(),
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                section=section,
            )
            old_line_no = old_start
            new_line_no = new_start
            continue

        # --- hunk 内容行 ---
        if current_hunk is None:
            continue

        if line.startswith("+"):
            current_hunk.lines.append(ChangedLine(
                type="add",
                content=line[1:],
                new_line_no=new_line_no,
            ))
            new_line_no += 1
        elif line.startswith("-"):
            current_hunk.lines.append(ChangedLine(
                type="delete",
                content=line[1:],
                old_line_no=old_line_no,
            ))
            old_line_no += 1
        elif line.startswith(" "):
            current_hunk.lines.append(ChangedLine(
                type="context",
                content=line[1:],
                old_line_no=old_line_no,
                new_line_no=new_line_no,
            ))
            old_line_no += 1
            new_line_no += 1
        elif line.startswith("\\"):  # \\ No newline at end of file
            continue

    # 收尾
    if current_file and current_hunk:
        current_file.hunks.append(current_hunk)
    if current_file:
        result.files.append(current_file)

    return result


# ============================================================
# 从两个代码字符串生成 diff
# ============================================================


def generate_diff(
    old_code: str,
    new_code: str,
    old_path: str = "old.py",
    new_path: str = "new.py",
    context_lines: int = 3,
) -> str:
    """
    从两段代码生成 unified diff 文本。

    Args:
        old_code: 旧版本的代码
        new_code: 新版本的代码
        old_path: 旧文件名（仅用于 diff 头部显示）
        new_path: 新文件名
        context_lines: 上下文行数

    Returns:
        unified diff 格式的文本（可直接传给 parse_diff）

    用法:
        diff_text = generate_diff(old_code, new_code)
        diff_result = parse_diff(diff_text)
    """
    old_lines = old_code.splitlines(keepends=True)
    new_lines = new_code.splitlines(keepends=True)

    diff_lines = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=old_path,
        tofile=new_path,
        n=context_lines,
    ))

    return "".join(diff_lines)


# ============================================================
# 格式化为 LLM prompt
# ============================================================


def format_diff_for_llm(diff_result: DiffResult) -> str:
    """
    将 diff 解析结果格式化为 LLM 友好的文本。

    输出格式：
      ## 文件: src/user.py
      ### 变更块 1: get_user() 函数 (L10-L17)

          10     def get_user(id):
      -   11         return query(f"SELECT * FROM users WHERE id = {id}")
      +   11         return query("SELECT * FROM users WHERE id = ?", [id])
          12         ...

    这样 LLM 能清晰看到：
      - "+" 行是新增的（重点关注）
      - "-" 行是删除的（不用管）
      - 无标记行是上下文（参考用）
    """
    parts = ["以下为代码变更内容（diff 格式）：\n"]

    for file_diff in diff_result.files:
        if file_diff.is_new_file:
            parts.append(f"## 📄 新增文件: {file_diff.new_path}\n")
        elif file_diff.is_deleted_file:
            parts.append(f"## 🗑️ 删除文件: {file_diff.old_path}\n")
            continue
        else:
            parts.append(f"## 📄 {file_diff.new_path}\n")

        parts.append("格式说明：")
        parts.append("  - ``+`` 开头的行 → **新增的代码**（请重点审查这些行）")
        parts.append("  - ``-`` 开头的行 → **删除的代码**（无需审查）")
        parts.append("  - 无前缀的行 → 上下文（仅供理解，不要对其提意见）")
        parts.append("")

        for hunk in file_diff.hunks:
            section_str = f" ({hunk.section})" if hunk.section else ""
            parts.append(f"### 变更块 L{hunk.old_start}-{hunk.old_start + hunk.old_count}{section_str}")
            parts.append("")

            # 计算行号显示宽度
            max_line = max(
                (l.new_line_no or 0) for l in hunk.lines
            ) if hunk.lines else 0
            width = len(str(max_line))

            for line in hunk.lines:
                if line.type == "add":
                    line_no = str(line.new_line_no or "").rjust(width)
                    parts.append(f"  + {line_no}  {line.content}")
                elif line.type == "delete":
                    line_no = str(line.old_line_no or "").rjust(width)
                    parts.append(f"  - {line_no}  {line.content}")
                else:
                    line_no = str(line.new_line_no or "").rjust(width)
                    parts.append(f"    {line_no}  {line.content}")

            parts.append("")

    return "\n".join(parts)


# ============================================================
# 构建 diff 感知的 Agent Prompt 模板
# ============================================================


def build_diff_review_prompt(
    diff_result: DiffResult,
    agent_system_prompt: str,
    agent_analysis_dimensions: str,
) -> list[dict]:
    """
    构建 diff 感知的审查消息。

    与 ``build_openai_messages`` 不同，这里：
    1. 用 diff 文本替换完整代码
    2. 在指令中明确告诉 Agent 只关注 "+" 行

    Returns:
        [system, user(code_diff), user(instruction)] 消息列表
    """
    diff_text = format_diff_for_llm(diff_result)

    review_instruction = (
        f"请审查以上代码变更（diff）。\n\n"
        f"**重要：** 只审查 ``+`` 开头的行（新增代码），"
        f"``-`` 行已被删除无需关注，上下文仅供参考。\n\n"
        f"请从以下角度分析：\n\n"
        f"{agent_analysis_dimensions}\n\n"
        f"如果你发现新增的代码存在问题，请明确指出。"
        f"如果变更内容不在你的专业范围内，请明确说明。"
        f"请严格按照要求的 JSON 格式输出审查结果。"
    )

    return [
        {"role": "system", "content": agent_system_prompt},
        {"role": "user", "content": diff_text},
        {"role": "user", "content": review_instruction},
    ]

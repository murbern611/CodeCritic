#!/usr/bin/env python3
"""
CodeCritic — 多智能体代码评审辩论系统
=====================================
基于 LangChain + LangGraph 的多 Agent 代码审查 CLI。

用法:
    python main.py --file path/to/code.py
    python main.py --code "def foo(): pass"
    python main.py --interactive
"""

from __future__ import annotations

import typer
from rich import print as rprint

from src.core.service import FileReadError, ReviewService
from src.utils.config_loader import load_env, load_settings
from src.utils.logger import setup_logger


# ── 初始化 ────────────────────────────────────────────────
load_env()
settings = load_settings()

# 主 CLI 应用
app = typer.Typer(
    name="codecritic",
    help="多智能体代码评审辩论系统",
    add_completion=False,
)


# ── CLI 回调 ──────────────────────────────────────────────


@app.callback()
def main_callback(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """全局回调"""
    if verbose:
        setup_logger(level="DEBUG", verbose=True)


# ── CLI 命令 ──────────────────────────────────────────────


@app.command()
def file(
    path: str = typer.Argument(..., help="代码文件路径"),
    skip_debate: bool = typer.Option(
        False, "--skip-debate", "-s", help="跳过辩论阶段"
    ),
    output: str = typer.Option(
        "markdown,json", "--output", "-o", help="输出格式"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """审查一个代码文件"""
    _print_banner()
    service = ReviewService()

    try:
        file_path = service.safe_path(path)
    except (FileNotFoundError, PermissionError, ValueError) as e:
        service.console.print(f"[red]❌ {e}[/]")
        raise typer.Exit(1)

    try:
        code = service.read_file(file_path)
    except FileReadError as e:
        service.console.print(f"[red]❌ {e}[/]")
        raise typer.Exit(1)
    ext = file_path.suffix.lstrip(".")
    service.console.print(
        f"🔍 正在加载 [bold]{file_path}[/] ({len(code)} 字符)"
    )

    result = service.run_review(
        code=code,
        language=ext,
        file_path=str(file_path),
        skip_debate=skip_debate,
        session_id=f"file:{file_path}",
    )

    output_formats = [f.strip() for f in output.split(",")]
    service.report_service.print_report(result, output_formats)


@app.command()
def diff_review(
    old_file: str = typer.Argument(..., help="旧版本文件路径"),
    new_file: str = typer.Argument(..., help="新版本文件路径"),
    skip_debate: bool = typer.Option(
        False, "--skip-debate", "-s", help="跳过辩论阶段"
    ),
    output: str = typer.Option(
        "markdown,json", "--output", "-o", help="输出格式"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """审查两个版本之间的代码变更（Diff 模式）"""
    _print_banner()
    service = ReviewService()

    try:
        old_path = service.safe_path(old_file)
        new_path = service.safe_path(new_file)
    except (FileNotFoundError, PermissionError, ValueError) as e:
        service.console.print(f"[red]❌ {e}[/]")
        raise typer.Exit(1)

    old_code = service.read_file(old_path)
    new_code = service.read_file(new_path)

    # 生成 diff 文本
    from src.diff.parser import generate_diff, format_diff_for_llm, parse_diff

    diff_text = generate_diff(
        old_code, new_code,
        old_path=str(old_path), new_path=str(new_path),
    )
    diff_result = parse_diff(diff_text)

    if not diff_result.files or diff_result.files[0].total_changes == 0:
        service.console.print("[yellow]⚠️ 两个版本之间没有差异[/]")
        raise typer.Exit(0)

    total_add = diff_result.files[0].all_added_lines
    total_del = diff_result.files[0].all_deleted_lines
    service.console.print(
        f"🔍 Diff 模式: [bold]{old_file}[/] → [bold]{new_file}[/]"
        f" ({len(total_add)} 行新增, {len(total_del)} 行删除)"
        f" | 智能体将仅审查新增代码"
    )

    # 生成 LLM 友好的 diff 文本
    llm_diff = format_diff_for_llm(diff_result)

    result = service.run_review(
        code=llm_diff,           # 传 diff 文本（但会被 diff_mode 覆盖）
        language="diff",
        file_path=f"{old_file} → {new_file}",
        skip_debate=skip_debate,
        session_id=f"diff:{old_file}:{new_file}",
        # ★ 注入 diff_mode 到 state
        diff_mode=True,
        diff_text=llm_diff,
    )

    output_formats = [f.strip() for f in output.split(",")]
    service.report_service.print_report(result, output_formats)


@app.command()
def git_diff(
    diff_file: str = typer.Argument(
        None, help="包含 git diff 输出的文件路径（不传则从 stdin 读取）"
    ),
    skip_debate: bool = typer.Option(
        False, "--skip-debate", "-s", help="跳过辩论阶段"
    ),
    output: str = typer.Option(
        "markdown,json", "--output", "-o", help="输出格式"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """从 git diff 审查代码变更（CI/CD 集成用）

    用法:
        git diff HEAD~1 > changes.diff
        python main.py git-diff changes.diff

        或通过管道:
        git diff HEAD~1 | python main.py git-diff
    """
    _print_banner()
    service = ReviewService()

    if diff_file:
        try:
            diff_path = service.safe_path(diff_file)
            diff_text = service.read_file(diff_path)
        except (FileNotFoundError, PermissionError, ValueError) as e:
            service.console.print(f"[red]❌ {e}[/]")
            raise typer.Exit(1)
    else:
        import sys
        diff_text = sys.stdin.read()
        if not diff_text.strip():
            service.console.print("[red]❌ 没有从 stdin 读取到 diff 内容[/]")
            service.console.print("用法: git diff HEAD~1 | python main.py git-diff")
            raise typer.Exit(1)

    from src.diff.parser import format_diff_for_llm, parse_diff
    diff_result = parse_diff(diff_text)

    if not diff_result.files:
        service.console.print("[yellow]⚠️ 未解析到有效的 diff 内容[/]")
        raise typer.Exit(0)

    total_change = sum(f.total_changes for f in diff_result.files)
    service.console.print(
        f"🔍 Git Diff 模式: {len(diff_result.files)} 个文件变更"
        f" ({total_change} 行变更) | 智能体将审查增量代码"
    )

    llm_diff = format_diff_for_llm(diff_result)

    result = service.run_review(
        code=llm_diff,
        language="diff",
        file_path=f"git-diff ({len(diff_result.files)} files)",
        skip_debate=skip_debate,
        session_id=f"git-diff:{diff_file or 'stdin'}",
        diff_mode=True,
        diff_text=llm_diff,
    )

    output_formats = [f.strip() for f in output.split(",")]
    service.report_service.print_report(result, output_formats)


@app.command()
def scan(
    path: str = typer.Argument(".", help="要扫描的目录或文件"),
    pattern: str = typer.Option(
        "*.py", "--pattern", "-p", help="文件匹配模式，如 *.py, *.js"
    ),
    recursive: bool = typer.Option(
        True, "--recursive", "-r", help="递归扫描子目录"
    ),
    skip_debate: bool = typer.Option(
        False, "--skip-debate", "-s", help="跳过辩论阶段"
    ),
    git_diff: Optional[str] = typer.Option(
        None, "--git-diff", help="仅审查 git diff 范围内的文件（如 HEAD~1）"
    ),
    output: str = typer.Option(
        "markdown,json", "--output", "-o", help="输出格式"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """批量扫描目录下的代码文件进行审查

    用法:
        python main.py scan ./src/                         # 扫描所有 .py 文件
        python main.py scan ./src/ --pattern "*.js"        # 扫描 JS 文件
        python main.py scan ./src/ --git-diff HEAD~1       # 仅审查最近变更的文件
    """
    _print_banner()
    service = ReviewService()

    try:
        scan_path = service.safe_path(path, must_exist=True)
    except (FileNotFoundError, PermissionError, ValueError) as e:
        service.console.print(f"[red]❌ {e}[/]")
        raise typer.Exit(1)

    # 收集要审查的文件
    if scan_path.is_file():
        files_to_review = [scan_path]
    else:
        glob_pattern = "**/" + pattern if recursive else pattern
        files_to_review = sorted(scan_path.glob(glob_pattern))

    if not files_to_review:
        service.console.print(f"[yellow]⚠️ 未找到匹配 {pattern} 的文件[/]")
        raise typer.Exit(0)

    # 如果指定了 --git-diff，过滤出有变更的文件
    if git_diff:
        import subprocess
        try:
            diff_output = subprocess.run(
                ["git", "diff", "--name-only", git_diff],
                capture_output=True, text=True, check=True,
            )
            changed_files = set(diff_output.stdout.strip().splitlines())
            files_to_review = [
                f for f in files_to_review
                if str(f.relative_to(scan_path if scan_path.is_dir() else scan_path.parent)) in changed_files
            ]
            service.console.print(
                f"🔍 Git diff ({git_diff}) 过滤: {len(files_to_review)} 个文件有变更"
            )
        except subprocess.CalledProcessError as e:
            service.console.print(f"[red]❌ git diff 执行失败: {e}[/]")
            raise typer.Exit(1)

    service.console.print(f"📂 发现 [bold]{len(files_to_review)}[/] 个文件，开始审查...\n")

    for i, file_path in enumerate(files_to_review, 1):
        try:
            code = service.read_file(file_path)
        except FileReadError:
            service.console.print(f"  [{i}/{len(files_to_review)}] ⚠️  {file_path} — 跳过（读取失败）")
            continue

        ext = file_path.suffix.lstrip(".")
        service.console.print(f"  [{i}/{len(files_to_review)}] 🔍 {file_path} ({len(code)} 字符)")

        result = service.run_review(
            code=code,
            language=ext,
            file_path=str(file_path),
            skip_debate=skip_debate,
            session_id=f"scan:{file_path}",
        )

        output_formats_list = [f.strip() for f in output.split(",")]
        service.report_service.print_report(result, output_formats_list)
        service.console.print("\n" + "=" * 50 + "\n")


@app.command()
def code(
    text: str = typer.Argument(..., help="要审查的代码"),
    language: str = typer.Option("python", "--lang", "-l", help="代码语言"),
    skip_debate: bool = typer.Option(
        False, "--skip-debate", "-s", help="跳过辩论阶段"
    ),
    output: str = typer.Option(
        "markdown,json", "--output", "-o", help="输出格式"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
):
    """直接传入代码字符串审查"""
    _print_banner()
    service = ReviewService()
    service.console.print(
        f"🔍 审查代码片段 ({len(text)} 字符, {language})"
    )

    result = service.run_review(code=text, language=language)

    output_formats = [f.strip() for f in output.split(",")]
    service.report_service.print_report(result, output_formats)


@app.command()
def interactive(
    skip_debate: bool = typer.Option(
        False, "--skip-debate", "-s", help="跳过辩论阶段"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="详细输出"),
    no_memory: bool = typer.Option(
        False, "--no-memory", help="禁用记忆功能"
    ),
):
    """交互模式"""
    _print_banner()
    service = ReviewService()

    session_id = f"interactive_{int(time.time())}"
    memory_enabled = not no_memory
    if memory_enabled:
        service.console.print(
            f"🧠 记忆已启用 (session: {session_id[-8:]})"
        )
    service.console.print("输入代码或文件路径（输入 'exit' 退出）\n")

    while True:
        try:
            user_input = service.console.input("[bold cyan]>> [/]")
        except (EOFError, KeyboardInterrupt):
            service.console.print("\n再见！")
            break

        if user_input.lower() in ("exit", "quit", "q"):
            break

        if user_input.lower().startswith("file:"):
            path = user_input[5:].strip()
            try:
                file_path = service.safe_path(path)
            except (FileNotFoundError, PermissionError, ValueError) as e:
                service.console.print(f"[red]❌ {e}[/]")
                continue

            try:
                code_content = service.read_file(file_path)
            except FileReadError as e:
                service.console.print(f"[red]❌ {e}[/]")
                continue
            ext = file_path.suffix.lstrip(".")
            service.console.print(
                f"  加载文件: {path} ({len(code_content)} 字符)"
            )
            result = service.run_review(
                code=code_content,
                language=ext,
                file_path=path,
                skip_debate=skip_debate,
                session_id=session_id,
                memory_enabled=memory_enabled,
            )
        else:
            result = service.run_review(
                code=user_input,
                language="python",
                skip_debate=skip_debate,
                session_id=session_id,
                memory_enabled=memory_enabled,
            )

        service.report_service.print_report(result, ["markdown", "json"])
        service.console.print("\n" + "=" * 50 + "\n")


@app.command()
def version():
    """显示版本信息"""
    service = ReviewService()
    ver = settings.get("project", {}).get("version", "0.1.0")
    service.console.print(f"[bold cyan]CodeCritic[/] v{ver}")
    service.console.print("多智能体代码评审辩论系统")
    service.console.print("基于 LangChain + LangGraph")


# ── 辅助函数 ──────────────────────────────────────────────


def _print_banner():
    """打印启动 Banner（纯展示，与业务逻辑无关）"""
    banner = """
╔══════════════════════════════════════════════╗
║        CodeCritic 🧠⚡                        ║
║     多智能体代码评审辩论系统                   ║
╚══════════════════════════════════════════════╝
    """
    rprint(f"[bold cyan]{banner}[/bold cyan]")


if __name__ == "__main__":
    app()

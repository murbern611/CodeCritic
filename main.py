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

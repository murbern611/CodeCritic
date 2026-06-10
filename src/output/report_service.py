"""
CodeCritic — 报告输出服务
==========================
将报告格式化、控制台输出和文件持久化职责分离，
避免 ``_print_report`` 单一函数承担过多职责。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import print as rprint

from src.utils.logger import logger


class ReportService:
    """
    报告输出服务。

    职责（单一职责原则）：
    - 格式化报告为终端文本 / Markdown / JSON / HTML
    - 输出到控制台
    - 持久化到文件

    使用方式:
        service = ReportService(settings, console)
        service.print_report(state, ["markdown", "json"])
    """

    def __init__(
        self,
        settings: dict[str, Any],
        console: Optional[Console] = None,
    ):
        self.settings = settings
        self.console = console or Console()

    # ── 主入口（协调方法）─────────────────────────────────────

    def print_report(
        self,
        state: dict[str, Any],
        output_formats: list[str],
    ) -> None:
        """输出最终报告：控制台显示 + 文件保存。"""
        report = state.get("final_report")
        if not report:
            self.console.print("[red]未生成报告[/]")
            return

        self._print_summary(report)
        self._print_findings(report)
        self._print_recommendations(report)
        self._print_token_summary(state)
        self._save_files(report, output_formats)

    # ── 控制台输出 ────────────────────────────────────────────

    def _print_summary(self, report: Any) -> None:
        """打印执行摘要"""
        self.console.print(Panel(
            f"[bold]总体评分: {report.overall_score}/10[/]\n\n"
            f"{report.summary}\n\n"
            f"[dim]共 {len(report.all_findings)} 个 finding，"
            f"{len(report.recommendations)} 条建议[/]",
            title="执行摘要",
            border_style="green",
        ))

    def _print_findings(self, report: Any) -> None:
        """打印评审结果表格"""
        if not report.all_findings:
            return

        table = Table(title="评审结果详表")
        table.add_column("严重度", style="bold")
        table.add_column("来源", style="cyan")
        table.add_column("标题", style="white")
        table.add_column("位置")
        table.add_column("状态")

        SEV_COLORS = {
            "critical": "red",
            "high": "orange1",
            "medium": "yellow",
            "low": "blue",
            "info": "dim",
        }

        for f in report.all_findings:
            sev = f.original_finding.severity.value
            sev_color = SEV_COLORS.get(sev, "white")
            loc = f.original_finding.location
            line_str = ""
            if loc.line_start:
                line_str = f"L{loc.line_start}"
                if loc.line_end and loc.line_end != loc.line_start:
                    line_str += f"-{loc.line_end}"

            status = "✅" if f.verdict.value == "upheld" else "⚠️"
            if f.was_disputed:
                status += " 💬"

            table.add_row(
                f"[{sev_color}]{sev.upper()}[/]",
                f.source_agent,
                f.original_finding.title,
                line_str,
                status,
            )

        self.console.print(table)

    def _print_recommendations(self, report: Any) -> None:
        """打印行动建议"""
        if not report.recommendations:
            return
        self.console.print("\n[bold]行动建议[/]")
        for i, rec in enumerate(report.recommendations, 1):
            self.console.print(f"  {i}. {rec}")

    def _print_token_summary(self, state: dict[str, Any]) -> None:
        """打印 Token 消耗汇总"""
        usage = state.get("usage_summary")
        if not usage:
            return

        table = Table(title="Token 消耗报告")
        table.add_column("项目", style="cyan")
        table.add_column("输入 Token", justify="right")
        table.add_column("输出 Token", justify="right")
        table.add_column("总计", justify="right")
        table.add_column("费用 ($)", justify="right")

        for agent, u in sorted(usage.by_agent.items()):
            table.add_row(
                agent,
                str(u.prompt_tokens),
                str(u.completion_tokens),
                str(u.total_tokens),
                f"{u.cost_usd:.4f}",
            )

        for phase, u in sorted(usage.by_phase.items()):
            table.add_row(
                f"  [dim]阶段: {phase}[/]",
                str(u.prompt_tokens),
                str(u.completion_tokens),
                str(u.total_tokens),
                f"{u.cost_usd:.4f}",
            )

        table.add_row(
            "[bold]总计[/]",
            str(usage.total.prompt_tokens),
            str(usage.total.completion_tokens),
            f"[bold]{usage.total.total_tokens}[/]",
            f"[bold green]${usage.total.cost_usd:.4f}[/]",
        )
        self.console.print(table)

    # ── 文件保存 ──────────────────────────────────────────────

    def _save_files(self, report: Any, output_formats: list[str]) -> None:
        """将报告持久化到文件"""
        report_dir = Path(
            self.settings.get("output", {}).get("report_dir", "./data/reports")
        )
        report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y%m%d_%H%M%S")

        for fmt in output_formats:
            if fmt == "markdown":
                self._save_markdown(report, report_dir / f"report_{timestamp}.md")
            elif fmt == "json":
                self._save_json(report, report_dir / f"report_{timestamp}.json")
            elif fmt == "html":
                self._save_html(report, report_dir / f"report_{timestamp}.html")

        self.console.print(f"\n[green]📄 报告已保存至: {report_dir}[/]")

    @staticmethod
    def _save_markdown(report: Any, path: Path) -> None:
        """保存 Markdown 格式报告"""
        lines = ["# CodeCritic 代码评审报告\n"]
        lines.append(f"**总体评分:** {report.overall_score}/10\n")
        lines.append(f"**摘要:** {report.summary}\n")
        lines.append("---\n")
        lines.append("## 评审结果\n")
        lines.append("| 严重度 | 来源 | 标题 | 位置 | 状态 |")
        lines.append("|--------|------|------|------|------|")
        for f in report.all_findings:
            loc = f.original_finding.location
            location = f"L{loc.line_start}" if loc and loc.line_start else "-"
            status = "已确认" if f.verdict.value == "upheld" else "存疑"
            if f.was_disputed:
                status += " (辩论)"
            lines.append(
                f"| {f.original_finding.severity.value} | {f.source_agent} "
                f"| {f.original_finding.title} | {location} | {status} |"
            )
        if report.recommendations:
            lines.append("\n## 行动建议\n")
            for i, rec in enumerate(report.recommendations, 1):
                lines.append(f"{i}. {rec}")
        lines.append("\n---\n")
        lines.append("*由 CodeCritic 自动生成*")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logger.info(f"Markdown 报告已保存: {path}")

    @staticmethod
    def _save_json(report: Any, path: Path) -> None:
        """保存 JSON 格式报告"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                report.model_dump(), f, ensure_ascii=False, indent=2, default=str
            )
        logger.info(f"JSON 报告已保存: {path}")

    @staticmethod
    def _save_html(report: Any, path: Path) -> None:
        """保存 HTML 格式报告"""
        score = report.overall_score
        score_color = "green" if score >= 7 else "orange" if score >= 4 else "red"

        rows = ""
        for f in report.all_findings:
            sev_class = f.original_finding.severity.value
            loc = f.original_finding.location
            location = f"L{loc.line_start}" if loc and loc.line_start else "-"
            status = "已确认" if f.verdict.value == "upheld" else "存疑"
            if f.was_disputed:
                status += " (辩论)"
            rows += (
                f'<tr><td class="{sev_class}">{sev_class.upper()}</td>'
                f"<td>{f.source_agent}</td>"
                f"<td>{f.original_finding.title}</td>"
                f"<td>{location}</td><td>{status}</td></tr>\n"
            )

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>CodeCritic 代码评审报告</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; max-width: 960px; margin: 0 auto; padding: 20px; }}
.score {{ font-size: 2em; font-weight: bold; color: {score_color}; }}
table {{ border-collapse: collapse; width: 100%; margin: 16px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #f5f5f5; }}
.critical {{ color: red; font-weight: bold; }}
.high {{ color: darkorange; font-weight: bold; }}
.medium {{ color: goldenrod; }}
.low {{ color: steelblue; }}
.info {{ color: gray; }}
</style>
</head>
<body>
<h1>🧠 CodeCritic 代码评审报告</h1>
<p>总体评分: <span class="score">{score}/10</span></p>
<p>{report.summary}</p>
<hr>
<h2>评审结果</h2>
<table>
<tr><th>严重度</th><th>来源</th><th>标题</th><th>位置</th><th>状态</th></tr>
{rows}
</table>
</body>
</html>"""

        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info(f"HTML 报告已保存: {path}")

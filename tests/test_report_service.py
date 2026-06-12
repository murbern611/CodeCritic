"""
测试报告输出服务
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from src.output.report_service import ReportService
from src.models.schemas import (
    CodeLocation,
    CodeSuggestion,
    FinalReport,
    FinalReportFinding,
    SecurityFinding,
    Severity,
    TokenUsage,
    UsageSummary,
    Verdict,
)


@pytest.fixture
def sample_report() -> FinalReport:
    """带样本数据的 FinalReport"""
    finding = SecurityFinding(
        id="sec-1",
        severity=Severity.CRITICAL,
        title="SQL 注入风险",
        description="使用了 f-string 拼接 SQL 查询",
        location=CodeLocation(line_start=10, line_end=12, snippet='f"SELECT...'),
        suggestion=CodeSuggestion(description="使用参数化查询"),
        vulnerability_type="SQL Injection",
    )
    report_finding = FinalReportFinding(
        original_finding=finding,
        source_agent="security_expert",
        verdict=Verdict.UPHELD,
    )
    return FinalReport(
        summary="发现 1 个安全问题",
        overall_score=6.5,
        all_findings=[report_finding],
        recommendations=["修复 SQL 注入漏洞"],
    )


@pytest.fixture
def report_service(mock_settings: dict) -> ReportService:
    console = Console(force_terminal=True, width=120)
    return ReportService(mock_settings, console)


class TestPrintSummary:
    """测试摘要输出"""

    def test_print_summary(self, report_service: ReportService, sample_report: FinalReport):
        """基础：打印摘要不应抛异常"""
        # 无法断言输出内容，但至少不能抛异常
        report_service._print_summary(sample_report)

    def test_print_summary_empty(self, report_service: ReportService):
        """边界：空报告"""
        report = FinalReport(summary="", overall_score=0.0)
        report_service._print_summary(report)


class TestPrintFindings:
    """测试评审结果表输出"""

    def test_print_findings(self, report_service: ReportService, sample_report: FinalReport):
        """基础：打印 findings 不抛异常"""
        report_service._print_findings(sample_report)

    def test_print_findings_empty(self, report_service: ReportService):
        """边界：无 findings"""
        report = FinalReport(summary="test", overall_score=5.0)
        report_service._print_findings(report)


class TestPrintTokenSummary:
    """测试 Token 汇总输出"""

    def test_print_token_summary(self, report_service: ReportService):
        """基础：打印 Token 汇总"""
        state = {
            "usage_summary": UsageSummary(
                by_agent={"security": TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)},
                by_phase={"review": TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)},
                total=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            )
        }
        report_service._print_token_summary(state)

    def test_print_token_summary_empty(self, report_service: ReportService):
        """边界：无用量数据"""
        report_service._print_token_summary({})


class TestSaveFiles:
    """测试文件保存"""

    def test_save_markdown(self, report_service: ReportService, sample_report: FinalReport, temp_dir: Path):
        """核心：保存 Markdown"""
        path = temp_dir / "report.md"
        report_service._save_markdown(sample_report, path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "SQL 注入" in content
        assert "总体评分" in content

    def test_save_json(self, report_service: ReportService, sample_report: FinalReport, temp_dir: Path):
        """核心：保存 JSON"""
        path = temp_dir / "report.json"
        report_service._save_json(sample_report, path)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["overall_score"] == 6.5
        assert len(data["all_findings"]) == 1

    def test_save_html(self, report_service: ReportService, sample_report: FinalReport, temp_dir: Path):
        """核心：保存 HTML"""
        path = temp_dir / "report.html"
        report_service._save_html(sample_report, path)
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "SQL 注入" in content
        assert "CodeCritic" in content

    def test_save_all_formats(self, report_service: ReportService, sample_report: FinalReport, temp_dir: Path):
        """集成：多格式输出"""
        mock_settings = {"output": {"report_dir": str(temp_dir)}}
        service = ReportService(mock_settings)

        # 直接注入 report
        state = {"final_report": sample_report}
        service.print_report(state, ["markdown", "json", "html"])

        # 应该生成至少一个文件
        files = list(temp_dir.glob("report_*"))
        # 可能有多个，但至少应该有一个
        assert len([f for f in temp_dir.iterdir() if f.name.startswith("report_")]) >= 1


class TestPrintReport:
    """测试完整报告输出"""

    def test_print_report_no_result(self, report_service: ReportService):
        """边界：无报告"""
        report_service.print_report({}, ["markdown"])

    def test_print_report_with_data(self, report_service: ReportService, sample_report: FinalReport):
        """核心：完整流程"""
        state = {"final_report": sample_report}
        # 不应抛异常
        report_service.print_report(state, ["markdown"])

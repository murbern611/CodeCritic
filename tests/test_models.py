"""
测试 Pydantic 数据模型
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.models.schemas import (
    AgentReview,
    ArchitectureFinding,
    BaseFinding,
    CodeLocation,
    CodeSuggestion,
    ConflictPair,
    CorrectnessFinding,
    DebateArgument,
    DebateResult,
    DebateRound,
    FinalReport,
    FinalReportFinding,
    FindingCategory,
    JudgeReport,
    PerformanceFinding,
    SecurityFinding,
    Severity,
    StyleFinding,
    TokenUsage,
    UsageSummary,
    Verdict,
    get_finding_model,
)


class TestEnums:
    """测试枚举类型"""

    def test_severity_values(self):
        """基础：严重级别枚举"""
        assert Severity.CRITICAL.value == "critical"
        assert Severity.HIGH.value == "high"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.LOW.value == "low"
        assert Severity.INFO.value == "info"

    def test_verdict_values(self):
        """基础：裁定枚举"""
        assert Verdict.UPHELD.value == "upheld"
        assert Verdict.REJECTED.value == "rejected"
        assert Verdict.COMPROMISE.value == "compromise"

    def test_finding_category_values(self):
        """基础：类别枚举"""
        assert FindingCategory.SECURITY.value == "security"
        assert FindingCategory.ARCHITECTURE.value == "architecture"


class TestCodeLocation:
    """测试代码位置模型"""

    def test_minimal_creation(self):
        """基础：仅必填字段"""
        loc = CodeLocation()
        assert loc.file is None
        assert loc.line_start is None

    def test_full_creation(self):
        """核心：所有字段"""
        loc = CodeLocation(file="test.py", line_start=10, line_end=20, snippet='x = 1')
        assert loc.file == "test.py"
        assert loc.line_start == 10
        assert loc.line_end == 20
        assert loc.snippet == 'x = 1'


class TestTokenUsage:
    """测试 Token 使用模型"""

    def test_default_values(self):
        """基础：默认值"""
        usage = TokenUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0
        assert usage.cost_usd == 0.0

    def test_total_auto_calculation(self):
        """注意：total_tokens 需要手动设置，Pydantic 不自动计算"""
        usage = TokenUsage(prompt_tokens=100, completion_tokens=50)
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        # total_tokens 不会自动求和
        assert usage.total_tokens == 0


class TestFindingModels:
    """测试各类 Finding 模型"""

    def test_base_finding(self):
        """基础：BaseFinding"""
        finding = BaseFinding(
            severity=Severity.HIGH,
            title="测试问题",
            description="测试描述",
        )
        assert finding.severity == Severity.HIGH
        assert finding.title == "测试问题"
        assert finding.get_category() == "base"

    def test_security_finding(self):
        """核心：SecurityFinding"""
        finding = SecurityFinding(
            severity=Severity.CRITICAL,
            title="SQL 注入",
            description="存在注入风险",
            vulnerability_type="SQL Injection",
        )
        assert finding.get_category() == "security"
        assert finding.vulnerability_type == "SQL Injection"

    def test_performance_finding(self):
        """核心：PerformanceFinding"""
        finding = PerformanceFinding(
            severity=Severity.MEDIUM,
            title="低效循环",
            description="O(n²) 复杂度",
            complexity="O(n²)",
            estimated_impact="高",
        )
        assert finding.get_category() == "performance"
        assert finding.complexity == "O(n²)"

    def test_style_finding(self):
        """核心：StyleFinding"""
        finding = StyleFinding(
            severity=Severity.LOW,
            title="命名不规范",
            description="变量名应为 snake_case",
            rule_reference="PEP8",
        )
        assert finding.get_category() == "style"
        assert finding.rule_reference == "PEP8"

    def test_correctness_finding(self):
        """核心：CorrectnessFinding"""
        finding = CorrectnessFinding(
            severity=Severity.HIGH,
            title="边界条件缺失",
            description="空列表会崩溃",
            scenario="输入为空列表时",
        )
        assert finding.get_category() == "correctness"
        assert finding.scenario == "输入为空列表时"

    def test_architecture_finding(self):
        """核心：ArchitectureFinding"""
        finding = ArchitectureFinding(
            severity=Severity.MEDIUM,
            title="循环依赖",
            description="A 和 B 互相引用",
            principle="依赖倒置原则",
        )
        assert finding.get_category() == "architecture"
        assert finding.principle == "依赖倒置原则"

    def test_finding_with_location_and_suggestion(self):
        """核心：含位置和建议的 Finding"""
        finding = SecurityFinding(
            severity=Severity.CRITICAL,
            title="SQL 注入",
            description="存在注入风险",
            location=CodeLocation(line_start=10, line_end=12, snippet='query = f"..."'),
            suggestion=CodeSuggestion(description="使用参数化查询", code_example="cursor.execute('SELECT...')"),
            vulnerability_type="SQL Injection",
        )
        assert finding.location.line_start == 10
        assert finding.suggestion.description == "使用参数化查询"

    def test_finding_requires_severity_title_description(self):
        """约束：必填字段缺失应报错"""
        with pytest.raises(ValidationError):
            SecurityFinding()  # 缺少 severity, title, description

    def test_get_finding_model_valid(self):
        """工具函数：正确映射"""
        assert get_finding_model("SecurityFinding") == SecurityFinding
        assert get_finding_model("PerformanceFinding") == PerformanceFinding
        assert get_finding_model("StyleFinding") == StyleFinding

    def test_get_finding_model_invalid(self):
        """工具函数：未知映射应报错"""
        with pytest.raises(ValueError, match="Unknown schema"):
            get_finding_model("UnknownFinding")


class TestAgentReview:
    """测试 AgentReview 模型"""

    def test_minimal_creation(self):
        """基础：最小创建"""
        review = AgentReview(
            agent_name="test",
            agent_label="测试",
            model_used="gpt-4o-mini",
        )
        assert review.agent_name == "test"
        assert review.confidence == 1.0  # 默认值
        assert review.overall_score == 0.0  # 默认值
        assert review.findings == []  # 默认值

    def test_with_findings(self, sample_agent_reviews: dict):
        """核心：携带 findings"""
        review = sample_agent_reviews["security_expert"]
        assert len(review.findings) == 1
        assert review.findings[0].title == "SQL 注入风险"

    def test_score_range(self):
        """约束：score 应在 0-10 范围"""
        with pytest.raises(ValidationError):
            AgentReview(
                agent_name="test",
                agent_label="测试",
                model_used="gpt-4o-mini",
                overall_score=15.0,  # 超过范围
            )


class TestConflictDetection:
    """测试分歧检测相关模型"""

    def test_conflict_pair(self):
        """基础：ConflictPair 创建"""
        conflict = ConflictPair(
            finding_a_id="sec-1",
            finding_b_id="perf-1",
            agent_a="security_expert",
            agent_b="performance_expert",
            description="对 eval() 安全性意见不一致",
            severity=Severity.HIGH,
        )
        assert conflict.agent_a == "security_expert"
        assert conflict.agent_b == "performance_expert"

    def test_judge_report_no_conflict(self):
        """基础：无分歧状态"""
        report = JudgeReport()
        assert not report.has_conflict
        assert report.conflicts_found == []

    def test_judge_report_with_conflicts(self):
        """核心：有分歧状态"""
        conflict = ConflictPair(
            finding_a_id="a-1", finding_b_id="b-1",
            agent_a="agent_a", agent_b="agent_b",
            description="冲突描述", severity=Severity.MEDIUM,
        )
        report = JudgeReport(
            total_pairs_compared=10,
            conflicts_found=[conflict],
            has_conflict=True,
            summary="发现 1 处分歧",
        )
        assert report.has_conflict
        assert len(report.conflicts_found) == 1


class TestDebateModels:
    """测试辩论相关模型"""

    def test_debate_argument(self):
        """基础：辩论论点"""
        arg = DebateArgument(
            speaker="security_expert",
            target_finding_id="sec-1",
            position="refute",
            argument="我认为这里确实存在风险",
        )
        assert not arg.concedes  # 默认值

    def test_debate_round(self):
        """核心：辩论轮次"""
        arg = DebateArgument(
            speaker="agent_a", target_finding_id="f-1",
            position="support", argument="同意",
        )
        round_data = DebateRound(
            round_number=1,
            conflict_pair=ConflictPair(
                finding_a_id="f-1", finding_b_id="f-2",
                agent_a="a", agent_b="b",
                description="test", severity=Severity.LOW,
            ),
            arguments=[arg],
            summary="第 1 轮辩论",
        )
        assert round_data.round_number == 1
        assert len(round_data.arguments) == 1

    def test_debate_result(self):
        """核心：辩论结果"""
        result = DebateResult(
            total_rounds=2,
            is_converged=True,
        )
        assert result.is_converged
        assert result.total_rounds == 2


class TestFinalReport:
    """测试最终报告模型"""

    def test_minimal_report(self):
        """基础：最小报告"""
        report = FinalReport(
            summary="测试报告",
            overall_score=7.5,
        )
        assert report.summary == "测试报告"
        assert report.overall_score == 7.5
        assert report.all_findings == []
        assert report.recommendations == []

    def test_report_with_findings(self, sample_agent_reviews: dict):
        """核心：携带 findings 的报告"""
        finding = sample_agent_reviews["security_expert"].findings[0]
        report_finding = FinalReportFinding(
            original_finding=finding,
            source_agent="security_expert",
            verdict=Verdict.UPHELD,
        )
        report = FinalReport(
            summary="发现 1 个问题",
            overall_score=6.0,
            all_findings=[report_finding],
            recommendations=["修复 SQL 注入"],
        )
        assert len(report.all_findings) == 1
        assert not report_finding.was_disputed
        assert report.recommendations[0] == "修复 SQL 注入"

    def test_score_range(self):
        """约束：超出范围的分数"""
        with pytest.raises(ValidationError):
            FinalReport(summary="test", overall_score=11.0)

    def test_recommendations_limit(self):
        """基础：建议列表"""
        report = FinalReport(
            summary="test",
            overall_score=5.0,
            recommendations=[f"建议{i}" for i in range(20)],
        )
        assert len(report.recommendations) == 20  # 不限制长度


class TestUsageSummary:
    """测试用量汇总模型"""

    def test_empty_summary(self):
        """基础：空汇总"""
        summary = UsageSummary()
        assert summary.by_agent == {}
        assert summary.by_phase == {}
        assert summary.total.total_tokens == 0

    def test_with_data(self):
        """核心：含数据"""
        summary = UsageSummary(
            by_agent={
                "security": TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            },
            by_phase={
                "review": TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
            },
            total=TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150),
        )
        assert summary.by_agent["security"].total_tokens == 150
        assert summary.total.total_tokens == 150

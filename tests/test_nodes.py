"""
测试 LangGraph 节点函数

注意：这些测试测试的是节点函数的逻辑和数据变换，不调用 LLM。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.graph.nodes import (
    MAX_DEBATE_ROUNDS,
    _apply_model_overrides,
    _format_memory_context,
    arbitrate,
    create_agents,
    parse_code,
)
from src.graph.state import CodeCriticState
from src.models.schemas import (
    AgentReview,
    ConflictPair,
    DebateResult,
    DebateRound,
    FinalReport,
    SecurityFinding,
    Severity,
    CodeLocation,
    CodeSuggestion,
)


class TestParseCode:
    """测试代码解析节点"""

    def test_parse_with_code(self):
        """核心：从 state 中的 code 字段解析"""
        state: CodeCriticState = {
            "code": "def foo(): pass",
            "code_language": None,
            "context": {"language": "python"},
        }
        result = parse_code(state)
        assert result["code"] == "def foo(): pass"
        assert result["code_language"] == "python"

    def test_parse_empty_code(self):
        """边界：无代码——parse_code 仅解析，不校验"""
        state: CodeCriticState = {
            "code": "",
            "code_language": None,
            "context": {},
        }
        result = parse_code(state)
        # parse_code 只做语言/文件解析，不检查代码是否为空
        # review_code 节点会检查空代码
        assert "code" in result
        assert result["code"] == ""

    def test_parse_language_from_context(self):
        """核心：从 context 获取语言"""
        state: CodeCriticState = {
            "code": "print(1)",
            "code_language": None,
            "context": {"language": "python"},
        }
        result = parse_code(state)
        assert result["code_language"] == "python"


class TestApplyModelOverrides:
    """测试模型覆盖配置"""

    def test_no_overrides(self):
        """基础：无覆盖不变"""
        models = {"gpt-4o": {"api_key": "key1"}}
        agents = {"security": {"model": "gpt-4o"}}
        state: CodeCriticState = {}

        _apply_model_overrides(models, agents, state)
        assert models["gpt-4o"]["api_key"] == "key1"
        assert agents["security"]["model"] == "gpt-4o"

    def test_model_config_overrides(self):
        """核心：覆盖 API Key"""
        models = {"gpt-4o": {"api_key": "old_key", "base_url": ""}}
        agents = {}
        state: CodeCriticState = {
            "model_configs": {
                "gpt-4o": {"api_key": "new_key", "base_url": "https://custom.com"},
            }
        }

        _apply_model_overrides(models, agents, state)
        assert models["gpt-4o"]["api_key"] == "new_key"
        assert models["gpt-4o"]["base_url"] == "https://custom.com"

    def test_agent_model_override(self):
        """核心：覆盖 Agent 使用的模型"""
        models = {"gpt-4o": {}, "gpt-4o-mini": {}}
        agents = {"security": {"model": "gpt-4o-mini"}}
        state: CodeCriticState = {
            "agent_models": {"security": "gpt-4o"},
        }

        _apply_model_overrides(models, agents, state)
        assert agents["security"]["model"] == "gpt-4o"

    def test_custom_models_added(self):
        """核心：新增自定义模型"""
        models = {}
        agents = {}
        state: CodeCriticState = {
            "custom_models": {
                "my-model": {"provider": "openai", "model_name": "my-model", "api_key": "key"},
            }
        }

        _apply_model_overrides(models, agents, state)
        assert "my-model" in models
        assert models["my-model"]["api_key"] == "key"


class TestCreateAgents:
    """测试 Agent 工厂"""

    def test_create_default_agents(self):
        """核心：创建预设 Agent"""
        agents = create_agents()
        assert len(agents) > 0
        # 默认应该创建 security, performance, style, correctness, architecture
        assert "security_expert" in agents
        assert "performance_expert" in agents
        assert "style_expert" in agents
        assert "correctness_expert" in agents
        assert "architecture_expert" in agents

    def test_create_with_custom_config(self):
        """核心：自定义配置创建"""
        config = {
            "security_expert": {
                "name": "安全审查专家",
                "model": "gpt-4o-mini",
                "temperature": 0.2,
                "enabled": True,
                "max_tokens": 4096,
            },
            "performance_expert": {
                "name": "性能优化专家",
                "model": "gpt-4o-mini",
                "temperature": 0.1,
                "enabled": True,
                "max_tokens": 4096,
            },
        }
        agents = create_agents(config)
        assert "security_expert" in agents
        assert "performance_expert" in agents

    def test_disabled_agent_not_created(self):
        """约束：禁用的 Agent 不创建"""
        config = {
            "security_expert": {
                "name": "安全",
                "model": "gpt-4o-mini",
                "enabled": False,
                "temperature": 0.2,
                "max_tokens": 4096,
            },
        }
        agents = create_agents(config)
        assert "security_expert" not in agents

    def test_system_agents_skipped(self):
        """约束：系统 Agent（judge/arbiter）不被创建为审查 Agent"""
        config = {
            "judge": {"name": "分歧检测器", "model": "gpt-4o-mini", "enabled": True, "temperature": 0.0, "max_tokens": 2048},
            "security_expert": {"name": "安全", "model": "gpt-4o-mini", "enabled": True, "temperature": 0.2, "max_tokens": 4096},
        }
        agents = create_agents(config)
        assert "judge" not in agents
        assert "security_expert" in agents


class TestFormatMemoryContext:
    """测试记忆上下文格式化"""

    def test_empty_report(self):
        """边界：空报告"""
        result = _format_memory_context({})
        assert result is None

    def test_empty_findings(self):
        """边界：无 findings"""
        result = _format_memory_context({"all_findings": []})
        assert result is None

    def test_format_with_findings(self):
        """核心：格式化包含 finding 的报告"""
        report = {
            "all_findings": [
                {
                    "original_finding": {
                        "severity": "critical",
                        "title": "SQL 注入",
                        "description": "存在注入风险",
                        "location": {"line_start": 42, "line_end": 42},
                    },
                    "source_agent": "security_expert",
                    "verdict": "upheld",
                    "was_disputed": False,
                }
            ]
        }
        result = _format_memory_context(report)
        assert result is not None
        assert "SQL 注入" in result
        assert "CRITICAL" in result
        assert "security_expert" in result


class TestArbitrate:
    """测试仲裁节点"""

    def test_arbitrate_no_reviews(self):
        """边界：无审查结果"""
        from src.models.schemas import UsageSummary
        state: CodeCriticState = {
            "agent_reviews": {},
            "usage_summary": UsageSummary(),
        }
        result = arbitrate(state)
        assert "final_report" in result
        report = result["final_report"]
        assert report.overall_score == 0.0
        assert report.all_findings == []

    def test_arbitrate_with_reviews(self, sample_agent_reviews: dict):
        """核心：有审查结果的仲裁"""
        from src.models.schemas import UsageSummary
        state: CodeCriticState = {
            "agent_reviews": sample_agent_reviews,
            "usage_summary": UsageSummary(),
        }
        result = arbitrate(state)
        report = result["final_report"]
        assert isinstance(report, FinalReport)
        assert len(report.all_findings) == 2  # 两个 Agent 各有一个 finding
        assert report.overall_score > 0
        assert report.summary != ""

    def test_arbitrate_with_resolved_disputes(self, sample_agent_reviews: dict):
        """核心：带已解决分歧的仲裁"""
        from src.models.schemas import UsageSummary
        conflict = ConflictPair(
            finding_a_id="sec-1",
            finding_b_id="perf-1",
            agent_a="security_expert",
            agent_b="performance_expert",
            description="冲突",
            severity=Severity.MEDIUM,
        )
        debate_result = DebateResult(
            rounds=[],
            total_rounds=1,
            is_converged=True,
            resolved_conflicts=[conflict],
            unresolved_conflicts=[],
        )
        state: CodeCriticState = {
            "agent_reviews": sample_agent_reviews,
            "debate_result": debate_result,
            "usage_summary": UsageSummary(),
        }
        result = arbitrate(state)
        report = result["final_report"]
        assert len(report.all_findings) == 2

    def test_arbitrate_with_unresolved_disputes(self, sample_agent_reviews: dict):
        """核心：带未解决分歧的仲裁（标记为 rejected）"""
        from src.models.schemas import UsageSummary
        # 注意：arbitrate 函数通过 conflict.finding_a_id.startswith(agent_name)
        # 来匹配 Agent，所以 finding ID 必须以 agent_name 开头
        conflict = ConflictPair(
            finding_a_id="security_expert-SQL 注入风险",
            finding_b_id="performance_expert-低效循环",
            agent_a="security_expert",
            agent_b="performance_expert",
            description="冲突",
            severity=Severity.MEDIUM,
        )
        debate_result = DebateResult(
            rounds=[],
            total_rounds=1,
            is_converged=False,
            resolved_conflicts=[],
            unresolved_conflicts=[conflict],
        )
        state: CodeCriticState = {
            "agent_reviews": sample_agent_reviews,
            "debate_result": debate_result,
            "usage_summary": UsageSummary(),
        }
        result = arbitrate(state)
        report = result["final_report"]
        # 至少有一个 finding 被标记为 disputed
        disputed = [f for f in report.all_findings if f.was_disputed]
        assert len(disputed) > 0

"""
测试 Token 用量追踪
"""

from __future__ import annotations

from src.models.schemas import TokenUsage
from src.tracking.token_tracker import TokenTracker, calculate_cost


class TestCalculateCost:
    """测试费用计算"""

    def test_gpt4o_mini_cost(self):
        """基础：gpt-4o-mini 定价"""
        cost = calculate_cost(1000, 500, "gpt-4o-mini")
        # input: 1000 * 0.00015/1K = 0.00015
        # output: 500 * 0.0006/1K = 0.0003
        assert cost == pytest.approx(0.00045, rel=0.01)

    def test_deepseek_cost(self):
        """核心：deepseek 定价"""
        cost = calculate_cost(1000, 500, "deepseek-v4-flash")
        # input: 1000 * 0.00014/1K = 0.00014
        # output: 500 * 0.00028/1K = 0.00014
        assert cost == pytest.approx(0.00028, rel=0.01)

    def test_ollama_is_free(self):
        """基础：本地模型免费"""
        cost = calculate_cost(10000, 5000, "ollama")
        assert cost == 0.0

    def test_unknown_model_uses_default(self):
        """边界：未知模型用默认定价"""
        cost = calculate_cost(1000, 1000, "unknown-model")
        # default: input=0.002, output=0.008
        assert cost == pytest.approx(0.01, rel=0.01)


class TestTokenTracker:
    """测试 Token 追踪器"""

    def test_empty_tracker(self):
        """基础：空追踪器"""
        tracker = TokenTracker()
        assert tracker.get_total_tokens() == 0
        assert tracker.get_total_cost() == 0.0

    def test_add_single_usage(self):
        """核心：单次记录"""
        tracker = TokenTracker()
        usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        tracker.add_usage("security", "review", usage, "gpt-4o-mini")

        assert tracker.get_total_tokens() == 150
        assert tracker.get_total_cost() > 0

    def test_add_multiple_agents(self):
        """核心：多个 Agent"""
        tracker = TokenTracker()
        tracker.add_usage("security", "review", TokenUsage(prompt_tokens=200, completion_tokens=100, total_tokens=300))
        tracker.add_usage("performance", "review", TokenUsage(prompt_tokens=150, completion_tokens=80, total_tokens=230))
        tracker.add_usage("judge", "judge", TokenUsage(prompt_tokens=50, completion_tokens=30, total_tokens=80))

        assert tracker.get_total_tokens() == 300 + 230 + 80

    def test_summary_by_agent(self):
        """核心：按 Agent 汇总"""
        tracker = TokenTracker()
        tracker.add_usage("security", "review", TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150))
        tracker.add_usage("security", "debate", TokenUsage(prompt_tokens=30, completion_tokens=20, total_tokens=50))

        summary = tracker.get_summary()
        assert "security" in summary.by_agent
        assert summary.by_agent["security"].total_tokens == 200

    def test_summary_by_phase(self):
        """核心：按阶段汇总"""
        tracker = TokenTracker()
        tracker.add_usage("security", "review", TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150))
        tracker.add_usage("performance", "review", TokenUsage(prompt_tokens=80, completion_tokens=40, total_tokens=120))

        summary = tracker.get_summary()
        assert "review" in summary.by_phase
        assert summary.by_phase["review"].total_tokens == 270

    def test_total_in_summary(self):
        """核心：汇总中的总计"""
        tracker = TokenTracker()
        tracker.add_usage("a", "review", TokenUsage(prompt_tokens=50, completion_tokens=25, total_tokens=75))
        tracker.add_usage("b", "debate", TokenUsage(prompt_tokens=30, completion_tokens=15, total_tokens=45))

        summary = tracker.get_summary()
        assert summary.total.total_tokens == 120

    def test_reset(self):
        """基础：重置"""
        tracker = TokenTracker()
        tracker.add_usage("a", "review", TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150))
        tracker.reset()
        assert tracker.get_total_tokens() == 0

    def test_check_budget_within_limit(self):
        """基础：未超预算"""
        tracker = TokenTracker()
        tracker.add_usage("a", "review", TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150))
        assert tracker.check_budget(max_per_run=100000) is None

    def test_check_budget_exceeded(self):
        """边界：超预算"""
        tracker = TokenTracker()
        tracker.add_usage("a", "review", TokenUsage(prompt_tokens=60000, completion_tokens=50000, total_tokens=110000))
        warning = tracker.check_budget(max_per_run=100000)
        assert warning is not None
        assert "超过" in warning


import pytest

"""
CodeCritic — Token 用量追踪
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.models.schemas import TokenUsage, UsageSummary

# 模型定价（每 1K tokens，单位 USD）
MODEL_PRICING = {
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4.1": {"input": 0.002, "output": 0.008},
    "claude-sonnet-4": {"input": 0.003, "output": 0.015},
    "claude-haiku-4": {"input": 0.00025, "output": 0.00125},
    "deepseek-chat": {"input": 0.00014, "output": 0.00028},
    "deepseek-coder": {"input": 0.00014, "output": 0.00028},
    "deepseek-v4-flash": {"input": 0.00014, "output": 0.00028},
    "deepseek-v4-pro": {"input": 0.00042, "output": 0.00084},
    "ollama": {"input": 0.0, "output": 0.0},  # 本地模型免费
}

DEFAULT_PRICING = {"input": 0.002, "output": 0.008}


def calculate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model_name: str = "gpt-4o-mini",
) -> float:
    """根据模型和 tokens 计算费用"""
    pricing = MODEL_PRICING.get(model_name, DEFAULT_PRICING)
    prompt_cost = (prompt_tokens / 1000) * pricing["input"]
    completion_cost = (completion_tokens / 1000) * pricing["output"]
    return round(prompt_cost + completion_cost, 6)


class TokenRecord:
    """单条 Token 使用记录"""
    def __init__(
        self,
        agent_name: str,
        phase: str,
        prompt_tokens: int,
        completion_tokens: int,
        model_name: str = "gpt-4o-mini",
    ):
        self.agent_name = agent_name
        self.phase = phase
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.cost = calculate_cost(prompt_tokens, completion_tokens, model_name)
        self.timestamp = datetime.now()

    def to_dict(self) -> dict:
        return {
            "agent": self.agent_name,
            "phase": self.phase,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost": self.cost,
            "timestamp": self.timestamp.isoformat(),
        }


class TokenTracker:
    """
    Token 用量追踪器。

    支持按 Agent 和按阶段统计用量，以及费用估算。
    """

    def __init__(self, history_path: Optional[str] = None):
        self._current_records: list[TokenRecord] = []
        self.history_path = history_path

    def add_usage(
        self,
        agent_name: str,
        phase: str,
        usage: TokenUsage,
        model_name: str = "gpt-4o-mini",
    ):
        """记录一次 Token 使用"""
        record = TokenRecord(
            agent_name=agent_name,
            phase=phase,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            model_name=model_name,
        )
        self._current_records.append(record)

    def get_summary(self) -> UsageSummary:
        """获取当前会话的用量汇总"""
        by_agent: dict[str, TokenUsage] = {}
        by_phase: dict[str, TokenUsage] = {}

        for record in self._current_records:
            # 按 Agent
            if record.agent_name not in by_agent:
                by_agent[record.agent_name] = TokenUsage()
            a = by_agent[record.agent_name]
            a.prompt_tokens += record.prompt_tokens
            a.completion_tokens += record.completion_tokens
            a.total_tokens += record.total_tokens
            a.cost_usd += record.cost

            # 按阶段
            if record.phase not in by_phase:
                by_phase[record.phase] = TokenUsage()
            p = by_phase[record.phase]
            p.prompt_tokens += record.prompt_tokens
            p.completion_tokens += record.completion_tokens
            p.total_tokens += record.total_tokens
            p.cost_usd += record.cost

        total = TokenUsage()
        for usage in list(by_agent.values()):
            total.prompt_tokens += usage.prompt_tokens
            total.completion_tokens += usage.completion_tokens
            total.total_tokens += usage.total_tokens
            total.cost_usd += usage.cost_usd

        return UsageSummary(
            by_agent=by_agent,
            by_phase=by_phase,
            total=total,
        )

    def get_total_tokens(self) -> int:
        """获取当前总 Token 数"""
        return sum(r.total_tokens for r in self._current_records)

    def get_total_cost(self) -> float:
        """获取当前总费用"""
        return sum(r.cost for r in self._current_records)

    def reset(self):
        """重置当前会话的追踪数据"""
        self._current_records.clear()

    def save_history(self):
        """保存历史记录到文件"""
        if not self.history_path:
            return
        path = Path(self.history_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        records = [r.to_dict() for r in self._current_records]
        with open(path, "a", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def check_budget(
        self,
        max_per_run: int = 100000,
        max_per_day: int = 500000,
    ) -> Optional[str]:
        """
        检查预算限制。

        Returns:
            如果超限，返回警告信息；否则返回 None
        """
        total = self.get_total_tokens()
        if total > max_per_run:
            return (
                f"⚠️ 本轮 Token 消耗 {total} 超过限制 {max_per_run}。"
            )

        # 检查今日累计
        if self.history_path and os.path.exists(self.history_path):
            today = datetime.now().date()
            daily_total = 0
            with open(self.history_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        record = json.loads(line)
                        ts = datetime.fromisoformat(record["timestamp"])
                        if ts.date() == today:
                            daily_total += record["total_tokens"]
                    except (json.JSONDecodeError, KeyError):
                        continue
            if daily_total + total > max_per_day:
                return (
                    f"⚠️ 今日累计 Token 消耗 {daily_total + total} 超过限制 {max_per_day}。"
                )

        return None

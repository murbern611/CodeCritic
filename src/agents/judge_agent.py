"""
CodeCritic — LLM 分歧检测器 (Judge Agent)

使用 LLM 分析所有审查 Agent 的输出，
基于语义理解找出真正的观点冲突。
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from src.agents.base import BaseAgent
from src.models.schemas import (
    AgentReview,
    CodeLocation,
    ConflictPair,
    JudgeReport,
    Severity,
    TokenUsage,
)
from src.tracking.token_tracker import TokenTracker
from src.utils.logger import logger


class JudgeAgent(BaseAgent):
    """
    分歧检测器 —— 使用 LLM 语义分析各 Agent 输出中的冲突。

    与普通审查 Agent 不同，JudgeAgent 不审查代码，而是分析其他 Agent 的审查结果。
    """

    agent_name = "judge"
    agent_label = "分歧检测器"
    output_schema = ""

    system_prompt = """你是一个代码审查分歧检测器（Judge Agent）。
你的任务是分析多个专业 Agent 的代码审查结果，找出它们之间存在的观点冲突。

## 冲突的定义
两个 Agent 在**相同或相邻的代码位置**（行号相差 ≤ 3 行），
对**同一类问题**给出了**矛盾或显著不同的判断**，才算冲突。

### 真正的冲突（需报告）
- Agent A 认为某处存在高风险问题，Agent B 认为此处没有问题（未提及）
- Agent A 评估为"高"严重度，Agent B 对同一位置的同类问题评估为"低"
- 两个 Agent 在同一位置给出相反的建议方向（如一个建议重构、一个说保持现状）
- 一个说"存在安全漏洞"，另一个说"此处安全"——即使类别不同，但涉及同一段代码

### 不是冲突（忽略）
- 不同类别的问题出现在同一位置（安全问题和性能问题是两个维度）
- 两个 Agent 提的问题没有语义矛盾，只是关注角度不同
- 严重度只差一级（如 high vs medium 不算，high vs low 或 none 才算）

## 输出格式（纯 JSON，不要前缀说明）
{
  "has_conflict": true,
  "conflicts": [
    {
      "finding_a_title": "eval() 存在代码注入风险",
      "finding_b_title": "eval() 使用在可控范围内",
      "agent_a": "security_expert",
      "agent_b": "performance_expert",
      "description": "安全专家认为 eval() 有注入风险，性能专家认为在当前场景下可控",
      "severity": "high",
      "line_start": 42,
      "line_end": 45
    }
  ],
  "summary": "发现 2 处分歧"
}
"""

    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        temperature: float = 0.0,
        max_tokens: int = 4096,
        models_config: Optional[dict[str, Any]] = None,
        token_tracker: Optional[TokenTracker] = None,
        enabled: bool = True,
    ):
        super().__init__(
            model_name=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
            models_config=models_config,
            token_tracker=token_tracker,
            enabled=enabled,
        )

    def detect_conflicts(
        self,
        reviews: dict[str, AgentReview],
        phase: str = "judge",
    ) -> JudgeReport:
        """
        使用 LLM 检测各 Agent 审查结果中的语义冲突。

        Args:
            reviews: agent_key -> AgentReview 的映射
            phase: 统计阶段名

        Returns:
            JudgeReport 包含检测到的冲突对
        """
        if not self.enabled or not reviews:
            return JudgeReport(
                total_pairs_compared=0,
                conflicts_found=[],
                has_conflict=False,
                summary="无审查结果可供分析",
            )

        self._init_llm()

        # 构造包含所有审查结果的消息
        reviews_text = self._format_reviews(reviews)
        user_message = (
            f"以下是各代码审查 Agent 的完整审查结果：```n```n"
            f"{reviews_text}```n```n"
            f"请严格按照要求的 JSON 格式输出分歧检测结果。"
        )

        messages = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=user_message),
        ]

        start_time = time.time()
        try:
            response = self._llm.invoke(messages)
            elapsed = time.time() - start_time

            # 记录 Token 消耗
            metadata = response.usage_metadata or {}
            usage = TokenUsage(
                prompt_tokens=metadata.get("input_tokens", 0),
                completion_tokens=metadata.get("output_tokens", 0),
            )
            usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
            self.token_tracker.add_usage(
                self.agent_name, phase, usage, model_name=self.model_name
            )

            logger.info(
                f"[{self.agent_label}] 分歧检测完成 | "
                f"Token: {usage.total_tokens} (入 {usage.prompt_tokens}) | "
                f"耗时: {elapsed:.1f}s"
            )

            return self._parse_judge_response(response.content, reviews)

        except Exception as e:
            logger.error(f"[{self.agent_label}] 分歧检测调用失败: {e}")
            return JudgeReport(
                total_pairs_compared=sum(
                    len(r.findings) for r in reviews.values()
                ),
                conflicts_found=[],
                has_conflict=False,
                summary=f"分歧检测过程出错: {e}",
            )

    def _format_reviews(self, reviews: dict[str, AgentReview]) -> str:
        """将 Agent 审查结果格式化为易读文本"""
        parts = []
        for agent_key, review in reviews.items():
            parts.append(f"## Agent: {review.agent_label} ({agent_key})")
            parts.append(f"评分: {review.overall_score}/10 | 置信度: {review.confidence:.2f}")
            parts.append(f"总结: {review.summary}")
            parts.append("")

            if not review.findings:
                parts.append("  该 Agent 未发现问题。")
                parts.append("")
                continue

            for i, f in enumerate(review.findings, 1):
                loc = f.location
                loc_str = ""
                if loc and loc.line_start:
                    loc_str = f"L{loc.line_start}"
                    if loc.line_end and loc.line_end != loc.line_start:
                        loc_str += f"-{loc.line_end}"

                parts.append(f"  Finding {i}: [{f.severity.value.upper()}] {f.title}")
                parts.append(f"    位置: {loc_str or 'N/A'}")
                parts.append(f"    描述: {f.description}")
                if f.suggestion:
                    parts.append(f"    建议: {f.suggestion.description}")
                parts.append("")

        return "```n".join(parts)

    def _parse_judge_response(
        self,
        content: str,
        reviews: dict[str, AgentReview],
    ) -> JudgeReport:
        """解析 LLM 返回的 JSON 为 JudgeReport"""
        json_str = content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                logger.warning(f"[{self.agent_label}] 无法解析 LLM 输出为 JSON")
                return JudgeReport(
                    total_pairs_compared=sum(
                        len(r.findings) for r in reviews.values()
                    ),
                    conflicts_found=[],
                    has_conflict=False,
                    summary="分歧检测输出解析失败",
                )

        has_conflict = data.get("has_conflict", False)
        conflicts_raw = data.get("conflicts", [])
        conflicts = []

        for item in conflicts_raw:
            try:
                agent_a = item.get("agent_a", "")
                agent_b = item.get("agent_b", "")
                finding_a_title = item.get("finding_a_title", "")
                finding_b_title = item.get("finding_b_title", "")

                conflict = ConflictPair(
                    finding_a_id=f"{agent_a}-{finding_a_title[:30]}",
                    finding_b_id=f"{agent_b}-{finding_b_title[:30]}",
                    agent_a=agent_a,
                    agent_b=agent_b,
                    description=item.get("description", ""),
                    severity=Severity(item.get("severity", "medium")),
                    code_location=CodeLocation(
                        line_start=item.get("line_start"),
                        line_end=item.get("line_end"),
                    ),
                )
                conflicts.append(conflict)
            except Exception as e:
                logger.warning(f"[{self.agent_label}] 跳过无效冲突项: {e}")

        summary = data.get("summary", "")
        if not summary:
            summary = (
                f"发现 {len(conflicts)} 处分歧"
                if has_conflict and conflicts
                else "未发现分歧"
            )

        logger.info(
            f"分歧检测: {'发现 ' + str(len(conflicts)) + ' 处分歧' if conflicts else '所有 Agent 意见一致'}"
        )
        for c in conflicts:
            logger.info(
                f"  [{c.agent_a} vs {c.agent_b}] "
                f"L{c.code_location.line_start}: {c.description[:80]}"
            )

        # 收集涉及的 Agent 集合
        conflict_sets = []
        for c in conflicts:
            conflict_sets.append({c.agent_a, c.agent_b})

        return JudgeReport(
            total_pairs_compared=sum(
                len(r.findings) for r in reviews.values()
            ),
            conflicts_found=conflicts,
            conflict_agent_sets=conflict_sets,
            has_conflict=len(conflicts) > 0,
            summary=summary,
        )

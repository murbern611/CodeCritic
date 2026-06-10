"""
CodeCritic — LangGraph 条件路由边

定义图节点的条件路由逻辑。
"""

from src.graph.state import CodeCriticState
from src.graph.nodes import MAX_DEBATE_ROUNDS


def route_after_judge(state: CodeCriticState) -> str:
    """分歧检测后路由：有分歧→debate，无分歧→skip"""
    judge_report = state.get("judge_report")
    if judge_report and judge_report.has_conflict and not state.get("skip_debate", False):
        return "debate"
    return "skip"


def route_after_debate(state: CodeCriticState) -> str:
    """辩论后路由：收敛或达上限→arbitrate，否则继续 debate"""
    debate_result = state.get("debate_result")
    debate_round = state.get("debate_round", 0)

    if debate_result and debate_result.is_converged:
        return "arbitrate"
    if debate_round >= MAX_DEBATE_ROUNDS:
        return "arbitrate"
    return "debate"

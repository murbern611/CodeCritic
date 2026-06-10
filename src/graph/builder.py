"""
CodeCritic — LangGraph 图构建

构建完整的代码评审状态图。
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from src.graph.nodes import (
    arbitrate,
    debate,
    judge_disputes,
    parse_code,
    review_code,
)
from src.graph.edges import route_after_debate, route_after_judge
from src.graph.state import CodeCriticState


def build_graph(
    checkpointer: Optional[Any] = None,
) -> StateGraph:
    """
    构建 CodeCritic 状态图。

    图结构：
        __start__ → parse → review → judge
            ├─ (无分歧) → skip → arbitrate → output → __end__
            └─ (有分歧) → debate → (未收敛) ↻
                                    └─ (已收敛) → arbitrate → output → __end__

    Args:
        checkpointer: LangGraph 检查点（用于记忆持久化）

    Returns:
        编译后的可执行图
    """
    builder = StateGraph(CodeCriticState)

    # === 定义节点 ===
    builder.add_node("parse", parse_code)
    builder.add_node("review", review_code)
    builder.add_node("judge", judge_disputes)
    builder.add_node("debate", debate)
    builder.add_node("arbitrate", arbitrate)

    # === 定义边 ===
    builder.add_edge(START, "parse")
    builder.add_edge("parse", "review")
    builder.add_edge("review", "judge")

    # 分歧检测后的条件路由
    builder.add_conditional_edges(
        "judge",
        route_after_judge,
        {
            "debate": "debate",
            "skip": "arbitrate",
        },
    )

    # 辩论后的条件路由
    builder.add_conditional_edges(
        "debate",
        route_after_debate,
        {
            "debate": "debate",    # 继续辩论（循环）
            "arbitrate": "arbitrate",  # 进入仲裁
        },
    )

    # 仲裁 → 结束
    builder.add_edge("arbitrate", END)

    # === 编译 ===
    graph = builder.compile(checkpointer=checkpointer or MemorySaver())

    return graph

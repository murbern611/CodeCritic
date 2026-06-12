"""
CodeCritic — LangGraph State 定义

State 是整个图流转的数据载体。
每个节点读取 state、处理、写入更新后的 state。
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import MessagesState

from src.models.schemas import (
    AgentReview,
    DebateResult,
    FinalReport,
    JudgeReport,
    TokenUsage,
    UsageSummary,
)


class CodeCriticState(MessagesState):
    """
    LangGraph State。

    继承 MessagesState 以支持消息传递，
    增加自定义字段用于代码评审流程。
    """

    # --- 输入 ---
    code: str = ""                       # 要审查的源代码
    code_language: Optional[str] = None  # 代码语言
    file_path: Optional[str] = None      # 源文件路径
    context: dict[str, Any] = {}         # 额外上下文
    diff_mode: bool = False              # ★ 是否使用 diff 审查模式
    diff_text: str = ""                  # ★ diff 格式的文本（diff_mode=True 时代替 code 传给 Agent）

    # --- 审查阶段 ---
    agent_reviews: dict[str, AgentReview] = {}  # agent_name -> review

    # --- 分歧检测 ---
    judge_report: Optional[JudgeReport] = None

    # --- 辩论阶段 ---
    debate_result: Optional[DebateResult] = None
    debate_approved: bool = False  # 用户是否批准进入辩论
    debate_round: int = 0         # 当前辩论轮次

    # --- 仲裁阶段 ---
    final_report: Optional[FinalReport] = None

    # --- 追踪 ---
    usage_summary: UsageSummary = UsageSummary()
    errors: list[str] = []  # 运行过程中的错误

    # --- 控制 ---
    skip_debate: bool = False  # 条件路由：是否跳过辩论

    # --- 记忆 ---
    session_id: str = ""       # 用户/会话索引（用于关联历史审查）
    memory_enabled: bool = True  # 是否启用记忆功能

    # --- Agent 选择（前端覆盖） ---
    enabled_agents: Optional[list[str]] = None  # None=全部启用
    agent_models: Optional[dict[str, str]] = None  # agent_key -> model_name 覆盖
    model_configs: Optional[dict[str, dict]] = None  # model_name -> {api_key, base_url} 覆盖
    custom_models: Optional[dict[str, dict]] = None  # 前端新增的自定义模型

"""
CodeCritic — Pydantic 数据模型

所有结构化数据的 Schema 定义，用于：
1. 约束 Agent 输出格式
2. 分歧检测时的观点对齐
3. 最终报告的结构化输出
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


# ============================================================
# 枚举
# ============================================================

class Severity(str, Enum):
    """严重级别"""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class FindingCategory(str, Enum):
    """发现类别"""
    SECURITY = "security"
    PERFORMANCE = "performance"
    STYLE = "style"
    CORRECTNESS = "correctness"
    ARCHITECTURE = "architecture"


class Verdict(str, Enum):
    """仲裁裁定"""
    UPHELD = "upheld"        # 维持原观点
    REJECTED = "rejected"    # 驳回
    COMPROMISE = "compromise"  # 折中


# ============================================================
# 基础模型
# ============================================================

class CodeLocation(BaseModel):
    """代码位置"""
    file: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    snippet: Optional[str] = None


class CodeSuggestion(BaseModel):
    """修复建议"""
    description: str = Field(description="建议描述")
    code_example: Optional[str] = Field(None, description="示例代码")


class TokenUsage(BaseModel):
    """Token 使用量"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


class UsageSummary(BaseModel):
    """完整的 Token 消耗汇总"""
    by_agent: dict[str, TokenUsage] = Field(
        default_factory=dict,
        description="按 Agent 统计"
    )
    by_phase: dict[str, TokenUsage] = Field(
        default_factory=dict,
        description="按阶段统计（review / debate / arbitrate）"
    )
    total: TokenUsage = Field(default_factory=TokenUsage)


# ============================================================
# Finding 模型（各 Agent 的输出单元）
# ============================================================

class BaseFinding(BaseModel):
    """所有 Finding 的基类"""
    id: str = Field(default="", description="唯一标识，解析时自动填充")
    severity: Severity
    title: str = Field(description="问题标题")
    description: str = Field(description="问题详细描述")
    location: CodeLocation = Field(default_factory=CodeLocation)
    suggestion: Optional[CodeSuggestion] = None

    def model_dump_for_alignment(self) -> dict:
        """提取用于观点对齐的关键字段"""
        return {
            "category": self.get_category(),
            "line_start": self.location.line_start,
            "line_end": self.location.line_end,
            "title": self.title,
            "severity": self.severity.value,
        }

    def get_category(self) -> str:
        """获取类别标识，子类覆盖"""
        return "base"


class SecurityFinding(BaseFinding):
    """安全发现"""
    vulnerability_type: Optional[str] = Field(None, description="漏洞类型")

    def get_category(self) -> str:
        return "security"


class PerformanceFinding(BaseFinding):
    """性能发现"""
    complexity: Optional[str] = Field(None, description="复杂度分析")
    estimated_impact: Optional[str] = Field(None, description="预估影响")

    def get_category(self) -> str:
        return "performance"


class StyleFinding(BaseFinding):
    """风格发现"""
    rule_reference: Optional[str] = Field(None, description="违反的规范规则")

    def get_category(self) -> str:
        return "style"


class CorrectnessFinding(BaseFinding):
    """正确性发现"""
    scenario: Optional[str] = Field(None, description="触发场景")

    def get_category(self) -> str:
        return "correctness"


class ArchitectureFinding(BaseFinding):
    """架构发现"""
    principle: Optional[str] = Field(None, description="违反的设计原则")

    def get_category(self) -> str:
        return "architecture"


# ============================================================
# Agent 输出
# ============================================================

class AgentReview(BaseModel):
    """单个 Agent 的审查结果"""
    agent_name: str = Field(description="Agent 名称")
    agent_label: str = Field(description="Agent 显示名")
    model_used: str = Field(description="使用的模型")
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="置信度"
    )
    overall_score: float = Field(
        default=0.0,
        ge=0.0,
        le=10.0,
        description="总体评分（0-10）"
    )
    findings: list[BaseFinding] = Field(
        default_factory=list,
        description="发现的问题列表"
    )
    summary: str = Field(default="", description="审查总结")
    token_usage: TokenUsage = Field(default_factory=TokenUsage)


# ============================================================
# 分歧检测
# ============================================================

class ConflictPair(BaseModel):
    """冲突观点对"""
    finding_a_id: str = Field(description="Agent A 的 finding ID")
    finding_b_id: str = Field(description="Agent B 的 finding ID")
    agent_a: str = Field(description="Agent A 名称")
    agent_b: str = Field(description="Agent B 名称")
    description: str = Field(description="冲突描述")
    severity: Severity = Field(description="冲突严重程度")
    code_location: CodeLocation = Field(default_factory=CodeLocation)


class JudgeReport(BaseModel):
    """分歧检测报告"""
    total_pairs_compared: int = 0
    conflicts_found: list[ConflictPair] = Field(default_factory=list)
    conflict_agent_sets: list[set[str]] = Field(
        default_factory=list,
        description="每对冲突涉及的 Agent 集合"
    )
    has_conflict: bool = False
    summary: str = ""


# ============================================================
# 辩论
# ============================================================

class DebateArgument(BaseModel):
    """单轮辩论中的一个论点"""
    speaker: str = Field(description="发言 Agent")
    target_finding_id: str = Field(description="针对的 finding ID")
    position: str = Field(description="立场：support / refute / neutral")
    argument: str = Field(description="论点内容")
    concedes: bool = Field(default=False, description="是否让步")


class DebateRound(BaseModel):
    """一轮辩论"""
    round_number: int
    conflict_pair: ConflictPair
    arguments: list[DebateArgument] = Field(default_factory=list)
    summary: str = ""


class DebateResult(BaseModel):
    """辩论结果"""
    rounds: list[DebateRound] = Field(default_factory=list)
    total_rounds: int = 0
    is_converged: bool = False
    resolved_conflicts: list[ConflictPair] = Field(default_factory=list)
    unresolved_conflicts: list[ConflictPair] = Field(default_factory=list)
    token_usage: TokenUsage = Field(default_factory=TokenUsage)


# ============================================================
# 最终报告
# ============================================================

class FinalReportFinding(BaseModel):
    """最终报告中的一条 finding（已仲裁）"""
    original_finding: BaseFinding
    source_agent: str = Field(description="来源 Agent")
    verdict: Verdict = Field(description="仲裁结果")
    arbiter_note: Optional[str] = Field(None, description="仲裁说明")
    was_disputed: bool = False


class FinalReport(BaseModel):
    """最终评审报告"""
    summary: str = Field(description="执行摘要")
    overall_score: float = Field(ge=0.0, le=10.0, description="总体评分")
    all_findings: list[FinalReportFinding] = Field(
        default_factory=list,
        description="所有 findings（已去重+仲裁）"
    )
    resolved_disputes: list[dict] = Field(
        default_factory=list,
        description="已解决的分歧"
    )
    unresolved_disputes: list[dict] = Field(
        default_factory=list,
        description="未解决的分歧（需人工介入）"
    )
    recommendations: list[str] = Field(
        default_factory=list,
        description="优先级排序的行动建议"
    )
    token_usage: UsageSummary = Field(
        default_factory=UsageSummary,
        description="Token 消耗汇总"
    )
    review_time_s: float = Field(default=0.0, description="审查耗时（秒）")
    code_language: Optional[str] = Field(None, description="代码语言")


# ============================================================
# 工具函数
# ============================================================

finding_schema_map = {
    "SecurityFinding": SecurityFinding,
    "PerformanceFinding": PerformanceFinding,
    "StyleFinding": StyleFinding,
    "CorrectnessFinding": CorrectnessFinding,
    "ArchitectureFinding": ArchitectureFinding,
}


def get_finding_model(schema_name: str) -> type[BaseFinding]:
    """根据 schema 名称获取对应的 Pydantic 模型"""
    model = finding_schema_map.get(schema_name)
    if model is None:
        raise ValueError(f"Unknown schema: {schema_name}")
    return model

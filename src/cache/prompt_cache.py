"""
CodeCritic — Prompt 缓存系统

消息结构（可扩展的多消息格式）：

  System: [Agent 特有针对 Prompt]

  User 1:  代码全文（共享前缀 → 命中 KV Cache）
  User 2:  （可选）辩论上下文、对方观点等
  User 3:  （可选）更多插入内容
  User 4:  专业分析指令 / 请回应的指令

  核心设计：
  - 代码独立成一条 User Message（最前面，可缓存）
  - 任何阶段想插入内容，加一条 User Message 在中间就行
  - 最后的 User Message 始终是"请做什么"的指令
  - 代码前缀不变 → 审查和辩论之间缓存依然有效
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from src.utils.logger import logger


# ============================================================
# 共享代码上下文
# ============================================================


@dataclass
class SharedCodeContext:
    """
    共享代码上下文——所有 Agent 共用的代码块。
    存为独立 User Message，保证审查和辩论阶段都能命中缓存。
    """
    code: str
    language: str = "python"
    file_path: Optional[str] = None
    extra_context: dict[str, Any] = field(default_factory=dict)

    def get_code_block_text(self) -> str:
        """获取代码块的文本"""
        context_str = ""
        if self.extra_context:
            context_str = (
                "\n\n额外上下文：\n"
                f"{json.dumps(self.extra_context, ensure_ascii=False, indent=2)}"
            )
        return (
            f"请审查以下 {self.language} 代码：\n\n"
            f"```{self.language}\n{self.code}\n```"
            f"{context_str}"
        )

    @property
    def shared_token_estimate(self) -> int:
        return int(len(self.code) * 0.25)


# ============================================================
# 模型组管理
# ============================================================

@dataclass
class ModelGroup:
    provider: str
    model_name: str
    base_url: Optional[str] = None
    agent_keys: list[str] = field(default_factory=list)

    @property
    def group_key(self) -> str:
        key = f"{self.provider}:{self.model_name}"
        if self.base_url:
            key += f"@{self.base_url}"
        return key


def group_agents_by_model(
    agents_config: dict[str, Any],
    models_config: dict[str, Any],
) -> tuple[dict[str, ModelGroup], dict[str, str]]:
    """将 Agent 按模型分组。"""
    from src.utils.config_loader import get_model_config

    groups: dict[str, ModelGroup] = {}
    agent_to_group: dict[str, str] = {}

    for agent_key, cfg in agents_config.items():
        if not cfg.get("enabled", True) or agent_key in ("judge", "arbiter"):
            continue
        model_name = cfg.get("model", "gpt-4o-mini")
        try:
            model_cfg = get_model_config(model_name, models_config)
        except ValueError:
            continue
        provider = model_cfg.get("provider", "openai")
        base_url = model_cfg.get("base_url")
        if provider == "openai_compatible":
            provider = "openai"
        group_key = f"{provider}:{model_name}"
        if base_url:
            group_key += f"@{base_url}"
        if group_key not in groups:
            groups[group_key] = ModelGroup(provider=provider, model_name=model_name, base_url=base_url)
        groups[group_key].agent_keys.append(agent_key)
        agent_to_group[agent_key] = group_key

    for key, group in groups.items():
        logger.info(f"  模型组 [{key}]: {len(group.agent_keys)} 个 Agent — {', '.join(group.agent_keys)}")
    return groups, agent_to_group


# ============================================================
# 消息构建器 —— 核心设计
# ============================================================
#
#   messages = [
#     {"role": "system",   "content": system_prompt},        ← Agent 特有
#     {"role": "user",     "content": code_block},            ← 共享！命中缓存
#     {"role": "user",     "content": extra_content_1},       ← 可选：辩论上下文等
#     {"role": "user",     "content": extra_content_2},       ← 可选：更多插入
#     {"role": "user",     "content": final_instruction},     ← 指令：分析或回应
#   ]
#
#   审查阶段：extra_contents = [] → 只有 system + code + 指令
#   辩论阶段：extra_contents = [对方观点] → system + code + 对方观点 + 指令
#   → code 所在的 User Message 位置固定、内容固定 → 缓存命中
# ============================================================


def build_code_message(shared_ctx: SharedCodeContext) -> dict:
    """
    构建代码 User Message。
    这是缓存的核心：对所有同模型 Agent 完全一样。
    """
    return {"role": "user", "content": shared_ctx.get_code_block_text()}


def build_openai_messages(
    system_prompt: str,
    code_block_text: str,
    final_instruction: str,
    extra_contents: Optional[list[str]] = None,
) -> list[dict]:
    """
    构建 OpenAI/DeepSeek 格式的多消息列表。

    Args:
        system_prompt: Agent 的 System Prompt
        code_block_text: shared_ctx.get_code_block_text()
        final_instruction: 最后的指令（分析代码 / 回应对方）
        extra_contents: 插入在代码和指令之间的内容

    Returns:
        [system, user(code), user(extra...), user(指令)]
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": code_block_text},
    ]

    # 插入中间内容（辩论上下文等）
    if extra_contents:
        for content in extra_contents:
            messages.append({"role": "user", "content": content})

    # 指令
    messages.append({"role": "user", "content": final_instruction})

    return messages


def build_anthropic_messages(
    system_prompt: str,
    code_block_text: str,
    final_instruction: str,
    extra_contents: Optional[list[str]] = None,
) -> list[dict]:
    """
    构建 Anthropic 格式的多消息列表。

    Anthropic 支持 content blocks 数组，可以对单个 block 标 cache_control。

    Args:
        system_prompt: Agent 的 System Prompt
        code_block_text: shared_ctx.get_code_block_text()
        final_instruction: 最后的指令
        extra_contents: 插入内容

    Returns:
        [system, user({content: [{code, cache_control}, {extra...}, {指令}]})]
    """
    content_blocks = []

    # Block 0: 代码（缓存标记）
    content_blocks.append({
        "type": "text",
        "text": code_block_text,
        "cache_control": {"type": "ephemeral"},
    })

    # Block 1..N: 中间内容（不缓存）
    if extra_contents:
        for content in extra_contents:
            content_blocks.append({"type": "text", "text": content})

    # Block N+1: 指令（不缓存）
    content_blocks.append({"type": "text", "text": final_instruction})

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content_blocks},
    ]


# ============================================================
# 缓存效果报告
# ============================================================

@dataclass
class CacheReport:
    total_agents: int = 0
    model_groups: int = 0
    cacheable_calls: int = 0
    total_tokens_saved: int = 0
    details: list[str] = field(default_factory=list)

    def print_summary(self):
        if self.total_agents == 0:
            return
        ratio = self.cacheable_calls / self.total_agents * 100 if self.total_agents else 0
        lines = [
            f"┌──────────────────────────────────────────┐",
            f"│ Prompt 缓存效果                            │",
            f"├──────────────────────────────────────────┤",
            f"│ 总 Agent 数:      {self.total_agents:>4}                    │",
            f"│ 模型组数:          {self.model_groups:>4}                    │",
            f"│ 可缓存调用:        {self.cacheable_calls:>4} ({ratio:>5.1f}%)             │",
            f"│ 估算节省 Token:    {self.total_tokens_saved:>6}                │",
            f"└──────────────────────────────────────────┘",
        ]
        for line in lines:
            print(line)


# ============================================================
# 默认配置
# ============================================================

CACHE_CONFIG_DEFAULTS = {
    "enabled": True,
    "anthropic_cache_control": True,
    "openai_auto_cache": True,
    "vllm_prefix_caching": True,
    "min_prefix_tokens": 512,
}

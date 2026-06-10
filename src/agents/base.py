"""
CodeCritic — Agent 基类

消息结构（多消息格式，可扩展）：

  System: [Agent 专业 System Prompt]
  User 1: 代码全文（共享 → 命中 KV Cache）
  User 2: (可选) 辩论上下文、对方观点
  User 3: ...
  User N: 分析指令 / 回应指令

  审查阶段：System + User(代码) + User(指令)
  辩论阶段：System + User(代码) + User(对方观点) + User(回应指令)
  → 代码的 User Message 在两种阶段完全一样 → 缓存命中
"""

from __future__ import annotations

import json
import time
from abc import ABC
from typing import Any, Optional

from langchain_openai import ChatOpenAI
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from src.cache.prompt_cache import (
    SharedCodeContext,
    build_anthropic_messages,
    build_openai_messages,
)
from src.models.schemas import (
    AgentReview,
    TokenUsage,
    get_finding_model,
)
from src.utils.config_loader import (
    build_llm_kwargs,
    load_models_config,
)
from src.utils.logger import logger
from src.tracking.token_tracker import TokenTracker


class BaseAgent(ABC):
    """审查 Agent 基类。"""

    agent_name: str = ""
    agent_label: str = ""
    output_schema: str = ""
    system_prompt: str = ""

    def __init__(
        self,
        model_name: str = "gpt-4o-mini",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        models_config: Optional[dict[str, Any]] = None,
        token_tracker: Optional[TokenTracker] = None,
        enabled: bool = True,
    ):
        self.model_name = model_name
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._models_config = models_config or load_models_config()
        self._llm: Optional[BaseChatModel] = None
        self._provider: str = "openai"
        self.token_tracker = token_tracker or TokenTracker()
        self.enabled = enabled

    def _init_llm(self):
        """初始化 LLM 实例（懒加载，同模型复用实例）"""
        if self._llm is not None:
            return
        kwargs = build_llm_kwargs(self.model_name, self._models_config)
        self._provider = kwargs.pop("_provider")

        if self._provider == "openai":
            self._llm = ChatOpenAI(**kwargs)
        elif self._provider == "anthropic":
            self._llm = ChatAnthropic(**kwargs)
        else:
            raise ValueError(f"Unsupported provider: {self._provider}")

    # ================================================================
    # Public API
    # ================================================================

    def review(self, code: str, context: Optional[dict] = None) -> AgentReview:
        """审查代码（兼容接口）"""
        shared_ctx = SharedCodeContext(
            code=code,
            language=(context or {}).get("language", ""),
            extra_context=context or {},
        )
        return self._invoke(
            shared_ctx=shared_ctx,
            final_instruction=(
                f"请从以下角度分析代码（严格按你被分配的专业领域）：\n\n"
                f"{self.system_prompt}\n\n"
                f"请严格按照要求的 JSON 格式输出审查结果。"
            ),
            extra_contents=None,
        )

    def review_cached(
        self,
        shared_ctx: SharedCodeContext,
        extra_contents: Optional[list[str]] = None,
    ) -> AgentReview:
        """
        使用共享代码上下文的缓存优化审查。

        Args:
            shared_ctx: 共享代码上下文
            extra_contents: 插入在代码和指令之间的内容列表
                            （辩论阶段传入对方观点等）
        """
        if not self.enabled:
            return AgentReview(
                agent_name=self.agent_name,
                agent_label=self.agent_label,
                model_used=self.model_name,
                findings=[],
                summary="[此 Agent 已禁用]",
            )

        final_instruction = (
            f"请从以下角度分析代码（严格按你被分配的专业领域）：\n\n"
            f"{self.system_prompt}\n\n"
            f"请严格按照要求的 JSON 格式输出审查结果。"
        )
        return self._invoke(shared_ctx, final_instruction, extra_contents)

    def debate_reply(
        self,
        shared_ctx: SharedCodeContext,
        opponent_view: str,
        debate_round: int,
    ) -> str:
        """
        辩论回应——从自己专业角度反驳/回应对方观点。

        Args:
            shared_ctx: 共享代码上下文
            opponent_view: 对方的观点文本
            debate_round: 当前辩论轮次

        Returns:
            辩论发言文本
        """
        if not self.enabled:
            return ""

        self._init_llm()
        final_instr = (
            f"这是第 {debate_round} 轮辩论。\n"
            f"你正在参与代码审查辩论，请从你的专业角度回应对方的观点。\n"
            f"如果你同意对方，可以承认；如果不同意，请给出专业理由。\n"
            f"请用简洁的语言直接回应。"
        )
        # 辩论用 raw 调用，不走 JSON 解析
        result_text = self._invoke_raw(shared_ctx, final_instr, [opponent_view])
        return result_text or "（无回应）"

    # ================================================================
    # 核心调用
    # ================================================================

    def _invoke(
        self,
        shared_ctx: SharedCodeContext,
        final_instruction: str,
        extra_contents: Optional[list[str]] = None,
        phase: str = "review",
    ) -> AgentReview:
        """调用 LLM（JSON 输出，用于审查和仲裁）"""
        self._init_llm()
        messages = self._build_messages(shared_ctx, final_instruction, extra_contents)

        start_time = time.time()
        try:
            response = self._llm.invoke(messages)
            elapsed = time.time() - start_time

            metadata = response.usage_metadata or {}
            usage = TokenUsage(
                prompt_tokens=metadata.get("input_tokens", 0),
                completion_tokens=metadata.get("output_tokens", 0),
            )
            usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
            self.token_tracker.add_usage(self.agent_name, phase, usage, model_name=self.model_name)

            result = self._parse_response(response.content)

            label = "审查" if phase == "review" else phase
            logger.info(
                f"[{self.agent_label}] {label}完成 | "
                f"发现 {len(result.findings)} 个问题 | "
                f"Token: {usage.total_tokens} (入 {usage.prompt_tokens}) | "
                f"耗时: {elapsed:.1f}s"
            )
            result.token_usage = usage
            return result

        except Exception as e:
            logger.error(f"[{self.agent_label}] {phase}调用失败: {e}")
            return AgentReview(
                agent_name=self.agent_name,
                agent_label=self.agent_label,
                model_used=self.model_name,
                findings=[],
                summary=f"调用过程出错: {e}",
            )

    def _invoke_raw(
        self,
        shared_ctx: SharedCodeContext,
        final_instruction: str,
        extra_contents: Optional[list[str]] = None,
    ) -> str:
        """调用 LLM（纯文本输出，用于辩论）"""
        self._init_llm()
        messages = self._build_messages(shared_ctx, final_instruction, extra_contents)

        start_time = time.time()
        try:
            response = self._llm.invoke(messages)
            elapsed = time.time() - start_time

            metadata = response.usage_metadata or {}
            usage = TokenUsage(
                prompt_tokens=metadata.get("input_tokens", 0),
                completion_tokens=metadata.get("output_tokens", 0),
            )
            usage.total_tokens = usage.prompt_tokens + usage.completion_tokens
            self.token_tracker.add_usage(self.agent_name, "debate", usage, model_name=self.model_name)

            text = response.content.strip()
            logger.info(
                f"[{self.agent_label}] 辩论回应 | "
                f"Token: {usage.total_tokens} (入 {usage.prompt_tokens}) | "
                f"耗时: {elapsed:.1f}s"
            )
            return text

        except Exception as e:
            logger.error(f"[{self.agent_label}] 辩论调用失败: {e}")
            return f"（调用出错: {e}）"

    # ================================================================
    # 消息构建
    # ================================================================

    def _build_messages(
        self,
        shared_ctx: SharedCodeContext,
        final_instruction: str,
        extra_contents: Optional[list[str]] = None,
    ) -> list:
        """
        构建消息列表。

        格式（无论是审查还是辩论）：
          System
          User: 代码        ← 缓存键
          User: (可选) 上下文
          User: 指令

        Anthropic 使用 content blocks，代码 block 标 cache_control
        OpenAI/DeepSeek 使用多条 User Message
        """
        code_text = shared_ctx.get_code_block_text()

        if self._provider == "anthropic":
            raw = build_anthropic_messages(
                system_prompt=self.system_prompt,
                code_block_text=code_text,
                final_instruction=final_instruction,
                extra_contents=extra_contents,
            )
        else:
            raw = build_openai_messages(
                system_prompt=self.system_prompt,
                code_block_text=code_text,
                final_instruction=final_instruction,
                extra_contents=extra_contents,
            )

        # 转为 LangChain 消息对象
        msgs = []
        for msg in raw:
            if msg["role"] == "system":
                content = msg["content"]
                # Anthropic 的 system 也会是 content blocks 吗？通常是纯字符串
                if isinstance(content, list):
                    content = " ".join(b.get("text", "") for b in content if b.get("type") == "text")
                msgs.append(SystemMessage(content=content))
            elif msg["role"] == "user":
                content = msg["content"]
                if isinstance(content, list):
                    # Anthropic content blocks → 拼成纯文本
                    texts = [b["text"] for b in content if b.get("type") == "text"]
                    content = "\n\n".join(texts)
                msgs.append(HumanMessage(content=content))
        return msgs

    # ================================================================
    # 输出解析
    # ================================================================

    def _parse_response(self, content: str) -> AgentReview:
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
                return AgentReview(
                    agent_name=self.agent_name,
                    agent_label=self.agent_label,
                    model_used=self.model_name,
                    findings=[],
                    summary="（解析失败）",
                )

        finding_model = get_finding_model(self.output_schema)
        findings = []
        for item in data.get("findings", []):
            try:
                findings.append(finding_model(**item))
            except Exception as e:
                logger.warning(f"[{self.agent_label}] 跳过无效 finding: {e}")

        return AgentReview(
            agent_name=self.agent_name,
            agent_label=self.agent_label,
            model_used=self.model_name,
            confidence=data.get("confidence", 1.0),
            overall_score=data.get("overall_score", 0.0),
            findings=findings,
            summary=data.get("summary", ""),
        )

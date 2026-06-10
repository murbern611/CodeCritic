"""
CodeCritic — LangGraph 图节点函数

定义了代码审查流程的各个节点：
  parse → review(并行) → judge → debate(可选) → arbitrate
"""

from __future__ import annotations

import concurrent.futures
from typing import Any, Optional

from src.agents.base import BaseAgent
from src.agents.security_agent import SecurityAgent
from src.agents.performance_agent import PerformanceAgent
from src.agents.style_agent import StyleAgent
from src.agents.correctness_agent import CorrectnessAgent
from src.agents.architecture_agent import ArchitectureAgent
from src.agents.judge_agent import JudgeAgent
from src.cache.prompt_cache import (
    CacheReport,
    SharedCodeContext,
    group_agents_by_model,
)
from src.graph.state import CodeCriticState
from src.models.schemas import (
    ConflictPair,
    DebateArgument,
    DebateResult,
    DebateRound,
    FinalReport,
    FinalReportFinding,
)
from src.memory.base import MemoryManager
from src.tracking.token_tracker import TokenTracker
from src.utils.logger import logger

# 跨节点缓存：review 产生的 SharedCodeContext 和 Agent 实例，供 debate 复用
_shared_ctx_cache: Optional[SharedCodeContext] = None
_agents_cache: dict[str, BaseAgent] = {}

MAX_DEBATE_ROUNDS = 3
CONVERGE_KEYWORDS = ["concede", "agreed", "you're right", "fair point",
                     "good point", "that's valid", "i agree", "you are right",
                     "correct", "fair enough", "conceded", "agreement"]


# ============================================================
# 记忆上下文格式化
# ============================================================

def _format_memory_context(report_dict: dict) -> Optional[str]:
    """
    将历史 FinalReport（dict 格式）转为 Agent 可读的记忆上下文。
    只提取 findings 中最关键的 10 条，按严重度排序。
    """
    findings = report_dict.get("all_findings", [])
    if not findings:
        return None

    # 按严重度排序
    sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    findings.sort(key=lambda f: sev_rank.get(
        f.get("original_finding", {}).get("severity", "info"), 99
    ))

    parts = [
        "【历史审查记录 — 上次评审发现的问题，请重点检查本次代码中是否已修复】",
        "",
    ]
    for i, f in enumerate(findings[:10], 1):
        of = f.get("original_finding", {})
        severity = of.get("severity", "unknown")
        title = of.get("title", "")
        source = f.get("source_agent", "")
        loc = of.get("location", {})
        line_str = ""
        if loc and loc.get("line_start"):
            line_str = f" (L{loc['line_start']}"
            if loc.get("line_end") and loc["line_end"] != loc["line_start"]:
                line_str += f"-{loc['line_end']}"
            line_str += ")"
        desc = of.get("description", "")
        parts.append(f"{i}. [{severity.upper()}] {title}{line_str} — {source}")
        if desc:
            parts.append(f"   描述: {desc[:200]}")
        parts.append("")

    parts.append("请在本次审查中重点关注以上问题是否已修复。")
    parts.append("如果发现未修复，请在 findings 中再次指出。")
    return "\n".join(parts)


# ============================================================
# 解析输入
# ============================================================

def parse_code(state: CodeCriticState) -> dict:
    """解析输入代码，检测语言类型。"""
    code = state.get("code", "")
    file_path = state.get("file_path")
    context = state.get("context", {})

    if not code and file_path:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                code = f.read()
            context["file_path"] = file_path
        except FileNotFoundError:
            return {"errors": [f"File not found: {file_path}"]}

    language = context.get("language")
    if not language and file_path:
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        lang_map = {
            "py": "python", "js": "javascript", "ts": "typescript",
            "java": "java", "go": "go", "rs": "rust", "rb": "ruby",
            "php": "php", "c": "c", "cpp": "cpp", "cs": "csharp",
            "swift": "swift", "kt": "kotlin", "scala": "scala",
        }
        language = lang_map.get(ext, ext)

    logger.info(f"解析代码: language={language}, length={len(code)}")
    return {"code": code, "code_language": language, "context": context}


# ============================================================
# Agent 工厂
# ============================================================

def create_agents(
    config: Optional[dict[str, Any]] = None,
    token_tracker: Optional[TokenTracker] = None,
    models_config: Optional[dict[str, Any]] = None,
) -> dict[str, BaseAgent]:
    """根据配置创建审查 Agent 实例。"""
    if config is None:
        from src.utils.config_loader import load_agents_config
        config = load_agents_config()

    agent_map = {
        "security_expert": SecurityAgent,
        "performance_expert": PerformanceAgent,
        "style_expert": StyleAgent,
        "correctness_expert": CorrectnessAgent,
        "architecture_expert": ArchitectureAgent,
    }

    system_agents = {"judge", "arbiter"}

    agents = {}
    for agent_key, agent_cfg in config.items():
        if agent_key in system_agents:
            continue
        if not agent_cfg.get("enabled", True):
            continue

        agent_cls = agent_map.get(agent_key)
        if agent_cls is None:
            logger.warning(f"未知 Agent 类型: {agent_key}")
            continue

        agent = agent_cls(
            model_name=agent_cfg.get("model", "gpt-4o-mini"),
            temperature=agent_cfg.get("temperature", 0.2),
            max_tokens=agent_cfg.get("max_tokens", 4096),
            models_config=models_config,
            token_tracker=token_tracker,
            enabled=True,
        )
        agents[agent_key] = agent

    return agents


# ============================================================
# 模型配置合并（review 和 judge 共用）
# ============================================================

def _apply_model_overrides(
    models_config: dict,
    agents_config: dict,
    state: CodeCriticState,
):
    """从 state 中读取前端覆盖的模型配置并合并到 config 中。"""
    model_overrides = state.get("model_configs")
    if model_overrides:
        for model_name, overrides in model_overrides.items():
            if model_name in models_config:
                if overrides.get("api_key"):
                    models_config[model_name]["api_key"] = overrides["api_key"]
                if overrides.get("base_url"):
                    models_config[model_name]["base_url"] = overrides["base_url"]

    custom_models = state.get("custom_models")
    if custom_models:
        models_config.update(custom_models)

    agent_models = state.get("agent_models")
    if agent_models:
        for agent_key, model_name in agent_models.items():
            if agent_key in agents_config:
                agents_config.setdefault(agent_key, {})["model"] = model_name


# ============================================================
# 审查（并行执行 + KV Cache 缓存预热）
# ============================================================

def review_code(state: CodeCriticState) -> dict:
    """
    多 Agent 并行审查代码。

    执行策略：
      1. 按模型分组（同 provider + model_name + base_url）
      2. 每组第一个 Agent 先执行（写入 KV Cache）
      3. 每组后续 Agent 并行执行（命中 KV Cache）
      4. 不同模型组之间第一阶段也并行
    """
    code = state.get("code", "")
    if not code:
        return {"errors": ["No code to review"]}

    context = state.get("context", {})
    context["language"] = state.get("code_language")

    from src.utils.config_loader import load_agents_config, load_models_config

    models_config = load_models_config()
    agents_config = load_agents_config()

    # 应用前端覆盖配置
    enabled_agents = state.get("enabled_agents")
    if enabled_agents is not None:
        for key in agents_config:
            if key not in ("judge", "arbiter"):
                agents_config[key]["enabled"] = key in enabled_agents
        logger.info(f"Agent 选择覆盖: {enabled_agents}")

    _apply_model_overrides(models_config, agents_config, state)

    token_tracker = TokenTracker()
    agents = create_agents(agents_config, token_tracker, models_config)

    # 按模型分组
    groups, _ = group_agents_by_model(agents_config, models_config)

    shared_ctx = SharedCodeContext(
        code=code,
        language=state.get("code_language", "python"),
        file_path=state.get("file_path"),
        extra_context=context,
    )

    shared_tokens = shared_ctx.shared_token_estimate
    cache_enabled = shared_tokens > 512

    logger.info(
        f"审查启动: {len(agents)} 个 Agent, "
        f"{len(groups)} 个模型组, "
        f"代码 {shared_tokens} tokens"
        f"{' [缓存预热开启]' if cache_enabled else ''}"
    )

    if cache_enabled:
        for gk, group in groups.items():
            logger.info(
                f"  模型组 [{gk}]: {len(group.agent_keys)} 个 Agent"
                f"（第 1 个预热，其余 %d 个享缓存）" % (len(group.agent_keys) - 1 if len(group.agent_keys) > 1 else 0)
            )

    # === 加载历史记忆 ===
    memory_context = None
    if state.get("memory_enabled", True) and state.get("session_id"):
        try:
            manager = MemoryManager()
            last_review = manager.load_latest_review(state["session_id"])
            if last_review:
                memory_context = _format_memory_context(last_review)
                if memory_context:
                    logger.info(f"  发现历史审查记录 (session={state['session_id'][:12]}…)")
        except Exception as e:
            logger.warning(f"历史记忆加载失败: {e}")

    # === 并行执行（缓存预热 + 同模型组并行） ===
    # 按模型分组：每组第 1 个 Agent 先跑（KV Cache 预热），其余后续并行
    reviews = {}
    first_batch: dict[str, BaseAgent] = {}
    rest_batch: dict[str, BaseAgent] = {}

    for group in groups.values():
        keys = group.agent_keys
        if keys:
            first_batch[keys[0]] = agents[keys[0]]
            for k in keys[1:]:
                rest_batch[k] = agents[k]

    # 单 Agent 组进入第一轮
    for key, agent in agents.items():
        if key not in first_batch and key not in rest_batch:
            first_batch[key] = agent

    def _run_one(agent_key: str, agent_obj: BaseAgent) -> tuple[str, Any]:
        extra = [memory_context] if memory_context else None
        result = agent_obj.review_cached(shared_ctx=shared_ctx, extra_contents=extra)
        logger.info(
            f"  [{agent_obj.agent_label}] "
            f"评分: {result.overall_score}/10 | "
            f"发现: {len(result.findings)} 个 | "
            f"置信度: {result.confidence:.2f}"
        )
        return agent_key, result

    # Phase 1: 每组第一个 Agent 缓存预热（组间并行）
    if first_batch:
        logger.info(f"Phase 1 — 缓存预热: {len(first_batch)} 个 Agent")
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(first_batch), 8)) as ex:
            fs = {ex.submit(_run_one, k, a): k for k, a in first_batch.items()}
            for f in concurrent.futures.as_completed(fs):
                k, r = f.result()
                reviews[k] = r

    # Phase 2: 每组后续 Agent 并行执行（命中 KV Cache）
    if rest_batch:
        logger.info(f"Phase 2 — 并行执行: {len(rest_batch)} 个 Agent（享缓存加速）")
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(rest_batch), 16)) as ex:
            fs = {ex.submit(_run_one, k, a): k for k, a in rest_batch.items()}
            for f in concurrent.futures.as_completed(fs):
                k, r = f.result()
                reviews[k] = r

    # 缓存供 debate 阶段复用
    global _shared_ctx_cache, _agents_cache
    _shared_ctx_cache = shared_ctx
    _agents_cache = agents

    # 缓存效果报告
    if cache_enabled:
        cacheable = sum(len(g.agent_keys) - 1 for g in groups.values() if len(g.agent_keys) > 1)
        report = CacheReport(
            total_agents=len(agents),
            model_groups=len(groups),
            cacheable_calls=cacheable,
            total_tokens_saved=cacheable * shared_tokens,
        )
        report.print_summary()

    logger.info(f"审查完成, 共消耗 {token_tracker.get_total_tokens()} tokens")

    return {
        "agent_reviews": reviews,
        "usage_summary": token_tracker.get_summary(),
    }


# ============================================================
# 分歧检测（Judge）
# ============================================================

def judge_disputes(state: CodeCriticState) -> dict:
    """检测各 Agent 审查结果之间的观点冲突。"""
    reviews = state.get('agent_reviews', {})
    if not reviews:
        return {'errors': ['No reviews to analyze']}

    from src.utils.config_loader import load_agents_config, load_models_config
    agents_config = load_agents_config()
    models_config = load_models_config()

    _apply_model_overrides(models_config, agents_config, state)

    token_tracker = TokenTracker()
    judge_cfg = agents_config.get('judge', {})
    judge = JudgeAgent(
        model_name=judge_cfg.get('model', 'gpt-4o-mini'),
        temperature=judge_cfg.get('temperature', 0.0),
        max_tokens=judge_cfg.get('max_tokens', 4096),
        models_config=models_config,
        token_tracker=token_tracker,
        enabled=judge_cfg.get('enabled', True),
    )

    judge_report = judge.detect_conflicts(reviews, phase='judge')
    return {'judge_report': judge_report}

def debate(state: CodeCriticState) -> dict:
    """
    靶向辩论：仅冲突 Agent 互看对方观点并反驳/辩护。
    每轮检查是否收敛（一方承认或双方达成一致），已达共识的分歧不再辩论。
    """
    judge_report = state.get("judge_report")
    reviews = state.get("agent_reviews", {})
    code = state.get("code", "")
    language = state.get("code_language", "python")
    current_round = state.get("debate_round", 0)
    existing_result = state.get("debate_result")

    if judge_report is None or not judge_report.conflicts_found:
        return {"skip_debate": True}

    conflicts = judge_report.conflicts_found
    if current_round >= MAX_DEBATE_ROUNDS:
        logger.info(f"辩论已达最大轮次 ({MAX_DEBATE_ROUNDS})，结束")
        return {"debate_round": current_round}

    # 只辩论未解决的分歧
    resolved_ids = set()
    if existing_result:
        for rc in existing_result.resolved_conflicts:
            resolved_ids.add((rc.finding_a_id, rc.finding_b_id))

    active_conflicts = [
        c for c in conflicts
        if (c.finding_a_id, c.finding_b_id) not in resolved_ids
    ]
    if not active_conflicts:
        logger.info("所有分歧已解决")
        return {"debate_round": current_round}

    # 复用 review 阶段的 SharedCodeContext 和 Agent 实例
    global _shared_ctx_cache, _agents_cache
    shared_ctx = _shared_ctx_cache if _shared_ctx_cache else SharedCodeContext(code=code, language=language)
    agents = _agents_cache if _agents_cache else {}

    if not agents:
        logger.warning("Agent 缓存为空，重新创建")
        from src.utils.config_loader import load_agents_config, load_models_config
        models_config = load_models_config()
        agents_config = load_agents_config()
        token_tracker = TokenTracker()
        agents = create_agents(agents_config, token_tracker, models_config)

    debate_rounds = list(existing_result.rounds) if existing_result else []

    for conflict in active_conflicts:
        a_key = conflict.agent_a
        b_key = conflict.agent_b
        logger.info(f"辩论第 {current_round + 1} 轮: [{a_key} vs {b_key}]")

        review_a = reviews.get(a_key)
        review_b = reviews.get(b_key)
        agent_a = agents.get(a_key)
        agent_b = agents.get(b_key)
        if not agent_a or not agent_b:
            logger.warning(f"找不到冲突 Agent: {a_key} / {b_key}")
            continue

        a_label = review_a.agent_label if review_a else a_key
        b_label = review_b.agent_label if review_b else b_key
        view_a = f"【{a_label} 的观点】\n{review_a.summary}" if review_a else ""
        view_b = f"【{b_label} 的观点】\n{review_b.summary}" if review_b else ""

        # 双方互看对方的观点并回应
        reply_b = agent_b.debate_reply(shared_ctx, view_a, current_round + 1)
        reply_a = agent_a.debate_reply(shared_ctx, view_b, current_round + 1)

        # 检测是否收敛
        concedes_a = any(kw in reply_b.lower() for kw in CONVERGE_KEYWORDS)
        concedes_b = any(kw in reply_a.lower() for kw in CONVERGE_KEYWORDS)
        is_resolved = concedes_a or concedes_b

        arguments = [
            DebateArgument(
                speaker=conflict.agent_a,
                target_finding_id=conflict.finding_a_id,
                position="concede" if concedes_a else "refute",
                argument=reply_a,
                concedes=concedes_a,
            ),
            DebateArgument(
                speaker=conflict.agent_b,
                target_finding_id=conflict.finding_b_id,
                position="concede" if concedes_b else "refute",
                argument=reply_b,
                concedes=concedes_b,
            ),
        ]

        debate_rounds.append(DebateRound(
            round_number=current_round + 1,
            conflict_pair=conflict,
            arguments=arguments,
            summary=(f"{'✅' if is_resolved else '❌'} {a_key}: {reply_a[:60]} | {b_key}: {reply_b[:60]}"),
        ))

        logger.info(f"  {a_key}: {reply_a[:60]}...")
        logger.info(f"  {b_key}: {reply_b[:60]}...")
        logger.info(f"  {'✅ 收敛' if is_resolved else '❌ 未收敛'} — {a_key} vs {b_key}")

    total_rounds = current_round + 1
    resolved = _get_resolved_conflicts(active_conflicts, debate_rounds)
    unresolved = [c for c in active_conflicts if c not in resolved]
    all_resolved = len(unresolved) == 0

    new_result = DebateResult(
        rounds=debate_rounds,
        total_rounds=total_rounds,
        is_converged=all_resolved or (total_rounds >= MAX_DEBATE_ROUNDS),
        resolved_conflicts=resolved,
        unresolved_conflicts=unresolved,
    )
    return {"debate_result": new_result, "debate_round": total_rounds}


# ============================================================
# 仲裁（汇总 + 记忆持久化）
# ============================================================

def arbitrate(state: CodeCriticState) -> dict:
    """汇总所有 Agent 的审查结果，标记争议，生成最终报告并保存到记忆。"""
    reviews = state.get("agent_reviews", {})
    debate_result = state.get("debate_result")
    usage = state.get("usage_summary")

    all_findings = []
    for agent_name, review in reviews.items():
        for finding in review.findings:
            was_disputed = False
            verdict = "upheld"

            if debate_result:
                for conflict in debate_result.resolved_conflicts:
                    if conflict.finding_a_id.startswith(agent_name) or conflict.finding_b_id.startswith(agent_name):
                        was_disputed = True
                        break
                for conflict in debate_result.unresolved_conflicts:
                    if conflict.finding_a_id.startswith(agent_name) or conflict.finding_b_id.startswith(agent_name):
                        was_disputed = True
                        verdict = "rejected"
                        break

            all_findings.append(FinalReportFinding(
                original_finding=finding,
                source_agent=review.agent_label,
                verdict=verdict,
                was_disputed=was_disputed,
            ))

    # 按严重度排序
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    all_findings.sort(key=lambda f: severity_rank.get(f.original_finding.severity.value, 99))

    scores = [r.overall_score for r in reviews.values()]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    recommendations = []
    for f in all_findings:
        if f.original_finding.suggestion:
            recommendations.append(
                f"[{f.original_finding.severity.value.upper()}] "
                f"{f.original_finding.title}: "
                f"{f.original_finding.suggestion.description}"
            )

    final_report = FinalReport(
        summary=f"共发现 {len(all_findings)} 个问题",
        overall_score=avg_score,
        all_findings=all_findings,
        resolved_disputes=[c.model_dump() for c in (debate_result.resolved_conflicts if debate_result else [])],
        unresolved_disputes=[c.model_dump() for c in (debate_result.unresolved_conflicts if debate_result else [])],
        recommendations=recommendations[:10],
        token_usage=usage,
    )

    logger.info(f"仲裁完成: {avg_score}/10, 共 {len(all_findings)} 个 finding")

    # 保存审查结果到记忆
    if state.get("memory_enabled", True) and state.get("session_id"):
        try:
            manager = MemoryManager()
            manager.save_review(
                session_id=state["session_id"],
                final_report=final_report.model_dump(),
                file_path=state.get("file_path", ""),
                code=state.get("code", ""),
            )
            logger.info(f"  审查结果已保存到记忆 (session={state['session_id'][:12]}…)")
        except Exception as e:
            logger.warning(f"记忆保存失败: {e}")

    return {"final_report": final_report}


# ============================================================
# 工具函数
# ============================================================

def _get_resolved_conflicts(
    active_conflicts: list[ConflictPair],
    debate_rounds: list[DebateRound],
) -> list[ConflictPair]:
    """从辩论轮次中筛选出已收敛（一方承认）的冲突对。"""
    resolved = []
    for conflict in active_conflicts:
        for round_data in debate_rounds:
            if round_data.conflict_pair == conflict:
                if any(arg.concedes for arg in round_data.arguments):
                    resolved.append(conflict)
                    break
    return resolved

"""
CodeCritic — Web 服务器
========================
FastAPI 后端，提供 REST API 和 GPT 风格前端页面。

启动方式:
    cd langchain
    python -m uvicorn web.server:app --host 127.0.0.1 --port 8080 --reload
"""

from __future__ import annotations

import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

# ── 路径设置：确保能 import src.* ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.graph.builder import build_graph
from src.graph.state import CodeCriticState
from src.utils.config_loader import load_agents_config, load_env, load_models_config
from src.utils.logger import logger

load_env()

# ── FastAPI ──

app = FastAPI(title="CodeCritic Web")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 后台任务存储 ──

_review_tasks: dict[str, dict[str, Any]] = {}
_executor = ThreadPoolExecutor(max_workers=2)

# ── 请求/响应模型 ──


class ReviewRequest(BaseModel):
    code: str
    language: str = "python"
    enabled_agents: Optional[list[str]] = None
    skip_debate: bool = False
    memory_enabled: bool = True
    session_id: Optional[str] = None  # 前端对话 ID，用于记忆关联
    agent_models: Optional[dict[str, str]] = None  # agent_key -> model_name
    model_configs: Optional[dict[str, Any]] = None  # model_name -> {api_key, base_url}
    custom_models: Optional[dict[str, Any]] = None  # 前端新增的自定义模型


class DiffReviewRequest(BaseModel):
    """Diff 审查请求：传两个版本或直接传 diff 文本"""
    old_code: str = ""           # 旧版本代码（方式一：双文件对比）
    new_code: str = ""           # 新版本代码
    diff_text: str = ""          # 方式二：直接传 git diff 文本
    language: str = "python"
    enabled_agents: Optional[list[str]] = None
    skip_debate: bool = True     # Diff 场景默认跳过辩论，省 token
    memory_enabled: bool = True
    session_id: Optional[str] = None
    agent_models: Optional[dict[str, str]] = None
    model_configs: Optional[dict[str, Any]] = None
    custom_models: Optional[dict[str, Any]] = None


# ── Agent 元数据（前端展示用） ──

AGENT_META: dict[str, dict[str, str]] = {
    "security_expert": {
        "label": "安全审查专家",
        "icon": "🛡️",
        "description": "检测 SQL 注入、XSS、命令注入等安全漏洞",
    },
    "performance_expert": {
        "label": "性能优化专家",
        "icon": "⚡",
        "description": "识别性能瓶颈、低效算法和资源浪费",
    },
    "style_expert": {
        "label": "代码风格专家",
        "icon": "🎨",
        "description": "检查命名规范、代码格式和最佳实践",
    },
    "correctness_expert": {
        "label": "正确性审查专家",
        "icon": "🐛",
        "description": "发现逻辑错误、边界条件和竞态条件",
    },
    "architecture_expert": {
        "label": "架构设计专家",
        "icon": "🏗️",
        "description": "评估设计模式、模块耦合和可扩展性",
    },
    "judge": {
        "label": "分歧检测器",
        "icon": "🔍",
        "description": "分析各 Agent 审查结果，识别语义冲突",
    },
    "arbiter": {
        "label": "总结生成器",
        "icon": "📋",
        "description": "汇总所有审查结果生成最终报告",
    },
}


# ════════════════════════════════════════════════════
# API 路由
# ════════════════════════════════════════════════════


@app.get("/api/agents")
def list_agents():
    """返回所有可用的审查 Agent 及其状态"""
    agents_config = load_agents_config()
    agents = []
    for key, cfg in agents_config.items():
        meta = AGENT_META.get(key, {})
        agent_type = "system" if key in ("judge", "arbiter") else "review"
        agents.append({
            "key": key,
            "label": meta.get("label", key),
            "icon": meta.get("icon", "🤖"),
            "enabled": cfg.get("enabled", True),
            "model": cfg.get("model", "gpt-4o-mini"),
            "description": meta.get("description", ""),
            "type": agent_type,
        })
    return {"agents": agents}


@app.get("/api/models")
def list_models():
    """返回所有可用的模型配置（API Key 脱敏）"""
    models_config = load_models_config()
    result = {}
    for name, cfg in models_config.items():
        api_key = cfg.get("api_key", "")
        masked = ""
        if api_key and len(api_key) > 8:
            masked = api_key[:4] + "****" + api_key[-4:]
        elif api_key:
            masked = "****"
        result[name] = {
            "provider": cfg.get("provider", ""),
            "model_name": cfg.get("model_name", name),
            "has_api_key": bool(api_key),
            "api_key_preview": masked,
            "base_url": cfg.get("base_url", ""),
            "max_tokens": cfg.get("max_tokens", 4096),
            "temperature": cfg.get("temperature", 0.2),
        }
    return {"models": result}


@app.post("/api/review")
def submit_review(req: ReviewRequest):
    """提交代码审查任务"""
    if not req.code.strip():
        raise HTTPException(400, "代码不能为空")

    task_id = f"rev_{uuid.uuid4().hex[:8]}"
    _review_tasks[task_id] = {"status": "queued", "result": None, "error": None}
    _executor.submit(_run_review, task_id, req)

    return {"task_id": task_id, "status": "queued"}


@app.post("/api/review/diff")
def submit_diff_review(req: DiffReviewRequest):
    """提交 Diff 审查任务

    两种方式：
    1. 传 old_code + new_code → 后端自动生成 diff
    2. 传 diff_text → 直接使用传入的 git diff 文本
    """
    from src.diff.parser import format_diff_for_llm, generate_diff, parse_diff

    if req.diff_text:
        diff_text = req.diff_text
    elif req.old_code and req.new_code:
        diff_text = generate_diff(req.old_code, req.new_code)
    else:
        raise HTTPException(400, "需要提供 old_code + new_code，或直接提供 diff_text")

    diff_result = parse_diff(diff_text)
    if not diff_result.files:
        raise HTTPException(400, "未解析到有效的代码变更")

    llm_diff = format_diff_for_llm(diff_result)

    task_id = f"diff_{uuid.uuid4().hex[:8]}"
    _review_tasks[task_id] = {"status": "queued", "result": None, "error": None}
    _executor.submit(_run_diff_review, task_id, req, llm_diff)

    return {
        "task_id": task_id,
        "status": "queued",
        "summary": {
            "files_changed": len(diff_result.files),
            "additions": sum(len(f.all_added_lines) for f in diff_result.files),
            "deletions": sum(len(f.all_deleted_lines) for f in diff_result.files),
        },
    }


@app.get("/api/review/{task_id}")
def get_review_result(task_id: str):
    """轮询审查任务结果"""
    task = _review_tasks.get(task_id)
    if task is None:
        raise HTTPException(404, "审查任务不存在")
    return task


@app.get("/")
def serve_frontend():
    """提供前端页面"""
    index_path = Path(__file__).parent / "static" / "index.html"
    if not index_path.exists():
        return {"error": "前端页面未生成，请先创建 web/static/index.html"}
    return FileResponse(str(index_path))


@app.get("/logo.png")
def serve_logo():
    """提供 Logo"""
    logo_path = Path(__file__).parent / "static" / "logo.png"
    if not logo_path.exists():
        return {"error": "Logo not found"}
    return FileResponse(str(logo_path))


# ════════════════════════════════════════════════════
# 后台审查任务
# ════════════════════════════════════════════════════


def _run_review(task_id: str, req: ReviewRequest):
    """在后台线程中执行审查流程"""
    _review_tasks[task_id]["status"] = "running"
    try:
        graph = build_graph()

        initial_state: CodeCriticState = {
            "code": req.code,
            "code_language": req.language,
            "skip_debate": req.skip_debate,
            "session_id": req.session_id or f"web_{task_id}",
            "memory_enabled": req.memory_enabled,
            "enabled_agents": req.enabled_agents,
            "agent_models": req.agent_models,
            "model_configs": req.model_configs,
            "custom_models": req.custom_models,
            "context": {"language": req.language},
        }

        config = {"configurable": {"thread_id": task_id}}
        result = graph.invoke(initial_state, config)

        final = result.get("final_report")
        if final is not None:
            report_data = _sanitize_report(final.model_dump())
            _review_tasks[task_id] = {
                "status": "completed",
                "result": report_data,
            }
        else:
            error_msg = result.get("errors", ["未知错误"])[0]
            _review_tasks[task_id]["status"] = "error"
            _review_tasks[task_id]["error"] = error_msg

    except Exception as e:
        logger.error(f"审查任务 {task_id} 失败: {e}")
        _review_tasks[task_id]["status"] = "error"
        _review_tasks[task_id]["error"] = str(e)


def _run_diff_review(task_id: str, req: DiffReviewRequest, llm_diff: str):
    """在后台线程中执行 Diff 审查流程"""
    _review_tasks[task_id]["status"] = "running"
    try:
        graph = build_graph()

        initial_state: CodeCriticState = {
            "code": llm_diff,
            "code_language": "diff",
            "file_path": "diff-review",
            "skip_debate": req.skip_debate,
            "session_id": req.session_id or f"web_diff_{task_id}",
            "memory_enabled": req.memory_enabled,
            "enabled_agents": req.enabled_agents,
            "agent_models": req.agent_models,
            "model_configs": req.model_configs,
            "custom_models": req.custom_models,
            "context": {"language": "diff", "review_mode": "diff"},
            "diff_mode": True,
            "diff_text": llm_diff,
        }

        config = {"configurable": {"thread_id": task_id}}
        result = graph.invoke(initial_state, config)

        final = result.get("final_report")
        if final is not None:
            report_data = _sanitize_report(final.model_dump())
            _review_tasks[task_id] = {
                "status": "completed",
                "result": report_data,
            }
        else:
            error_msg = result.get("errors", ["未知错误"])[0]
            _review_tasks[task_id]["status"] = "error"
            _review_tasks[task_id]["error"] = error_msg

    except Exception as e:
        logger.error(f"Diff 审查任务 {task_id} 失败: {e}")
        _review_tasks[task_id]["status"] = "error"
        _review_tasks[task_id]["error"] = str(e)


def _sanitize_report(data: dict) -> dict:
    """清理报告中不可 JSON 序列化的字段"""
    # 移除超大文本、确保枚举转字符串
    if "all_findings" in data:
        for f in data["all_findings"]:
            if isinstance(f.get("verdict"), dict) and "value" in f["verdict"]:
                f["verdict"] = f["verdict"]["value"]
            if isinstance(f.get("original_finding"), dict):
                of = f["original_finding"]
                if isinstance(of.get("severity"), dict) and "value" in of["severity"]:
                    of["severity"] = of["severity"]["value"]
                if isinstance(of.get("location"), dict):
                    loc = of["location"]
                    loc["line_start"] = loc.get("line_start")
                    loc["line_end"] = loc.get("line_end")
    if "token_usage" in data:
        tu = data["token_usage"]
        if isinstance(tu, dict):
            # 嵌套 total 字段
            if "total" in tu and isinstance(tu["total"], dict):
                tu["total_tokens"] = tu["total"].get("total_tokens", 0)
                tu["total_cost_usd"] = tu["total"].get("cost_usd", 0.0)
    return data

"""
CodeCritic — 测试配置与共享 Fixtures
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest


# ============================================================
# Fixtures: 样本数据
# ============================================================


@pytest.fixture
def sample_code() -> str:
    return """def get_user(user_id):
    query = f"SELECT * FROM users WHERE id = {user_id}"
    return db.execute(query)


def process_items(items):
    result = []
    for i in range(len(items)):
        result.append(items[i] * 2)
    return result
"""


@pytest.fixture
def sample_code_v2() -> str:
    """sample_code 的修改版，用于 diff 测试"""
    return """def get_user(user_id):
    query = "SELECT * FROM users WHERE id = ?"
    return db.execute(query, [user_id])


def process_items(items):
    return [item * 2 for item in items]
"""


@pytest.fixture
def sample_git_diff() -> str:
    return """--- a/user.py
+++ b/user.py
@@ -10,8 +10,8 @@ def get_user(id):
-    query = f"SELECT * FROM users WHERE id = {id}"
+    query = "SELECT * FROM users WHERE id = ?"
     print("fetching user")
-    return db.execute(query)
+    return db.execute(query, [id])
@@ -20,6 +20,8 @@ def process_items(items):
     result = []
-    for i in range(len(items)):
-        result.append(items[i] * 2)
+    for item in items:
+        result.append(item * 2)
+        print(f"processed {item}")
     return result
"""


@pytest.fixture
def sample_agent_reviews() -> dict:
    """模拟的多个 Agent 审查结果"""
    from src.models.schemas import AgentReview, SecurityFinding, PerformanceFinding, CodeLocation, Severity, CodeSuggestion

    return {
        "security_expert": AgentReview(
            agent_name="security_expert",
            agent_label="安全审查专家",
            model_used="gpt-4o-mini",
            overall_score=6.0,
            confidence=0.85,
            findings=[
                SecurityFinding(
                    id="sec-1",
                    severity=Severity.CRITICAL,
                    title="SQL 注入风险",
                    description="使用了 f-string 拼接 SQL 查询",
                    location=CodeLocation(line_start=2, line_end=2, snippet='f"SELECT...'),
                    suggestion=CodeSuggestion(description="改用参数化查询"),
                    vulnerability_type="SQL Injection",
                )
            ],
            summary="发现 1 个安全问题",
        ),
        "performance_expert": AgentReview(
            agent_name="performance_expert",
            agent_label="性能优化专家",
            model_used="gpt-4o-mini",
            overall_score=7.0,
            confidence=0.75,
            findings=[
                PerformanceFinding(
                    id="perf-1",
                    severity=Severity.MEDIUM,
                    title="低效循环",
                    description="使用索引遍历列表，效率较低",
                    location=CodeLocation(line_start=9, line_end=11),
                    suggestion=CodeSuggestion(description="改用直接遍历"),
                    complexity="O(n)",
                    estimated_impact="低",
                )
            ],
            summary="发现 1 个性能问题",
        ),
    }


# ============================================================
# Fixtures: 临时目录和文件
# ============================================================


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def temp_config_dir(temp_dir: Path) -> Path:
    """创建临时配置目录"""
    config_dir = temp_dir / "config"
    config_dir.mkdir()
    return config_dir


@pytest.fixture
def temp_db_path(temp_dir: Path) -> str:
    return str(temp_dir / "test_memory.db")


# ============================================================
# Fixtures: 测试用配置
# ============================================================


@pytest.fixture
def mock_settings() -> dict[str, Any]:
    return {
        "project": {"name": "CodeCritic", "version": "0.1.0"},
        "output": {"report_dir": "./data/reports", "formats": ["markdown", "json"]},
        "ui": {"show_progress": False},
        "memory": {"enabled": True, "backend": "sqlite", "path": "./data/memory/memory.db"},
    }

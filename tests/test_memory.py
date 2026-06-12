"""
测试记忆系统
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.memory.base import MemoryEntry, MemoryManager, SessionMemory, SQLiteMemory


class TestSessionMemory:
    """测试会话记忆（内存）"""

    def test_save_and_load(self):
        """核心：保存后能读取"""
        mem = SessionMemory()
        mem.save("key1", "这是一条记忆", "review", {"agent": "security"})

        entry = mem.load("key1")
        assert entry is not None
        assert entry.content == "这是一条记忆"
        assert entry.entry_type == "review"
        assert entry.metadata == {"agent": "security"}

    def test_load_nonexistent(self):
        """边界：读取不存在的 key"""
        mem = SessionMemory()
        assert mem.load("nonexistent") is None

    def test_delete(self):
        """基础：删除记忆"""
        mem = SessionMemory()
        mem.save("key1", "content")
        mem.delete("key1")
        assert mem.load("key1") is None

    def test_clear(self):
        """基础：清空所有记忆"""
        mem = SessionMemory()
        mem.save("k1", "c1")
        mem.save("k2", "c2")
        mem.clear()
        assert mem.load("k1") is None
        assert mem.load("k2") is None

    def test_search_by_content(self):
        """核心：关键词搜索"""
        mem = SessionMemory()
        mem.save("key1", "SQL 注入风险")
        mem.save("key2", "性能优化建议")
        mem.save("key3", "XSS 攻击")

        results = mem.search("注入")
        assert len(results) >= 1
        assert any("SQL" in r.content for r in results)

    def test_get_recent(self):
        """基础：获取最近记录"""
        mem = SessionMemory()
        mem.save("k1", "first")
        mem.save("k2", "second")

        recent = mem.get_recent(limit=2)
        assert len(recent) == 2


class TestSQLiteMemory:
    """测试 SQLite 持久化记忆"""

    def test_save_and_load(self, temp_db_path: str):
        """核心：持久化保存后能读取"""
        mem = SQLiteMemory(temp_db_path, "test_memory")
        mem.save("k1", "持久化内容", "review", {"version": 1})

        entry = mem.load("k1")
        assert entry is not None
        assert entry.content == "持久化内容"
        assert entry.metadata == {"version": 1}

    def test_persistence_across_instances(self, temp_db_path: str):
        """核心：跨实例持久化"""
        mem1 = SQLiteMemory(temp_db_path, "test_memory")
        mem1.save("persist_key", "跨实例数据")

        mem2 = SQLiteMemory(temp_db_path, "test_memory")
        entry = mem2.load("persist_key")
        assert entry is not None
        assert entry.content == "跨实例数据"

    def test_overwrite(self, temp_db_path: str):
        """基础：覆写已有 key"""
        mem = SQLiteMemory(temp_db_path, "test_memory")
        mem.save("k1", "旧内容")
        mem.save("k1", "新内容")

        entry = mem.load("k1")
        assert entry.content == "新内容"

    def test_delete(self, temp_db_path: str):
        """基础：删除"""
        mem = SQLiteMemory(temp_db_path, "test_memory")
        mem.save("k1", "content")
        mem.delete("k1")
        assert mem.load("k1") is None

    def test_search_like(self, temp_db_path: str):
        """核心：SQL LIKE 搜索"""
        mem = SQLiteMemory(temp_db_path, "test_memory")
        mem.save("sql_note", "SQL 注入修复方案")
        mem.save("perf_note", "性能优化建议")

        results = mem.search("SQL")
        assert len(results) >= 1

    def test_clear(self, temp_db_path: str):
        """基础：清空"""
        mem = SQLiteMemory(temp_db_path, "test_memory")
        mem.save("k1", "c1")
        mem.clear()
        assert mem.load("k1") is None

    def test_touch_updates_timestamp(self, temp_db_path: str):
        """核心：访问更新最后访问时间"""
        import time
        mem = SQLiteMemory(temp_db_path, "test_memory")
        mem.save("k1", "content")
        entry1 = mem.load("k1")
        access1 = entry1.last_accessed

        time.sleep(0.01)
        entry2 = mem.load("k1")
        access2 = entry2.last_accessed

        assert access2 >= access1


class TestMemoryManager:
    """测试记忆管理器"""

    def test_save_and_load_review(self, temp_db_path: str):
        """核心：保存和加载审查记录"""
        manager = MemoryManager(db_path=temp_db_path)
        report = {
            "summary": "测试报告",
            "overall_score": 7.5,
            "all_findings": [],
        }

        manager.save_review(
            session_id="test_session",
            final_report=report,
            file_path="test.py",
            code="print('hello')",
        )

        loaded = manager.load_latest_review("test_session")
        assert loaded is not None
        assert loaded["overall_score"] == 7.5

    def test_load_no_review(self, temp_db_path: str):
        """边界：没有记录应返回 None"""
        manager = MemoryManager(db_path=temp_db_path)
        assert manager.load_latest_review("nonexistent") is None

    def test_load_latest_only(self, temp_db_path: str):
        """核心：load_latest_review 只返回最新一条"""
        manager = MemoryManager(db_path=temp_db_path)
        manager.save_review("session_1", {"summary": "old"}, "f1.py", "old_code")
        manager.save_review("session_1", {"summary": "new"}, "f2.py", "new_code")

        latest = manager.load_latest_review("session_1")
        assert latest is not None
        assert latest["summary"] == "new"

    def test_different_sessions(self, temp_db_path: str):
        """核心：不同 session 的记录不互相干扰"""
        manager = MemoryManager(db_path=temp_db_path)
        manager.save_review("session_a", {"summary": "A的结果"})
        manager.save_review("session_b", {"summary": "B的结果"})

        result_a = manager.load_latest_review("session_a")
        result_b = manager.load_latest_review("session_b")
        assert result_a["summary"] == "A的结果"
        assert result_b["summary"] == "B的结果"

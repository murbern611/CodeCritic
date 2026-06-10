"""
CodeCritic — 记忆系统基类

三级记忆：
- 会话记忆（SessionMemory）：单次运行，存在内存中
- 项目记忆（ProjectMemory）：单个项目，存 SQLite
- 全局记忆（GlobalMemory）：跨项目经验，存 SQLite
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


class MemoryEntry:
    """一条记忆记录"""
    def __init__(
        self,
        key: str,
        content: str,
        entry_type: str = "review",
        metadata: Optional[dict] = None,
    ):
        self.key = key
        self.content = content
        self.entry_type = entry_type
        self.metadata = metadata or {}
        self.created_at = datetime.now()
        self.last_accessed = datetime.now()

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "content": self.content,
            "type": self.entry_type,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
        }


class BaseMemory(ABC):
    """记忆基类"""

    @abstractmethod
    def save(self, key: str, content: str, entry_type: str = "review",
             metadata: Optional[dict] = None):
        """保存一条记忆"""

    @abstractmethod
    def load(self, key: str) -> Optional[MemoryEntry]:
        """读取一条记忆"""

    @abstractmethod
    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        """搜索相关记忆"""

    @abstractmethod
    def get_recent(self, limit: int = 10) -> list[MemoryEntry]:
        """获取最近记忆"""

    @abstractmethod
    def delete(self, key: str):
        """删除记忆"""

    @abstractmethod
    def clear(self):
        """清空所有记忆"""


class SessionMemory(BaseMemory):
    """
    会话记忆——存在内存中，进程结束即消失。
    用于跟踪当前对话上下文。
    """

    def __init__(self, ttl_seconds: int = 3600):
        self._entries: dict[str, MemoryEntry] = {}
        self.ttl_seconds = ttl_seconds

    def save(self, key: str, content: str, entry_type: str = "review",
             metadata: Optional[dict] = None):
        self._entries[key] = MemoryEntry(
            key=key,
            content=content,
            entry_type=entry_type,
            metadata=metadata,
        )

    def load(self, key: str) -> Optional[MemoryEntry]:
        entry = self._entries.get(key)
        if entry:
            entry.last_accessed = datetime.now()
        return entry

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        """简单的关键词匹配搜索"""
        query_lower = query.lower()
        results = []
        for entry in self._entries.values():
            if (query_lower in entry.content.lower()
                    or query_lower in entry.key.lower()):
                results.append(entry)
        results.sort(key=lambda e: e.last_accessed, reverse=True)
        return results[:limit]

    def get_recent(self, limit: int = 10) -> list[MemoryEntry]:
        entries = sorted(
            self._entries.values(),
            key=lambda e: e.created_at,
            reverse=True,
        )
        return entries[:limit]

    def delete(self, key: str):
        self._entries.pop(key, None)

    def clear(self):
        self._entries.clear()


class SQLiteMemory(BaseMemory):
    """
    SQLite 持久化记忆。
    用于项目记忆和全局记忆。
    """

    def __init__(self, db_path: str, table_name: str = "memory"):
        self.db_path = db_path
        self.table_name = table_name
        self._init_db()

    def _init_db(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self.table_name} (
                key TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                entry_type TEXT DEFAULT 'review',
                metadata TEXT DEFAULT '{{}}',
                created_at TEXT NOT NULL,
                last_accessed TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def save(self, key: str, content: str, entry_type: str = "review",
             metadata: Optional[dict] = None):
        now = datetime.now().isoformat()
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"""
            INSERT OR REPLACE INTO {self.table_name}
            (key, content, entry_type, metadata, created_at, last_accessed)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (key, content, entry_type, meta_str, now, now))
        conn.commit()
        conn.close()

    def load(self, key: str) -> Optional[MemoryEntry]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            f"SELECT * FROM {self.table_name} WHERE key = ?",
            (key,),
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None

        entry = MemoryEntry(
            key=row[0],
            content=row[1],
            entry_type=row[2],
            metadata=json.loads(row[3]),
        )
        entry.created_at = datetime.fromisoformat(row[4])
        entry.last_accessed = datetime.fromisoformat(row[5])

        # 更新访问时间
        self._touch(key)
        return entry

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            f"SELECT * FROM {self.table_name} WHERE content LIKE ? "
            f"OR key LIKE ? ORDER BY last_accessed DESC LIMIT ?",
            (f"%{query}%", f"%{query}%", limit),
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_entry(r) for r in rows]

    def get_recent(self, limit: int = 10) -> list[MemoryEntry]:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute(
            f"SELECT * FROM {self.table_name} "
            f"ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        conn.close()
        return [self._row_to_entry(r) for r in rows]

    def delete(self, key: str):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            f"DELETE FROM {self.table_name} WHERE key = ?",
            (key,),
        )
        conn.commit()
        conn.close()

    def clear(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"DELETE FROM {self.table_name}")
        conn.commit()
        conn.close()

    def _touch(self, key: str):
        """更新访问时间"""
        now = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            f"UPDATE {self.table_name} SET last_accessed = ? WHERE key = ?",
            (now, key),
        )
        conn.commit()
        conn.close()

    def _row_to_entry(self, row) -> MemoryEntry:
        entry = MemoryEntry(
            key=row[0],
            content=row[1],
            entry_type=row[2],
            metadata=json.loads(row[3]),
        )
        entry.created_at = datetime.fromisoformat(row[4])
        entry.last_accessed = datetime.fromisoformat(row[5])
        return entry


class MemoryManager:
    """
    记忆管理器——统一管理三级记忆。

    使用方式：
        manager = MemoryManager(db_path="./data/memory/memory.db")
        manager.save("project_X", "review_result", "review", {...})
        result = manager.search("eval() 安全问题")
    """

    def __init__(
        self,
        db_path: str = "./data/memory/memory.db",
        session_ttl: int = 3600,
    ):
        self.session = SessionMemory(ttl_seconds=session_ttl)
        self.project = SQLiteMemory(db_path, "project_memory")
        self.global_memory = SQLiteMemory(db_path, "global_memory")
        self._db_path = db_path
        self._ensure_review_table()

    def save(
        self,
        key: str,
        content: str,
        entry_type: str = "review",
        level: str = "session",
        metadata: Optional[dict] = None,
    ):
        """保存到指定级别的记忆"""
        store = self._get_store(level)
        store.save(key, content, entry_type, metadata)

    def load(self, key: str, level: str = "session") -> Optional[MemoryEntry]:
        store = self._get_store(level)
        return store.load(key)

    def search(
        self,
        query: str,
        limit: int = 5,
        levels: Optional[list[str]] = None,
    ) -> list[MemoryEntry]:
        """跨级别搜索记忆"""
        if levels is None:
            levels = ["session", "project", "global"]
        results = []
        for level in levels:
            store = self._get_store(level)
            results.extend(store.search(query, limit))
        # 按访问时间排序
        results.sort(key=lambda e: e.last_accessed, reverse=True)
        return results[:limit]

    def get_context(self, code_context: str, limit: int = 3) -> str:
        """
        获取与当前代码相关的历史记忆，拼成上下文文本。
        用于注入到 Agent 的 prompt 中。
        """
        entries = self.search(code_context, limit=limit)
        if not entries:
            return ""

        context_parts = ["【历史记忆参考】"]
        for i, entry in enumerate(entries, 1):
            context_parts.append(
                f"{i}. [{entry.entry_type}] {entry.key}: {entry.content[:200]}"
            )
        return "\n".join(context_parts)

    def _get_store(self, level: str) -> BaseMemory:
        if level == "session":
            return self.session
        elif level == "project":
            return self.project
        elif level == "global":
            return self.global_memory
        raise ValueError(f"Unknown memory level: {level}")

    # ================================================================
    # Review History（审查历史记忆）
    # ================================================================

    def _ensure_review_table(self):
        """创建 review_history 表（用户索引 + 审查结果持久化）"""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS review_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                file_path TEXT DEFAULT '',
                code_hash TEXT DEFAULT '',
                final_report TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_review_history_session "
            "ON review_history(session_id)"
        )
        conn.commit()
        conn.close()

    def save_review(
        self,
        session_id: str,
        final_report: dict,
        file_path: str = "",
        code: str = "",
    ) -> None:
        """
        保存审查结果到历史记忆。

        Args:
            session_id: 用户/会话索引
            final_report: FinalReport.model_dump() 输出的 dict
            file_path: 文件路径（可选）
            code: 原始代码（用于计算 code_hash）
        """
        now = datetime.now().isoformat()
        code_hash = hashlib.md5(code.encode()).hexdigest() if code else ""
        report_json = json.dumps(final_report, ensure_ascii=False, default=str)
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT INTO review_history "
            "(session_id, file_path, code_hash, final_report, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, file_path, code_hash, report_json, now),
        )
        conn.commit()
        conn.close()

    def load_latest_review(self, session_id: str) -> Optional[dict]:
        """
        加载指定会话的最新审查结果。

        Args:
            session_id: 用户/会话索引

        Returns:
            FinalReport 的 dict 格式，或 None
        """
        conn = sqlite3.connect(self._db_path)
        cursor = conn.execute(
            "SELECT final_report FROM review_history "
            "WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        )
        row = cursor.fetchone()
        conn.close()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

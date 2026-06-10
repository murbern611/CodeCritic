"""CodeCritic 记忆数据库查看（直接运行版）"""
import sqlite3, json, sys
from pathlib import Path

# 自动定位到 langchain 目录
db = Path(__file__).resolve().parent / 'data' / 'memory' / 'memory.db'
if not db.exists():
    print(f'[错误] 找不到数据库: {db}')
    sys.exit(1)

conn = sqlite3.connect(str(db))
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
print('数据库表:', [r[0] for r in cur.fetchall()])

cur.execute('SELECT COUNT(*) FROM review_history')
print(f'\n审查历史: {cur.fetchone()[0]} 条\n')

cur.execute('SELECT id, session_id, created_at FROM review_history ORDER BY id DESC')
for r in cur.fetchall():
    print(f'  #{r[0]}  session={r[1]}  时间={r[2]}')

conn.close()

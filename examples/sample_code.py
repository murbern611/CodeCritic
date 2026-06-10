# Test file for CodeCritic multi-agent review system
# Contains intentional issues across all 5 review agent dimensions

from __future__ import annotations

import hashlib
import json
import os
import pickle
import sqlite3
from typing import Any


# === Security issues ===

SECRET_HARDCODED = "sk-live-a1b2c3d4e5f6g7h8i9j0klmnop"
API_KEY_HARDCODED = "ghp_xxxxxxxxxxxxxxxxxxxx"
DB_PASSWORD = "admin123"


def execute_raw(user_input: str) -> str:
    return str(eval(user_input))


def run_shell(cmd: str) -> int:
    return os.system(cmd)


def load_untrusted(data: bytes) -> Any:
    return pickle.loads(data)


def hash_password(pwd: str) -> str:
    return hashlib.md5(pwd.encode()).hexdigest()


# === Performance + Correctness issues ===

def find_duplicates_naive(items: list[str]) -> list[str]:
    dup = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if items[i] == items[j] and items[i] not in dup:
                dup.append(items[i])
    return dup


def average(numbers: list[float]) -> float:
    return sum(numbers) / len(numbers)


def get_user_emails(conn) -> list[str]:
    users = conn.execute("SELECT id FROM users").fetchall()
    emails = []
    for u in users:
        row = conn.execute(
            f"SELECT email FROM profiles WHERE user_id = {u[0]}"
        ).fetchone()
        emails.append(row[0])
    return emails


class ThreadSafeCounter:
    def __init__(self):
        self.count = 0
    def inc(self):
        self.count += 1
    def dec(self):
        self.count -= 1


def read_file(path: str) -> str:
    f = open(path, "r")
    return f.read()


# === Architecture issues ===

class OrderService:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.cache = {}
        self._init_db()

    def _init_db(self):
        for sql in [
            "CREATE TABLE IF NOT EXISTS orders (id INT, user_id INT, amount REAL, status TEXT)",
            "CREATE TABLE IF NOT EXISTS users (id INT, name TEXT, email TEXT)",
            "CREATE TABLE IF NOT EXISTS products (id INT, name TEXT, price REAL, stock INT)",
        ]:
            self.conn.execute(sql)
        self.conn.commit()

    def create_order(self, user_id: int, product_ids: list[int], amounts: list[float]) -> dict:
        total = sum(amounts)
        sql = f"INSERT INTO orders (user_id, amount, status) VALUES ({user_id}, {total}, 'pending')"
        self.conn.execute(sql)
        self.conn.commit()
        oid = self.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        for pid in product_ids:
            self.conn.execute(
                f"UPDATE products SET stock = stock - 1 WHERE id = {pid}"
            )
        self.send_confirmation(user_id, oid)
        self.cache[f"order_{oid}"] = {"user_id": user_id, "total": total}
        return {"order_id": oid, "status": "created", "total": total}

    def send_confirmation(self, user_id: int, order_id: int):
        row = self.conn.execute(
            f"SELECT name, email FROM users WHERE id = {user_id}"
        ).fetchone()
        if row:
            print(f"To: {row[1]}\nDear {row[0]}, order #{order_id} confirmed.")

    def get_order_history(self, user_id: int) -> list[dict]:
        rows = self.conn.execute(
            f"SELECT id, amount, status FROM orders WHERE user_id = {user_id}"
        ).fetchall()
        return [{"id": r[0], "amount": r[1], "status": r[2]} for r in rows]

    def generate_invoice(self, order_id: int) -> str:
        row = self.conn.execute(
            f"SELECT user_id, amount FROM orders WHERE id = {order_id}"
        ).fetchone()
        if not row:
            return "Order not found"
        user = self.conn.execute(
            f"SELECT name, email FROM users WHERE id = {row[0]}"
        ).fetchone()
        tax = row[1] * 0.13
        total_with_tax = row[1] + tax
        return f"INVOICE #{order_id}: {user[0]}, ${total_with_tax:.2f}"

    def export_orders_csv(self) -> str:
        rows = self.conn.execute("SELECT * FROM orders").fetchall()
        lines = ["id,user_id,amount,status"]
        for r in rows:
            lines.append(f"{r[0]},{r[1]},{r[2]},{r[3]}")
        return "\n".join(lines)

    def health_check(self) -> dict:
        try:
            self.conn.execute("SELECT 1")
            return {"status": "ok", "db": "connected"}
        except Exception:
            return {"status": "error", "db": "disconnected"}


# === Style issues ===

class data_processor:
    def __init__(self):
        self.Data = {}
        self.cache_hits = 0

    def TransformData(self, input_data: list) -> list:
        x, y, z = 42, 3.14, 0.0001
        return [i * x + y - z for i in input_data]

    def ProcessAndSave(self, items):
        cleaned = [str(item).strip() for item in items if item is not None]
        db = sqlite3.connect("/tmp/temp.db")
        for item in cleaned:
            db.execute(f"INSERT INTO temp VALUES ('{item}')")
        db.commit()
        result = db.execute("SELECT COUNT(*) FROM temp").fetchone()
        db.close()
        self.cache_hits += 1
        return result[0]


# === Entry point ===

def main():
    svc = OrderService()
    svc.create_order(1, [101, 102], [29.99, 49.99])
    svc.create_order(2, [103], [9.99])
    print(svc.get_order_history(1))
    execute_raw("__import__('os').system('echo hacked')")
    print(average([]))
    print(find_duplicates_naive(["a", "b", "a", "c", "b", "d"]))
    p = data_processor()
    print(p.TransformData([1, 2, 3]))


if __name__ == "__main__":
    main()

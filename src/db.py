"""
数据库模块
使用 SQLite 存储监控状态、变更历史和追踪版本
"""

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import Config


class Database:
    """OFAC 监控数据库"""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = str(db_path or Config.DB_PATH)
        self.conn: Optional[sqlite3.Connection] = None

    # ==================== 连接管理 ====================

    def connect(self):
        """建立数据库连接"""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        return self

    def close(self):
        """关闭数据库连接"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, *args):
        self.close()

    # ==================== 表初始化 ====================

    def init_tables(self):
        """创建数据库表"""
        self.conn.executescript("""
            -- 监控状态表：追踪每次检查
            CREATE TABLE IF NOT EXISTS monitor_state (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                check_date      TEXT NOT NULL,
                dataset_version TEXT,
                last_change     TEXT,
                entity_count    INTEGER,
                target_count    INTEGER,
                changes_found   INTEGER DEFAULT 0,
                delta_processed INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            -- 变更记录表：存储从 delta 解析出的每条变更
            CREATE TABLE IF NOT EXISTS changes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                check_date      TEXT NOT NULL,
                version         TEXT NOT NULL,
                operation       TEXT NOT NULL,  -- 'ADD', 'MOD', 'DEL'
                entity_id       TEXT,
                entity_name     TEXT,
                entity_schema   TEXT,           -- Person, Organization, Vessel, etc.
                programs        TEXT,           -- JSON array of sanction programs
                countries       TEXT,           -- JSON array of countries
                is_target       INTEGER DEFAULT 1,
                delta_raw       TEXT,           -- raw JSON from delta
                created_at      TEXT DEFAULT (datetime('now'))
            );

            -- 索引
            CREATE INDEX IF NOT EXISTS idx_changes_check_date
                ON changes(check_date);
            CREATE INDEX IF NOT EXISTS idx_changes_operation
                ON changes(operation, check_date);
            CREATE INDEX IF NOT EXISTS idx_changes_schema
                ON changes(entity_schema);
            CREATE INDEX IF NOT EXISTS idx_monitor_check_date
                ON monitor_state(check_date);
        """)
        self.conn.commit()

    # ==================== 监控状态操作 ====================

    def get_latest_version(self) -> Optional[str]:
        """获取最近一次检查的版本号"""
        row = self.conn.execute(
            "SELECT dataset_version FROM monitor_state "
            "WHERE delta_processed = 1 "
            "ORDER BY check_date DESC LIMIT 1"
        ).fetchone()
        return row["dataset_version"] if row else None

    def get_latest_check_date(self) -> Optional[str]:
        """获取最近一次检查的日期"""
        row = self.conn.execute(
            "SELECT check_date FROM monitor_state ORDER BY check_date DESC LIMIT 1"
        ).fetchone()
        return row["check_date"] if row else None

    def save_monitor_state(self, check_date: str, version: str,
                           last_change: str, entity_count: int,
                           target_count: int, changes_found: int = 0,
                           delta_processed: int = 0):
        """保存监控状态"""
        self.conn.execute("""
            INSERT INTO monitor_state
                (check_date, dataset_version, last_change, entity_count,
                 target_count, changes_found, delta_processed)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (check_date, version, last_change, entity_count,
              target_count, changes_found, delta_processed))
        self.conn.commit()

    # ==================== 变更操作 ====================

    def save_changes_batch(self, changes: list):
        """批量保存变更记录"""
        self.conn.executemany("""
            INSERT INTO changes
                (check_date, version, operation, entity_id, entity_name,
                 entity_schema, programs, countries, is_target, delta_raw)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, changes)
        self.conn.commit()

    def get_changes_by_date(self, check_date: str) -> list:
        """获取指定日期的变更"""
        rows = self.conn.execute("""
            SELECT * FROM changes WHERE check_date = ?
            ORDER BY operation, entity_schema, entity_name
        """, (check_date,)).fetchall()
        return [dict(r) for r in rows]

    def get_recent_changes(self, limit: int = 100) -> list:
        """获取最近的变更"""
        rows = self.conn.execute("""
            SELECT * FROM changes
            ORDER BY check_date DESC, operation, entity_name
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_change_stats(self, check_date: str) -> dict:
        """获取某次检查的变更统计"""
        row = self.conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN operation = 'ADD' THEN 1 ELSE 0 END) as added,
                SUM(CASE WHEN operation = 'MOD' THEN 1 ELSE 0 END) as modified,
                SUM(CASE WHEN operation = 'DEL' THEN 1 ELSE 0 END) as removed,
                SUM(CASE WHEN is_target = 1 THEN 1 ELSE 0 END) as targets
            FROM changes
            WHERE check_date = ?
        """, (check_date,)).fetchone()
        return dict(row) if row else {}

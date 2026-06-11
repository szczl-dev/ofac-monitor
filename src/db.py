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
        self.conn.execute("PRAGMA journal_mode=DELETE")
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

            -- OFAC 官方行动追踪表（从 recent-actions 页面爬取）
            CREATE TABLE IF NOT EXISTS ofac_actions (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                action_url          TEXT UNIQUE NOT NULL,  -- e.g., "/recent-actions/20260605"
                title               TEXT NOT NULL,
                action_date         TEXT NOT NULL,         -- "YYYY-MM-DD"
                category            TEXT,                  -- "Sanctions List Updates" etc.
                category_url        TEXT,
                press_release_url   TEXT,                  -- treasury.gov press release
                press_release_title TEXT,
                body_html           TEXT,                  -- raw HTML from detail page (用于补推时重新解析)
                body_text           TEXT,                  -- plain text from detail page
                first_seen_at       TEXT DEFAULT (datetime('now')),
                pushed              INTEGER DEFAULT 0     -- 是否已推送到飞书
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
            CREATE INDEX IF NOT EXISTS idx_ofac_actions_date
                ON ofac_actions(action_date);
            CREATE INDEX IF NOT EXISTS idx_ofac_actions_pushed
                ON ofac_actions(pushed);
        """)
        # 迁移：为旧数据库添加 body_html 列
        try:
            self.conn.execute(
                "ALTER TABLE ofac_actions ADD COLUMN body_html TEXT"
            )
        except sqlite3.OperationalError as e:
            if "duplicate column name" not in str(e):
                raise  # 不是"列已存在"，是真正的错误（损坏、权限等）
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

    # ==================== OFAC 官方行动操作 ====================

    def is_action_seen(self, action_url: str) -> bool:
        """检查某条行动是否已经被记录过"""
        row = self.conn.execute(
            "SELECT id FROM ofac_actions WHERE action_url = ?",
            (action_url,),
        ).fetchone()
        return row is not None

    def get_seen_action_urls(self) -> set:
        """获取所有已记录的 action URL"""
        rows = self.conn.execute(
            "SELECT action_url FROM ofac_actions"
        ).fetchall()
        return {r["action_url"] for r in rows}

    def save_action(self, action: dict):
        """保存一条 OFAC 行动记录（如已存在则更新元数据，保留首次发现时间和推送状态）"""
        self.conn.execute("""
            INSERT INTO ofac_actions
                (action_url, title, action_date, category, category_url,
                 press_release_url, press_release_title, body_html, body_text, pushed)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(action_url) DO UPDATE SET
                title = excluded.title,
                action_date = excluded.action_date,
                category = excluded.category,
                category_url = excluded.category_url,
                press_release_url = excluded.press_release_url,
                press_release_title = excluded.press_release_title,
                body_html = excluded.body_html,
                body_text = excluded.body_text,
                pushed = CASE WHEN ofac_actions.pushed = 1 THEN 1 ELSE excluded.pushed END
        """, (
            action.get("action_url", ""),
            action.get("title", ""),
            action.get("action_date", ""),
            action.get("category", ""),
            action.get("category_url", ""),
            action.get("press_release_url", ""),
            action.get("press_release_title", ""),
            action.get("body_html", ""),
            action.get("body_text", ""),
            action.get("pushed", 0),
        ))
        self.conn.commit()

    def mark_action_pushed(self, action_url: str):
        """标记某条行动已推送"""
        self.conn.execute(
            "UPDATE ofac_actions SET pushed = 1 WHERE action_url = ?",
            (action_url,),
        )
        self.conn.commit()

    def get_unpushed_actions(self) -> list:
        """获取所有未推送的行动"""
        rows = self.conn.execute("""
            SELECT * FROM ofac_actions
            WHERE pushed = 0
            ORDER BY action_date DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_recent_ofac_actions(self, limit: int = 20) -> list:
        """获取最近的 OFAC 行动记录"""
        rows = self.conn.execute("""
            SELECT * FROM ofac_actions
            ORDER BY action_date DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_latest_action_date(self) -> str:
        """获取最新记录的行动日期"""
        row = self.conn.execute(
            "SELECT MAX(action_date) as max_date FROM ofac_actions"
        ).fetchone()
        return row["max_date"] if row and row["max_date"] else ""

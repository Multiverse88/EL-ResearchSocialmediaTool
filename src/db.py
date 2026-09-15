from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Tuple
from .models import Account, Post, ScrapeLog


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    username TEXT NOT NULL,
    is_own_brand INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(platform, username)
);

CREATE INDEX IF NOT EXISTS idx_accounts_platform_username ON accounts(platform, username);

CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    platform_post_id TEXT NOT NULL,
    caption TEXT NOT NULL,
    media_url TEXT NOT NULL,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    views INTEGER,
    posted_at TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    FOREIGN KEY(account_id) REFERENCES accounts(id),
    UNIQUE(account_id, platform_post_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_account_posted_at ON posts(account_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_posted_at ON posts(posted_at);

CREATE TABLE IF NOT EXISTS scrape_logs (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    run_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scrape_logs_platform_run ON scrape_logs(platform, run_at DESC);
"""


class Database:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        self.conn.close()

    # Accounts
    def upsert_account(self, account: Account) -> Account:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO accounts (id, platform, username, is_own_brand, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform, username) DO UPDATE SET
                    is_own_brand = excluded.is_own_brand
                RETURNING id, platform, username, is_own_brand, created_at;
                """,
                (account.id, account.platform, account.username, int(account.is_own_brand), account.created_at),
            )
            row = cursor.fetchone()
            return Account(
                id=row["id"],
                platform=row["platform"],
                username=row["username"],
                is_own_brand=bool(row["is_own_brand"]),
                created_at=row["created_at"],
            )

    def get_account(self, account_id: str) -> Optional[Account]:
        cursor = self.conn.execute(
            "SELECT id, platform, username, is_own_brand, created_at FROM accounts WHERE id = ?",
            (account_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return Account(
            id=row["id"],
            platform=row["platform"],
            username=row["username"],
            is_own_brand=bool(row["is_own_brand"]),
            created_at=row["created_at"],
        )

    def get_account_by_username(self, platform: str, username: str) -> Optional[Account]:
        norm_user = username.lower().strip().lstrip("@")
        cursor = self.conn.execute(
            "SELECT id, platform, username, is_own_brand, created_at FROM accounts WHERE platform = ? AND username = ?",
            (platform.lower(), norm_user),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return Account(
            id=row["id"],
            platform=row["platform"],
            username=row["username"],
            is_own_brand=bool(row["is_own_brand"]),
            created_at=row["created_at"],
        )

    def list_accounts(self) -> List[Account]:
        cursor = self.conn.execute(
            "SELECT id, platform, username, is_own_brand, created_at FROM accounts ORDER BY username ASC"
        )
        return [
            Account(
                id=row["id"],
                platform=row["platform"],
                username=row["username"],
                is_own_brand=bool(row["is_own_brand"]),
                created_at=row["created_at"],
            )
            for row in cursor.fetchall()
        ]

    # Posts
    def upsert_posts(self, posts: List[Post]) -> int:
        if not posts:
            return 0
        records = [
            (
                p.id,
                p.account_id,
                p.platform_post_id,
                p.caption,
                p.media_url,
                p.likes,
                p.comments,
                p.views,
                p.posted_at,
                p.scraped_at,
            )
            for p in posts
        ]
        with self.conn:
            cursor = self.conn.executemany(
                """
                INSERT INTO posts (
                    id, account_id, platform_post_id, caption, media_url,
                    likes, comments, views, posted_at, scraped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, platform_post_id) DO UPDATE SET
                    caption = excluded.caption,
                    media_url = excluded.media_url,
                    likes = excluded.likes,
                    comments = excluded.comments,
                    views = excluded.views,
                    scraped_at = excluded.scraped_at;
                """,
                records,
            )
            return cursor.rowcount

    def query_posts(
        self,
        account_id: Optional[str] = None,
        platform: Optional[str] = None,
        username: Optional[str] = None,
        keyword: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []

        if account_id:
            clauses.append("p.account_id = ?")
            params.append(account_id)

        if platform:
            clauses.append("a.platform = ?")
            params.append(platform.lower())

        if username:
            norm_user = username.lower().strip().lstrip("@")
            clauses.append("a.username = ?")
            params.append(norm_user)

        if keyword:
            clauses.append("p.caption LIKE ?")
            params.append(f"%{keyword}%")

        if date_from:
            clauses.append("p.posted_at >= ?")
            params.append(date_from)

        if date_to:
            clauses.append("p.posted_at <= ?")
            params.append(date_to)

        where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""

        sql = f"""
            SELECT
                p.id, p.account_id, a.platform, a.username,
                p.platform_post_id, p.caption, p.media_url,
                p.likes, p.comments, p.views,
                p.posted_at, p.scraped_at
            FROM posts p
            JOIN accounts a ON p.account_id = a.id
            {where_clause}
            ORDER BY p.posted_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def get_account_summary(self, account_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.execute(
            """
            SELECT
                COUNT(*) as total_posts,
                COALESCE(SUM(likes), 0) as total_likes,
                COALESCE(AVG(likes), 0.0) as avg_likes,
                COALESCE(SUM(comments), 0) as total_comments,
                COALESCE(AVG(comments), 0.0) as avg_comments,
                COALESCE(SUM(views), 0) as total_views,
                COALESCE(AVG(views), 0.0) as avg_views,
                MIN(posted_at) as earliest_post,
                MAX(posted_at) as latest_post
            FROM posts
            WHERE account_id = ?
            """,
            (account_id,),
        )
        row = cursor.fetchone()
        if not row or row["total_posts"] == 0:
            return None
        return dict(row)

    # Scrape Logs
    def insert_scrape_log(self, log: ScrapeLog) -> str:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO scrape_logs (id, platform, status, error_message, run_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (log.id, log.platform, log.status, log.error_message, log.run_at),
            )
        return log.id

    def list_scrape_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        cursor = self.conn.execute(
            "SELECT id, platform, status, error_message, run_at FROM scrape_logs ORDER BY run_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

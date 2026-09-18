from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional, Tuple
from .models import Account, Post, ScrapeLog, Topic, TopicScrape


SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -64000;
PRAGMA mmap_size = 268435456;

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    username TEXT NOT NULL,
    is_own_brand INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    monitoring_enabled INTEGER NOT NULL DEFAULT 1,
    follower_count INTEGER,
    UNIQUE(platform, username)
);

CREATE INDEX IF NOT EXISTS idx_accounts_platform_username ON accounts(platform, username);

CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    keyword TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'Umum',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topics_keyword ON topics(keyword);

CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    content_type TEXT NOT NULL DEFAULT '',
    platform_post_id TEXT NOT NULL,
    caption TEXT NOT NULL,
    media_url TEXT NOT NULL,
    post_url TEXT NOT NULL DEFAULT '',
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    views INTEGER,
    posted_at TEXT NOT NULL,
    scraped_at TEXT NOT NULL,
    FOREIGN KEY(account_id) REFERENCES accounts(id),
    UNIQUE(account_id, platform_post_id)
);

CREATE INDEX IF NOT EXISTS idx_posts_account_posted_at ON posts(account_id, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_platform_posted ON posts(platform, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_topic ON posts(topic, posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_likes ON posts(likes DESC);
CREATE INDEX IF NOT EXISTS idx_posts_posted_at ON posts(posted_at DESC);

CREATE TABLE IF NOT EXISTS scrape_logs (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT,
    run_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scrape_logs_platform_run ON scrape_logs(platform, run_at DESC);
CREATE TABLE IF NOT EXISTS topic_scrapes (
    id TEXT PRIMARY KEY,
    keyword TEXT NOT NULL,
    platform TEXT NOT NULL,
    posts_found INTEGER NOT NULL DEFAULT 0,
    scraped_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topic_scrapes_platform_keyword ON topic_scrapes(platform, keyword);
CREATE INDEX IF NOT EXISTS idx_topic_scrapes_scraped_at ON topic_scrapes(scraped_at DESC);
"""

SUMMARY_COLS = (
    "total_posts", "total_likes", "avg_likes", "total_comments",
    "avg_comments", "total_views", "avg_views", "earliest_post", "latest_post"
)


class Database:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._accounts_by_id: Dict[str, Account] = {}
        self._accounts_by_plat_user: Dict[Tuple[str, str], Account] = {}
        self._account_usernames: Dict[str, str] = {}
        self._all_accounts: Optional[List[Account]] = None
        self._account_summaries: Dict[str, Optional[Dict[str, Any]]] = {}
        self._top_posts: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
        self._query_cache: Dict[Tuple[Any, ...], Tuple[List[Dict[str, Any]], bool]] = {}
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            try:
                cursor = self.conn.execute("PRAGMA table_info(posts)")
                cols = [row[1] for row in cursor.fetchall()]
                if cols and "topic" not in cols:
                    self.conn.execute("ALTER TABLE posts ADD COLUMN topic TEXT NOT NULL DEFAULT ''")
                if cols and "content_type" not in cols:
                    self.conn.execute("ALTER TABLE posts ADD COLUMN content_type TEXT NOT NULL DEFAULT ''")
                if cols and "post_url" not in cols:
                    self.conn.execute("ALTER TABLE posts ADD COLUMN post_url TEXT NOT NULL DEFAULT ''")
            except Exception:
                pass
            try:
                cursor = self.conn.execute("PRAGMA table_info(accounts)")
                cols = [row[1] for row in cursor.fetchall()]
                if cols and "monitoring_enabled" not in cols:
                    self.conn.execute("ALTER TABLE accounts ADD COLUMN monitoring_enabled INTEGER NOT NULL DEFAULT 1")
                if cols and "follower_count" not in cols:
                    self.conn.execute("ALTER TABLE accounts ADD COLUMN follower_count INTEGER")
            except Exception:
                pass
            self.conn.executescript(SCHEMA_SQL)
    def close(self) -> None:
        self.conn.close()

    # Accounts
    def upsert_account(self, account: Account) -> Account:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO accounts (id, platform, username, is_own_brand, created_at, monitoring_enabled)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, username) DO UPDATE SET
                    is_own_brand = excluded.is_own_brand
                RETURNING id, platform, username, is_own_brand, created_at, monitoring_enabled, follower_count;
                """,
                (
                    account.id, account.platform, account.username, int(account.is_own_brand),
                    account.created_at, int(account.monitoring_enabled),
                ),
            )
            row = cursor.fetchone()
            saved = Account(
                id=row[0],
                platform=row[1],
                username=row[2],
                is_own_brand=bool(row[3]),
                created_at=row[4],
                monitoring_enabled=bool(row[5]),
                follower_count=row[6],
            )
            self._accounts_by_id[saved.id] = saved
            self._accounts_by_plat_user[(saved.platform, saved.username)] = saved
            self._account_usernames[saved.id] = saved.username
            self._all_accounts = None
            return saved

    def get_account(self, account_id: str) -> Optional[Account]:
        if account_id in self._accounts_by_id:
            return self._accounts_by_id[account_id]
        cursor = self.conn.execute(
            "SELECT id, platform, username, is_own_brand, created_at, monitoring_enabled, follower_count FROM accounts WHERE id = ?",
            (account_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        acc = Account(
            id=row[0],
            platform=row[1],
            username=row[2],
            is_own_brand=bool(row[3]),
            created_at=row[4],
            monitoring_enabled=bool(row[5]),
            follower_count=row[6],
        )
        self._accounts_by_id[acc.id] = acc
        self._accounts_by_plat_user[(acc.platform, acc.username)] = acc
        self._account_usernames[acc.id] = acc.username
        return acc

    def get_account_by_username(self, platform: str, username: str) -> Optional[Account]:
        norm_plat = platform.lower()
        norm_user = username.lower().strip().lstrip("@")
        cache_key = (norm_plat, norm_user)
        if cache_key in self._accounts_by_plat_user:
            return self._accounts_by_plat_user[cache_key]
        cursor = self.conn.execute(
            "SELECT id, platform, username, is_own_brand, created_at, monitoring_enabled, follower_count FROM accounts WHERE platform = ? AND username = ?",
            (norm_plat, norm_user),
        )
        row = cursor.fetchone()
        if not row:
            return None
        acc = Account(
            id=row[0],
            platform=row[1],
            username=row[2],
            is_own_brand=bool(row[3]),
            created_at=row[4],
            monitoring_enabled=bool(row[5]),
            follower_count=row[6],
        )
        self._accounts_by_id[acc.id] = acc
        self._accounts_by_plat_user[cache_key] = acc
        self._account_usernames[acc.id] = acc.username
        return acc

    def list_accounts(self) -> List[Account]:
        if self._all_accounts is not None:
            return self._all_accounts
        cursor = self.conn.execute(
            "SELECT id, platform, username, is_own_brand, created_at, monitoring_enabled, follower_count FROM accounts ORDER BY username ASC"
        )
        accounts = [
            Account(
                id=row[0],
                platform=row[1],
                username=row[2],
                is_own_brand=bool(row[3]),
                created_at=row[4],
                monitoring_enabled=bool(row[5]),
                follower_count=row[6],
            )
            for row in cursor.fetchall()
        ]
        self._all_accounts = accounts
        for acc in accounts:
            self._accounts_by_id[acc.id] = acc
            self._accounts_by_plat_user[(acc.platform, acc.username)] = acc
            self._account_usernames[acc.id] = acc.username
        return accounts

    def list_monitored_accounts(self) -> List[Account]:
        """Accounts eligible for scheduled scraping (monitoring_enabled=1)."""
        return [a for a in self.list_accounts() if a.monitoring_enabled]

    def search_accounts(self, keyword: str, platform: Optional[str] = None, limit: int = 20) -> List[Account]:
        """Search accounts by username keyword with optional platform filter."""
        clean_kw = f"%{keyword.strip().lower()}%"
        clauses = ["(username LIKE ? OR platform LIKE ?)"]
        params: List[Any] = [clean_kw, clean_kw]
        if platform:
            clauses.append("platform = ?")
            params.append(platform.lower())
        where_sql = "WHERE " + " AND ".join(clauses)
        cursor = self.conn.execute(
            f"SELECT id, platform, username, is_own_brand, created_at, monitoring_enabled, follower_count FROM accounts {where_sql} ORDER BY username ASC LIMIT ?",
            [*params, limit],
        )
        return [
            Account(
                id=r[0], platform=r[1], username=r[2], is_own_brand=bool(r[3]), created_at=r[4],
                monitoring_enabled=bool(r[5]), follower_count=r[6],
            )
            for r in cursor.fetchall()
        ]

    def _invalidate_account_caches(self) -> None:
        """Full-clear on any account mutation (rename/monitoring toggle): cheap, rare, and
        avoids leaking stale usernames baked into cached query/summary results."""
        self._accounts_by_id.clear()
        self._accounts_by_plat_user.clear()
        self._account_usernames.clear()
        self._all_accounts = None
        self._account_summaries.clear()
        self._query_cache.clear()
        self._top_posts.clear()

    def set_account_monitoring(self, account_id: str, enabled: bool) -> Account:
        """Enables/disables scheduled monitoring for an account without touching its posts."""
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE accounts SET monitoring_enabled = ? WHERE id = ?
                RETURNING id, platform, username, is_own_brand, created_at, monitoring_enabled, follower_count;
                """,
                (int(enabled), account_id),
            )
            row = cursor.fetchone()
        if not row:
            raise ValueError(f"Account not found: {account_id}")
        self._invalidate_account_caches()
        return Account(
            id=row[0], platform=row[1], username=row[2], is_own_brand=bool(row[3]), created_at=row[4],
            monitoring_enabled=bool(row[5]), follower_count=row[6],
        )

    def update_account_follower_count(self, account_id: str, follower_count: Optional[int]) -> Account:
        """Records the account's follower count captured during a profile scrape. Read-only
        metric — does not touch posts or monitoring state. `follower_count=None` is a no-op
        (some scrape backends don't expose it), so a fresh scrape never clobbers a
        previously-captured value with unknown data."""
        if follower_count is None:
            acc = self.get_account(account_id)
            if acc is None:
                raise ValueError(f"Account not found: {account_id}")
            return acc
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE accounts SET follower_count = ? WHERE id = ?
                RETURNING id, platform, username, is_own_brand, created_at, monitoring_enabled, follower_count;
                """,
                (int(follower_count), account_id),
            )
            row = cursor.fetchone()
        if not row:
            raise ValueError(f"Account not found: {account_id}")
        self._invalidate_account_caches()
        return Account(
            id=row[0], platform=row[1], username=row[2], is_own_brand=bool(row[3]), created_at=row[4],
            monitoring_enabled=bool(row[5]), follower_count=row[6],
        )

    def rename_account_username(self, account_id: str, new_username: str) -> Account:
        """Renames an account's monitored username in place, preserving its id and post history."""
        norm_user = new_username.lower().strip().lstrip("@")
        try:
            with self.conn:
                cursor = self.conn.execute(
                    """
                    UPDATE accounts SET username = ? WHERE id = ?
                    RETURNING id, platform, username, is_own_brand, created_at, monitoring_enabled, follower_count;
                    """,
                    (norm_user, account_id),
                )
                row = cursor.fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Username @{norm_user} is already registered on this platform: {exc}") from exc
        if not row:
            raise ValueError(f"Account not found: {account_id}")
        self._invalidate_account_caches()
        return Account(
            id=row[0], platform=row[1], username=row[2], is_own_brand=bool(row[3]), created_at=row[4],
            monitoring_enabled=bool(row[5]), follower_count=row[6],
        )

    def get_account_freshness(self, account_id: str) -> Optional[str]:
        """Returns the most recent `scraped_at` timestamp among this account's posts, or None."""
        cursor = self.conn.execute(
            "SELECT MAX(scraped_at) FROM posts WHERE account_id = ?",
            (account_id,),
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] else None


    # Posts & Ingestion
    def upsert_posts(self, posts: List[Post]) -> int:
        if not posts:
            return 0
        records = []
        for p in posts:
            plat = p.platform
            if not plat and p.account_id in self._accounts_by_id:
                plat = self._accounts_by_id[p.account_id].platform
            topic_val = getattr(p, "topic", "") or ""
            records.append((
                p.id,
                p.account_id,
                plat,
                topic_val,
                getattr(p, "content_type", "") or "",
                p.platform_post_id,
                p.caption,
                p.media_url,
                getattr(p, "post_url", "") or "",
                p.likes,
                p.comments,
                p.views,
                p.posted_at,
                p.scraped_at,
            ))
        with self.conn:
            cursor = self.conn.executemany(
                """
                INSERT INTO posts (
                    id, account_id, platform, topic, content_type, platform_post_id, caption, media_url,
                    post_url, likes, comments, views, posted_at, scraped_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_id, platform_post_id) DO UPDATE SET
                    platform = excluded.platform,
                    topic = excluded.topic,
                    content_type = excluded.content_type,
                    caption = excluded.caption,
                    media_url = excluded.media_url,
                    post_url = excluded.post_url,
                    likes = excluded.likes,
                    comments = excluded.comments,
                    views = excluded.views,
                    scraped_at = excluded.scraped_at;
                """,
                records,
            )
            self._account_summaries.clear()
            self._query_cache.clear()
            self._top_posts.clear()
            return cursor.rowcount

    def query_posts(
        self,
        account_id: Optional[str] = None,
        platform: Optional[str] = None,
        username: Optional[str] = None,
        keyword: Optional[str] = None,
        topic: Optional[str] = None,
        order_by: str = "posted_at",
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        base_key = (
            account_id, platform, username, keyword, topic, order_by,
            date_from, date_to,
        )
        needed = offset + limit
        cached = self._query_cache.get(base_key)
        if cached is not None:
            posts_list, is_complete = cached
            if len(posts_list) >= needed or is_complete:
                return posts_list[offset:needed]

        fetch_limit = max(needed, 60)
        clauses = []
        params: List[Any] = []

        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        elif username:
            norm_user = username.lower().strip().lstrip("@")
            acc = None
            if platform:
                acc = self.get_account_by_username(platform, norm_user)
            else:
                for p in ("instagram", "tiktok"):
                    acc = self.get_account_by_username(p, norm_user)
                    if acc:
                        break
            if not acc:
                return []
            clauses.append("account_id = ?")
            params.append(acc.id)
        elif platform:
            clauses.append("platform = ?")
            params.append(platform.lower())

        if topic:
            clauses.append("(topic = ? OR caption LIKE ?)")
            params.extend([topic.lower().strip(), f"%{topic.strip()}%"])
        elif keyword:
            clauses.append("caption LIKE ?")
            params.append(f"%{keyword.strip()}%")

        if date_from:
            clauses.append("posted_at >= ?")
            params.append(date_from)

        if date_to:
            clauses.append("posted_at <= ?")
            params.append(date_to)

        where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""

        order_col = "posted_at DESC"
        if order_by == "likes":
            order_col = "likes DESC"
        elif order_by == "views":
            order_col = "views DESC"
        elif order_by == "comments":
            order_col = "comments DESC"

        sql = f"""
            SELECT
                id, account_id, platform, platform_post_id, caption, media_url,
                likes, comments, views, posted_at, scraped_at, topic, content_type, post_url
            FROM posts
            {where_clause}
            ORDER BY {order_col}
            LIMIT ? OFFSET 0
        """
        params.append(fetch_limit)

        cursor = self.conn.execute(sql, params)
        rows = cursor.fetchall()
        u_get = self._account_usernames.get
        res = []
        for r in rows:
            u_name = u_get(r[1])
            if not u_name:
                acc = self.get_account(r[1])
                u_name = acc.username if acc else ""
                if u_name:
                    self._account_usernames[r[1]] = u_name
            res.append({
                "id": r[0],
                "account_id": r[1],
                "platform": r[2],
                "username": u_name or "easylegal_id",
                "platform_post_id": r[3],
                "caption": r[4],
                "media_url": r[5],
                "likes": r[6],
                "comments": r[7],
                "views": r[8],
                "posted_at": r[9],
                "scraped_at": r[10],
                "topic": r[11],
                "content_type": r[12],
                "post_url": r[13],
            })
        is_complete = len(res) < fetch_limit
        if len(self._query_cache) >= 512:
            self._query_cache.clear()
        self._query_cache[base_key] = (res, is_complete)
        return res[offset:needed]

    def get_account_summary(self, account_id: str) -> Optional[Dict[str, Any]]:
        if account_id in self._account_summaries:
            return self._account_summaries[account_id]
        cursor = self.conn.execute(
            """
            SELECT
                COUNT(*),
                COALESCE(SUM(likes), 0),
                COALESCE(AVG(likes), 0.0),
                COALESCE(SUM(comments), 0),
                COALESCE(AVG(comments), 0.0),
                COALESCE(SUM(views), 0),
                COALESCE(AVG(views), 0.0),
                MIN(posted_at),
                MAX(posted_at)
            FROM posts
            WHERE account_id = ?
            """,
            (account_id,),
        )
        row = cursor.fetchone()
        if not row or row[0] == 0:
            self._account_summaries[account_id] = None
            return None
        res = dict(zip(SUMMARY_COLS, row))
        self._account_summaries[account_id] = res
        return res

    def get_top_posts(self, account_id: str, limit: int = 3) -> List[Dict[str, Any]]:
        cache_key = (account_id, limit)
        if cache_key in self._top_posts:
            return self._top_posts[cache_key]
        posts = self.query_posts(account_id=account_id, limit=limit)
        self._top_posts[cache_key] = posts
        return posts

    # Topics & Content Research Methods
    def upsert_topic(self, topic: Topic) -> Topic:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO topics (id, keyword, category, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(keyword) DO UPDATE SET
                    category = excluded.category
                RETURNING id, keyword, category, created_at;
                """,
                (topic.id, topic.keyword.lower().strip(), topic.category, topic.created_at),
            )
            row = cursor.fetchone()
            return Topic(
                id=row[0],
                keyword=row[1],
                category=row[2],
                created_at=row[3],
            )

    def list_topics(self) -> List[Topic]:
        cursor = self.conn.execute("SELECT id, keyword, category, created_at FROM topics ORDER BY keyword ASC")
        return [Topic(id=r[0], keyword=r[1], category=r[2], created_at=r[3]) for r in cursor.fetchall()]

    def record_topic_scrape(self, scrape: TopicScrape) -> str:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO topic_scrapes (id, keyword, platform, posts_found, scraped_at, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (scrape.id, scrape.keyword, scrape.platform, scrape.posts_found, scrape.scraped_at, scrape.status),
            )
        return scrape.id

    def get_topic_last_scraped(self, keyword: str, platform: Optional[str] = None) -> Optional[str]:
        """Returns the most recent `scraped_at` timestamp for this topic from topic_scrapes or posts."""
        clean_kw = keyword.strip().lower()
        if platform:
            cursor = self.conn.execute(
                "SELECT MAX(scraped_at) FROM topic_scrapes WHERE keyword = ? AND platform = ? AND status = 'success'",
                (clean_kw, platform.lower()),
            )
            row = cursor.fetchone()
            if row and row[0]:
                return row[0]

        # Fallback to general topic_scrapes
        cursor = self.conn.execute(
            "SELECT MAX(scraped_at) FROM topic_scrapes WHERE keyword = ? AND status = 'success'",
            (clean_kw,),
        )
        row = cursor.fetchone()
        if row and row[0]:
            return row[0]

        # Ultimate fallback: posts table tagged explicitly with this topic
        cursor = self.conn.execute(
            "SELECT MAX(scraped_at) FROM posts WHERE topic = ?",
            (clean_kw,),
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def get_topic_summary(self, keyword: str, platform: Optional[str] = None) -> Dict[str, Any]:
        clean_kw = keyword.strip().lower()
        clauses = ["(topic = ? OR caption LIKE ?)"]
        params: List[Any] = [clean_kw, f"%{clean_kw}%"]
        if platform:
            clauses.append("platform = ?")
            params.append(platform.lower())

        where_sql = "WHERE " + " AND ".join(clauses)
        sql = f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(likes), 0),
                COALESCE(AVG(likes), 0.0),
                COALESCE(MAX(likes), 0),
                COALESCE(SUM(comments), 0),
                COALESCE(AVG(comments), 0.0),
                COALESCE(SUM(views), 0),
                COALESCE(AVG(views), 0.0),
                COALESCE(MAX(views), 0)
            FROM posts
            {where_sql}
        """
        cursor = self.conn.execute(sql, params)
        row = cursor.fetchone()

        total_posts = row[0]
        total_likes = row[1]
        avg_likes = round(row[2], 1)
        max_likes = row[3]
        total_comments = row[4]
        avg_comments = round(row[5], 1)
        total_views = row[6]
        avg_views = round(row[7], 1)
        max_views = row[8]

        # Engagement Rate
        if total_views > 0:
            er = round(((total_likes + total_comments) / total_views) * 100, 2)
        elif total_posts > 0:
            er = round((total_likes + total_comments) / total_posts, 1)
        else:
            er = 0.0

        # Fetch top viral posts for this topic
        viral_posts = self.query_posts(topic=clean_kw, platform=platform, order_by="likes", limit=5)

        return {
            "status": "success",
            "keyword": clean_kw,
            "platform": platform or "all",
            "total_posts": total_posts,
            "total_likes": total_likes,
            "avg_likes": avg_likes,
            "max_likes": max_likes,
            "total_comments": total_comments,
            "avg_comments": avg_comments,
            "total_views": total_views,
            "avg_views": avg_views,
            "max_views": max_views,
            "engagement_rate": er,
            "viral_references": [
                {
                    "id": p["id"],
                    "platform": p["platform"],
                    "username": p["username"],
                    "content_type": p["content_type"],
                    "caption": p["caption"][:140] + ("..." if len(p["caption"]) > 140 else ""),
                    "likes": p["likes"],
                    "comments": p["comments"],
                    "views": p["views"],
                    "posted_at": p["posted_at"],
                }
                for p in viral_posts
            ],
        }

    def get_topic_account_breakdown(self, keyword: str, platform: Optional[str] = None, limit: int = 8) -> List[Dict[str, Any]]:
        """Per-account post count & avg likes for accounts posting about this topic, most active first."""
        clean_kw = keyword.strip().lower()
        clauses = ["(p.topic = ? OR p.caption LIKE ?)"]
        params: List[Any] = [clean_kw, f"%{clean_kw}%"]
        if platform:
            clauses.append("p.platform = ?")
            params.append(platform.lower())
        where_sql = "WHERE " + " AND ".join(clauses)
        sql = f"""
            SELECT a.username, a.platform, COUNT(*), COALESCE(AVG(p.likes), 0.0), COALESCE(MAX(p.likes), 0)
            FROM posts p
            JOIN accounts a ON a.id = p.account_id
            {where_sql}
            GROUP BY p.account_id
            ORDER BY COUNT(*) DESC, AVG(p.likes) DESC
            LIMIT ?
        """
        params.append(limit)
        cursor = self.conn.execute(sql, params)
        return [
            {
                "username": r[0],
                "platform": r[1],
                "post_count": r[2],
                "avg_likes": round(r[3], 1),
                "max_likes": r[4],
            }
            for r in cursor.fetchall()
        ]

    def compare_topics(self, keywords: List[str]) -> Dict[str, Any]:
        summaries = [self.get_topic_summary(kw) for kw in keywords if kw.strip()]
        ranked = sorted(summaries, key=lambda s: s.get("avg_likes", 0), reverse=True)
        for idx, r in enumerate(ranked):
            r["rank"] = idx + 1

        return {
            "status": "success",
            "compared_topics": len(summaries),
            "leaderboard": [
                {
                    "rank": r["rank"],
                    "keyword": r["keyword"],
                    "total_posts": r["total_posts"],
                    "avg_likes": r["avg_likes"],
                    "max_likes": r["max_likes"],
                    "avg_views": r["avg_views"],
                    "engagement_rate": r["engagement_rate"],
                }
                for r in ranked
            ],
            "details": summaries,
        }

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
        cols = ("id", "platform", "status", "error_message", "run_at")
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

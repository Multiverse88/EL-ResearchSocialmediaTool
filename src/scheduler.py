from __future__ import annotations

import concurrent.futures
import logging
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
import uuid

from .db import Database
from .models import Account, ScrapeLog

logger = logging.getLogger("scheduler.daily_sync")

# WIB is UTC+7
WIB_TZ = timezone(timedelta(hours=7))

# Default target accounts for EasyCorp
DAILY_BRAND_TARGETS: List[Tuple[str, str]] = [
    ("instagram", "id.easylegal"),
    ("instagram", "id.easytax"),
    ("instagram", "id.easyoffice"),
    ("threads", "id.easylegal"),
    ("tiktok", "id.easylegal"),
]

# Core competitor topics mapped to the 3 business lines
DAILY_COMPETITOR_TOPICS: List[str] = [
    "pendirian pt",
    "konsultasi pajak",
    "virtual office jakarta",
]

DEFAULT_DAILY_SYNC_POSTS = 10


def get_current_wib_datetime() -> datetime:
    """Returns the current datetime in Western Indonesia Time (WIB, UTC+7)."""
    return datetime.now(WIB_TZ)


def get_last_daily_sync_date(db: Database) -> Optional[str]:
    """
    Returns the YYYY-MM-DD date string (in WIB) of the most recent successful daily sync,
    or None if no sync has ever been recorded.
    """
    cursor = db.conn.execute(
        """
        SELECT run_at FROM scrape_logs
        WHERE platform = 'daily_sync' AND status = 'success'
        ORDER BY run_at DESC LIMIT 1
        """
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return None

    run_at_str = str(row[0])
    try:
        # Handle ISO strings like 2026-09-20T08:00:00+07:00 or 2026-09-20T01:00:00Z
        dt = datetime.fromisoformat(run_at_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_wib = dt.astimezone(WIB_TZ)
        return dt_wib.strftime("%Y-%m-%d")
    except Exception:
        # Fallback to prefix if standard YYYY-MM-DD
        return run_at_str[:10]


def should_run_daily_sync(db: Database, now_wib: Optional[datetime] = None) -> bool:
    """
    Determines whether the daily sync should execute right now.
    Triggers if:
      1. Current time in WIB has reached or passed 08:00 AM (hour >= 8).
      2. No successful daily sync has been completed yet for today's WIB date.
    This also handles auto-catchup if the server boots or restarts after 08:00 AM.
    """
    if now_wib is None:
        now_wib = get_current_wib_datetime()

    if now_wib.hour < 8:
        return False

    today_date = now_wib.strftime("%Y-%m-%d")
    last_sync_date = get_last_daily_sync_date(db)

    return last_sync_date != today_date


def _sync_single_brand_account(
    db: Database,
    platform: str,
    username: str,
    max_posts: int,
) -> Dict[str, Any]:
    """Worker task to scrape a single brand account."""
    acc = db.get_account_by_username(platform, username)
    if not acc:
        acc = db.upsert_account(Account.create(platform=platform, username=username, is_own_brand=True))

    from .chat_actions import _run_profile_scrape
    count, err, backend = _run_profile_scrape(db, acc, max_posts=max_posts)
    return {
        "type": "brand_account",
        "platform": platform,
        "target": username,
        "posts_added": count,
        "backend": backend,
        "error": err,
        "success": not bool(err),
    }


def _sync_single_topic(
    db: Database,
    keyword: str,
    max_posts_per_platform: int,
) -> Dict[str, Any]:
    """Worker task to scrape competitor posts for a topic keyword."""
    from .scrapers.keyword_scraper import scrape_topic_content
    result = scrape_topic_content(db, keyword, max_posts_per_platform=max_posts_per_platform)
    ig_err = (result.get("instagram") or {}).get("error")
    tt_err = (result.get("tiktok") or {}).get("error")
    has_err = bool(ig_err and tt_err)
    err_msg = f"IG: {ig_err} | TT: {tt_err}" if has_err else (ig_err or tt_err or None)

    return {
        "type": "competitor_topic",
        "platform": "multi",
        "target": keyword,
        "posts_added": result.get("total_posts_added", 0),
        "backend": "multi",
        "error": err_msg,
        "success": result.get("total_posts_added", 0) > 0 or not has_err,
    }


def execute_daily_sync(
    db: Database,
    max_posts: int = DEFAULT_DAILY_SYNC_POSTS,
    max_workers: int = 2,
    brand_targets: Optional[List[Tuple[str, str]]] = None,
    competitor_topics: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Executes the full daily sync in parallel across brand accounts and competitor topics.
    Records run summary and status to scrape_logs table.
    """
    start_dt = get_current_wib_datetime()
    start_iso = start_dt.isoformat()
    logger.info(f"Starting daily sync at {start_iso} (max_posts={max_posts}, max_workers={max_workers})")

    brands = brand_targets if brand_targets is not None else DAILY_BRAND_TARGETS
    topics = competitor_topics if competitor_topics is not None else DAILY_COMPETITOR_TOPICS

    results: List[Dict[str, Any]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_name = {}

        # 1. Enqueue brand account scrapes
        for plat, user in brands:
            fut = executor.submit(_sync_single_brand_account, db, plat, user, max_posts)
            future_to_name[fut] = f"brand:{plat}@{user}"

        # 2. Enqueue competitor topic scrapes
        for kw in topics:
            fut = executor.submit(_sync_single_topic, db, kw, max_posts)
            future_to_name[fut] = f"topic:{kw}"

        for future in concurrent.futures.as_completed(future_to_name):
            task_name = future_to_name[future]
            try:
                res = future.result()
                results.append(res)
                logger.info(f"Daily sync completed for {task_name}: {res.get('posts_added', 0)} posts (err={res.get('error')})")
            except Exception as exc:
                logger.error(f"Daily sync worker raised unhandled exception for {task_name}: {exc}", exc_info=True)
                results.append({
                    "target": task_name,
                    "posts_added": 0,
                    "error": str(exc),
                    "success": False,
                })

    end_dt = get_current_wib_datetime()
    duration_secs = round((end_dt - start_dt).total_seconds(), 2)

    total_posts = sum(r.get("posts_added", 0) for r in results)
    failures = [r for r in results if not r.get("success")]

    if len(failures) == 0:
        overall_status = "success"
        error_msg = None
    elif len(failures) < len(results):
        overall_status = "success"  # Partial success still counts as success to avoid infinite catch-up loop
        error_msg = f"{len(failures)}/{len(results)} targets had errors"
    else:
        overall_status = "failed"
        error_msg = "All targets failed"

    # Persist log to scrape_logs
    log_entry = ScrapeLog(
        id=str(uuid.uuid4()),
        platform="daily_sync",
        status=overall_status,
        error_message=error_msg,
        run_at=start_iso,
    )
    db.insert_scrape_log(log_entry)

    summary = {
        "status": overall_status,
        "started_at": start_iso,
        "completed_at": end_dt.isoformat(),
        "duration_seconds": duration_secs,
        "total_posts_added": total_posts,
        "total_targets": len(results),
        "successful_targets": len(results) - len(failures),
        "failed_targets": len(failures),
        "details": results,
    }
    logger.info(f"Daily sync finished: {overall_status}, {total_posts} total posts added in {duration_secs}s")
    return summary


class DailySyncSchedulerThread(threading.Thread):
    """
    Background daemon thread that periodically checks if 08:00 WIB daily sync is due.
    Checks every check_interval_seconds (default 60s).
    """

    def __init__(self, db_factory, check_interval_seconds: int = 60):
        super().__init__(name="DailySyncScheduler", daemon=True)
        self.db_factory = db_factory
        self.check_interval_seconds = check_interval_seconds
        self.stop_event = threading.Event()
        self.is_running_sync = False

    def run(self):
        logger.info(f"DailySyncScheduler thread started (check interval={self.check_interval_seconds}s)")
        while not self.stop_event.is_set():
            try:
                db = self.db_factory()
                if should_run_daily_sync(db) and not self.is_running_sync:
                    self.is_running_sync = True
                    try:
                        logger.info("Daily sync trigger condition met. Executing daily sync...")
                        execute_daily_sync(db)
                    finally:
                        self.is_running_sync = False
            except Exception as exc:
                logger.error(f"Error in daily sync scheduler loop: {exc}", exc_info=True)

            self.stop_event.wait(self.check_interval_seconds)

    def stop(self):
        self.stop_event.set()

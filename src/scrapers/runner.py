from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from ..models import Account
from ..db import Database
from .instagram import scrape_instagram_profile
from .tiktok import scrape_tiktok_profile
from .threads import scrape_threads_profile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scrapers.runner")

# Default brand accounts of EasyCorp to ensure exist on startup
DEFAULT_SEEDS = [
    ("instagram", "id.easylegal", True),
    ("tiktok", "easylegal_tiktok", True),
    ("instagram", "id.easytax", True),
    ("tiktok", "easytax_tiktok", True),
    ("instagram", "id.easyoffice", True),
    ("instagram", "easylegal_id", True),
    ("instagram", "easytax_id", True),
    ("instagram", "easyoffice_id", True),
]


def seed_default_accounts_if_empty(db: Database) -> List[Account]:
    """Ensures all default EasyCorp brand accounts exist in the database,
    inserting any that are missing even if the database already has other accounts."""
    created = []
    for platform, username, is_own in DEFAULT_SEEDS:
        if not db.get_account_by_username(platform, username):
            acc = Account.create(platform=platform, username=username, is_own_brand=is_own)
            saved = db.upsert_account(acc)
            created.append(saved)
    return db.list_accounts()


def run_scraping_job(
    db: Database,
    platform: Optional[str] = None,
    username: Optional[str] = None,
    max_posts_per_account: int = 30,
) -> Dict[str, Any]:
    """
    Executes the scheduled scraping workflow across monitored accounts.
    Can be filtered by platform or single username.
    """
    logger.info("=== Starting Social Media Scraping Job ===")
    seed_default_accounts_if_empty(db)
    accounts = db.list_accounts() if username else db.list_monitored_accounts()

    # Filter target accounts if requested
    targets: List[Account] = []
    for acc in accounts:
        if platform and acc.platform != platform.lower():
            continue
        if username and acc.username.lower() != username.lower().strip().lstrip("@"):
            continue
        targets.append(acc)

    if not targets:
        logger.warning("No matching accounts found to scrape.")
        return {
            "status": "warning",
            "message": "No accounts found matching criteria",
            "total_accounts": 0,
            "posts_collected": 0,
            "failed_count": 0,
            "results": [],
        }

    total_posts = 0
    success_count = 0
    failed_count = 0
    results = []

    for acc in targets:
        logger.info(f"Processing @{acc.username} on {acc.platform} (own_brand={acc.is_own_brand})...")
        backend = None
        if acc.platform == "instagram":
            count, err, backend = scrape_instagram_profile(db, acc, max_posts=max_posts_per_account)
        elif acc.platform == "tiktok":
            count, err, backend = scrape_tiktok_profile(db, acc, max_posts=max_posts_per_account)
        elif acc.platform == "threads":
            count, err, backend = scrape_threads_profile(db, acc, max_posts=max_posts_per_account)
        else:
            err = f"Unsupported platform: {acc.platform}"
            count = 0

        if err:
            failed_count += 1
            results.append({
                "username": acc.username,
                "platform": acc.platform,
                "status": "failed",
                "posts_added": 0,
                "backend": backend,
                "error": err,
            })
        else:
            success_count += 1
            total_posts += count
            results.append({
                "username": acc.username,
                "platform": acc.platform,
                "status": "success",
                "posts_added": count,
                "backend": backend,
            })

    summary = {
        "status": "success" if failed_count == 0 else ("partial" if success_count > 0 else "failed"),
        "total_accounts": len(targets),
        "success_count": success_count,
        "failed_count": failed_count,
        "posts_collected": total_posts,
        "results": results,
    }
    logger.info(f"=== Scraping Job Complete: {success_count}/{len(targets)} succeeded, {total_posts} posts collected ===")
    return summary


def refresh_stale_topics(
    db: Database,
    staleness_hours: float = 6.0,
    max_posts_per_platform: int = 20,
) -> Dict[str, Any]:
    """
    Background refresh: re-scrapes registered topics whose last successful
    `topic_scrapes` audit entry is older than `staleness_hours`. Intended to run
    from cron (e.g. every 6 hours) so chat questions hit fresh data without
    paying scrape latency on the request path.
    """
    from datetime import datetime, timezone
    from .keyword_scraper import scrape_topic_content

    now = datetime.now(timezone.utc)
    refreshed = 0
    failed = 0
    skipped = 0
    results: List[Dict[str, Any]] = []

    for topic in db.list_topics():
        last = db.get_topic_last_scraped(topic.keyword)
        is_stale = True
        if last:
            try:
                last_dt = datetime.fromisoformat(last)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                is_stale = (now - last_dt).total_seconds() / 3600 >= staleness_hours
            except Exception:
                is_stale = True

        if not is_stale:
            skipped += 1
            continue

        logger.info(f"Refreshing stale topic '{topic.keyword}' (last scrape: {last or 'never'})")
        try:
            result = scrape_topic_content(
                db,
                topic.keyword,
                max_posts_per_platform=max_posts_per_platform,
                since=last,
            )
            refreshed += 1
            results.append({"keyword": topic.keyword, "added": result.get("total_posts_added", 0)})
        except Exception as exc:
            failed += 1
            logger.warning(f"Topic refresh failed for '{topic.keyword}': {exc}")
            results.append({"keyword": topic.keyword, "error": str(exc)})

    return {
        "status": "success",
        "topics_refreshed": refreshed,
        "topics_skipped": skipped,
        "topics_failed": failed,
        "results": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Social Media Scraper Scheduled Job Runner")
    parser.add_argument("--db", type=str, default=os.getenv("DATABASE_PATH", "social_media.db"), help="Database path")
    parser.add_argument("--platform", type=str, choices=["instagram", "tiktok", "threads"], help="Filter by platform")
    parser.add_argument("--username", type=str, help="Scrape specific username only")
    parser.add_argument("--limit", type=int, default=int(os.getenv("MAX_POSTS_PER_SCRAPE", 30)), help="Max posts per account")
    parser.add_argument("--topics", action="store_true", help="Refresh stale registered topics instead of accounts")
    parser.add_argument(
        "--staleness-hours",
        type=float,
        default=float(os.getenv("TOPIC_STALENESS_HOURS", 24)),
        help="Re-scrape topics whose last scrape is older than this",
    )
    args = parser.parse_args()

    db = Database(args.db)
    try:
        if args.topics:
            result = refresh_stale_topics(
                db,
                staleness_hours=args.staleness_hours,
                max_posts_per_platform=args.limit,
            )
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            run_scraping_job(
                db=db,
                platform=args.platform,
                username=args.username,
                max_posts_per_account=args.limit,
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()

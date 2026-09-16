from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from ..models import Account
from ..db import Database
from .instagram import scrape_instagram_profile
from .tiktok import scrape_tiktok_profile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scrapers.runner")

# Default seed accounts if DB has no accounts yet
DEFAULT_SEEDS = [
    ("instagram", "easylegal_id", True),
    ("tiktok", "easylegal_tiktok", True),
    ("instagram", "easytax_id", True),
    ("tiktok", "easytax_tiktok", True),
    ("instagram", "easyoffice_id", True),
]


def seed_default_accounts_if_empty(db: Database) -> List[Account]:
    """Ensures at least default brand accounts exist in database."""
    accounts = db.list_accounts()
    if accounts:
        return accounts

    logger.info("Database has no registered accounts. Seeding initial accounts...")
    created = []
    for platform, username, is_own in DEFAULT_SEEDS:
        acc = Account.create(platform=platform, username=username, is_own_brand=is_own)
        saved = db.upsert_account(acc)
        created.append(saved)
    return created


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


def main():
    parser = argparse.ArgumentParser(description="Social Media Scraper Scheduled Job Runner")
    parser.add_argument("--db", type=str, default=os.getenv("DATABASE_PATH", "social_media.db"), help="Database path")
    parser.add_argument("--platform", type=str, choices=["instagram", "tiktok"], help="Filter by platform")
    parser.add_argument("--username", type=str, help="Scrape specific username only")
    parser.add_argument("--limit", type=int, default=int(os.getenv("MAX_POSTS_PER_SCRAPE", 30)), help="Max posts per account")
    args = parser.parse_args()

    db = Database(args.db)
    try:
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

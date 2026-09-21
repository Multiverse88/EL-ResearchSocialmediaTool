from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..models import Account, ScrapeLog
from ..db import Database
from ..ingest import ingest_scraped_batch
from .bright_data_client import (
    THREADS_PROFILES_DATASET_ID,
    is_bright_data_configured,
    run_dataset,
)

logger = logging.getLogger("scrapers.threads")


def _bright_data_item_to_raw_post(
    item: Dict[str, Any], username: str, profile_url: str,
) -> Optional[Dict[str, Any]]:
    """Adapts one entry of the Threads Profiles dataset's embedded `threads` list to the
    ingestion contract. Field names below are confirmed live (2026-09-21) against a real
    Bright Data response for @id.easylegal — not guesses.

    This dataset has no per-post id or permalink, and individual posts usually carry
    null likes/comments (Threads' own public surface mostly omits them, confirmed: 3 of
    4 real posts had null likes) — we report 0 rather than fabricate a number, and fall
    back to the profile URL for the permalink since there's nothing more specific.
    """
    if item.get("error"):
        return None
    post_date = item.get("post_date")
    if not post_date:
        return None
    profile_id = item.get("profile_id") or username
    likes = item.get("likes")
    comments = item.get("comments_amount")
    return {
        "id": f"{profile_id}_{post_date}",
        "caption": item.get("post_content_formatted") or "",
        "media_url": "",
        "post_url": profile_url,
        "likes": max(0, int(likes)) if likes is not None else 0,
        "comments": max(0, int(comments)) if comments is not None else 0,
        "views": None,
        "posted_at": post_date,
    }


def _scrape_threads_profile_bright_data(
    db: Database,
    account: Account,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """Scrapes a Threads profile through Bright Data's Profiles dataset — a single
    synchronous call (confirmed live 2026-09-21, ~10-20s) that returns profile info,
    follower count, AND recent posts together in one response.

    Replaces the previous two-call approach: a separate async "profile" discovery
    collector for posts (verified live to routinely take 300s+ per token and, on
    2026-09-21, to sit "running" at Bright Data's own progress endpoint for 30+ minutes
    without ever completing across both configured tokens) plus a second call for
    follower count. That collector's field-name mapping had also never actually been
    confirmed against a real response (see git history) — every attempt had failed
    before a payload could be inspected.
    """
    username = account.username.strip().lstrip("@")
    profile_url = f"https://www.threads.com/@{username}"
    logger.info("Starting Threads scrape (Bright Data Profiles) for @%s (limit=%s)", username, max_posts)

    try:
        items = run_dataset(
            THREADS_PROFILES_DATASET_ID,
            [{"url": profile_url}],
            query={"notify": "false"},
        )
        profile = items[0] if items else None
        if not profile or profile.get("error"):
            err_msg = (
                f"Bright Data returned no usable Threads profile for @{username} "
                "(profile may be private, empty, or not found)"
            )
            logger.warning(err_msg)
            db.insert_scrape_log(
                ScrapeLog.create(platform="threads", status="failed", error_message=err_msg, target=username)
            )
            return 0, err_msg

        follower_count = profile.get("number_of_followers")
        if follower_count is not None:
            db.update_account_follower_count(account.id, int(follower_count))

        thread_items = profile.get("threads") or []
        raw_posts: List[Dict[str, Any]] = [
            post for post in (
                _bright_data_item_to_raw_post(item, username, profile_url) for item in thread_items[:max_posts]
            )
            if post is not None
        ]
        if not raw_posts:
            err_msg = (
                f"Bright Data returned no usable Threads posts for @{username} "
                "(profile may be private, empty, or not found)"
            )
            logger.warning(err_msg)
            db.insert_scrape_log(
                ScrapeLog.create(platform="threads", status="failed", error_message=err_msg, target=username)
            )
            return 0, err_msg

        inserted_count, err = ingest_scraped_batch(
            db=db,
            platform="threads",
            account=account,
            raw_posts=raw_posts,
        )
        if err:
            logger.error("Ingestion error for Threads @%s: %s", username, err)
            return 0, err
        logger.info(
            "Successfully scraped (Bright Data Profiles) and stored %s Threads posts for @%s",
            inserted_count,
            username,
        )
        return inserted_count, None
    except Exception as exc:
        err_msg = f"Bright Data Threads scrape failed for @{username}: {str(exc)}"
        logger.error(err_msg)
        db.insert_scrape_log(
            ScrapeLog.create(platform="threads", status="failed", error_message=err_msg, target=username)
        )
        return 0, err_msg


def scrape_threads_profile(
    db: Database,
    account: Account,
    max_posts: int = 30,
) -> Tuple[int, Optional[str], str]:
    """
    Scrapes public posts from a Threads profile and saves them to the database.
    Threads has no free/self-hosted fallback scraper in this codebase (unlike Instagram's
    Instaloader or TikTok's TikTokApi path) — Bright Data is required.
    """
    if not is_bright_data_configured():
        err_msg = "Threads scraping requires Bright Data (BRIGHT_DATA_API_TOKEN not configured)"
        logger.warning(err_msg)
        db.insert_scrape_log(
            ScrapeLog.create(platform="threads", status="failed", error_message=err_msg, target=account.username)
        )
        return 0, err_msg, "bright_data"
    count, err = _scrape_threads_profile_bright_data(db, account, max_posts)
    return count, err, "bright_data"

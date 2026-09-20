from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from ..models import Account, ScrapeLog
from ..db import Database
from ..ingest import ingest_scraped_batch
from .bright_data_client import (
    THREADS_POSTS_DATASET_ID,
    THREADS_PROFILES_DATASET_ID,
    is_bright_data_configured,
    run_dataset,
)

logger = logging.getLogger("scrapers.threads")


def _bright_data_item_to_raw_post(item: Dict[str, Any], username: str) -> Optional[Dict[str, Any]]:
    """Adapts a Bright Data Threads post record to the ingestion contract.

    NOTE: Threads' exact field names have not been confirmed against a live response
    (Bright Data trial account was congested during development). Each value below tries
    several plausible field-name candidates, matching the defensive pattern already used
    for the Instagram/TikTok adapters — verify against a real response and tighten once
    Bright Data is reachable.
    """
    if item.get("error"):
        return None
    post_id = item.get("post_id") or item.get("id") or item.get("thread_id")
    if not post_id:
        return None
    likes = item.get("likes") or item.get("num_likes") or item.get("like_count") or 0
    comments = item.get("comments") or item.get("num_comments") or item.get("comment_count") or item.get("replies") or 0
    posted_at = (
        item.get("date_posted") or item.get("post_time") or item.get("timestamp") or item.get("created_at")
    )
    return {
        "id": str(post_id),
        "caption": item.get("post_content") or item.get("content") or item.get("text") or item.get("description") or "",
        "media_url": item.get("image_url") or item.get("video_url") or item.get("thumbnail") or "",
        "post_url": item.get("url") or item.get("post_url") or f"https://www.threads.com/@{username}/post/{post_id}",
        "likes": max(0, int(likes or 0)),
        "comments": max(0, int(comments or 0)),
        "views": None,
        "posted_at": posted_at,
    }


def _fetch_threads_follower_count(username: str) -> Optional[int]:
    """Fetches follower count via Bright Data's Threads Profiles dataset (separate from
    the Posts dataset). Best-effort: any failure here is logged and swallowed — a missing
    follower count never fails the post scrape."""
    try:
        items = run_dataset(
            THREADS_PROFILES_DATASET_ID,
            [{"url": f"https://www.threads.com/@{username}"}],
        )
        for item in items:
            if item.get("error"):
                continue
            followers = item.get("followers") or item.get("num_followers") or item.get("follower_count")
            if followers is not None:
                return int(followers)
    except Exception as exc:
        logger.warning("Bright Data Threads follower count fetch failed for @%s: %s", username, exc)
    return None


def _scrape_threads_profile_bright_data(
    db: Database,
    account: Account,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """Scrapes a Threads profile through Bright Data post discovery."""
    username = account.username.strip().lstrip("@")
    logger.info("Starting Threads scrape (Bright Data) for @%s (limit=%s)", username, max_posts)

    try:
        items = run_dataset(
            THREADS_POSTS_DATASET_ID,
            [{"profile_url": f"https://www.threads.com/@{username}"}],
            query={"type": "discover_new", "discover_by": "profile"},
            # Threads' "profile" discovery collector is verified (live, 2026-09-20) to
            # routinely take 200s+ per token to complete — much slower than Instagram/
            # TikTok's "url"-based discovery. The default 180s budget cuts it off right
            # before completion; 300s gives it a realistic chance without hanging forever.
            timeout=300.0,
        )
        raw_posts: List[Dict[str, Any]] = [
            post for post in (_bright_data_item_to_raw_post(item, username) for item in items)
            if post is not None
        ][:max_posts]
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

        follower_count = _fetch_threads_follower_count(username)
        if follower_count is not None:
            db.update_account_follower_count(account.id, follower_count)

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
            "Successfully scraped (Bright Data) and stored %s Threads posts for @%s",
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

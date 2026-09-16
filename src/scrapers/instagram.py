from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import instaloader
from ..models import Account, Post, ScrapeLog
from ..db import Database
from ..ingest import ingest_scraped_batch
from .apify_client import INSTAGRAM_ACTOR_ID, is_apify_configured, run_actor_sync

logger = logging.getLogger("scrapers.instagram")


def create_instaloader_instance() -> instaloader.Instaloader:
    """Creates a configured Instaloader instance with optional credentials from env."""
    L = instaloader.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        max_connection_attempts=1,
        request_timeout=15.0,
        fatal_status_codes=[429],
    )

    ig_user = os.getenv("INSTAGRAM_USERNAME")
    ig_pass = os.getenv("INSTAGRAM_PASSWORD")
    session_file = os.getenv("INSTAGRAM_SESSION_FILE")

    if ig_user:
        try:
            if session_file and os.path.exists(session_file):
                L.load_session_from_file(ig_user, filename=session_file)
                logger.info(f"Loaded Instagram session for {ig_user}")
            elif ig_pass:
                L.login(ig_user, ig_pass)
                logger.info(f"Logged in to Instagram as {ig_user}")
        except Exception as exc:
            logger.warning(f"Failed Instagram login/session load: {exc}. Proceeding anonymously.")

    return L


def _scrape_instagram_profile_apify(
    db: Database,
    account: Account,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """Scrapes an Instagram profile via the Apify `apify/instagram-scraper` actor."""
    username = account.username.strip().lstrip("@")
    logger.info(f"Starting Instagram scrape (Apify) for @{username} (limit={max_posts})")

    try:
        items = run_actor_sync(
            INSTAGRAM_ACTOR_ID,
            {
                "directUrls": [f"https://www.instagram.com/{username}/"],
                "resultsType": "posts",
                "resultsLimit": max_posts,
            },
        )

        raw_posts: List[Dict[str, Any]] = []
        for item in items:
            if item.get("error") or not (item.get("shortCode") or item.get("id")):
                continue
            raw_posts.append({
                "shortcode": item.get("shortCode") or item.get("id"),
                "id": str(item.get("id") or item.get("shortCode")),
                "caption": item.get("caption") or "",
                "display_url": item.get("displayUrl") or "",
                "likes": item.get("likesCount") or 0,
                "comments": item.get("commentsCount") or 0,
                "video_view_count": item.get("videoViewCount"),
                "date_utc": item.get("timestamp"),
            })

        if not raw_posts:
            err_msg = f"Apify Instagram scraper returned no usable posts for @{username} (profile may be private, empty, or not found)"
            logger.warning(err_msg)
            db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="failed", error_message=err_msg))
            return 0, err_msg

        inserted_count, err = ingest_scraped_batch(
            db=db,
            platform="instagram",
            account=account,
            raw_posts=raw_posts,
        )
        if err:
            logger.error(f"Ingestion error for @{username}: {err}")
            return 0, err

        logger.info(f"Successfully scraped (Apify) and stored {inserted_count} posts for @{username}")
        return inserted_count, None

    except Exception as exc:
        err_msg = f"Apify Instagram scrape failed for @{username}: {str(exc)}"
        logger.error(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="failed", error_message=err_msg))
        return 0, err_msg


def _scrape_instagram_profile_instaloader(
    db: Database,
    account: Account,
    max_posts: int,
    delay_between_requests: float,
) -> Tuple[int, Optional[str]]:
    """Scrapes an Instagram profile via Instaloader (free, anonymous by default; blockable by IP)."""
    username = account.username.strip().lstrip("@")
    logger.info(f"Starting Instagram scrape (Instaloader) for @{username} (limit={max_posts})")

    L = create_instaloader_instance()
    raw_posts: List[Dict[str, Any]] = []

    try:
        profile = instaloader.Profile.from_username(L.context, username)

        count = 0
        for post in profile.get_posts():
            if count >= max_posts:
                break

            raw_post = {
                "shortcode": post.shortcode,
                "id": str(post.mediaid),
                "caption": post.caption or "",
                "display_url": post.url or "",
                "likes": post.likes,
                "comments": post.comments,
                "video_view_count": post.video_view_count if post.is_video else None,
                "date_utc": post.date_utc.isoformat() + "+00:00" if post.date_utc else None,
            }
            raw_posts.append(raw_post)
            count += 1
            if delay_between_requests > 0:
                time.sleep(delay_between_requests)

        inserted_count, err = ingest_scraped_batch(
            db=db,
            platform="instagram",
            account=account,
            raw_posts=raw_posts,
        )
        if err:
            logger.error(f"Ingestion error for @{username}: {err}")
            return 0, err

        logger.info(f"Successfully scraped and stored {inserted_count} posts for @{username}")
        return inserted_count, None

    except instaloader.exceptions.ProfileNotExistsException:
        err_msg = f"Instagram profile @{username} does not exist"
        logger.error(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="failed", error_message=err_msg))
        return 0, err_msg

    except Exception as exc:
        err_str = str(exc)
        if "429" in err_str or "Too Many Requests" in err_str:
            err_msg = (
                f"Instagram rate limit (429) saat scrape @{username}. "
                "Instagram membatasi IP cloud/VPS untuk request anonim. "
                "Solusi: Tambahkan INSTAGRAM_USERNAME & INSTAGRAM_PASSWORD (akun burner) di tab Environment Dokploy."
            )
        else:
            err_msg = f"Instagram scrape failed for @{username}: {err_str}"
        logger.error(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="failed", error_message=err_msg))
        return 0, err_msg


def scrape_instagram_profile(
    db: Database,
    account: Account,
    max_posts: int = 30,
    delay_between_requests: float = 1.0,
) -> Tuple[int, Optional[str], str]:
    """
    Scrapes public posts from an Instagram profile and saves them to the database.
    Uses Apify (apify/instagram-scraper) when APIFY_API_TOKEN is configured — reliable,
    runs on Apify's own residential proxies, not blockable from this VPS's IP.
    Falls back to Instaloader (with INSTAGRAM_USERNAME/PASSWORD if set) when Apify
    is unconfigured or fails (e.g. quota exhausted).
    Returns (posts_added, error, backend) where backend is "apify" or "instaloader" —
    the scraper that actually produced the result, never assumed from configuration
    alone. If Apify was attempted and failed before falling back, and Instaloader then
    also fails, both failure reasons are included in `error` — a silent fallback would
    leave the caller (and the user, via chat receipts) unable to tell that Apify was
    ever tried at all, let alone why it failed.
    """
    apify_err: Optional[str] = None
    if is_apify_configured():
        count, err = _scrape_instagram_profile_apify(db, account, max_posts)
        if not err:
            return count, None, "apify"
        apify_err = err
        logger.warning(f"Apify failed, falling back to Instaloader: {err}")
    count, err = _scrape_instagram_profile_instaloader(db, account, max_posts, delay_between_requests)
    if err and apify_err:
        err = f"Apify: {apify_err} | Instaloader: {err}"
    return count, err, "instaloader"

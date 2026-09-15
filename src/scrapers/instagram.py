from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import instaloader
from ..models import Account, Post, ScrapeLog
from ..db import Database
from ..ingest import ingest_scraped_batch

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


def scrape_instagram_profile(
    db: Database,
    account: Account,
    max_posts: int = 30,
    delay_between_requests: float = 1.0,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes public posts from an Instagram profile and saves them to the database.
    Handles rate-limits with retries and logs outcome to scrape_logs.
    """
    username = account.username.strip().lstrip("@")
    logger.info(f"Starting Instagram scrape for @{username} (limit={max_posts})")
    
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

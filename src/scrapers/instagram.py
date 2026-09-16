from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

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


def _merge_recent_posts(
    feed_posts: List[Dict[str, Any]],
    reel_posts: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    """Deduplicates Feed/Reels and returns the newest items within one shared limit."""
    by_id: Dict[str, Dict[str, Any]] = {}
    for post in feed_posts:
        by_id[str(post["shortcode"])] = post
    for post in reel_posts:
        # Prefer the Reels result for duplicates: it carries the more specific content
        # type and generally exposes video play/view data absent from the Feed result.
        by_id[str(post["shortcode"])] = post
    return sorted(
        by_id.values(),
        key=lambda post: str(post.get("date_utc") or ""),
        reverse=True,
    )[:limit]


def _apify_item_to_raw_post(item: Dict[str, Any], content_type: str) -> Optional[Dict[str, Any]]:
    if item.get("error") or not (item.get("shortCode") or item.get("id")):
        return None
    views = item.get("videoPlayCount")
    if views is None:
        views = item.get("videoViewCount")
    return {
        "shortcode": item.get("shortCode") or item.get("id"),
        "id": str(item.get("id") or item.get("shortCode")),
        "caption": item.get("caption") or "",
        "display_url": (
            item.get("videoUrl") if content_type == "reel" else None
        ) or item.get("displayUrl") or "",
        # Apify uses -1 for hidden/unavailable engagement counts.
        "likes": max(0, item.get("likesCount") or 0),
        "comments": max(0, item.get("commentsCount") or 0),
        "video_view_count": max(0, views) if views is not None else None,
        "date_utc": item.get("timestamp"),
        "content_type": content_type,
    }


def _instaloader_post_to_raw_post(post: Any, content_type: str) -> Dict[str, Any]:
    return {
        "shortcode": post.shortcode,
        "id": str(post.mediaid),
        "caption": post.caption or "",
        "display_url": post.video_url if content_type == "reel" and post.video_url else post.url or "",
        "likes": max(0, post.likes),
        "comments": max(0, post.comments),
        "video_view_count": max(0, post.video_view_count) if post.is_video and post.video_view_count is not None else None,
        "date_utc": post.date_utc.isoformat() + "+00:00" if post.date_utc else None,
        "content_type": content_type,
    }


def _scrape_instagram_profile_apify(
    db: Database,
    account: Account,
    max_posts: int,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[int, Optional[str]]:
    """Scrapes both Instagram Feed posts and Reels via Apify."""
    username = account.username.strip().lstrip("@")
    logger.info(f"Starting Instagram Feed + Reels scrape (Apify) for @{username} (combined limit={max_posts})")

    try:
        source_posts: Dict[str, List[Dict[str, Any]]] = {"feed": [], "reel": []}
        for content_type, results_type in (("feed", "posts"), ("reel", "reels")):
            if progress_callback:
                label = "Feed" if content_type == "feed" else "Reels"
                progress_callback(f"Mengambil postingan {label} @{username}…")
            items = run_actor_sync(
                INSTAGRAM_ACTOR_ID,
                {
                    "directUrls": [f"https://www.instagram.com/{username}/"],
                    "resultsType": results_type,
                    "resultsLimit": max_posts,
                },
            )
            for item in items:
                raw_post = _apify_item_to_raw_post(item, content_type)
                if raw_post is not None:
                    source_posts[content_type].append(raw_post)

        if progress_callback:
            progress_callback("Menggabungkan Feed dan Reels, menghapus duplikasi, lalu menyimpan data…")
        raw_posts = _merge_recent_posts(source_posts["feed"], source_posts["reel"], max_posts)
        if not raw_posts:
            err_msg = f"Apify Instagram scraper returned no usable Feed posts or Reels for @{username} (profile may be private, empty, or not found)"
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

        logger.info(f"Successfully scraped (Apify) and stored {inserted_count} Feed/Reels items for @{username}")
        if progress_callback:
            progress_callback(f"Selesai: {inserted_count} postingan Feed/Reels tersimpan")
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
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[int, Optional[str]]:
    """Scrapes both Instagram Feed posts and Reels via Instaloader."""
    username = account.username.strip().lstrip("@")
    logger.info(f"Starting Instagram Feed + Reels scrape (Instaloader) for @{username} (combined limit={max_posts})")

    L = create_instaloader_instance()

    try:
        profile = instaloader.Profile.from_username(L.context, username)
        source_posts: Dict[str, List[Dict[str, Any]]] = {"feed": [], "reel": []}
        for content_type, iterator in (("feed", profile.get_posts()), ("reel", profile.get_reels())):
            if progress_callback:
                label = "Feed" if content_type == "feed" else "Reels"
                progress_callback(f"Mengambil postingan {label} @{username}…")
            posts = source_posts[content_type]
            for post in iterator:
                if len(posts) >= max_posts:
                    break
                posts.append(_instaloader_post_to_raw_post(post, content_type))
                if delay_between_requests > 0:
                    time.sleep(delay_between_requests)

        if progress_callback:
            progress_callback("Menggabungkan Feed dan Reels, menghapus duplikasi, lalu menyimpan data…")
        raw_posts = _merge_recent_posts(source_posts["feed"], source_posts["reel"], max_posts)
        if not raw_posts:
            err_msg = f"Instaloader returned no usable Feed posts or Reels for @{username}"
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

        logger.info(f"Successfully scraped and stored {inserted_count} Feed/Reels items for @{username}")
        if progress_callback:
            progress_callback(f"Selesai: {inserted_count} postingan Feed/Reels tersimpan")
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
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[int, Optional[str], str]:
    """
    Scrapes public Feed posts and Reels from an Instagram profile and saves them.
    Uses Apify when configured, then falls back to Instaloader. The returned backend
    always identifies the scraper that produced the result. If both fail, the error
    includes both reasons. `progress_callback`, when supplied, receives user-facing
    phase updates suitable for a streaming chat UI.
    """
    apify_err: Optional[str] = None
    if is_apify_configured():
        count, err = _scrape_instagram_profile_apify(
            db, account, max_posts, progress_callback=progress_callback,
        )
        if not err:
            return count, None, "apify"
        apify_err = err
        logger.warning(f"Apify failed, falling back to Instaloader: {err}")
    count, err = _scrape_instagram_profile_instaloader(
        db, account, max_posts, delay_between_requests,
        progress_callback=progress_callback,
    )
    if err and apify_err:
        err = f"Apify: {apify_err} | Instaloader: {err}"
    return count, err, "instaloader"

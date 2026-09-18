from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from ..models import Account, Post, ScrapeLog
from ..db import Database
from ..ingest import ingest_scraped_batch
from .bright_data_client import (
    TIKTOK_POSTS_DATASET_ID,
    TIKTOK_PROFILES_DATASET_ID,
    is_bright_data_configured,
    run_dataset,
)
from .tiktokapi_client import (
    _tiktokapi_item_to_raw_post,
    fetch_user_videos,
    is_tiktokapi_available,
)

logger = logging.getLogger("scrapers.tiktok")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _extract_sigi_or_hydration_data(html: str) -> Optional[Dict[str, Any]]:
    """Extracts JSON payload embedded in TikTok profile HTML."""
    # Method 1: __UNIVERSAL_DATA_FOR_REHYDRATION__
    match = re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>([^<]+)</script>', html)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # Method 2: SIGI_STATE
    match = re.search(r'<script id="SIGI_STATE"[^>]*>([^<]+)</script>', html)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # Method 3: window['SIGI_STATE'] assignment
    match = re.search(r"window\['SIGI_STATE'\]\s*=\s*(\{.*?\});", html, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    return None


def _bright_data_item_to_raw_post(item: Dict[str, Any], username: str) -> Optional[Dict[str, Any]]:
    """Adapts a Bright Data TikTok post record to the ingestion contract."""
    if item.get("error"):
        return None
    video_id = item.get("post_id") or item.get("id") or item.get("video_id")
    if not video_id:
        return None
    return {
        "id": str(video_id),
        "desc": item.get("description") or item.get("caption") or item.get("text") or "",
        "video": {
            "downloadAddr": item.get("video_url") or item.get("web_video_url") or "",
            "playAddr": "",
        },
        "post_url": (
            item.get("url")
            or (f"https://www.tiktok.com/@{username}/video/{video_id}" if username else "")
        ),
        "stats": {
            "diggCount": max(0, int(item.get("digg_count") or item.get("likes") or 0)),
            "commentCount": max(0, int(item.get("comment_count") or item.get("comments") or 0)),
            "playCount": max(0, int(item.get("play_count") or item.get("views") or 0)),
        },
        "createTime": item.get("create_time") or item.get("created_at"),
    }


def _fetch_tiktok_follower_count(username: str) -> Optional[int]:
    """Fetches follower count via Bright Data's TikTok Profiles dataset (separate from the
    Posts dataset, which doesn't carry follower count per video). Best-effort: any failure
    here is logged and swallowed — a missing follower count never fails the post scrape."""
    try:
        items = run_dataset(
            TIKTOK_PROFILES_DATASET_ID,
            [{"url": f"https://www.tiktok.com/@{username}"}],
        )
        for item in items:
            if item.get("error"):
                continue
            followers = item.get("followers")
            if followers is not None:
                return int(followers)
    except Exception as exc:
        logger.warning("Bright Data TikTok follower count fetch failed for @%s: %s", username, exc)
    return None


def _scrape_tiktok_profile_bright_data(
    db: Database,
    account: Account,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """Scrapes a TikTok profile through Bright Data post discovery."""
    username = account.username.strip().lstrip("@")
    logger.info("Starting TikTok scrape (Bright Data) for @%s (limit=%s)", username, max_posts)

    try:
        items = run_dataset(
            TIKTOK_POSTS_DATASET_ID,
            [{
                "url": f"https://www.tiktok.com/@{username}",
                "num_of_posts": max_posts,
            }],
            query={"type": "discover_new", "discover_by": "profile_url"},
        )
        raw_posts = [
            post for post in (_bright_data_item_to_raw_post(item, username) for item in items)
            if post is not None
        ][:max_posts]
        if not raw_posts:
            err_msg = (
                f"Bright Data returned no usable TikTok videos for @{username} "
                "(profile may be private, empty, or not found)"
            )
            logger.warning(err_msg)
            db.insert_scrape_log(
                ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg)
            )
            return 0, err_msg

        follower_count = _fetch_tiktok_follower_count(username)
        if follower_count is not None:
            db.update_account_follower_count(account.id, follower_count)

        inserted_count, err = ingest_scraped_batch(
            db=db,
            platform="tiktok",
            account=account,
            raw_posts=raw_posts,
        )
        if err:
            logger.error("Ingestion error for TikTok @%s: %s", username, err)
            return 0, err
        logger.info(
            "Successfully scraped (Bright Data) and stored %s TikTok videos for @%s",
            inserted_count,
            username,
        )
        return inserted_count, None
    except Exception as exc:
        err_msg = f"Bright Data TikTok scrape failed for @{username}: {str(exc)}"
        logger.error(err_msg)
        db.insert_scrape_log(
            ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg)
        )
        return 0, err_msg


def _scrape_tiktok_profile_playwright(
    db: Database,
    account: Account,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """Scrapes a TikTok profile via TikTokApi (free, self-hosted Playwright/Chromium)."""
    username = account.username.strip().lstrip("@")
    logger.info(f"Starting TikTok scrape (TikTokApi/Playwright) for @{username} (limit={max_posts})")

    try:
        items = fetch_user_videos(username, max_posts)
        raw_posts = [p for p in (_tiktokapi_item_to_raw_post(item) for item in items) if p is not None]

        if not raw_posts:
            err_msg = f"TikTokApi returned no usable videos for @{username} (profile may be private, empty, or not found)"
            logger.warning(err_msg)
            db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
            return 0, err_msg

        inserted_count, err = ingest_scraped_batch(
            db=db,
            platform="tiktok",
            account=account,
            raw_posts=raw_posts,
        )
        if err:
            logger.error(f"Ingestion error for TikTok @{username}: {err}")
            return 0, err

        logger.info(f"Successfully scraped (TikTokApi) and stored {inserted_count} TikTok videos for @{username}")
        return inserted_count, None

    except Exception as exc:
        err_msg = f"TikTokApi scrape failed for @{username}: {str(exc)}"
        logger.error(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
        return 0, err_msg


def _scrape_tiktok_profile_html(
    db: Database,
    account: Account,
    max_posts: int,
    delay_between_requests: float,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes public videos from a TikTok profile via raw HTML parsing (free, no external
    dependency, but fragile — TikTok frequently changes page structure / requires captcha).
    """
    username = account.username.strip().lstrip("@")
    logger.info(f"Starting TikTok scrape (HTML) for @{username} (limit={max_posts})")

    url = f"https://www.tiktok.com/@{username}"
    ms_token = os.getenv("TIKTOK_MS_TOKEN")

    headers = {
        "User-Agent": os.getenv("TIKTOK_USER_AGENT", DEFAULT_USER_AGENT),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        "Referer": "https://www.tiktok.com/",
    }

    cookies = {}
    if ms_token:
        cookies["msToken"] = ms_token
    session_cookie = os.getenv("TIKTOK_SESSION_COOKIE")
    if session_cookie:
        cookies["sessionid"] = session_cookie

    raw_posts: List[Dict[str, Any]] = []

    try:
        with httpx.Client(headers=headers, cookies=cookies, follow_redirects=True, timeout=25.0) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                err_msg = f"TikTok account @{username} not found (HTTP 404)"
                logger.error(err_msg)
                db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
                return 0, err_msg

            if resp.status_code != 200:
                err_msg = f"TikTok request failed with status code {resp.status_code}"
                logger.error(err_msg)
                db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
                return 0, err_msg

            data = _extract_sigi_or_hydration_data(resp.text)
            if not data:
                err_msg = f"Could not extract video data from TikTok profile HTML for @{username} (page structure updated or captcha required)"
                logger.warning(err_msg)
                db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
                return 0, err_msg

            item_module = data.get("ItemModule", {})
            if not item_module:
                default_scope = data.get("__DEFAULT_SCOPE__", {})
                user_detail = default_scope.get("webapp.user-detail", {})
                item_module = user_detail.get("itemModule", {})

            count = 0
            for item_id, item in item_module.items():
                if count >= max_posts:
                    break

                raw_post = {
                    "id": str(item.get("id") or item_id),
                    "desc": item.get("desc") or item.get("caption") or "",
                    "video": {
                        "downloadAddr": item.get("video", {}).get("downloadAddr") or "",
                        "playAddr": item.get("video", {}).get("playAddr") or "",
                    },
                    "post_url": f"https://www.tiktok.com/@{username}/video/{item.get('id') or item_id}",
                    "stats": {
                        "diggCount": item.get("stats", {}).get("diggCount") or item.get("diggCount", 0),
                        "commentCount": item.get("stats", {}).get("commentCount") or item.get("commentCount", 0),
                        "playCount": item.get("stats", {}).get("playCount") or item.get("playCount", 0),
                    },
                    "createTime": item.get("createTime") or int(time.time()),
                }
                raw_posts.append(raw_post)
                count += 1

            if delay_between_requests > 0:
                time.sleep(delay_between_requests)

        inserted_count, err = ingest_scraped_batch(
            db=db,
            platform="tiktok",
            account=account,
            raw_posts=raw_posts,
        )
        if err:
            logger.error(f"Ingestion error for TikTok @{username}: {err}")
            return 0, err

        logger.info(f"Successfully scraped and stored {inserted_count} TikTok videos for @{username}")
        return inserted_count, None

    except Exception as exc:
        err_msg = f"TikTok scrape failed for @{username}: {str(exc)}"
        logger.error(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
        return 0, err_msg


def scrape_tiktok_profile(
    db: Database,
    account: Account,
    max_posts: int = 30,
    delay_between_requests: float = 1.0,
) -> Tuple[int, Optional[str], str]:
    """
    Scrapes public videos from a TikTok profile and saves them to the database.
    Dispatch order: Bright Data, TikTokApi/Playwright, then raw HTML.
    Returns the backend that actually produced the result.
    """
    bright_data_err: Optional[str] = None
    if is_bright_data_configured():
        count, err = _scrape_tiktok_profile_bright_data(db, account, max_posts)
        if not err:
            return count, None, "bright_data"
        bright_data_err = err
        logger.warning("Bright Data failed, falling back to local TikTok scraper: %s", err)
    if is_tiktokapi_available():
        count, err = _scrape_tiktok_profile_playwright(db, account, max_posts)
        if err and bright_data_err:
            err = f"Bright Data: {bright_data_err} | TikTokApi: {err}"
        return count, err, "playwright"
    count, err = _scrape_tiktok_profile_html(db, account, max_posts, delay_between_requests)
    if err and bright_data_err:
        err = f"Bright Data: {bright_data_err} | HTML: {err}"
    return count, err, "html"

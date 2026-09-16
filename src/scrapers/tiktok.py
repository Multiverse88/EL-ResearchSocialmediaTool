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
from .apify_client import TIKTOK_ACTOR_ID, is_apify_configured, run_actor_sync
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


def _apify_item_to_raw_post(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Adapts a clockworks/tiktok-scraper dataset item into ingest.py's expected raw_post shape."""
    video_id = item.get("id")
    if not video_id:
        return None
    video_meta = item.get("videoMeta") or {}
    return {
        "id": str(video_id),
        "desc": item.get("text") or "",
        "video": {
            "downloadAddr": video_meta.get("downloadAddr") or item.get("webVideoUrl") or "",
            "playAddr": video_meta.get("playAddr") or "",
        },
        "stats": {
            # Defensively clamp: engagement counts must never be negative regardless of
            # source quirks (Instagram's Apify actor uses -1 as a hidden-count sentinel;
            # guard TikTok's mapping the same way rather than assume it can't happen).
            "diggCount": max(0, item.get("diggCount") or 0),
            "commentCount": max(0, item.get("commentCount") or 0),
            "playCount": max(0, item.get("playCount") or 0),
        },
        "createTime": item.get("createTime") or int(time.time()),
    }


def _scrape_tiktok_profile_apify(
    db: Database,
    account: Account,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """Scrapes a TikTok profile via the Apify `clockworks/tiktok-scraper` actor."""
    username = account.username.strip().lstrip("@")
    logger.info(f"Starting TikTok scrape (Apify) for @{username} (limit={max_posts})")

    try:
        items = run_actor_sync(
            TIKTOK_ACTOR_ID,
            {
                "profiles": [username],
                "maxProfileVideos": max_posts,
                "profileScrapeSections": ["videos"],
                "profileSorting": "Latest",
            },
        )

        raw_posts = [p for p in (_apify_item_to_raw_post(item) for item in items) if p is not None]

        if not raw_posts:
            err_msg = f"Apify TikTok scraper returned no usable videos for @{username} (profile may be private, empty, or not found)"
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

        logger.info(f"Successfully scraped (Apify) and stored {inserted_count} TikTok videos for @{username}")
        return inserted_count, None

    except Exception as exc:
        err_msg = f"Apify TikTok scrape failed for @{username}: {str(exc)}"
        logger.error(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
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
    Dispatch order:
      1. Apify (clockworks/tiktok-scraper) when APIFY_API_TOKEN is configured — most reliable,
         handles TikTok's anti-bot measures on Apify's own infrastructure.
      2. TikTokApi/Playwright (free, self-hosted headless Chromium) when installed.
      3. Raw HTML parsing (free, no extra dependency, but fragile — breaks when TikTok
         changes page markup).
    Returns (posts_added, error, backend) — the scraper that actually ran, never assumed
    from configuration alone.
    """
    if is_apify_configured():
        count, err = _scrape_tiktok_profile_apify(db, account, max_posts)
        return count, err, "apify"
    if is_tiktokapi_available():
        count, err = _scrape_tiktok_profile_playwright(db, account, max_posts)
        return count, err, "playwright"
    count, err = _scrape_tiktok_profile_html(db, account, max_posts, delay_between_requests)
    return count, err, "html"

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


def scrape_tiktok_profile(
    db: Database,
    account: Account,
    max_posts: int = 30,
    delay_between_requests: float = 1.0,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes public videos from a TikTok profile without requiring heavy third-party driver binaries.
    Uses public SSR profile data, supports ms_token & cookies from env, and logs to scrape_logs.
    """
    username = account.username.strip().lstrip("@")
    logger.info(f"Starting TikTok scrape for @{username} (limit={max_posts})")

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
                # If extraction fails, log warning
                err_msg = f"Could not extract video data from TikTok profile HTML for @{username} (page structure updated or captcha required)"
                logger.warning(err_msg)
                db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
                return 0, err_msg

            # Parse itemModule or itemList from extracted state
            item_module = data.get("ItemModule", {})
            if not item_module:
                # Look inside __DEFAULT_SCOPE__ -> webapp.user-detail
                default_scope = data.get("__DEFAULT_SCOPE__", {})
                user_detail = default_scope.get("webapp.user-detail", {})
                item_module = user_detail.get("itemModule", {})

            # Convert itemModule items to raw_posts
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

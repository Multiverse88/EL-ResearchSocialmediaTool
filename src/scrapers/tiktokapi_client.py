from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger("scrapers.tiktokapi")


def is_tiktokapi_available() -> bool:
    """
    Checks whether the TikTokApi + Playwright free scraping path can be used.
    Returns False gracefully if the (heavy, optional) dependency isn't installed
    or its Chromium browser wasn't provisioned, instead of crashing the app.
    """
    try:
        import TikTokApi  # noqa: F401
        return True
    except Exception as exc:
        logger.debug(f"TikTokApi not available: {exc}")
        return False


def _get_ms_token() -> Optional[str]:
    token = os.getenv("TIKTOK_MS_TOKEN", "").strip()
    return token or None


def _tiktokapi_item_to_raw_post(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Adapts a TikTokApi `video.as_dict` item into ingest.py's expected raw_post shape."""
    video_id = item.get("id")
    if not video_id:
        return None
    stats = item.get("stats") or item.get("statsV2") or {}
    video_meta = item.get("video") or {}
    author_username = _tiktokapi_item_author_username(item)
    return {
        "id": str(video_id),
        "desc": item.get("desc") or "",
        "video": {
            "downloadAddr": video_meta.get("downloadAddr") or "",
            "playAddr": video_meta.get("playAddr") or "",
        },
        "post_url": (
            f"https://www.tiktok.com/@{author_username}/video/{video_id}" if author_username else ""
        ),
        "stats": {
            "diggCount": int(stats.get("diggCount") or 0),
            "commentCount": int(stats.get("commentCount") or 0),
            "playCount": int(stats.get("playCount") or 0),
        },
        "createTime": item.get("createTime") or 0,
    }


def _tiktokapi_item_author_username(item: Dict[str, Any]) -> Optional[str]:
    author = item.get("author") or {}
    if isinstance(author, dict):
        return author.get("uniqueId") or author.get("nickname")
    return None


async def _fetch_user_videos_async(username: str, count: int) -> List[Dict[str, Any]]:
    from TikTokApi import TikTokApi

    ms_token = _get_ms_token()
    videos: List[Dict[str, Any]] = []
    async with TikTokApi() as api:
        await api.create_sessions(
            ms_tokens=[ms_token] if ms_token else None,
            num_sessions=1,
            sleep_after=3,
            browser="chromium",
            headless=True,
        )
        user = api.user(username=username)
        async for video in user.videos(count=count):
            videos.append(video.as_dict)
    return videos


async def _fetch_hashtag_videos_async(hashtag: str, count: int) -> List[Dict[str, Any]]:
    from TikTokApi import TikTokApi

    ms_token = _get_ms_token()
    videos: List[Dict[str, Any]] = []
    async with TikTokApi() as api:
        await api.create_sessions(
            ms_tokens=[ms_token] if ms_token else None,
            num_sessions=1,
            sleep_after=3,
            browser="chromium",
            headless=True,
        )
        tag = api.hashtag(name=hashtag)
        async for video in tag.videos(count=count):
            videos.append(video.as_dict)
    return videos


def fetch_user_videos(username: str, count: int) -> List[Dict[str, Any]]:
    """Sync bridge: fetches a TikTok profile's recent videos via TikTokApi/Playwright."""
    return asyncio.run(_fetch_user_videos_async(username, count))


def fetch_hashtag_videos(hashtag: str, count: int) -> List[Dict[str, Any]]:
    """Sync bridge: fetches videos for a TikTok hashtag via TikTokApi/Playwright."""
    return asyncio.run(_fetch_hashtag_videos_async(hashtag, count))

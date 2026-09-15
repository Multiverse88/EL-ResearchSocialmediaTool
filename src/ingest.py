from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from .models import Account, Post, ScrapeLog
from .db import Database


def _parse_timestamp(val: Any) -> str:
    if isinstance(val, (int, float)):
        # UNIX timestamp (seconds or milliseconds)
        ts = val if val < 1e11 else val / 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    elif isinstance(val, str):
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def normalize_instagram_post(raw: Dict[str, Any], account_id: str) -> Post:
    platform_post_id = str(raw.get("shortcode") or raw.get("id") or raw.get("mediaid") or "")
    caption = str(raw.get("caption") or raw.get("edge_media_to_caption", {}).get("edges", [{}])[0].get("node", {}).get("text", "") or "")
    media_url = str(raw.get("display_url") or raw.get("thumbnail_src") or raw.get("media_url") or "")
    likes = int(raw.get("likes") or raw.get("edge_media_preview_like", {}).get("count") or raw.get("like_count") or 0)
    comments = int(raw.get("comments") or raw.get("edge_media_to_comment", {}).get("count") or raw.get("comment_count") or 0)
    
    views = raw.get("video_view_count") or raw.get("views")
    views_int = int(views) if views is not None else None

    posted_at_raw = raw.get("date_utc") or raw.get("taken_at_timestamp") or raw.get("posted_at")
    posted_at = _parse_timestamp(posted_at_raw)
    scraped_at = datetime.now(timezone.utc).isoformat()

    return Post.create(
        account_id=account_id,
        platform_post_id=platform_post_id,
        caption=caption,
        media_url=media_url,
        likes=likes,
        comments=comments,
        views=views_int,
        posted_at=posted_at,
        scraped_at=scraped_at,
        platform="instagram",
    )


def normalize_tiktok_post(raw: Dict[str, Any], account_id: str) -> Post:
    platform_post_id = str(raw.get("id") or raw.get("video_id") or "")
    caption = str(raw.get("desc") or raw.get("caption") or "")
    
    media_url = ""
    if "video" in raw and isinstance(raw["video"], dict):
        media_url = str(raw["video"].get("downloadAddr") or raw["video"].get("playAddr") or "")
    elif "media_url" in raw:
        media_url = str(raw["media_url"])

    stats = raw.get("stats", {})
    likes = int(stats.get("diggCount") or raw.get("likes") or raw.get("digg_count") or 0)
    comments = int(stats.get("commentCount") or raw.get("comments") or raw.get("comment_count") or 0)
    views = stats.get("playCount") or raw.get("views") or raw.get("play_count")
    views_int = int(views) if views is not None else None

    create_time = raw.get("createTime") or raw.get("created_at") or raw.get("posted_at")
    posted_at = _parse_timestamp(create_time)
    scraped_at = datetime.now(timezone.utc).isoformat()

    return Post.create(
        account_id=account_id,
        platform_post_id=platform_post_id,
        caption=caption,
        media_url=media_url,
        likes=likes,
        comments=comments,
        views=views_int,
        posted_at=posted_at,
        scraped_at=scraped_at,
        platform="tiktok",
    )


def ingest_scraped_batch(
    db: Database,
    platform: str,
    account: Account,
    raw_posts: List[Dict[str, Any]],
) -> Tuple[int, Optional[str]]:
    """Normalizes and ingests a batch of raw scraped posts, with automatic scrape logging."""
    platform = platform.lower()
    try:
        norm_posts: List[Post] = []
        for raw in raw_posts:
            if platform == "instagram":
                post = normalize_instagram_post(raw, account.id)
            elif platform == "tiktok":
                post = normalize_tiktok_post(raw, account.id)
            else:
                raise ValueError(f"Unsupported platform: {platform}")
            norm_posts.append(post)

        inserted_count = db.upsert_posts(norm_posts)
        
        log = ScrapeLog.create(
            platform=platform,
            status="success",
            error_message=None,
        )
        db.insert_scrape_log(log)
        return inserted_count, None

    except Exception as exc:
        error_msg = str(exc)
        log = ScrapeLog.create(
            platform=platform,
            status="failed",
            error_message=error_msg,
        )
        db.insert_scrape_log(log)
        return 0, error_msg

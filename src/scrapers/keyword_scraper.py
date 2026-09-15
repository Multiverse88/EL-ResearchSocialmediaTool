from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import instaloader

from ..models import Account, Post, ScrapeLog, Topic
from ..db import Database
from ..ingest import ingest_scraped_batch
from .instagram import create_instaloader_instance
from .tiktok import DEFAULT_USER_AGENT, _extract_sigi_or_hydration_data

logger = logging.getLogger("scrapers.keyword")


def scrape_instagram_hashtag(
    db: Database,
    keyword_or_hashtag: str,
    max_posts: int = 25,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes Instagram content by hashtag / topic keyword.
    """
    clean_tag = re.sub(r"[^a-zA-Z0-9_]", "", keyword_or_hashtag).lower()
    logger.info(f"Starting Instagram hashtag scrape for #{clean_tag} (limit={max_posts})")

    # Get or create a virtual topic account for foreign posts
    topic_acc = db.get_account_by_username("instagram", f"tag_{clean_tag}")
    if not topic_acc:
        topic_acc = Account.create(
            platform="instagram",
            username=f"tag_{clean_tag}",
            is_own_brand=False,
        )
        db.upsert_account(topic_acc)

    L = create_instaloader_instance()
    raw_posts = []

    try:
        hashtag_obj = instaloader.Hashtag.from_name(L.context, clean_tag)
        count = 0
        for post in hashtag_obj.get_posts():
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
            time.sleep(0.5)

        norm_posts = []
        for raw in raw_posts:
            p = Post.create(
                account_id=topic_acc.id,
                platform_post_id=raw["shortcode"],
                caption=raw["caption"],
                media_url=raw["display_url"],
                likes=raw["likes"],
                comments=raw["comments"],
                views=raw["video_view_count"],
                posted_at=raw["date_utc"],
                platform="instagram",
                topic=keyword_or_hashtag.lower().strip(),
            )
            norm_posts.append(p)

        inserted = db.upsert_posts(norm_posts)
        db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="success"))
        logger.info(f"Ingested {inserted} Instagram posts for topic #{clean_tag}")
        return inserted, None

    except Exception as exc:
        err_msg = f"Instagram hashtag #{clean_tag} scrape error: {str(exc)}"
        logger.warning(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="failed", error_message=err_msg))
        return 0, err_msg


def scrape_tiktok_topic(
    db: Database,
    keyword_or_tag: str,
    max_posts: int = 25,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes TikTok content by tag / topic keyword.
    """
    clean_tag = re.sub(r"[^a-zA-Z0-9_]", "", keyword_or_tag).lower()
    logger.info(f"Starting TikTok topic scrape for #{clean_tag} (limit={max_posts})")

    topic_acc = db.get_account_by_username("tiktok", f"tag_{clean_tag}")
    if not topic_acc:
        topic_acc = Account.create(
            platform="tiktok",
            username=f"tag_{clean_tag}",
            is_own_brand=False,
        )
        db.upsert_account(topic_acc)

    url = f"https://www.tiktok.com/tag/{clean_tag}"
    headers = {
        "User-Agent": os.getenv("TIKTOK_USER_AGENT", DEFAULT_USER_AGENT),
        "Referer": "https://www.tiktok.com/",
    }

    try:
        with httpx.Client(headers=headers, follow_redirects=True, timeout=20.0) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                err_msg = f"TikTok tag page returned status {resp.status_code}"
                db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
                return 0, err_msg

            data = _extract_sigi_or_hydration_data(resp.text)
            if not data:
                err_msg = f"No hydration data found for TikTok tag #{clean_tag}"
                db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
                return 0, err_msg

            item_module = data.get("ItemModule", {})
            if not item_module:
                default_scope = data.get("__DEFAULT_SCOPE__", {})
                item_module = default_scope.get("webapp.challenge-detail", {}).get("itemModule", {})

            norm_posts = []
            count = 0
            for item_id, item in item_module.items():
                if count >= max_posts:
                    break
                stats = item.get("stats", {})
                p = Post.create(
                    account_id=topic_acc.id,
                    platform_post_id=str(item.get("id") or item_id),
                    caption=item.get("desc") or item.get("caption") or "",
                    media_url=item.get("video", {}).get("playAddr") or "",
                    likes=int(stats.get("diggCount") or 0),
                    comments=int(stats.get("commentCount") or 0),
                    views=int(stats.get("playCount") or 0),
                    posted_at=str(item.get("createTime")),
                    platform="tiktok",
                    topic=keyword_or_tag.lower().strip(),
                )
                norm_posts.append(p)
                count += 1

            inserted = db.upsert_posts(norm_posts)
            db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="success"))
            logger.info(f"Ingested {inserted} TikTok videos for topic #{clean_tag}")
            return inserted, None

    except Exception as exc:
        err_msg = f"TikTok topic #{clean_tag} scrape error: {str(exc)}"
        logger.warning(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
        return 0, err_msg


def scrape_topic_content(
    db: Database,
    keyword: str,
    max_posts_per_platform: int = 25,
) -> Dict[str, Any]:
    """
    Executes cross-platform content scraping for a specific topic / keyword.
    """
    # Register topic
    db.upsert_topic(Topic.create(keyword=keyword))

    ig_count, ig_err = scrape_instagram_hashtag(db, keyword, max_posts=max_posts_per_platform)
    tt_count, tt_err = scrape_tiktok_topic(db, keyword, max_posts=max_posts_per_platform)

    return {
        "status": "success",
        "keyword": keyword,
        "instagram": {"posts_added": ig_count, "error": ig_err},
        "tiktok": {"posts_added": tt_count, "error": tt_err},
        "total_posts_added": ig_count + tt_count,
    }

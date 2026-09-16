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
from .apify_client import INSTAGRAM_ACTOR_ID, TIKTOK_ACTOR_ID, is_apify_configured, run_actor_sync
from .instagram import create_instaloader_instance
from .tiktok import DEFAULT_USER_AGENT, _apify_item_to_raw_post, _extract_sigi_or_hydration_data
from .tiktokapi_client import (
    _tiktokapi_item_author_username,
    _tiktokapi_item_to_raw_post,
    fetch_hashtag_videos,
    is_tiktokapi_available,
)

logger = logging.getLogger("scrapers.keyword")


def _get_or_create_account(db: Database, platform: str, username: str, is_own_brand: bool = False) -> Account:
    """Resolves an existing account or registers a new one for real per-author attribution."""
    clean_user = (username or "").strip().lstrip("@").lower() or "unknown"
    acc = db.get_account_by_username(platform, clean_user)
    if acc:
        return acc
    acc = Account.create(platform=platform, username=clean_user, is_own_brand=is_own_brand)
    return db.upsert_account(acc)


def _scrape_instagram_hashtag_apify(
    db: Database,
    keyword_or_hashtag: str,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes Instagram content by hashtag via Apify `apify/instagram-scraper`.
    Attributes each post to its real author account (not a lumped placeholder),
    since Apify returns `ownerUsername` per post.
    """
    clean_tag = re.sub(r"[^a-zA-Z0-9_]", "", keyword_or_hashtag).lower()
    logger.info(f"Starting Instagram hashtag scrape (Apify) for #{clean_tag} (limit={max_posts})")

    try:
        items = run_actor_sync(
            INSTAGRAM_ACTOR_ID,
            {
                "search": clean_tag,
                "searchType": "hashtag",
                "resultsType": "posts",
                "searchLimit": 1,
                "resultsLimit": max_posts,
            },
        )

        norm_posts: List[Post] = []
        for item in items:
            if item.get("error") or not (item.get("shortCode") or item.get("id")):
                continue
            owner_username = item.get("ownerUsername") or f"tag_{clean_tag}"
            acc = _get_or_create_account(db, "instagram", owner_username)
            norm_posts.append(Post.create(
                account_id=acc.id,
                platform_post_id=str(item.get("shortCode") or item.get("id")),
                caption=item.get("caption") or "",
                media_url=item.get("displayUrl") or "",
                # Apify's Instagram actor returns -1 (not None) for hidden/unavailable
                # like/comment counts — clamp so it never stores a negative metric.
                likes=max(0, item.get("likesCount") or 0),
                comments=max(0, item.get("commentsCount") or 0),
                views=item.get("videoViewCount"),
                posted_at=item.get("timestamp"),
                platform="instagram",
                topic=keyword_or_hashtag.lower().strip(),
            ))

        if not norm_posts:
            err_msg = f"Apify Instagram hashtag scraper returned no usable posts for #{clean_tag}"
            logger.warning(err_msg)
            db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="failed", error_message=err_msg))
            return 0, err_msg

        inserted = db.upsert_posts(norm_posts)
        db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="success"))
        logger.info(f"Ingested {inserted} Instagram posts (Apify) for topic #{clean_tag}")
        return inserted, None

    except Exception as exc:
        err_msg = f"Apify Instagram hashtag #{clean_tag} scrape error: {str(exc)}"
        logger.warning(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="failed", error_message=err_msg))
        return 0, err_msg


def _scrape_instagram_hashtag_instaloader(
    db: Database,
    keyword_or_hashtag: str,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """Scrapes Instagram content by hashtag via free anonymous Instaloader (blockable by IP)."""
    clean_tag = re.sub(r"[^a-zA-Z0-9_]", "", keyword_or_hashtag).lower()
    logger.info(f"Starting Instagram hashtag scrape (Instaloader) for #{clean_tag} (limit={max_posts})")

    # Lumps all matched posts under one virtual per-tag account (no per-author attribution
    # available from this free scraping path).
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


def scrape_instagram_hashtag(
    db: Database,
    keyword_or_hashtag: str,
    max_posts: int = 25,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes Instagram content by hashtag / topic keyword.
    Uses Apify (apify/instagram-scraper) when APIFY_API_TOKEN is configured — reliable and
    attributes each post to its real author. Falls back to free anonymous Instaloader otherwise.
    """
    if is_apify_configured():
        return _scrape_instagram_hashtag_apify(db, keyword_or_hashtag, max_posts)
    return _scrape_instagram_hashtag_instaloader(db, keyword_or_hashtag, max_posts)


def _scrape_tiktok_topic_apify(
    db: Database,
    keyword_or_tag: str,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes TikTok content by hashtag via Apify `clockworks/tiktok-scraper`.
    Attributes each video to its real author account via `authorMeta.name`.
    """
    clean_tag = re.sub(r"[^a-zA-Z0-9_]", "", keyword_or_tag).lower()
    logger.info(f"Starting TikTok topic scrape (Apify) for #{clean_tag} (limit={max_posts})")

    try:
        items = run_actor_sync(
            TIKTOK_ACTOR_ID,
            {
                "hashtags": [clean_tag],
                "maxHashtagVideos": max_posts,
            },
        )

        norm_posts: List[Post] = []
        for item in items:
            raw = _apify_item_to_raw_post(item)
            if not raw:
                continue
            author_meta = item.get("authorMeta") or {}
            author_username = author_meta.get("name") or f"tag_{clean_tag}"
            acc = _get_or_create_account(db, "tiktok", author_username)

            stats = raw["stats"]
            norm_posts.append(Post.create(
                account_id=acc.id,
                platform_post_id=raw["id"],
                caption=raw["desc"],
                media_url=raw["video"]["downloadAddr"] or raw["video"]["playAddr"],
                likes=stats["diggCount"],
                comments=stats["commentCount"],
                views=stats["playCount"],
                posted_at=str(raw["createTime"]),
                platform="tiktok",
                topic=keyword_or_tag.lower().strip(),
            ))

        if not norm_posts:
            err_msg = f"Apify TikTok hashtag scraper returned no usable videos for #{clean_tag}"
            logger.warning(err_msg)
            db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
            return 0, err_msg

        inserted = db.upsert_posts(norm_posts)
        db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="success"))
        logger.info(f"Ingested {inserted} TikTok videos (Apify) for topic #{clean_tag}")
        return inserted, None

    except Exception as exc:
        err_msg = f"Apify TikTok topic #{clean_tag} scrape error: {str(exc)}"
        logger.warning(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
        return 0, err_msg


def _scrape_tiktok_topic_playwright(
    db: Database,
    keyword_or_tag: str,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes TikTok content by hashtag via TikTokApi (free, self-hosted Playwright/Chromium).
    Attributes each video to its real author account via the video's `author.uniqueId`.
    """
    clean_tag = re.sub(r"[^a-zA-Z0-9_]", "", keyword_or_tag).lower()
    logger.info(f"Starting TikTok topic scrape (TikTokApi/Playwright) for #{clean_tag} (limit={max_posts})")

    try:
        items = fetch_hashtag_videos(clean_tag, max_posts)

        norm_posts: List[Post] = []
        for item in items:
            raw = _tiktokapi_item_to_raw_post(item)
            if not raw:
                continue
            author_username = _tiktokapi_item_author_username(item) or f"tag_{clean_tag}"
            acc = _get_or_create_account(db, "tiktok", author_username)

            stats = raw["stats"]
            norm_posts.append(Post.create(
                account_id=acc.id,
                platform_post_id=raw["id"],
                caption=raw["desc"],
                media_url=raw["video"]["downloadAddr"] or raw["video"]["playAddr"],
                likes=stats["diggCount"],
                comments=stats["commentCount"],
                views=stats["playCount"],
                posted_at=str(raw["createTime"]),
                platform="tiktok",
                topic=keyword_or_tag.lower().strip(),
            ))

        if not norm_posts:
            err_msg = f"TikTokApi hashtag scraper returned no usable videos for #{clean_tag}"
            logger.warning(err_msg)
            db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
            return 0, err_msg

        inserted = db.upsert_posts(norm_posts)
        db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="success"))
        logger.info(f"Ingested {inserted} TikTok videos (TikTokApi) for topic #{clean_tag}")
        return inserted, None

    except Exception as exc:
        err_msg = f"TikTokApi topic #{clean_tag} scrape error: {str(exc)}"
        logger.warning(err_msg)
        db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg))
        return 0, err_msg



def _scrape_tiktok_topic_html(
    db: Database,
    keyword_or_tag: str,
    max_posts: int,
) -> Tuple[int, Optional[str]]:
    """Scrapes TikTok content by tag via free raw HTML parsing (fragile; blockable/captcha-prone)."""
    clean_tag = re.sub(r"[^a-zA-Z0-9_]", "", keyword_or_tag).lower()
    logger.info(f"Starting TikTok topic scrape (HTML) for #{clean_tag} (limit={max_posts})")

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


def scrape_tiktok_topic(
    db: Database,
    keyword_or_tag: str,
    max_posts: int = 25,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes TikTok content by tag / topic keyword.
    Dispatch order:
      1. Apify (clockworks/tiktok-scraper) when APIFY_API_TOKEN is configured — most reliable,
         attributes each video to its real author.
      2. TikTokApi/Playwright (free, self-hosted headless Chromium) when installed — also
         attributes each video to its real author.
      3. Raw HTML parsing (free, no extra dependency, but fragile and lumps posts under a
         single virtual `tag_<hashtag>` account).
    """
    if is_apify_configured():
        return _scrape_tiktok_topic_apify(db, keyword_or_tag, max_posts)
    if is_tiktokapi_available():
        return _scrape_tiktok_topic_playwright(db, keyword_or_tag, max_posts)
    return _scrape_tiktok_topic_html(db, keyword_or_tag, max_posts)


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

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
import instaloader

from ..models import Account, Post, ScrapeLog, Topic, TopicScrape
from ..ingest import ingest_scraped_batch
from .bright_data_client import (
    INSTAGRAM_POSTS_DATASET_ID,
    INSTAGRAM_REELS_DATASET_ID,
    TIKTOK_POSTS_DATASET_ID,
    is_bright_data_configured,
    run_dataset,
    search_instagram_urls,
)
from .instagram import _bright_data_item_to_raw_post as _bright_data_instagram_post
from .instagram import create_instaloader_instance
from .tiktok import (
    DEFAULT_USER_AGENT,
    _bright_data_item_to_raw_post as _bright_data_tiktok_post,
    _extract_sigi_or_hydration_data,
)
from .tiktokapi_client import (
    _tiktokapi_item_author_username,
    _tiktokapi_item_to_raw_post,
    fetch_hashtag_videos,
    is_tiktokapi_available,
)


# Indonesian legal/corporate keyword expansion map.
# Maps a user-facing topic keyword to realistic hashtags and search terms
# so multi-word queries ("pendirian PT") are not collapsed into a single
# alphanumeric slug that returns zero results.
TOPIC_EXPANSION: Dict[str, List[str]] = {
    "pendirian pt": ["pendirianpt", "ptperorangan", "legalkonsultan", "badanhukum"],
    "izin usaha": ["izinusaha", "oss", "nib", "legalitasusaha"],
    "konsultasi pajak": ["konsultasipajak", "pajakperusahaan", "taxplanning", "konsultasiwajibpajak"],
    "merek dagang": ["merekdagang", "hki", "trademark", "daftarmerek"],
    "badan usaha": ["badanusaha", "ptcitizen", "yayasan", "koperasi"],
    "perizinan": ["perizinan", "pengurusanizin", "legalops", "sertifikasi"],
}


def expand_topic_queries(keyword: str) -> List[str]:
    """
    Expands a topic keyword into realistic hashtags and search terms.
    Returns a list including the original keyword plus mapped variants.
    """
    clean = keyword.strip().lower()
    if clean in TOPIC_EXPANSION:
        return [clean] + TOPIC_EXPANSION[clean]
    # Fallback: treat the keyword itself as a tag and add a generic variant.
    slug = re.sub(r"[^a-zA-Z0-9_]", "", clean)
    if slug:
        return [slug, clean]
    return [clean]


logger = logging.getLogger("scrapers.keyword")


def _get_or_create_account(db: Database, platform: str, username: str, is_own_brand: bool = False) -> Account:
    """Resolves an existing account or registers a new one for real per-author attribution."""
    clean_user = (username or "").strip().lstrip("@").lower() or "unknown"
    acc = db.get_account_by_username(platform, clean_user)
    if acc:
        return acc
    acc = Account.create(platform=platform, username=clean_user, is_own_brand=is_own_brand)
    return db.upsert_account(acc)

def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _passes_since(value: Optional[str], since: Optional[str]) -> bool:
    if not since:
        return True
    posted = _parse_iso(value)
    cutoff = _parse_iso(since)
    return posted is not None and cutoff is not None and posted >= cutoff


def _canonical_instagram_content_url(value: str) -> Optional[str]:
    parsed = urlparse(value)
    host = parsed.netloc.lower().split(":", 1)[0]
    if host not in {"instagram.com", "www.instagram.com"}:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 2 or parts[0].lower() not in {"p", "reel"}:
        return None
    return f"https://www.instagram.com/{parts[0].lower()}/{parts[1]}/"


def _collect_bright_data_instagram_urls(urls: List[str]) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for content_type, dataset_id in (
        ("p", INSTAGRAM_POSTS_DATASET_ID),
        ("reel", INSTAGRAM_REELS_DATASET_ID),
    ):
        selected = [url for url in urls if f"/{content_type}/" in url]
        for start in range(0, len(selected), 20):
            records.extend(
                run_dataset(
                    dataset_id,
                    [{"url": url} for url in selected[start:start + 20]],
                )
            )
    return records


def _scrape_instagram_topic_bright_data(
    db: Database,
    queries: List[str],
    max_posts: int,
    since: Optional[str] = None,
    topic_label: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
    """Discovers broad Instagram topics through SERP, then collects structured records."""
    label = (topic_label or queries[0]).lower().strip()
    candidate_cap = max(max_posts * 2, max_posts)
    urls: List[str] = []
    seen_urls = set()
    try:
        per_query = max(3, candidate_cap // max(1, len(queries)))
        for query in queries:
            search_query = (
                f'(site:instagram.com/p/ OR site:instagram.com/reel/) "{query}"'
            )
            for value in search_instagram_urls(search_query, limit=per_query):
                canonical = _canonical_instagram_content_url(value)
                if canonical and canonical not in seen_urls:
                    seen_urls.add(canonical)
                    urls.append(canonical)
                    if len(urls) >= candidate_cap:
                        break
            if len(urls) >= candidate_cap:
                break
        if not urls:
            raise RuntimeError("Bright Data SERP returned no Instagram post or reel URLs")

        items = _collect_bright_data_instagram_urls(urls)
        norm_posts: List[Post] = []
        seen_ids = set()
        for item in items:
            content_type = "reel" if "/reel/" in str(item.get("url") or "") else "feed"
            raw = _bright_data_instagram_post(item, content_type)
            if not raw or raw["shortcode"] in seen_ids:
                continue
            if not _passes_since(raw.get("date_utc"), since):
                continue
            seen_ids.add(raw["shortcode"])
            username = item.get("user_posted") or item.get("account") or "unknown"
            account = _get_or_create_account(db, "instagram", str(username))
            norm_posts.append(Post.create(
                account_id=account.id,
                platform_post_id=raw["shortcode"],
                caption=raw["caption"],
                media_url=raw["display_url"],
                likes=raw["likes"],
                comments=raw["comments"],
                views=raw["video_view_count"],
                posted_at=raw["date_utc"],
                platform="instagram",
                topic=label,
                content_type=content_type,
            ))
            if len(norm_posts) >= max_posts:
                break
        if not norm_posts:
            raise RuntimeError("Bright Data returned no usable Instagram topic records")
        inserted = db.upsert_posts(norm_posts)
        db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="success"))
        logger.info("Ingested %s Instagram topic posts through Bright Data", inserted)
        return inserted, None
    except Exception as exc:
        err_msg = f"Bright Data Instagram topic scrape error: {str(exc)}"
        logger.warning(err_msg)
        db.insert_scrape_log(
            ScrapeLog.create(platform="instagram", status="failed", error_message=err_msg)
        )
        return 0, err_msg

def _scrape_instagram_hashtag_bright_data(
    db: Database,
    keyword_or_hashtag: str,
    max_posts: int,
    since: Optional[str] = None,
    topic_label: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
    return _scrape_instagram_topic_bright_data(
        db,
        [keyword_or_hashtag],
        max_posts,
        since=since,
        topic_label=topic_label,
    )

def _scrape_instagram_hashtag_instaloader(
    db: Database,
    keyword_or_hashtag: str,
    max_posts: int,
    topic_label: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
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
                topic=(topic_label or keyword_or_hashtag).lower().strip(),
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
    since: Optional[str] = None,
    topic_label: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes Instagram topic content through Bright Data SERP and record APIs.
    Falls back to anonymous Instaloader hashtag scraping when Bright Data fails.
    """
    bright_data_err: Optional[str] = None
    if is_bright_data_configured():
        count, err = _scrape_instagram_hashtag_bright_data(
            db, keyword_or_hashtag, max_posts, since=since, topic_label=topic_label,
        )
        if not err:
            return count, None
        bright_data_err = err
    count, err = _scrape_instagram_hashtag_instaloader(
        db, keyword_or_hashtag, max_posts, topic_label=topic_label,
    )
    if err and bright_data_err:
        err = f"Bright Data: {bright_data_err} | Instaloader: {err}"
    return count, err


def _scrape_tiktok_topic_bright_data(
    db: Database,
    keyword_or_tag: str,
    max_posts: int,
    since: Optional[str] = None,
    topic_label: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
    """Discovers TikTok posts by keyword through Bright Data."""
    label = (topic_label or keyword_or_tag).lower().strip()
    logger.info(
        "Starting TikTok topic discovery (Bright Data) for %r (limit=%s)",
        keyword_or_tag,
        max_posts,
    )
    try:
        items = run_dataset(
            TIKTOK_POSTS_DATASET_ID,
            [{"search_keyword": keyword_or_tag, "num_of_posts": max_posts}],
            query={"type": "discover_new", "discover_by": "keyword"},
        )
        norm_posts: List[Post] = []
        seen_ids = set()
        for item in items:
            raw = _bright_data_tiktok_post(item)
            if not raw or raw["id"] in seen_ids:
                continue
            if not _passes_since(str(raw.get("createTime") or ""), since):
                continue
            seen_ids.add(raw["id"])
            username = item.get("profile_username") or f"topic_{re.sub(r'[^a-zA-Z0-9_]', '', keyword_or_tag)}"
            account = _get_or_create_account(db, "tiktok", str(username))
            stats = raw["stats"]
            norm_posts.append(Post.create(
                account_id=account.id,
                platform_post_id=raw["id"],
                caption=raw["desc"],
                media_url=raw["video"]["downloadAddr"] or raw["video"]["playAddr"],
                likes=stats["diggCount"],
                comments=stats["commentCount"],
                views=stats["playCount"],
                posted_at=str(raw["createTime"]),
                platform="tiktok",
                topic=label,
            ))
            if len(norm_posts) >= max_posts:
                break
        if not norm_posts:
            raise RuntimeError("Bright Data returned no usable TikTok topic records")
        inserted = db.upsert_posts(norm_posts)
        db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="success"))
        logger.info("Ingested %s TikTok topic posts through Bright Data", inserted)
        return inserted, None
    except Exception as exc:
        err_msg = f"Bright Data TikTok topic scrape error: {str(exc)}"
        logger.warning(err_msg)
        db.insert_scrape_log(
            ScrapeLog.create(platform="tiktok", status="failed", error_message=err_msg)
        )
        return 0, err_msg

def _scrape_tiktok_topic_playwright(
    db: Database,
    keyword_or_tag: str,
    max_posts: int,
    topic_label: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes TikTok content by hashtag via TikTokApi (free, self-hosted Playwright/Chromium).
    Attributes each video to its real author account via the videos author.uniqueId.
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
                topic=(topic_label or keyword_or_tag).lower().strip(),
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
    topic_label: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes TikTok content by hashtag via raw HTML parsing.
    Lumps all matched posts under one virtual per-tag account
    (no per-author attribution). Fragile and not recommended.
    """
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
                    topic=(topic_label or keyword_or_tag).lower().strip(),
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
    since: Optional[str] = None,
    topic_label: Optional[str] = None,
) -> Tuple[int, Optional[str]]:
    """
    Scrapes TikTok topic content through Bright Data keyword discovery, with
    TikTokApi/Playwright and raw HTML fallbacks.
    """
    bright_data_err: Optional[str] = None
    if is_bright_data_configured():
        count, err = _scrape_tiktok_topic_bright_data(
            db, keyword_or_tag, max_posts, since=since, topic_label=topic_label,
        )
        if not err:
            return count, None
        bright_data_err = err
    if is_tiktokapi_available():
        count, err = _scrape_tiktok_topic_playwright(
            db, keyword_or_tag, max_posts, topic_label=topic_label,
        )
    else:
        count, err = _scrape_tiktok_topic_html(
            db, keyword_or_tag, max_posts, topic_label=topic_label,
        )
    if err and bright_data_err:
        err = f"Bright Data: {bright_data_err} | Local: {err}"
    return count, err



def scrape_topic_content(
    db: Database,
    keyword: str,
    max_posts_per_platform: int = 25,
    since: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes cross-platform content scraping for a specific topic / keyword,
    expanding the query into realistic variant tags and recording an audit entry.
    """
    clean_kw = keyword.strip().lower()
    # Register topic
    db.upsert_topic(Topic.create(keyword=clean_kw))

    queries = expand_topic_queries(clean_kw)
    logger.info(f"Expanded topic '{clean_kw}' to: {queries}")

    total_ig = 0
    last_ig_err = None
    total_tt = 0
    last_tt_err = None

    per_tag_posts = max(5, max_posts_per_platform // len(queries))

    # Bright Data can deduplicate Instagram SERP URLs across all variants in one run.
    if is_bright_data_configured():
        total_ig, last_ig_err = _scrape_instagram_topic_bright_data(
            db, queries, max_posts_per_platform, since=since, topic_label=clean_kw,
        )
    else:
        for q in queries:
            ig_count, ig_err = scrape_instagram_hashtag(
                db, q, max_posts=per_tag_posts, since=since, topic_label=clean_kw,
            )
            total_ig += ig_count
            if ig_err:
                last_ig_err = ig_err

    for q in queries:
        tt_count, tt_err = scrape_tiktok_topic(
            db, q, max_posts=per_tag_posts, since=since, topic_label=clean_kw,
        )
        total_tt += tt_count
        if tt_err:
            last_tt_err = tt_err

    # Record topic scrape audit logs
    db.record_topic_scrape(TopicScrape.create(
        keyword=clean_kw,
        platform="instagram",
        posts_found=total_ig,
        status="failed" if last_ig_err and total_ig == 0 else "success",
    ))
    db.record_topic_scrape(TopicScrape.create(
        keyword=clean_kw,
        platform="tiktok",
        posts_found=total_tt,
        status="failed" if last_tt_err and total_tt == 0 else "success",
    ))

    return {
        "status": "success",
        "keyword": clean_kw,
        "queries_used": queries,
        "instagram": {"posts_added": total_ig, "error": last_ig_err},
        "tiktok": {"posts_added": total_tt, "error": last_tt_err},
        "total_posts_added": total_ig + total_tt,
    }

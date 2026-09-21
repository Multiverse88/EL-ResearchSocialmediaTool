from __future__ import annotations

from typing import Any, Dict, List, Optional
from .db import Database


# Claude API Tool Specifications
CLAUDE_TOOLS_SPEC = [
    {
        "name": "research_topic",
        "description": "Lakukan riset mendalam performa suatu topik/kata kunci konten di Instagram & TikTok (total post, likes rata-rata, views rata-rata, engagement rate, dan postingan paling viral). Gunakan ini jika user menanyakan topik, tren, atau keyword tertentu.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Kata kunci atau topik yang ingin diriset (contoh: 'pendirian PT', 'virtual office', 'pajak UMKM', 'merek')",
                },
                "platform": {
                    "type": "string",
                    "enum": ["instagram", "tiktok"],
                    "description": "Filter platform spesifik (opsional, kosongkan untuk semua platform)",
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "find_viral_content",
        "description": "Cari referensi konten dan postingan paling viral (likes dan views tertinggi) untuk suatu topik/kata kunci tertentu sebagai inspirasi ide konten marketing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Kata kunci atau topik konten",
                },
                "platform": {
                    "type": "string",
                    "enum": ["instagram", "tiktok"],
                    "description": "Filter platform (opsional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Jumlah postingan viral yang dicari (default 5)",
                    "default": 5,
                },
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "compare_topics",
        "description": "Bandingkan performa engagement dan antusiasme audiens antar beberapa topik/kata kunci konten untuk menentukan topik prioritas marketing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Daftar kata kunci atau topik yang ingin dibandingkan (contoh: ['pendirian PT', 'virtual office', 'pajak'])",
                },
            },
            "required": ["keywords"],
        },
    },
    {
        "name": "search_scraped_posts",
        "description": "Cari postingan media sosial berdasarkan kata kunci caption, akun, atau rentang tanggal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "Kata kunci pencarian dalam caption",
                },
                "platform": {
                    "type": "string",
                    "enum": ["instagram", "tiktok"],
                    "description": "Platform sosial media",
                },
                "username": {
                    "type": "string",
                    "description": "Username akun (opsional)",
                },
                "date_from": {
                    "type": "string",
                    "description": "Tanggal awal filter (ISO format)",
                },
                "date_to": {
                    "type": "string",
                    "description": "Tanggal akhir filter (ISO format)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Jumlah maksimal post (default 15)",
                    "default": 15,
                },
            },
        },
    },
    {
        "name": "get_engagement_summary",
        "description": "Dapatkan ringkasan performa akun tertentu (total post, likes, comments, views, dan engagement rate).",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "Username akun target",
                },
                "platform": {
                    "type": "string",
                    "enum": ["instagram", "tiktok"],
                    "description": "Platform sosial media",
                },
            },
            "required": ["username", "platform"],
        },
    },
    {
        "name": "compare_accounts",
        "description": "Bandingkan performa engagement antar beberapa akun sosial media (brand vs kompetitor).",
        "input_schema": {
            "type": "object",
            "properties": {
                "usernames": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Daftar username yang ingin dibandingkan",
                },
                "platform": {
                    "type": "string",
                    "enum": ["instagram", "tiktok"],
                    "description": "Filter platform (opsional)",
                },
            },
            "required": ["usernames"],
        },
    },
]


def research_topic(
    db: Database,
    keyword: str,
    platform: Optional[str] = None,
) -> Dict[str, Any]:
    """Riset komprehensif performa suatu topik/kata kunci konten."""
    return db.get_topic_summary(keyword=keyword, platform=platform)


def find_viral_content(
    db: Database,
    keyword: str,
    platform: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    """Mencari postingan dengan engagement/likes tertinggi pada topik tertentu."""
    posts = db.query_posts(
        topic=keyword,
        platform=platform,
        order_by="likes",
        limit=limit,
    )
    return {
        "status": "success",
        "topic": keyword,
        "count": len(posts),
        "viral_posts": [
            {
                "id": p["id"],
                "platform": p["platform"],
                "username": p["username"],
                "content_type": p["content_type"],
                "caption": p["caption"],
                "likes": p["likes"],
                "comments": p["comments"],
                "views": p["views"],
                "posted_at": p["posted_at"],
            }
            for p in posts
        ],
    }


def compare_topics_tool(
    db: Database,
    keywords: List[str],
) -> Dict[str, Any]:
    """Bandingkan performa beberapa topik/kata kunci konten."""
    return db.compare_topics(keywords=keywords)


def search_scraped_posts(
    db: Database,
    platform: Optional[str] = None,
    username: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 15,
    offset: int = 0,
) -> Dict[str, Any]:
    """Cari post sesuai kriteria filter."""
    posts = db.query_posts(
        platform=platform,
        username=username,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return {
        "status": "success",
        "query": {
            "platform": platform,
            "username": username,
            "keyword": keyword,
            "date_from": date_from,
            "date_to": date_to,
            "limit": limit,
            "offset": offset,
        },
        "count": len(posts),
        "posts": posts,
    }


def get_engagement_summary(
    db: Database,
    username: str,
    platform: str,
) -> Dict[str, Any]:
    """Ringkasan engagement untuk satu akun."""
    account = db.get_account_by_username(platform, username)
    if not account:
        return {
            "status": "error",
            "message": f"Account '{username}' on platform '{platform}' not found.",
        }

    summary = db.get_account_summary(account.id)
    if not summary:
        return {
            "status": "success",
            "account": account.to_dict(),
            "summary": {
                "total_posts": 0,
                "total_likes": 0,
                "avg_likes": 0.0,
                "total_comments": 0,
                "avg_comments": 0.0,
                "total_views": 0,
                "avg_views": 0.0,
                "engagement_rate": None,
                "engagements_per_post": 0.0,
            },
        }

    total_posts = summary["total_posts"]
    total_likes = summary["total_likes"]
    total_comments = summary["total_comments"]
    total_views = summary["total_views"]

    # A true engagement rate (%) requires a views denominator. Without views, expose
    # the per-post interaction count as `engagements_per_post` instead — it is a real
    # number, but not a percentage, so it must never be rendered with a "%" suffix.
    if total_views > 0:
        engagement_rate = round(((total_likes + total_comments) / total_views) * 100, 2)
    else:
        engagement_rate = None
    engagements_per_post = round((total_likes + total_comments) / total_posts, 2) if total_posts > 0 else 0.0

    top_posts = db.get_top_posts(account_id=account.id, limit=3)

    return {
        "status": "success",
        "account": account.to_dict(),
        "summary": {
            "total_posts": total_posts,
            "total_likes": total_likes,
            "avg_likes": round(summary["avg_likes"], 2),
            "total_comments": total_comments,
            "avg_comments": round(summary["avg_comments"], 2),
            "total_views": total_views,
            "avg_views": round(summary["avg_views"], 2),
            "engagement_rate": engagement_rate,
            "engagements_per_post": engagements_per_post,
            "date_range": {
                "earliest_post": summary["earliest_post"],
                "latest_post": summary["latest_post"],
            },
            "top_posts_sample": [
                {
                    "id": p["id"],
                    "caption": p["caption"][:80] + ("..." if len(p["caption"]) > 80 else ""),
                    "content_type": p["content_type"],
                    "likes": p["likes"],
                    "comments": p["comments"],
                    "views": p["views"],
                    "posted_at": p["posted_at"],
                }
                for p in top_posts
            ],
        },
    }


def compare_accounts(
    db: Database,
    usernames: List[str],
    platform: Optional[str] = None,
) -> Dict[str, Any]:
    """Bandingkan performa antar akun (mis. brand vs kompetitor)."""
    results: List[Dict[str, Any]] = []
    
    all_accounts = db.list_accounts()
    account_map = {}
    for acc in all_accounts:
        if platform and acc.platform != platform.lower():
            continue
        account_map[acc.username.lower()] = acc

    for u in usernames:
        clean_u = u.lower().strip().lstrip("@")
        acc = account_map.get(clean_u)
        if not acc:
            for p in (["instagram", "tiktok"] if not platform else [platform.lower()]):
                acc = db.get_account_by_username(p, clean_u)
                if acc:
                    break

        if not acc:
            results.append({
                "username": clean_u,
                "found": False,
                "error": "Account not found",
            })
            continue

        sum_data = db.get_account_summary(acc.id)
        if not sum_data:
            results.append({
                "username": acc.username,
                "platform": acc.platform,
                "is_own_brand": acc.is_own_brand,
                "found": True,
                "total_posts": 0,
                "total_likes": 0,
                "avg_likes": 0.0,
                "total_comments": 0,
                "avg_comments": 0.0,
                "total_views": 0,
                "avg_views": 0.0,
                "engagement_rate": None,
                "engagements_per_post": 0.0,
            })
            continue

        total_posts = sum_data["total_posts"]
        total_likes = sum_data["total_likes"]
        total_comments = sum_data["total_comments"]
        total_views = sum_data["total_views"]
        # A true engagement rate (%) requires a views denominator. Without views, expose
        # the per-post interaction count as `engagements_per_post` instead of mislabeling
        # it as a rate/percentage.
        if total_views > 0:
            rate = round(((total_likes + total_comments) / total_views) * 100, 2)
        else:
            rate = None
        per_post = round((total_likes + total_comments) / total_posts, 2) if total_posts > 0 else 0.0

        results.append({
            "username": acc.username,
            "platform": acc.platform,
            "is_own_brand": acc.is_own_brand,
            "found": True,
            "total_posts": total_posts,
            "total_likes": total_likes,
            "avg_likes": round(sum_data["avg_likes"], 2),
            "total_comments": total_comments,
            "avg_comments": round(sum_data["avg_comments"], 2),
            "total_views": total_views,
            "avg_views": round(sum_data["avg_views"], 2),
            "engagement_rate": rate,
            "engagements_per_post": per_post,
        })

    found_results = [r for r in results if r.get("found")]
    ranked = sorted(found_results, key=lambda x: x.get("avg_likes", 0), reverse=True)
    for idx, r in enumerate(ranked):
        r["rank"] = idx + 1

    return {
        "status": "success",
        "compared_count": len(found_results),
        "accounts": results,
        "leaderboard_by_avg_likes": [
            {
                "rank": r["rank"],
                "username": r["username"],
                "platform": r["platform"],
                "is_own_brand": r["is_own_brand"],
                "avg_likes": r["avg_likes"],
                "engagement_rate": r["engagement_rate"],
                "engagements_per_post": r["engagements_per_post"],
            }
            for r in ranked
        ],
    }


def execute_claude_tool(db: Database, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatcher for Claude tool-use tool calls."""
    if tool_name == "research_topic":
        return research_topic(
            db=db,
            keyword=tool_input.get("keyword", ""),
            platform=tool_input.get("platform"),
        )
    elif tool_name == "find_viral_content":
        return find_viral_content(
            db=db,
            keyword=tool_input.get("keyword", ""),
            platform=tool_input.get("platform"),
            limit=tool_input.get("limit", 5),
        )
    elif tool_name == "compare_topics":
        return compare_topics_tool(
            db=db,
            keywords=tool_input.get("keywords", []),
        )
    elif tool_name == "search_scraped_posts":
        return search_scraped_posts(
            db=db,
            platform=tool_input.get("platform"),
            username=tool_input.get("username"),
            keyword=tool_input.get("keyword"),
            date_from=tool_input.get("date_from"),
            date_to=tool_input.get("date_to"),
            limit=tool_input.get("limit", 15),
            offset=tool_input.get("offset", 0),
        )
    elif tool_name == "get_engagement_summary":
        return get_engagement_summary(
            db=db,
            username=tool_input.get("username", ""),
            platform=tool_input.get("platform", ""),
        )
    elif tool_name == "compare_accounts":
        return compare_accounts(
            db=db,
            usernames=tool_input.get("usernames", []),
            platform=tool_input.get("platform"),
        )
    else:
        return {
            "status": "error",
            "message": f"Unknown tool: {tool_name}",
        }

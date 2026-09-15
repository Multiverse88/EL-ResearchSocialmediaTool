from __future__ import annotations

from typing import Any, Dict, List, Optional
from .db import Database


# Claude API Tool Specifications
CLAUDE_TOOLS_SPEC = [
    {
        "name": "search_scraped_posts",
        "description": "Cari post sosial media (Instagram/TikTok) berdasarkan kriteria platform, username, kata kunci caption, dan rentang tanggal.",
        "input_schema": {
            "type": "object",
            "properties": {
                "platform": {
                    "type": "string",
                    "enum": ["instagram", "tiktok"],
                    "description": "Platform sosial media",
                },
                "username": {
                    "type": "string",
                    "description": "Username akun yang ingin dicari post-nya",
                },
                "keyword": {
                    "type": "string",
                    "description": "Kata kunci pencarian dalam caption",
                },
                "date_from": {
                    "type": "string",
                    "description": "Tanggal awal filter (ISO format, mis. 2026-01-01)",
                },
                "date_to": {
                    "type": "string",
                    "description": "Tanggal akhir filter (ISO format, mis. 2026-01-31)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Jumlah maksimal post yang dikembalikan (default 20)",
                    "default": 20,
                },
            },
        },
    },
    {
        "name": "get_engagement_summary",
        "description": "Dapatkan ringkasan statistik performa dan engagement suatu akun sosial media (total post, likes, comments, views, rata-rata, engagement rate).",
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
        "description": "Bandingkan performa engagement antar beberapa akun sosial media (misal brand sendiri vs kompetitor).",
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


def search_scraped_posts(
    db: Database,
    platform: Optional[str] = None,
    username: Optional[str] = None,
    keyword: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 20,
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
                "engagement_rate": 0.0,
            },
        }

    total_posts = summary["total_posts"]
    total_likes = summary["total_likes"]
    total_comments = summary["total_comments"]
    total_views = summary["total_views"]

    # Engagement Rate calculation
    # If views are available (video/TikTok), rate = (likes + comments) / views * 100
    # Else rate per post = (likes + comments) / total_posts
    if total_views > 0:
        engagement_rate = round(((total_likes + total_comments) / total_views) * 100, 2)
    elif total_posts > 0:
        engagement_rate = round(((total_likes + total_comments) / total_posts), 2)
    else:
        engagement_rate = 0.0

    # Top posts by likes
    top_posts = db.query_posts(account_id=account.id, limit=3)

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
            "date_range": {
                "earliest_post": summary["earliest_post"],
                "latest_post": summary["latest_post"],
            },
            "top_posts_sample": [
                {
                    "id": p["id"],
                    "caption": p["caption"][:80] + ("..." if len(p["caption"]) > 80 else ""),
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
    
    # Collect summaries
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
            # Fallback direct lookup
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
                "engagement_rate": 0.0,
            })
            continue

        total_posts = sum_data["total_posts"]
        total_likes = sum_data["total_likes"]
        total_comments = sum_data["total_comments"]
        total_views = sum_data["total_views"]
        if total_views > 0:
            rate = round(((total_likes + total_comments) / total_views) * 100, 2)
        elif total_posts > 0:
            rate = round(((total_likes + total_comments) / total_posts), 2)
        else:
            rate = 0.0

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
        })

    # Sort results by avg_likes descending for ranking
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
            }
            for r in ranked
        ],
    }


def execute_claude_tool(db: Database, tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """Dispatcher for Claude tool-use tool calls."""
    if tool_name == "search_scraped_posts":
        return search_scraped_posts(
            db=db,
            platform=tool_input.get("platform"),
            username=tool_input.get("username"),
            keyword=tool_input.get("keyword"),
            date_from=tool_input.get("date_from"),
            date_to=tool_input.get("date_to"),
            limit=tool_input.get("limit", 20),
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

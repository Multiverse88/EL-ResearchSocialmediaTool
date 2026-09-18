from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional
from .db import Database
from .models import Account
from .tools import execute_claude_tool, search_scraped_posts, get_engagement_summary, compare_accounts


def handle_get_accounts(db: Database) -> Dict[str, Any]:
    """GET /accounts - List akun yang dimonitor."""
    accounts = db.list_accounts()
    return {
        "status": "success",
        "count": len(accounts),
        "data": [acc.to_dict() for acc in accounts],
    }


def handle_post_accounts(db: Database, payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST /accounts - Tambah akun baru untuk discrape."""
    platform = payload.get("platform", "").strip().lower()
    username = payload.get("username", "").strip()
    is_own_brand = bool(payload.get("is_own_brand", False))

    if not platform or not username:
        return {"status": "error", "message": "platform and username are required"}
    if platform not in ("instagram", "tiktok", "threads"):
        return {"status": "error", "message": "platform must be 'instagram', 'tiktok', or 'threads'"}

    account = Account.create(
        platform=platform,
        username=username,
        is_own_brand=is_own_brand,
    )
    saved = db.upsert_account(account)
    return {
        "status": "success",
        "message": "Account registered successfully",
        "data": saved.to_dict(),
    }


def handle_get_posts(db: Database, params: Dict[str, Any]) -> Dict[str, Any]:
    """GET /posts?account_id=&from=&to=&limit=&offset="""
    account_id = params.get("account_id")
    date_from = params.get("from") or params.get("date_from")
    date_to = params.get("to") or params.get("date_to")
    keyword = params.get("keyword")
    limit = int(params.get("limit", 50))
    offset = int(params.get("offset", 0))

    posts = db.query_posts(
        account_id=account_id,
        keyword=keyword,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        offset=offset,
    )
    return {
        "status": "success",
        "count": len(posts),
        "data": posts,
    }


def handle_get_posts_summary(db: Database, params: Dict[str, Any]) -> Dict[str, Any]:
    """GET /posts/summary?account_id="""
    account_id = params.get("account_id")
    if not account_id:
        return {"status": "error", "message": "account_id parameter is required"}

    account = db.get_account(account_id)
    if not account:
        return {"status": "error", "message": "Account not found"}

    summary = db.get_account_summary(account_id)
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
            },
        }

    return {
        "status": "success",
        "account": account.to_dict(),
        "summary": summary,
    }


def handle_chat_message(
    db: Database,
    message: str,
    llm_tool_runner: Optional[Callable[[str, List[Dict[str, Any]]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    POST /chat - Endpoint utama chat panel.
    Terima pesan user, tentukan / panggil Claude API dengan tool use, return jawaban.
    """
    if not message or not message.strip():
        return {"status": "error", "message": "Chat message cannot be empty"}

    # If an external or mock LLM runner is supplied, invoke it
    if llm_tool_runner is not None:
        tool_call = llm_tool_runner(message, db.list_accounts())
        tool_name = tool_call.get("tool_name")
        tool_input = tool_call.get("tool_input", {})
        tool_result = execute_claude_tool(db, tool_name, tool_input)
        return {
            "status": "success",
            "user_query": message,
            "tool_used": tool_name,
            "tool_input": tool_input,
            "tool_result": tool_result,
            "reply": f"Berdasarkan data {tool_name}, ditemukan hasil untuk query Anda.",
        }

    # Deterministic intent parser for chat panel
    msg_lower = message.lower()
    
    # Check for compare accounts intent
    if "banding" in msg_lower or "vs" in msg_lower or "compare" in msg_lower:
        accounts = db.list_accounts()
        # Find usernames mentioned in query
        matched_users = [acc.username for acc in accounts if acc.username.lower() in msg_lower]
        if not matched_users:
            matched_users = [acc.username for acc in accounts[:3]]
        tool_result = compare_accounts(db, usernames=matched_users)
        return {
            "status": "success",
            "user_query": message,
            "tool_used": "compare_accounts",
            "tool_result": tool_result,
            "reply": f"Berikut perbandingan performa akun: {', '.join(matched_users)}.",
        }

    # Check for engagement summary intent
    elif any(term in msg_lower for term in ["engagement", "rata-rata", "average", "summary", "performa"]):
        accounts = db.list_accounts()
        matched_account = None
        for acc in accounts:
            if acc.username.lower() in msg_lower:
                matched_account = acc
                break
        if not matched_account and accounts:
            matched_account = accounts[0]

        if matched_account:
            tool_result = get_engagement_summary(db, matched_account.username, matched_account.platform)
            return {
                "status": "success",
                "user_query": message,
                "tool_used": "get_engagement_summary",
                "tool_result": tool_result,
                "reply": f"Ringkasan engagement untuk @{matched_account.username} ({matched_account.platform}): "
                         f"Rata-rata likes {tool_result['summary']['avg_likes']}, engagement rate {tool_result['summary']['engagement_rate']}%.",
            }

    # Default to search_scraped_posts
    words = [w for w in msg_lower.split() if len(w) > 3 and w not in ["cari", "post", "tentang", "yang", "akun", "bulan", "ini"]]
    keyword = words[0] if words else None
    tool_result = search_scraped_posts(db, keyword=keyword, limit=10)
    return {
        "status": "success",
        "user_query": message,
        "tool_used": "search_scraped_posts",
        "tool_result": tool_result,
        "reply": f"Ditemukan {tool_result['count']} post yang sesuai dengan kriteria.",
    }

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from .db import Database


def parse_timeframe_days(timeframe: Optional[str]) -> int:
    """Converts user/API timeframe keyword into number of days."""
    if not timeframe:
        return 30
    tf = timeframe.strip().lower()
    if tf in ("daily", "1d", "day", "hari_ini", "today"):
        return 3
    if tf in ("weekly", "7d", "week", "minggu_ini"):
        return 7
    if tf in ("monthly", "30d", "month", "bulan_ini"):
        return 30
    if tf in ("quarterly", "90d"):
        return 90
    if tf in ("all", "all_time", "semua"):
        return 3650
    try:
        val = int(timeframe)
        return max(1, min(3650, val))
    except ValueError:
        return 30


def _get_cutoff_iso(days: int) -> str:
    """Returns ISO cutoff timestamp (UTC) for `days` days ago."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return cutoff.isoformat()


def extract_hook_preview(caption: Optional[str], max_chars: int = 120) -> str:
    """Extracts the first punchy line or opening hook of a caption."""
    if not caption:
        return ""
    clean = caption.strip()
    first_line = clean.split("\n")[0].strip()
    if len(first_line) > max_chars:
        return first_line[:max_chars].rstrip() + "..."
    return first_line


def get_analytics_overview(db: Database, timeframe_days: int = 30) -> Dict[str, Any]:
    """
    Returns high-level KPI overview across all monitored posts in the timeframe:
    Total posts, total views, total likes, average engagement rate, and platform breakdown.
    """
    cutoff_iso = _get_cutoff_iso(timeframe_days)

    # 1. Overall totals
    cursor = db.conn.execute(
        """
        SELECT
            COUNT(*),
            COALESCE(SUM(views), 0),
            COALESCE(SUM(likes), 0),
            COALESCE(SUM(comments), 0),
            ROUND(CASE WHEN SUM(views) > 0 THEN ((SUM(likes) + SUM(comments)) * 100.0 / SUM(views)) ELSE (SUM(likes) + SUM(comments)) * 1.0 / MAX(COUNT(*), 1) END, 2)
        FROM posts
        WHERE posted_at >= ?
        """,
        (cutoff_iso,),
    )
    row = cursor.fetchone() or (0, 0, 0, 0, 0.0)
    total_posts, total_views, total_likes, total_comments, avg_er = row

    # 2. Brand vs Competitor breakdown
    cursor = db.conn.execute(
        """
        SELECT
            a.is_own_brand,
            COUNT(*),
            COALESCE(SUM(p.views), 0),
            ROUND(CASE WHEN SUM(p.views) > 0 THEN ((SUM(p.likes) + SUM(p.comments)) * 100.0 / SUM(p.views)) ELSE (SUM(p.likes) + SUM(p.comments)) * 1.0 / MAX(COUNT(*), 1) END, 2)
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        WHERE p.posted_at >= ?
        GROUP BY a.is_own_brand
        """,
        (cutoff_iso,),
    )
    brand_posts = 0
    competitor_posts = 0
    brand_views = 0
    competitor_views = 0
    brand_er = 0.0
    competitor_er = 0.0

    for is_own, count, views, er in cursor.fetchall():
        if is_own == 1:
            brand_posts = count
            brand_views = views
            brand_er = er
        else:
            competitor_posts = count
            competitor_views = views
            competitor_er = er

    # 3. Platform breakdown
    cursor = db.conn.execute(
        """
        SELECT
            platform,
            COUNT(*),
            COALESCE(SUM(views), 0),
            COALESCE(SUM(likes), 0),
            ROUND(CASE WHEN SUM(views) > 0 THEN ((SUM(likes) + SUM(comments)) * 100.0 / SUM(views)) ELSE (SUM(likes) + SUM(comments)) * 1.0 / MAX(COUNT(*), 1) END, 2)
        FROM posts
        WHERE posted_at >= ? AND platform != ''
        GROUP BY platform
        ORDER BY count(*) DESC
        """,
        (cutoff_iso,),
    )
    platforms_breakdown = {}
    for plat, count, views, likes, er in cursor.fetchall():
        platforms_breakdown[plat] = {
            "posts_count": count,
            "total_views": views,
            "total_likes": likes,
            "avg_engagement_rate": er,
        }

    return {
        "timeframe_days": timeframe_days,
        "cutoff_date": cutoff_iso[:10],
        "kpis": {
            "total_posts": total_posts,
            "total_views": total_views,
            "total_likes": total_likes,
            "total_comments": total_comments,
            "avg_engagement_rate": avg_er,
        },
        "brand_vs_competitor": {
            "brand": {
                "posts_count": brand_posts,
                "total_views": brand_views,
                "avg_engagement_rate": brand_er,
            },
            "competitor": {
                "posts_count": competitor_posts,
                "total_views": competitor_views,
                "avg_engagement_rate": competitor_er,
            },
        },
        "platforms": platforms_breakdown,
    }


def get_timeseries_trends(
    db: Database,
    timeframe: str = "daily",
    platform: Optional[str] = None,
    is_own_brand: Optional[int] = None,
    days: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    Returns time-series daily aggregation for charts.
    Each item contains date (YYYY-MM-DD), views, likes, post_count, and avg_er.
    """
    effective_days = days if days is not None else parse_timeframe_days(timeframe)
    cutoff_iso = _get_cutoff_iso(effective_days)

    norm_plat = platform.strip().lower() if platform and platform.lower() != "all" else None

    sql = """
        SELECT
            SUBSTR(p.posted_at, 1, 10) as post_date,
            COUNT(*) as post_count,
            COALESCE(SUM(p.views), 0) as total_views,
            COALESCE(SUM(p.likes), 0) as total_likes,
            COALESCE(SUM(p.comments), 0) as total_comments,
            ROUND(CASE WHEN SUM(p.views) > 0 THEN ((SUM(p.likes) + SUM(p.comments)) * 100.0 / SUM(p.views)) ELSE (SUM(p.likes) + SUM(p.comments)) * 1.0 / MAX(COUNT(*), 1) END, 2) as avg_engagement_rate
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        WHERE p.posted_at >= ?
          AND (? IS NULL OR p.platform = ?)
          AND (? IS NULL OR a.is_own_brand = ?)
        GROUP BY SUBSTR(p.posted_at, 1, 10)
        ORDER BY post_date ASC
    """
    params = (cutoff_iso, norm_plat, norm_plat, is_own_brand, is_own_brand)
    cursor = db.conn.execute(sql, params)

    results = []
    for row in cursor.fetchall():
        results.append({
            "date": row[0],
            "post_count": row[1],
            "total_views": row[2],
            "total_likes": row[3],
            "total_comments": row[4],
            "avg_engagement_rate": row[5],
        })
    return results


def get_viral_leaderboard(
    db: Database,
    limit: int = 10,
    days: int = 30,
    is_own_brand: Optional[int] = None,
    platform: Optional[str] = None,
    topic: Optional[str] = None,
    brand: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Returns top posts sorted by views (or likes if views null), with author info,
    engagement metrics, permalink, and opening caption hook preview.
    """
    cutoff_iso = _get_cutoff_iso(days)
    norm_plat = platform.strip().lower() if platform and platform.lower() != "all" else None
    norm_topic = topic.strip().lower() if topic and topic.lower() != "all" else None
    norm_brand = brand.strip().lower() if brand and brand.lower() != "all" else None
    brand_aliases = {
        "legal": "easylegal",
        "tax": "easytax",
        "pajak": "easytax",
        "office": "easyoffice",
    }
    norm_brand = brand_aliases.get(norm_brand, norm_brand)
    brand_pattern = f"%{norm_brand}%" if norm_brand in {"easylegal", "easytax", "easyoffice"} else None

    sql = """
        SELECT
            p.id,
            p.platform_post_id,
            p.platform,
            a.username,
            a.is_own_brand,
            p.topic,
            p.caption,
            p.likes,
            p.comments,
            p.views,
            ROUND(CASE WHEN p.views > 0 THEN ((p.likes + p.comments) * 100.0 / p.views) ELSE (p.likes + p.comments) * 1.0 END, 2) as engagement_rate,
            p.posted_at,
            p.post_url
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        WHERE p.posted_at >= ?
          AND (? IS NULL OR p.platform = ?)
          AND (? IS NULL OR a.is_own_brand = ?)
          AND (? IS NULL OR LOWER(p.topic) = ?)
          AND (? IS NULL OR LOWER(a.username) LIKE ?)
        ORDER BY COALESCE(p.views, 0) DESC, p.likes DESC
        LIMIT ?
    """
    params = (
        cutoff_iso,
        norm_plat,
        norm_plat,
        is_own_brand,
        is_own_brand,
        norm_topic,
        norm_topic,
        brand_pattern,
        brand_pattern,
        limit,
    )
    cursor = db.conn.execute(sql, params)

    leaderboard = []
    for r in cursor.fetchall():
        caption = r[6] or ""
        hook = extract_hook_preview(caption)
        leaderboard.append({
            "id": r[0],
            "platform_post_id": r[1],
            "platform": r[2],
            "username": r[3],
            "is_own_brand": bool(r[4]),
            "topic": r[5] or "Umum",
            "caption": caption[:200] + ("..." if len(caption) > 200 else ""),
            "hook": hook,
            "likes": r[7],
            "comments": r[8],
            "views": r[9] if r[9] is not None else 0,
            "engagement_rate": r[10],
            "posted_at": r[11],
            "permalink": r[12] or f"https://www.{r[2]}.com",
        })
    return leaderboard


def get_competitor_comparison(
    db: Database,
    days: int = 30,
    topic: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Provides side-by-side performance comparison between EasyCorp brand accounts
    and competitor accounts, optionally filtered by topic keyword.
    """
    cutoff_iso = _get_cutoff_iso(days)
    norm_topic = topic.strip().lower() if topic and topic.lower() != "all" else None

    # Summary by brand vs competitor
    sql_summary = """
        SELECT
            a.is_own_brand,
            COUNT(*),
            COALESCE(SUM(p.views), 0),
            ROUND(COALESCE(AVG(p.views), 0), 1),
            ROUND(COALESCE(AVG(p.likes), 0), 1),
            ROUND(CASE WHEN SUM(p.views) > 0 THEN ((SUM(p.likes) + SUM(p.comments)) * 100.0 / SUM(p.views)) ELSE (SUM(p.likes) + SUM(p.comments)) * 1.0 / MAX(COUNT(*), 1) END, 2)
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        WHERE p.posted_at >= ?
          AND (? IS NULL OR LOWER(p.topic) = ?)
        GROUP BY a.is_own_brand
    """
    cursor = db.conn.execute(sql_summary, (cutoff_iso, norm_topic, norm_topic))

    brand_metrics = {"posts_count": 0, "total_views": 0, "avg_views": 0.0, "avg_likes": 0.0, "avg_er": 0.0}
    comp_metrics = {"posts_count": 0, "total_views": 0, "avg_views": 0.0, "avg_likes": 0.0, "avg_er": 0.0}

    for row in cursor.fetchall():
        data = {
            "posts_count": row[1],
            "total_views": row[2],
            "avg_views": row[3],
            "avg_likes": row[4],
            "avg_er": row[5],
        }
        if row[0] == 1:
            brand_metrics = data
        else:
            comp_metrics = data

    # Topic breakdown — restricted to a curated set of niche keywords (not every raw
    # value ever stored in posts.topic, which also picks up one-off test/exploration
    # queries like "coba"). Matches BOTH the explicit topic tag AND caption content
    # (same pattern as Database.get_topic_summary/query_posts), because EasyCorp's own
    # posts come from profile scraping and are never topic-tagged — caption matching is
    # what lets the brand side of this chart show real data instead of always 0.
    #
    # Deliberately NOT filtered by the `days` window (unlike brand_performance/
    # competitor_performance above): niche-topic research is run occasionally, not
    # continuously, so almost all matching posts predate a rolling 30-day window —
    # applying it here made every topic look empty even when real historical data
    # existed (confirmed: 0 posts in any topic at days=30 vs 4-24 posts per topic
    # at days=3650). This widget answers "who has ever posted more about this niche",
    # not "who posted about it this month".
    NICHE_KEYWORDS = [
        ("pendirian pt", "Pendirian PT"),
        ("pajak", "Konsultasi Pajak"),
        ("virtual office", "Virtual Office"),
    ]
    topic_map: Dict[str, Dict[str, Any]] = {}
    for keyword, label in NICHE_KEYWORDS:
        like_kw = f"%{keyword}%"
        sql_topic = """
            SELECT a.is_own_brand, COUNT(*), COALESCE(SUM(p.views), 0),
                   COALESCE(SUM(p.likes), 0) + COALESCE(SUM(p.comments), 0)
            FROM posts p
            JOIN accounts a ON p.account_id = a.id
            WHERE (LOWER(p.topic) LIKE ? OR LOWER(p.caption) LIKE ?)
            GROUP BY a.is_own_brand
        """
        cursor = db.conn.execute(sql_topic, (like_kw, like_kw))
        entry = {
            "brand_posts": 0, "brand_views": 0, "brand_engagement": 0,
            "competitor_posts": 0, "competitor_views": 0, "competitor_engagement": 0,
        }
        for is_own, count, views, engagement in cursor.fetchall():
            if is_own == 1:
                entry["brand_posts"] = count
                entry["brand_views"] = views
                entry["brand_engagement"] = engagement
            else:
                entry["competitor_posts"] = count
                entry["competitor_views"] = views
                entry["competitor_engagement"] = engagement
        topic_map[label] = entry

    # Top viral hooks for brand vs competitor
    brand_hooks = [
        item["hook"] for item in get_viral_leaderboard(db, limit=3, days=days, is_own_brand=1, topic=norm_topic)
        if item["hook"]
    ]
    comp_hooks = [
        item["hook"] for item in get_viral_leaderboard(db, limit=3, days=days, is_own_brand=0, topic=norm_topic)
        if item["hook"]
    ]

    return {
        "timeframe_days": days,
        "filter_topic": topic or "all",
        "brand_performance": brand_metrics,
        "competitor_performance": comp_metrics,
        "sample_viral_hooks": {
            "brand": brand_hooks,
            "competitor": comp_hooks,
        },
        "topics_distribution": topic_map,
    }


def get_brand_overview_stats(
    db: Database,
    brand: Optional[str] = "all",
    platform: Optional[str] = "all",
    limit: int = 50,
) -> Dict[str, Any]:
    """
    Returns consolidated social media statistics for the 3 EasyCorp brand accounts
    (EasyLegal, EasyTax, EasyOffice) across Instagram, Threads, and TikTok.
    """
    norm_brand = (brand or "all").strip().lower()
    norm_plat = (platform or "all").strip().lower()

    brand_filter_sql = ""
    params: List[Any] = []

    if norm_brand in ("easylegal", "legal"):
        brand_filter_sql = " AND (a.username LIKE '%easylegal%' OR a.username LIKE '%legal%')"
    elif norm_brand in ("easytax", "tax", "pajak"):
        brand_filter_sql = " AND (a.username LIKE '%easytax%' OR a.username LIKE '%tax%')"
    elif norm_brand in ("easyoffice", "office"):
        brand_filter_sql = " AND (a.username LIKE '%easyoffice%' OR a.username LIKE '%office%')"

    plat_filter_sql = ""
    if norm_plat != "all":
        plat_filter_sql = " AND p.platform = ?"
        params.append(norm_plat)

    # 1. Overall Brand KPIs
    sql_kpi = f"""
        SELECT
            COUNT(*),
            COALESCE(SUM(p.views), 0),
            ROUND(COALESCE(AVG(p.views), 0), 1),
            COALESCE(SUM(p.likes), 0),
            ROUND(COALESCE(AVG(p.likes), 0), 1),
            COALESCE(SUM(p.comments), 0),
            ROUND(CASE WHEN SUM(p.views) > 0 THEN ((SUM(p.likes) + SUM(p.comments)) * 100.0 / SUM(p.views)) ELSE (SUM(p.likes) + SUM(p.comments)) * 1.0 / MAX(COUNT(*), 1) END, 2) as avg_er
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        WHERE a.is_own_brand = 1 {brand_filter_sql} {plat_filter_sql}
    """
    cursor = db.conn.execute(sql_kpi, params)
    kpi_row = cursor.fetchone() or (0, 0, 0.0, 0, 0.0, 0, 0.0)
    kpis = {
        "total_posts": kpi_row[0],
        "total_views": kpi_row[1],
        "avg_views": kpi_row[2],
        "total_likes": kpi_row[3],
        "avg_likes": kpi_row[4],
        "total_comments": kpi_row[5],
        "avg_engagement_rate": kpi_row[6],
    }

    # 2. Platform Breakdown (Instagram, Threads, TikTok)
    sql_platforms = f"""
        SELECT
            p.platform,
            COUNT(*),
            COALESCE(SUM(p.views), 0),
            COALESCE(SUM(p.likes), 0),
            ROUND(CASE WHEN SUM(p.views) > 0 THEN ((SUM(p.likes) + SUM(p.comments)) * 100.0 / SUM(p.views)) ELSE (SUM(p.likes) + SUM(p.comments)) * 1.0 / MAX(COUNT(*), 1) END, 2)
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        WHERE a.is_own_brand = 1 {brand_filter_sql}
        GROUP BY p.platform
    """
    cursor = db.conn.execute(sql_platforms)
    platforms_breakdown = {
        "instagram": {"posts": 0, "views": 0, "likes": 0, "avg_er": 0.0, "account_count": 0, "usernames": []},
        "threads": {"posts": 0, "views": 0, "likes": 0, "avg_er": 0.0, "account_count": 0, "usernames": []},
        "tiktok": {"posts": 0, "views": 0, "likes": 0, "avg_er": 0.0, "account_count": 0, "usernames": []},
    }
    for plat, count, views, likes, er in cursor.fetchall():
        if plat in platforms_breakdown:
            platforms_breakdown[plat]["posts"] = count
            platforms_breakdown[plat]["views"] = views
            platforms_breakdown[plat]["likes"] = likes
            platforms_breakdown[plat]["avg_er"] = er

    sql_account_counts = f"""
        SELECT a.platform, COUNT(*), GROUP_CONCAT(a.username, ',')
        FROM accounts a
        WHERE a.is_own_brand = 1 {brand_filter_sql}
        GROUP BY a.platform
    """
    for plat, acc_count, usernames in db.conn.execute(sql_account_counts).fetchall():
        if plat in platforms_breakdown:
            platforms_breakdown[plat]["account_count"] = acc_count
            platforms_breakdown[plat]["usernames"] = usernames.split(",") if usernames else []

    # 3. Individual Brand Performance (EasyLegal vs EasyTax vs EasyOffice)
    brand_groups = [
        ("easylegal", "EasyLegal", ["id.easylegal", "easylegal_id", "easylegal_tiktok"]),
        ("easytax", "EasyTax", ["id.easytax", "easytax_id", "easytax_tiktok"]),
        ("easyoffice", "EasyOffice", ["id.easyoffice", "easyoffice_id"]),
    ]
    brands_data = {}
    for b_key, b_name, handles in brand_groups:
        placeholders = ",".join("?" * len(handles))
        sql_b = f"""
            SELECT
                COUNT(*),
                COALESCE(SUM(p.views), 0),
                ROUND(COALESCE(AVG(p.likes), 0), 1),
                ROUND(CASE WHEN SUM(p.views) > 0 THEN ((SUM(p.likes) + SUM(p.comments)) * 100.0 / SUM(p.views)) ELSE (SUM(p.likes) + SUM(p.comments)) * 1.0 / MAX(COUNT(*), 1) END, 2),
                COALESCE(SUM(p.likes), 0),
                COALESCE(SUM(p.comments), 0)
            FROM posts p
            JOIN accounts a ON p.account_id = a.id
            WHERE a.username IN ({placeholders})
        """
        cursor = db.conn.execute(sql_b, handles)
        brow = cursor.fetchone() or (0, 0, 0.0, 0.0, 0, 0)
        brands_data[b_key] = {
            "name": b_name,
            "total_posts": brow[0],
            "total_views": brow[1],
            "avg_likes": brow[2],
            "avg_er": brow[3],
            "total_likes": brow[4],
            "total_comments": brow[5],
        }

    # 4. Posts List
    sql_posts = f"""
        SELECT
            p.id,
            p.platform_post_id,
            p.platform,
            a.username,
            p.caption,
            p.likes,
            p.comments,
            p.views,
            ROUND(CASE WHEN p.views > 0 THEN ((p.likes + p.comments) * 100.0 / p.views) ELSE (p.likes + p.comments) * 1.0 END, 2) as engagement_rate,
            p.posted_at,
            p.post_url
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        WHERE a.is_own_brand = 1 {brand_filter_sql} {plat_filter_sql}
        ORDER BY COALESCE(p.views, 0) DESC, p.likes DESC, p.posted_at DESC
        LIMIT ?
    """
    post_params = list(params) + [limit]
    cursor = db.conn.execute(sql_posts, post_params)
    posts_list = []
    for r in cursor.fetchall():
        caption = r[4] or ""
        hook = extract_hook_preview(caption)
        posts_list.append({
            "id": r[0],
            "platform_post_id": r[1],
            "platform": r[2],
            "username": r[3],
            "caption": caption,
            "hook": hook,
            "likes": r[5],
            "comments": r[6],
            "views": r[7] if r[7] is not None else 0,
            "engagement_rate": r[8],
            "posted_at": r[9],
            "permalink": r[10] or f"https://www.{r[2]}.com",
        })

    return {
        "selected_brand": norm_brand,
        "selected_platform": norm_plat,
        "kpis": kpis,
        "platforms": platforms_breakdown,
        "brands": brands_data,
        "posts": posts_list,
    }


def _safe_parse_dt(iso_str: Optional[str]) -> Optional[datetime]:
    """Best-effort ISO-8601 parse; returns None (never raises) for malformed/missing values."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def get_content_format_breakdown(db: Database, days: Optional[int] = None) -> Dict[str, Any]:
    """
    Returns post count and average engagement per content format (feed/reel/etc), split
    into brand vs competitor. Not date-windowed by default (own-brand posts are too few
    to split meaningfully across formats within a rolling 30-day window — see the
    niche-topic chart fix for the same lesson).
    """
    where = "WHERE p.content_type != ''"
    params: List[Any] = []
    if days:
        where += " AND p.posted_at >= ?"
        params.append(_get_cutoff_iso(days))
    sql = f"""
        SELECT a.is_own_brand, p.content_type, COUNT(*),
               ROUND(COALESCE(AVG(p.likes), 0), 1),
               ROUND(COALESCE(AVG(p.comments), 0), 1),
               ROUND(COALESCE(AVG(p.views), 0), 1)
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        {where}
        GROUP BY a.is_own_brand, p.content_type
        ORDER BY p.content_type
    """
    cursor = db.conn.execute(sql, params)
    result: Dict[str, Dict[str, Any]] = {"brand": {}, "competitor": {}}
    for is_own, content_type, count, avg_likes, avg_comments, avg_views in cursor.fetchall():
        key = "brand" if is_own == 1 else "competitor"
        label = content_type.capitalize() if content_type else "Lainnya"
        result[key][label] = {
            "post_count": count,
            "avg_likes": avg_likes,
            "avg_comments": avg_comments,
            "avg_views": avg_views,
        }
    return result


def get_posting_cadence(db: Database) -> List[Dict[str, Any]]:
    """Returns posting frequency and recency per own-brand account, so gaps/slowdowns
    in content output are visible instead of buried in raw post lists."""
    sql = """
        SELECT a.id, a.platform, a.username, COUNT(p.id), MAX(p.posted_at), MIN(p.posted_at)
        FROM accounts a
        LEFT JOIN posts p ON p.account_id = a.id
        WHERE a.is_own_brand = 1
        GROUP BY a.id
        ORDER BY a.username ASC, a.platform ASC
    """
    cursor = db.conn.execute(sql)
    now = datetime.now(timezone.utc)
    results = []
    for account_id, platform, username, post_count, last_posted, first_posted in cursor.fetchall():
        last_dt = _safe_parse_dt(last_posted)
        first_dt = _safe_parse_dt(first_posted)
        if not post_count or not last_dt:
            results.append({
                "account_id": account_id, "platform": platform, "username": username,
                "post_count": 0, "posts_per_week": 0.0, "days_since_last_post": None,
                "status": "Belum ada data",
            })
            continue
        days_since_last = (now - last_dt).days
        span_days = max((last_dt - first_dt).days, 1) if first_dt else 1
        posts_per_week = round(post_count / (span_days / 7.0), 1)
        if days_since_last <= 3:
            status = "Aktif"
        elif days_since_last <= 14:
            status = "Mulai Melambat"
        else:
            status = "Perlu Perhatian"
        results.append({
            "account_id": account_id, "platform": platform, "username": username,
            "post_count": post_count, "posts_per_week": posts_per_week,
            "days_since_last_post": days_since_last, "status": status,
        })
    return results


DAY_LABELS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
HOUR_BUCKET_LABELS = ["00-04", "04-08", "08-12", "12-16", "16-20", "20-24"]


def get_best_posting_time(db: Database, is_own_brand: Optional[int] = None) -> List[Dict[str, Any]]:
    """Returns average engagement per (day-of-week, 4-hour bucket), built from posted_at —
    a directional 'when does content land best' signal, not a guarantee given sample size."""
    where = ""
    params: List[Any] = []
    if is_own_brand is not None:
        where = "WHERE a.is_own_brand = ?"
        params.append(is_own_brand)
    sql = f"""
        SELECT p.posted_at, p.likes, p.comments
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        {where}
    """
    cursor = db.conn.execute(sql, params)
    buckets: Dict[Any, List[int]] = {}
    for posted_at, likes, comments in cursor.fetchall():
        dt = _safe_parse_dt(posted_at)
        if not dt:
            continue
        key = (dt.weekday(), dt.hour // 4)
        entry = buckets.setdefault(key, [0, 0])
        entry[0] += (likes or 0) + (comments or 0)
        entry[1] += 1
    result = []
    for dow in range(7):
        for hb in range(6):
            total, count = buckets.get((dow, hb), [0, 0])
            result.append({
                "day": DAY_LABELS[dow],
                "hour_range": HOUR_BUCKET_LABELS[hb],
                "avg_engagement": round(total / count, 1) if count else 0,
                "post_count": count,
            })
    return result


_HASHTAG_RE = re.compile(r"#(\w+)")


def get_hashtag_performance(
    db: Database, min_posts: int = 2, limit: int = 15, is_own_brand: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Ranks hashtags actually used in captions by average engagement. Hashtags used only
    once are dropped (min_posts) — a single lucky/unlucky post isn't a pattern."""
    where = ""
    params: List[Any] = []
    if is_own_brand is not None:
        where = "WHERE a.is_own_brand = ?"
        params.append(is_own_brand)
    sql = f"""
        SELECT p.caption, p.likes, p.comments
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        {where}
    """
    cursor = db.conn.execute(sql, params)
    stats: Dict[str, List[int]] = {}
    for caption, likes, comments in cursor.fetchall():
        if not caption:
            continue
        tags = {t.lower() for t in _HASHTAG_RE.findall(caption)}
        engagement = (likes or 0) + (comments or 0)
        for tag in tags:
            entry = stats.setdefault(tag, [0, 0])
            entry[0] += engagement
            entry[1] += 1
    rows = [
        {"hashtag": f"#{tag}", "post_count": count, "avg_engagement": round(total / count, 1)}
        for tag, (total, count) in stats.items() if count >= min_posts
    ]
    rows.sort(key=lambda r: r["avg_engagement"], reverse=True)
    return rows[:limit]


def get_competitor_leaderboard(db: Database, days: Optional[int] = None, limit: int = 10) -> List[Dict[str, Any]]:
    """Ranks INDIVIDUAL competitor accounts by total engagement, instead of collapsing
    every competitor into one aggregate number — answers 'which competitor', not just
    'competitors in general'."""
    where = "WHERE a.is_own_brand = 0"
    params: List[Any] = []
    if days:
        where += " AND p.posted_at >= ?"
        params.append(_get_cutoff_iso(days))
    sql = f"""
        SELECT a.username, a.platform, COUNT(*),
               COALESCE(SUM(p.likes), 0) + COALESCE(SUM(p.comments), 0),
               ROUND(COALESCE(AVG(p.likes), 0), 1)
        FROM posts p
        JOIN accounts a ON p.account_id = a.id
        {where}
        GROUP BY a.username, a.platform
        ORDER BY (COALESCE(SUM(p.likes), 0) + COALESCE(SUM(p.comments), 0)) DESC
        LIMIT ?
    """
    params.append(limit)
    cursor = db.conn.execute(sql, params)
    return [
        {
            "username": r[0], "platform": r[1], "post_count": r[2],
            "total_engagement": r[3], "avg_likes": r[4],
        }
        for r in cursor.fetchall()
    ]


def get_data_health(db: Database) -> List[Dict[str, Any]]:
    """Per own-brand account: how much of its stored data is actually complete (views
    field populated) and when it last successfully synced — surfaces scraping gaps
    (e.g. Bright Data not returning view counts for a given account) directly, instead
    of only showing up as an unexplained '0' somewhere else in the dashboard."""
    sql = """
        SELECT a.id, a.platform, a.username, COUNT(p.id),
               SUM(CASE WHEN p.views IS NOT NULL THEN 1 ELSE 0 END),
               MAX(p.scraped_at)
        FROM accounts a
        LEFT JOIN posts p ON p.account_id = a.id
        WHERE a.is_own_brand = 1
        GROUP BY a.id
        ORDER BY a.username ASC, a.platform ASC
    """
    cursor = db.conn.execute(sql)
    results = []
    for account_id, platform, username, post_count, views_present, last_scraped in cursor.fetchall():
        post_count = post_count or 0
        completeness = round((views_present / post_count) * 100) if post_count else 0
        results.append({
            "account_id": account_id,
            "platform": platform,
            "username": username,
            "post_count": post_count,
            "views_data_completeness_pct": completeness,
            "last_scraped_at": last_scraped,
        })
    return results

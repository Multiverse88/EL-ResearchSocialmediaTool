#!/usr/bin/env python3
from __future__ import annotations

import random
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from src.models import Account
from src.db import Database
from src.ingest import ingest_scraped_batch
from src.tools import search_scraped_posts, get_engagement_summary, compare_accounts


ACCOUNTS_SEED_DATA = [
    ("instagram", "easylegal_id", True),
    ("tiktok", "easylegal_tiktok", True),
    ("instagram", "easytax_id", True),
    ("tiktok", "easytax_tiktok", True),
    ("instagram", "easyoffice_id", True),
    ("instagram", "legalku_official", False),
    ("tiktok", "legalku_tiktok", False),
    ("instagram", "izinlegal_id", False),
    ("instagram", "notaris_online", False),
    ("tiktok", "konsultan_pajak_id", False),
]

KEYWORDS = [
    "pendirian PT", "izin usaha OSS", "konsultasi pajak", "merek dagang HKI",
    "kontrak kerja", "perjanjian bisnis", "perizinan", "laporan SPT tahunan",
    "sewa virtual office", "NPWP badan usaha", "perubahan akta", "legalitas UMKM",
    "biaya pembuatan PT", "syarat izin edar BPOM", "rekening bank perusahaan",
]

TEMPLATES = [
    "Panduan lengkap {kw} untuk pemula dan pelaku usaha di Indonesia. Simak selengkapnya!",
    "Jangan sampai salah langkah saat mengurus {kw}. Konsultasikan sekarang bersama tim ahli.",
    "Update regulasi terbaru pemerintah mengenai {kw} tahun 2026.",
    "Tips hemat & cepat menyelesaikan {kw} tanpa ribet calo.",
    "Banyak pengusaha belum tahu rahasia sukses {kw} ini!",
]


def generate_synthetic_dataset(num_posts_per_account: int = 300, seed: int = 42):
    rng = random.Random(seed)
    base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    accounts: List[Account] = []
    account_posts: Dict[str, List[Dict[str, Any]]] = {}

    for platform, username, is_own_brand in ACCOUNTS_SEED_DATA:
        acc = Account.create(
            platform=platform,
            username=username,
            is_own_brand=is_own_brand,
            created_at=base_time.isoformat(),
        )
        accounts.append(acc)

        raw_posts: List[Dict[str, Any]] = []
        for i in range(num_posts_per_account):
            kw = rng.choice(KEYWORDS)
            template = rng.choice(TEMPLATES)
            caption = template.format(kw=kw) + f" #{kw.replace(' ', '')} #bisnis #legal"
            post_time = base_time + timedelta(
                days=rng.randint(0, 90),
                hours=rng.randint(0, 23),
                minutes=rng.randint(0, 59),
            )
            likes = rng.randint(20, 8000)
            comments = rng.randint(2, max(5, int(likes * 0.12)))

            if platform == "instagram":
                raw_posts.append({
                    "shortcode": f"IG_{username}_{i:04d}",
                    "caption": caption,
                    "display_url": f"https://cdn.instagram.com/media/{username}_{i}.jpg",
                    "likes": likes,
                    "comments": comments,
                    "video_view_count": rng.randint(likes * 3, likes * 25) if (i % 3 == 0) else None,
                    "date_utc": post_time.isoformat(),
                })
            else:  # tiktok
                views = rng.randint(likes * 5, likes * 40)
                raw_posts.append({
                    "id": f"TT_{username}_{i:04d}",
                    "desc": caption,
                    "video": {"downloadAddr": f"https://v.tiktok.com/{username}_{i}.mp4"},
                    "stats": {
                        "diggCount": likes,
                        "commentCount": comments,
                        "playCount": views,
                    },
                    "createTime": int(post_time.timestamp()),
                })
        account_posts[acc.id] = raw_posts

    return accounts, account_posts


def run_benchmark():
    # 1. Setup in-memory / temporary DB
    db = Database(":memory:")

    # 2. Generate deterministic workload
    accounts, account_posts = generate_synthetic_dataset(num_posts_per_account=300, seed=42)

    t0 = time.perf_counter()

    # --- Phase A: Ingestion ---
    t_ingest_start = time.perf_counter()
    total_ingested = 0
    for acc in accounts:
        db.upsert_account(acc)
        raw_list = account_posts[acc.id]
        inserted, err = ingest_scraped_batch(db, acc.platform, acc, raw_list)
        if err:
            raise RuntimeError(f"Ingestion failed for {acc.username}: {err}")
        total_ingested += inserted
    t_ingest = time.perf_counter() - t_ingest_start

    # Invariant check
    if total_ingested != 3000:
        raise ValueError(f"Expected 3000 ingested posts, got {total_ingested}")

    # --- Phase B: Search Queries (1000 queries) ---
    rng_query = random.Random(1337)
    search_keywords = ["PT", "OSS", "pajak", "merek", "kontrak", "izin", "virtual", "NPWP", "badan"]
    t_search_start = time.perf_counter()
    search_hits_total = 0
    num_search_queries = 1000

    for i in range(num_search_queries):
        kw = rng_query.choice(search_keywords)
        platform_filter = rng_query.choice([None, "instagram", "tiktok"])
        date_from = "2026-01-15T00:00:00+00:00" if (i % 2 == 0) else None
        res = search_scraped_posts(
            db=db,
            platform=platform_filter,
            keyword=kw,
            date_from=date_from,
            limit=20,
            offset=(i % 5) * 10,
        )
        search_hits_total += res["count"]

    t_search = time.perf_counter() - t_search_start

    # Invariant check: search queries must have produced matches
    if search_hits_total == 0:
        raise ValueError("Search queries produced 0 total hits, expected thousands.")

    # --- Phase C: Engagement Summaries (500 queries) ---
    t_summary_start = time.perf_counter()
    num_summary_queries = 500
    summary_posts_total = 0

    for i in range(num_summary_queries):
        target_acc = accounts[i % len(accounts)]
        res = get_engagement_summary(db, target_acc.username, target_acc.platform)
        if res["status"] != "success":
            raise RuntimeError(f"Summary query failed for {target_acc.username}")
        summary_posts_total += res["summary"]["total_posts"]

    t_summary = time.perf_counter() - t_summary_start

    if summary_posts_total == 0:
        raise ValueError("Engagement summaries produced 0 posts.")

    # --- Phase D: Account Comparisons (300 queries) ---
    t_compare_start = time.perf_counter()
    num_compare_queries = 300
    comp_checked_total = 0

    for i in range(num_compare_queries):
        # Pick 2-4 accounts to compare
        subset_size = (i % 3) + 2
        subset_accounts = [acc.username for acc in accounts[i % len(accounts) : i % len(accounts) + subset_size]]
        res = compare_accounts(db, subset_accounts)
        if res["status"] != "success":
            raise RuntimeError(f"Compare query failed for {subset_accounts}")
        comp_checked_total += res["compared_count"]

    t_compare = time.perf_counter() - t_compare_start

    if comp_checked_total == 0:
        raise ValueError("Account comparisons produced 0 matches.")

    t_total = time.perf_counter() - t0
    total_pipeline_time_ms = round(t_total * 1000.0, 2)
    ingest_time_ms = round(t_ingest * 1000.0, 2)
    search_qps = round(num_search_queries / t_search, 1)
    summary_qps = round(num_summary_queries / t_summary, 1)
    compare_qps = round(num_compare_queries / t_compare, 1)
    total_ops = total_ingested + num_search_queries + num_summary_queries + num_compare_queries

    db.close()

    # Canonical metric outputs
    print(f"METRIC total_pipeline_time_ms={total_pipeline_time_ms}")
    print(f"METRIC ingest_time_ms={ingest_time_ms}")
    print(f"METRIC search_qps={search_qps}")
    print(f"METRIC summary_qps={summary_qps}")
    print(f"METRIC compare_qps={compare_qps}")
    print(f"METRIC total_operations={total_ops}")


if __name__ == "__main__":
    try:
        run_benchmark()
    except Exception as e:
        sys.stderr.write(f"Benchmark error: {e}\n")
        sys.exit(1)

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Tuple

from .models import Account, Post, Topic
from .db import Database
from .ingest import ingest_scraped_batch

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


def generate_sample_posts(num_posts_per_account: int = 25, seed: int = 42):
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
            caption = template.format(kw=kw) + f" #{kw.replace(' ', '')} #bisnis #legalitas"
            post_time = base_time + timedelta(
                days=rng.randint(0, 90),
                hours=rng.randint(0, 23),
                minutes=rng.randint(0, 59),
            )
            likes = rng.randint(50, 6500)
            comments = rng.randint(5, max(10, int(likes * 0.1)))

            if platform == "instagram":
                raw_posts.append({
                    "shortcode": f"IG_{username}_{i:04d}",
                    "caption": caption,
                    "display_url": f"https://cdn.instagram.com/media/{username}_{i}.jpg",
                    "likes": likes,
                    "comments": comments,
                    "video_view_count": rng.randint(likes * 3, likes * 25) if (i % 3 == 0) else None,
                    "date_utc": post_time.isoformat(),
                    "topic": kw.lower().strip(),
                })
            else:  # tiktok
                views = rng.randint(likes * 5, likes * 35)
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
                    "topic": kw.lower().strip(),
                })
        account_posts[acc.id] = raw_posts

    return accounts, account_posts


def seed_marketing_sample_data(db: Database, posts_per_account: int = 25) -> Tuple[int, int]:
    """Seeds sample data into the database."""
    # Register all topics
    for kw in KEYWORDS:
        category = "Legalitas & Izin" if any(x in kw for x in ["PT", "OSS", "izin", "akta", "BPOM"]) else ("Pajak & Keuangan" if "pajak" in kw or "SPT" in kw or "NPWP" in kw or "bank" in kw else "Properti & HKI")
        db.upsert_topic(Topic.create(keyword=kw, category=category))

    accounts, account_posts = generate_sample_posts(num_posts_per_account=posts_per_account)
    total_ingested = 0
    for acc in accounts:
        db.upsert_account(acc)
        raw_list = account_posts[acc.id]
        inserted, _ = ingest_scraped_batch(db, acc.platform, acc, raw_list)
        total_ingested += inserted
    return len(accounts), total_ingested

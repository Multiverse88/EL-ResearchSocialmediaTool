from datetime import datetime, timezone, timedelta
import unittest
from fastapi.testclient import TestClient

from src.db import Database
from src.models import Account, Post
from src.analytics import (
    parse_timeframe_days,
    extract_hook_preview,
    get_analytics_overview,
    get_timeseries_trends,
    get_viral_leaderboard,
    get_competitor_comparison,
)
from src.server import app


class TestAnalyticsEngine(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self._seed_test_data()

    def tearDown(self):
        self.db.close()

    def _seed_test_data(self):
        # 1. Brand Account: @id.easylegal
        acc_brand = self.db.upsert_account(
            Account.create(platform="instagram", username="id.easylegal", is_own_brand=True)
        )
        # 2. Competitor Account: @legal_competitor
        acc_comp = self.db.upsert_account(
            Account.create(platform="instagram", username="legal_competitor", is_own_brand=False)
        )

        now = datetime.now(timezone.utc)
        date_today = now.isoformat()
        date_3d_ago = (now - timedelta(days=3)).isoformat()
        date_40d_ago = (now - timedelta(days=40)).isoformat()

        # Posts for Brand
        self.db.insert_post(Post.create(
            account_id=acc_brand.id,
            platform="instagram",
            topic="pendirian pt",
            platform_post_id="post-b1",
            caption="3 Syarat utama pendirian PT Perorangan 2026 yang wajib kamu tahu:\n1. KTP\n2. NPWP",
            media_url="",
            likes=150,
            comments=20,
            views=3000,
            posted_at=date_today,
            post_url="https://instagram.com/p/b1",
        ))
        self.db.insert_post(Post.create(
            account_id=acc_brand.id,
            platform="instagram",
            topic="pendirian pt",
            platform_post_id="post-b2",
            caption="Berapa lama proses izin OSS terbit? Ini jawabannya!",
            media_url="",
            likes=50,
            comments=5,
            views=1000,
            posted_at=date_3d_ago,
            post_url="https://instagram.com/p/b2",
        ))

        # Posts for Competitor
        self.db.insert_post(Post.create(
            account_id=acc_comp.id,
            platform="instagram",
            topic="pendirian pt",
            platform_post_id="post-c1",
            caption="Jangan bikin PT sebelum nonton video ini! Banyak yang salah pilih KBLI!",
            media_url="",
            likes=500,
            comments=80,
            views=12000,
            posted_at=date_today,
            post_url="https://instagram.com/p/c1",
        ))
        # Old post (40 days ago) to test timeframe cutoff
        self.db.insert_post(Post.create(
            account_id=acc_comp.id,
            platform="instagram",
            topic="pendirian pt",
            platform_post_id="post-c2_old",
            caption="Tips legalitas lama",
            media_url="",
            likes=10,
            comments=1,
            views=200,
            posted_at=date_40d_ago,
            post_url="https://instagram.com/p/c2",
        ))

    def test_parse_timeframe_days(self):
        self.assertEqual(parse_timeframe_days("daily"), 3)
        self.assertEqual(parse_timeframe_days("weekly"), 7)
        self.assertEqual(parse_timeframe_days("monthly"), 30)
        self.assertEqual(parse_timeframe_days("all"), 3650)
        self.assertEqual(parse_timeframe_days("14"), 14)
        self.assertEqual(parse_timeframe_days(None), 30)

    def test_extract_hook_preview(self):
        caption = "Hook kalimat pertama yang sangat menarik!\nBaris kedua penjelasan panjang..."
        hook = extract_hook_preview(caption)
        self.assertEqual(hook, "Hook kalimat pertama yang sangat menarik!")

        empty_hook = extract_hook_preview("")
        self.assertEqual(empty_hook, "")

    def test_get_analytics_overview(self):
        # Timeframe: 30 days (should exclude the 40-day-old post)
        overview = get_analytics_overview(self.db, timeframe_days=30)
        kpis = overview["kpis"]
        self.assertEqual(kpis["total_posts"], 3)
        self.assertEqual(kpis["total_views"], 16000)  # 3000 + 1000 + 12000
        self.assertEqual(kpis["total_likes"], 700)    # 150 + 50 + 500

        # Brand vs Competitor breakdown
        bvc = overview["brand_vs_competitor"]
        self.assertEqual(bvc["brand"]["posts_count"], 2)
        self.assertEqual(bvc["brand"]["total_views"], 4000)
        self.assertEqual(bvc["competitor"]["posts_count"], 1)
        self.assertEqual(bvc["competitor"]["total_views"], 12000)

    def test_get_timeseries_trends(self):
        trends = get_timeseries_trends(self.db, days=30)
        self.assertTrue(len(trends) >= 1)
        today_entry = next((t for t in trends if t["date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")), None)
        self.assertIsNotNone(today_entry)
        self.assertEqual(today_entry["post_count"], 2)
        self.assertEqual(today_entry["total_views"], 15000)

    def test_get_viral_leaderboard(self):
        leaderboard = get_viral_leaderboard(self.db, limit=5, days=30)
        self.assertEqual(len(leaderboard), 3)
        # Top 1 should be competitor post (12,000 views)
        top1 = leaderboard[0]
        self.assertEqual(top1["platform_post_id"], "post-c1")
        self.assertEqual(top1["views"], 12000)
        self.assertFalse(top1["is_own_brand"])
        self.assertIn("Jangan bikin PT", top1["hook"])

        # Filter by brand only
        brand_lb = get_viral_leaderboard(self.db, limit=5, is_own_brand=1, days=30)
        self.assertEqual(len(brand_lb), 2)
        self.assertEqual(brand_lb[0]["platform_post_id"], "post-b1")
        self.assertTrue(brand_lb[0]["is_own_brand"])
        acc_tax = self.db.upsert_account(
            Account.create(platform="instagram", username="id.easytax", is_own_brand=True)
        )
        acc_office = self.db.upsert_account(
            Account.create(platform="instagram", username="id.easyoffice", is_own_brand=True)
        )
        self.db.insert_post(Post.create(
            account_id=acc_tax.id,
            platform="instagram",
            platform_post_id="post-tax",
            caption="Tips pajak untuk UMKM",
            media_url="",
            likes=80,
            comments=10,
            views=2000,
            posted_at=datetime.now(timezone.utc).isoformat(),
            post_url="https://instagram.com/p/tax",
        ))
        self.db.insert_post(Post.create(
            account_id=acc_office.id,
            platform="instagram",
            platform_post_id="post-office",
            caption="Solusi virtual office",
            media_url="",
            likes=60,
            comments=5,
            views=1500,
            posted_at=datetime.now(timezone.utc).isoformat(),
            post_url="https://instagram.com/p/office",
        ))

        tax_lb = get_viral_leaderboard(self.db, limit=5, days=30, brand="easytax")
        self.assertEqual([item["username"] for item in tax_lb], ["id.easytax"])

        office_lb = get_viral_leaderboard(self.db, limit=5, days=30, brand="easyoffice")
        self.assertEqual([item["username"] for item in office_lb], ["id.easyoffice"])

        brand_asc = get_viral_leaderboard(
            self.db, limit=10, days=30, sort_by="brand", sort_order="asc"
        )
        brand_asc_usernames = [item["username"] for item in brand_asc]
        self.assertEqual(brand_asc_usernames, sorted(brand_asc_usernames))

        brand_desc = get_viral_leaderboard(
            self.db, limit=10, days=30, sort_by="brand", sort_order="desc"
        )
        brand_desc_usernames = [item["username"] for item in brand_desc]
        self.assertEqual(brand_desc_usernames, sorted(brand_desc_usernames, reverse=True))

        oldest_first = get_viral_leaderboard(
            self.db, limit=10, days=30, sort_by="posted_at", sort_order="asc"
        )
        oldest_dates = [item["posted_at"] for item in oldest_first]
        self.assertEqual(oldest_dates, sorted(oldest_dates))

        newest_first = get_viral_leaderboard(
            self.db, limit=10, days=30, sort_by="posted_at", sort_order="desc"
        )
        newest_dates = [item["posted_at"] for item in newest_first]
        self.assertEqual(newest_dates, sorted(newest_dates, reverse=True))

    def test_get_competitor_comparison(self):
        comp = get_competitor_comparison(self.db, days=30, topic="pendirian pt")
        self.assertEqual(comp["brand_performance"]["posts_count"], 2)
        self.assertEqual(comp["brand_performance"]["total_views"], 4000)
        self.assertEqual(comp["competitor_performance"]["posts_count"], 1)
        self.assertEqual(comp["competitor_performance"]["total_views"], 12000)

        # Hook previews included
        self.assertTrue(len(comp["sample_viral_hooks"]["competitor"]) >= 1)
        self.assertIn("Jangan bikin PT", comp["sample_viral_hooks"]["competitor"][0])

    def test_analytics_api_endpoints(self):
        client = TestClient(app)

        # 1. Overview
        resp = client.get("/api/analytics/overview?days=30")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")
        self.assertIn("kpis", resp.json()["data"])

        # 2. Trends
        resp = client.get("/api/analytics/trends?timeframe=daily")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")

        # 3. Leaderboard
        resp = client.get("/api/analytics/leaderboard?limit=5")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")

        # 4. Competitor comparison
        resp = client.get("/api/analytics/competitor-comparison?topic=pendirian%20pt")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")
        self.assertIn("brand_performance", resp.json()["data"])

        # 5. Brand stats (EasyLegal, EasyTax, EasyOffice)
        resp = client.get("/api/analytics/brand-stats?brand=all&platform=all")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")
        self.assertIn("kpis", resp.json()["data"])
        self.assertIn("platforms", resp.json()["data"])
        self.assertIn("brands", resp.json()["data"])
        self.assertIn("posts", resp.json()["data"])

    def test_get_brand_overview_stats(self):
        from src.analytics import get_brand_overview_stats
        stats = get_brand_overview_stats(self.db, brand="all", platform="all")
        self.assertIn("kpis", stats)
        self.assertEqual(stats["kpis"]["total_posts"], 2)  # 2 brand posts seeded in setUp
        self.assertEqual(stats["kpis"]["total_views"], 4000)  # 3000 + 1000
        self.assertIn("easylegal", stats["brands"])
        self.assertEqual(len(stats["posts"]), 2)

        # Filter by easylegal specifically
        legal_stats = get_brand_overview_stats(self.db, brand="easylegal", platform="all")
        self.assertEqual(legal_stats["kpis"]["total_posts"], 2)

    def test_get_content_format_breakdown(self):
        from src.analytics import get_content_format_breakdown
        acc_brand = self.db.get_account_by_username("instagram", "id.easylegal")
        self.db.insert_post(Post.create(
            account_id=acc_brand.id, platform="instagram", content_type="reel",
            platform_post_id="post-reel1", caption="Reel test", media_url="",
            likes=200, comments=10, views=5000, posted_at=datetime.now(timezone.utc).isoformat(),
        ))
        breakdown = get_content_format_breakdown(self.db)
        self.assertIn("Reel", breakdown["brand"])
        self.assertEqual(breakdown["brand"]["Reel"]["post_count"], 1)
        self.assertEqual(breakdown["brand"]["Reel"]["avg_likes"], 200.0)

    def test_get_posting_cadence(self):
        from src.analytics import get_posting_cadence
        cadence = get_posting_cadence(self.db)
        self.assertEqual(len(cadence), 1)  # only id.easylegal is own_brand in seed data
        entry = cadence[0]
        self.assertEqual(entry["username"], "id.easylegal")
        self.assertEqual(entry["post_count"], 2)
        self.assertEqual(entry["days_since_last_post"], 0)  # most recent seeded post is "today"
        self.assertEqual(entry["status"], "Aktif")

    def test_get_posting_cadence_no_posts(self):
        from src.analytics import get_posting_cadence
        self.db.upsert_account(Account.create(platform="tiktok", username="id.easylegal", is_own_brand=True))
        cadence = get_posting_cadence(self.db)
        empty_entry = next(c for c in cadence if c["platform"] == "tiktok")
        self.assertEqual(empty_entry["post_count"], 0)
        self.assertEqual(empty_entry["status"], "Belum ada data")

    def test_get_best_posting_time_shape(self):
        from src.analytics import get_best_posting_time
        heatmap = get_best_posting_time(self.db)
        self.assertEqual(len(heatmap), 42)  # 7 days x 6 four-hour buckets
        total_posts_counted = sum(cell["post_count"] for cell in heatmap)
        self.assertEqual(total_posts_counted, 4)  # all 4 seeded posts have parseable posted_at

    def test_get_hashtag_performance(self):
        from src.analytics import get_hashtag_performance
        acc_brand = self.db.get_account_by_username("instagram", "id.easylegal")
        self.db.insert_post(Post.create(
            account_id=acc_brand.id, platform="instagram",
            platform_post_id="post-tag1", caption="Info penting #legalitas #pendirianpt", media_url="",
            likes=100, comments=10, views=1000, posted_at=datetime.now(timezone.utc).isoformat(),
        ))
        self.db.insert_post(Post.create(
            account_id=acc_brand.id, platform="instagram",
            platform_post_id="post-tag2", caption="Update terbaru #legalitas", media_url="",
            likes=50, comments=5, views=500, posted_at=datetime.now(timezone.utc).isoformat(),
        ))
        result = get_hashtag_performance(self.db, min_posts=2)
        tags = {r["hashtag"]: r for r in result}
        self.assertIn("#legalitas", tags)
        self.assertEqual(tags["#legalitas"]["post_count"], 2)
        # #pendirianpt only appears once -> dropped by min_posts=2
        self.assertNotIn("#pendirianpt", tags)

    def test_get_competitor_leaderboard(self):
        from src.analytics import get_competitor_leaderboard, get_competitor_posts
        acc_comp2 = self.db.upsert_account(
            Account.create(platform="instagram", username="another_competitor", is_own_brand=False)
        )
        self.db.insert_post(Post.create(
            account_id=acc_comp2.id, platform="instagram",
            platform_post_id="post-c3", caption="Competitor 2 post", media_url="",
            likes=5, comments=1, views=100, posted_at=datetime.now(timezone.utc).isoformat(),
        ))
        leaderboard = get_competitor_leaderboard(self.db, days=3650)
        usernames = [r["username"] for r in leaderboard]
        self.assertIn("legal_competitor", usernames)
        self.assertIn("another_competitor", usernames)
        # legal_competitor has far higher engagement (500+80 vs 5+1) -> ranked first
        self.assertEqual(leaderboard[0]["username"], "legal_competitor")

        details = get_competitor_posts(
            self.db,
            username="legal_competitor",
            platform="instagram",
            days=3650,
        )
        self.assertEqual(details["total"], 2)
        self.assertEqual(details["count"], 2)
        self.assertFalse(details["truncated"])
        self.assertEqual(
            [post["platform_post_id"] for post in details["posts"]],
            ["post-c1", "post-c2_old"],
        )
        self.assertEqual(details["posts"][0]["caption"], "Jangan bikin PT sebelum nonton video ini! Banyak yang salah pilih KBLI!")
        self.assertEqual(details["posts"][0]["post_url"], "https://instagram.com/p/c1")

        limited = get_competitor_posts(
            self.db,
            username="legal_competitor",
            platform="instagram",
            days=3650,
            limit=1,
        )
        self.assertEqual(limited["count"], 1)
        self.assertTrue(limited["truncated"])

        own_brand = get_competitor_posts(
            self.db,
            username="id.easylegal",
            platform="instagram",
            days=3650,
        )
        self.assertEqual(own_brand["total"], 0)
        self.assertEqual(own_brand["posts"], [])

    def test_get_data_health(self):
        from src.analytics import get_data_health
        health = get_data_health(self.db)
        self.assertEqual(len(health), 1)
        entry = health[0]
        self.assertEqual(entry["username"], "id.easylegal")
        self.assertEqual(entry["post_count"], 2)
        self.assertEqual(entry["views_data_completeness_pct"], 100)  # both seeded posts have views set

if __name__ == "__main__":
    unittest.main()

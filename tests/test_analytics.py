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


if __name__ == "__main__":
    unittest.main()

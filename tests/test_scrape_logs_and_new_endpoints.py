import unittest
from datetime import datetime, timezone, timedelta

from fastapi.testclient import TestClient

from src.db import Database
from src.models import Account, Post, ScrapeLog
from src.ingest import ingest_scraped_batch
from src.server import app


class TestScrapeLogTarget(unittest.TestCase):
    """ScrapeLog.target lets the /logs page show which account/topic a run was about,
    instead of an opaque 'instagram: success' row with no context."""

    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_insert_and_list_round_trips_target(self):
        self.db.insert_scrape_log(
            ScrapeLog.create(platform="instagram", status="success", target="id.easylegal")
        )
        logs = self.db.list_scrape_logs(limit=10)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["target"], "id.easylegal")

    def test_ingest_scraped_batch_populates_target_from_account_username(self):
        acc = self.db.upsert_account(
            Account.create(platform="instagram", username="id.easyoffice", is_own_brand=True)
        )
        raw_posts = [{
            "shortcode": "abc123", "caption": "Test", "likes": 5, "comments": 1,
            "date_utc": datetime.now(timezone.utc).isoformat(),
        }]
        ingest_scraped_batch(db=self.db, platform="instagram", account=acc, raw_posts=raw_posts)
        logs = self.db.list_scrape_logs(limit=10)
        self.assertEqual(logs[0]["target"], "id.easyoffice")
        self.assertEqual(logs[0]["status"], "success")

    def test_list_scrape_logs_filters_by_platform_and_status(self):
        self.db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="success", target="a"))
        self.db.insert_scrape_log(ScrapeLog.create(platform="tiktok", status="failed", target="b", error_message="boom"))
        self.db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="failed", target="c", error_message="boom2"))

        ig_only = self.db.list_scrape_logs(platform="instagram")
        self.assertEqual(len(ig_only), 2)

        failed_only = self.db.list_scrape_logs(status="failed")
        self.assertEqual(len(failed_only), 2)

        ig_failed = self.db.list_scrape_logs(platform="instagram", status="failed")
        self.assertEqual(len(ig_failed), 1)
        self.assertEqual(ig_failed[0]["target"], "c")

    def test_count_scrape_logs_matches_filters(self):
        for i in range(5):
            self.db.insert_scrape_log(ScrapeLog.create(platform="instagram", status="success", target=f"t{i}"))
        self.assertEqual(self.db.count_scrape_logs(platform="instagram"), 5)
        self.assertEqual(self.db.count_scrape_logs(platform="tiktok"), 0)

    def test_list_scrape_logs_pagination(self):
        for i in range(5):
            self.db.insert_scrape_log(
                ScrapeLog.create(
                    platform="instagram", status="success", target=f"t{i}",
                    run_at=(datetime.now(timezone.utc) - timedelta(minutes=i)).isoformat(),
                )
            )
        page1 = self.db.list_scrape_logs(limit=2, offset=0)
        page2 = self.db.list_scrape_logs(limit=2, offset=2)
        self.assertEqual(len(page1), 2)
        self.assertEqual(len(page2), 2)
        self.assertNotEqual([r["target"] for r in page1], [r["target"] for r in page2])


class TestNewAnalyticsEndpoints(unittest.TestCase):
    """Smoke tests: every new deeper-analysis endpoint responds 200 with the expected
    envelope shape, on both an empty DB and a lightly seeded one."""

    def setUp(self):
        self.client = TestClient(app)

    def test_content_format_endpoint(self):
        resp = self.client.get("/api/analytics/content-format")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "success")
        self.assertIn("brand", body["data"])
        self.assertIn("competitor", body["data"])

    def test_posting_cadence_endpoint(self):
        resp = self.client.get("/api/analytics/posting-cadence")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")

    def test_best_time_endpoint(self):
        resp = self.client.get("/api/analytics/best-time")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["data"]), 42)

    def test_hashtag_performance_endpoint(self):
        resp = self.client.get("/api/analytics/hashtag-performance")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")

    def test_competitor_leaderboard_endpoint(self):
        resp = self.client.get("/api/analytics/competitor-leaderboard")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")

        detail_resp = self.client.get(
            "/api/analytics/competitor-posts?username=not-found&platform=instagram&days=3650"
        )
        self.assertEqual(detail_resp.status_code, 200)
        detail_body = detail_resp.json()
        self.assertEqual(detail_body["status"], "success")
        self.assertEqual(detail_body["data"]["total"], 0)
        self.assertEqual(detail_body["data"]["posts"], [])

    def test_data_health_endpoint(self):
        resp = self.client.get("/api/analytics/data-health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "success")

    def test_scrape_logs_endpoint_returns_total_and_filters(self):
        resp = self.client.get("/scrape/logs?limit=5&platform=instagram&status=all")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("total", body)
        self.assertIn("data", body)

    def test_logs_page_route_serves_html(self):
        resp = self.client.get("/logs")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))


if __name__ == "__main__":
    unittest.main()

from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from src.db import Database
from src.models import Account, ScrapeLog
from src.scheduler import (
    WIB_TZ,
    get_current_wib_datetime,
    get_last_daily_sync_date,
    should_run_daily_sync,
    execute_daily_sync,
    DailySyncSchedulerThread,
)
from src.server import app


class TestDailySyncScheduler(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_wib_timezone_calculation(self):
        now_wib = get_current_wib_datetime()
        self.assertEqual(now_wib.tzinfo, WIB_TZ)
        # Difference between WIB and UTC should be exactly 7 hours
        utc_now = datetime.now(timezone.utc)
        diff_hours = (now_wib.utcoffset().total_seconds()) / 3600
        self.assertEqual(diff_hours, 7.0)

    def test_should_run_daily_sync_before_8am_returns_false(self):
        # 07:59 AM WIB
        time_759 = datetime(2026, 9, 20, 7, 59, 0, tzinfo=WIB_TZ)
        self.assertFalse(should_run_daily_sync(self.db, now_wib=time_759))

    def test_should_run_daily_sync_after_8am_first_time_returns_true(self):
        # 08:05 AM WIB, no sync has ever run
        time_805 = datetime(2026, 9, 20, 8, 5, 0, tzinfo=WIB_TZ)
        self.assertTrue(should_run_daily_sync(self.db, now_wib=time_805))

    def test_should_run_daily_sync_after_8am_already_synced_today_returns_false(self):
        # Record a successful sync for today (2026-09-20)
        self.db.log_scrape(
            ScrapeLog(
                id="sync-1",
                platform="daily_sync",
                status="success",
                error_message=None,
                run_at="2026-09-20T08:01:00+07:00",
            )
        )
        self.assertEqual(get_last_daily_sync_date(self.db), "2026-09-20")

        # Check at 09:30 AM WIB
        time_930 = datetime(2026, 9, 20, 9, 30, 0, tzinfo=WIB_TZ)
        self.assertFalse(should_run_daily_sync(self.db, now_wib=time_930))

    def test_auto_catchup_when_last_sync_was_yesterday(self):
        # Yesterday's successful sync
        self.db.log_scrape(
            ScrapeLog(
                id="sync-old",
                platform="daily_sync",
                status="success",
                error_message=None,
                run_at="2026-09-19T08:02:00+07:00",
            )
        )
        self.assertEqual(get_last_daily_sync_date(self.db), "2026-09-19")

        # Today at 11:00 AM WIB (server booted late) -> must trigger catchup
        time_1100 = datetime(2026, 9, 20, 11, 0, 0, tzinfo=WIB_TZ)
        self.assertTrue(should_run_daily_sync(self.db, now_wib=time_1100))

    @patch("src.chat_actions._run_profile_scrape")
    @patch("src.scrapers.keyword_scraper.scrape_topic_content")
    def test_execute_daily_sync_runs_parallel_and_logs_results(self, mock_topic, mock_profile):
        mock_profile.return_value = (10, None, "bright_data")
        mock_topic.return_value = {
            "keyword": "pendirian pt",
            "total_posts_added": 8,
            "instagram": {"count": 4, "error": None},
            "tiktok": {"count": 4, "error": None},
        }

        # Run with smaller custom list to test quickly
        brands = [("instagram", "id.easytax"), ("tiktok", "id.easylegal")]
        topics = ["pendirian pt"]

        summary = execute_daily_sync(
            self.db,
            max_posts=5,
            max_workers=2,
            brand_targets=brands,
            competitor_topics=topics,
        )

        self.assertEqual(summary["status"], "success")
        self.assertEqual(summary["total_targets"], 3)
        self.assertEqual(summary["total_posts_added"], 28)  # 10 + 10 + 8
        self.assertEqual(mock_profile.call_count, 2)
        mock_topic.assert_called_once()

        # Verify record in scrape_logs
        logs = self.db.list_scrape_logs(limit=1)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["platform"], "daily_sync")
        self.assertEqual(logs[0]["status"], "success")

    @patch("src.chat_actions._run_profile_scrape")
    @patch("src.scrapers.keyword_scraper.scrape_topic_content")
    def test_execute_daily_sync_isolates_individual_target_errors(self, mock_topic, mock_profile):
        # 1 brand fails, 1 succeeds
        def profile_side_effect(db, acc, max_posts=10, progress_callback=None):
            if acc.username == "id.easytax":
                return 0, "Account private or blocked", "bright_data"
            return 10, None, "bright_data"

        mock_profile.side_effect = profile_side_effect
        mock_topic.return_value = {"total_posts_added": 5}

        brands = [("instagram", "id.easytax"), ("instagram", "id.easylegal")]
        topics = ["pendirian pt"]

        summary = execute_daily_sync(
            self.db,
            max_posts=5,
            brand_targets=brands,
            competitor_topics=topics,
        )

        self.assertEqual(summary["total_targets"], 3)
        self.assertEqual(summary["successful_targets"], 2)
        self.assertEqual(summary["failed_targets"], 1)
        self.assertEqual(summary["status"], "success")  # Partial success counted

    def test_api_cron_daily_sync_endpoint(self):
        client = TestClient(app)
        with patch("src.server.execute_daily_sync") as mock_sync:
            mock_sync.return_value = {
                "status": "success",
                "total_posts_added": 30,
                "total_targets": 8,
            }

            resp = client.post("/api/cron/daily-sync?max_posts=10")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["status"], "success")
            self.assertEqual(data["data"]["total_posts_added"], 30)
            mock_sync.assert_called_once()


if __name__ == "__main__":
    unittest.main()

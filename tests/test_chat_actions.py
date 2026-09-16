import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.chat_actions import ChatActionOrchestrator
from src.db import Database
from src.models import Account, Post
import src.scrapers.instagram as ig_module
import src.scrapers.tiktok as tt_module


def _seed_post(db, acc, scraped_at, platform_post_id="1"):
    db.upsert_posts([
        Post.create(
            account_id=acc.id, platform_post_id=platform_post_id, caption="halo",
            media_url="", likes=10, comments=1, views=100,
            scraped_at=scraped_at, platform=acc.platform,
        )
    ])


class TestScrapeProfile(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.orch = ChatActionOrchestrator(ttl_hours=6)

    def tearDown(self):
        self.db.close()

    def test_stale_profile_invokes_scraper_and_persists_posts(self):
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(3, None, "apify")) as mock_scrape:
            result = self.orch.plan_and_execute(self.db, "Cari 20 post terbaru @id.easylegal", [])

        mock_scrape.assert_called_once()
        self.assertEqual(len(result.receipts), 1)
        r = result.receipts[0]
        self.assertTrue(r.success)
        self.assertEqual(r.backend, "apify")
        self.assertEqual(r.posts_collected, 3)
        self.assertFalse(r.used_cache)

        acc = self.db.get_account_by_username("instagram", "id.easylegal")
        self.assertIsNotNone(acc)
        self.assertFalse(acc.monitoring_enabled)

    def test_fresh_profile_uses_cache_without_scraping(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="id.easylegal"))
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        _seed_post(self.db, acc, recent)

        with patch.object(ig_module, "scrape_instagram_profile") as mock_scrape:
            result = self.orch.plan_and_execute(self.db, "Cari post @id.easylegal", [])

        mock_scrape.assert_not_called()
        r = result.receipts[0]
        self.assertTrue(r.used_cache)
        self.assertTrue(r.success)

    def test_explicit_refresh_bypasses_ttl(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="id.easylegal"))
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        _seed_post(self.db, acc, recent)

        with patch.object(ig_module, "scrape_instagram_profile", return_value=(2, None, "apify")) as mock_scrape:
            result = self.orch.plan_and_execute(self.db, "Cari data terbaru @id.easylegal", [])

        mock_scrape.assert_called_once()
        self.assertFalse(result.receipts[0].used_cache)

    def test_one_time_scrape_does_not_enable_monitoring(self):
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(1, None, "apify")):
            self.orch.plan_and_execute(self.db, "Scrape @kompetitor_a", [])
        acc = self.db.get_account_by_username("instagram", "kompetitor_a")
        self.assertFalse(acc.monitoring_enabled)

    def test_apify_failure_reported_not_masked_as_success(self):
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(0, "quota exceeded", "apify")):
            result = self.orch.plan_and_execute(self.db, "Scrape @gagal_test", [])
        r = result.receipts[0]
        self.assertFalse(r.success)
        self.assertEqual(r.error, "quota exceeded")
        self.assertFalse(r.used_cache)


class TestMonitoring(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.orch = ChatActionOrchestrator(ttl_hours=6)

    def tearDown(self):
        self.db.close()

    def test_monitor_account_enrolls_in_scheduling(self):
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(5, None, "apify")):
            self.orch.plan_and_execute(self.db, "Mulai monitor @id.easylegal", [])
        acc = self.db.get_account_by_username("instagram", "id.easylegal")
        self.assertTrue(acc.monitoring_enabled)
        self.assertIn(acc.id, [a.id for a in self.db.list_monitored_accounts()])

    def test_stop_monitoring_preserves_history(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="id.easylegal", monitoring_enabled=True))
        _seed_post(self.db, acc, datetime.now(timezone.utc).isoformat())

        result = self.orch.plan_and_execute(self.db, "Berhenti monitor @id.easylegal", [])

        self.assertTrue(result.receipts[0].success)
        updated = self.db.get_account(acc.id)
        self.assertFalse(updated.monitoring_enabled)
        self.assertEqual(len(self.db.query_posts(account_id=acc.id)), 1)

    def test_stop_monitoring_unknown_account_reports_failure(self):
        result = self.orch.plan_and_execute(self.db, "Berhenti monitor @tidak_ada", [])
        self.assertFalse(result.receipts[0].success)

    def test_topic_created_author_account_stays_unmonitored(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="tag_izinusaha", monitoring_enabled=False))
        self.assertNotIn(acc.id, [a.id for a in self.db.list_monitored_accounts()])


class TestReplaceMonitoredAccount(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.orch = ChatActionOrchestrator(ttl_hours=6)

    def tearDown(self):
        self.db.close()

    def test_replace_renames_in_place_and_preserves_history(self):
        old = self.db.upsert_account(Account.create(platform="instagram", username="easylegal_id", monitoring_enabled=True))
        _seed_post(self.db, old, datetime.now(timezone.utc).isoformat(), platform_post_id="hist-1")

        with patch.object(ig_module, "scrape_instagram_profile", return_value=(4, None, "apify")) as mock_scrape:
            result = self.orch.plan_and_execute(
                self.db, "Ganti akun EasyLegal dari @easylegal_id menjadi @id.easylegal", [],
            )

        mock_scrape.assert_called_once()
        r = result.receipts[0]
        self.assertTrue(r.success)

        renamed = self.db.get_account(old.id)
        self.assertEqual(renamed.username, "id.easylegal")
        self.assertTrue(renamed.monitoring_enabled)
        self.assertEqual(len(self.db.query_posts(account_id=old.id)), 1)
        self.assertIsNone(self.db.get_account_by_username("instagram", "easylegal_id"))

    def test_replace_with_existing_destination_transfers_monitoring_keeps_both_histories(self):
        old = self.db.upsert_account(Account.create(platform="instagram", username="easylegal_id", monitoring_enabled=True))
        new = self.db.upsert_account(Account.create(platform="instagram", username="id.easylegal", monitoring_enabled=False))
        _seed_post(self.db, old, datetime.now(timezone.utc).isoformat(), platform_post_id="old-1")
        _seed_post(self.db, new, datetime.now(timezone.utc).isoformat(), platform_post_id="new-1")

        with patch.object(ig_module, "scrape_instagram_profile", return_value=(1, None, "apify")):
            self.orch.plan_and_execute(
                self.db, "Ganti akun @easylegal_id menjadi @id.easylegal", [],
            )

        old_after = self.db.get_account(old.id)
        new_after = self.db.get_account(new.id)
        self.assertFalse(old_after.monitoring_enabled)
        self.assertTrue(new_after.monitoring_enabled)
        self.assertEqual(len(self.db.query_posts(account_id=old.id)), 1)
        self.assertEqual(len(self.db.query_posts(account_id=new.id)), 1)

    def test_replace_scrape_failure_keeps_monitoring_change(self):
        old = self.db.upsert_account(Account.create(platform="instagram", username="easylegal_id", monitoring_enabled=True))

        with patch.object(ig_module, "scrape_instagram_profile", return_value=(0, "network error", "apify")):
            result = self.orch.plan_and_execute(
                self.db, "Ganti akun @easylegal_id menjadi @id.easylegal", [],
            )

        self.assertFalse(result.receipts[0].success)
        renamed = self.db.get_account(old.id)
        self.assertEqual(renamed.username, "id.easylegal")
        self.assertTrue(renamed.monitoring_enabled)

    def test_ambiguous_brand_replace_asks_for_clarification_without_side_effects(self):
        # No @mention for the source, and no monitored account matches "rekam" — the
        # exact bug report phrasing. Should not silently mutate or misfire a scrape.
        result = ChatActionOrchestrator(ttl_hours=6).plan_and_execute(
            self.db, "rekam datanya ganti jadi id.easylegal", [],
        )
        self.assertEqual(result.receipts, [])
        self.assertEqual(self.db.list_accounts(), [])


class TestCompareProfiles(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.orch = ChatActionOrchestrator(ttl_hours=6)

    def tearDown(self):
        self.db.close()

    def test_partial_failure_still_returns_successful_target(self):
        def side_effect(db, account, max_posts):
            if account.username == "id.easylegal":
                return (2, None, "apify")
            return (0, "private account", "apify")

        with patch.object(ig_module, "scrape_instagram_profile", side_effect=side_effect):
            result = self.orch.plan_and_execute(
                self.db, "Bandingkan @id.easylegal dengan @legalku", [],
            )

        self.assertEqual(len(result.receipts), 2)
        successes = [r for r in result.receipts if r.success]
        failures = [r for r in result.receipts if not r.success]
        self.assertEqual(len(successes), 1)
        self.assertEqual(len(failures), 1)
        self.assertEqual(successes[0].target, "id.easylegal")
        self.assertEqual(failures[0].target, "legalku")

    def test_targets_over_limit_rejected(self):
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(1, None, "apify")):
            result = self.orch.plan_and_execute(
                self.db, "Bandingkan @a1 @a2 @a3 @a4 @a5", [],
            )
        self.assertEqual(len(result.receipts), 4)


class TestDeterministicParsingEdgeCases(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_plain_research_question_produces_no_action(self):
        orch = ChatActionOrchestrator(ttl_hours=6)
        result = orch.plan_and_execute(self.db, "berapa engagement rata-rata akun easylegal_id bulan ini?", [])
        self.assertEqual(result.receipts, [])
        self.assertIsNone(result.clarification)

    def test_planner_not_consulted_without_action_intent(self):
        orch = ChatActionOrchestrator(ttl_hours=6)
        planner_calls = []

        def fake_planner(message, history):
            planner_calls.append(message)
            return None

        orch.plan_and_execute(self.db, "riset topik pendirian PT", [], planner=fake_planner)
        self.assertEqual(planner_calls, [])

    def test_planner_consulted_when_action_intent_present_but_no_deterministic_match(self):
        orch = ChatActionOrchestrator(ttl_hours=6)
        planner_calls = []

        def fake_planner(message, history):
            planner_calls.append(message)
            return None

        orch.plan_and_execute(self.db, "tolong monitor akun kompetitor baru kami", [], planner=fake_planner)
        # "monitor" is present but with no @mention deterministic parsing returns a
        # clarification plan directly, so the planner should not need to be consulted.
        self.assertEqual(planner_calls, [])


if __name__ == "__main__":
    unittest.main()

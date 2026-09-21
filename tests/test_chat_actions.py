import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.chat_actions import ChatActionOrchestrator, is_competitor_analysis_intent
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
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(3, None, "bright_data")) as mock_scrape:
            result = self.orch.plan_and_execute(self.db, "Cari 20 post terbaru @id.easylegal", [])

        mock_scrape.assert_called_once()
        self.assertEqual(len(result.receipts), 1)
        r = result.receipts[0]
        self.assertTrue(r.success)
        self.assertEqual(r.backend, "bright_data")
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

        with patch.object(ig_module, "scrape_instagram_profile", return_value=(2, None, "bright_data")) as mock_scrape:
            result = self.orch.plan_and_execute(self.db, "Cari data terbaru @id.easylegal", [])

        mock_scrape.assert_called_once()
        self.assertFalse(result.receipts[0].used_cache)

    def test_one_time_scrape_does_not_enable_monitoring(self):
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(1, None, "bright_data")):
            self.orch.plan_and_execute(self.db, "Scrape @kompetitor_a", [])
        acc = self.db.get_account_by_username("instagram", "kompetitor_a")
        self.assertFalse(acc.monitoring_enabled)

    def test_provider_failure_reported_not_masked_as_success(self):
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(0, "quota exceeded", "bright_data")):
            result = self.orch.plan_and_execute(self.db, "Scrape @gagal_test", [])
        r = result.receipts[0]
        self.assertFalse(r.success)
        self.assertEqual(r.error, "quota exceeded")
        self.assertFalse(r.used_cache)

    def test_bare_account_reference_without_at_sign_triggers_scrape(self):
        # Regression test for a real production incident: "saya mau riset soal akun
        # instagram id.easylegal" has no "@" and no cari/scrape/ambil/refresh verb, so
        # it was falling through to the old topic-research flow and being misread as a
        # keyword search for "soal" — the account was never actually looked up.
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(5, None, "bright_data")) as mock_scrape:
            result = self.orch.plan_and_execute(
                self.db, "saya mau riset soal akun instagram id.easylegal", [],
            )

        mock_scrape.assert_called_once()
        self.assertEqual(len(result.receipts), 1)
        r = result.receipts[0]
        self.assertTrue(r.success)
        self.assertEqual(r.target, "id.easylegal")
        self.assertEqual(r.platform, "instagram")

        acc = self.db.get_account_by_username("instagram", "id.easylegal")
        self.assertIsNotNone(acc)

    def test_bare_account_reference_username_before_platform(self):
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(1, None, "bright_data")) as mock_scrape:
            result = self.orch.plan_and_execute(self.db, "cek akun id.easylegal di instagram dong", [])
        mock_scrape.assert_called_once()
        self.assertTrue(result.receipts[0].success)

    def test_brand_name_after_platform_resolves_monitored_account(self):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        instagram = self.db.upsert_account(Account.create(
            platform="instagram",
            username="id.easylegal",
            is_own_brand=True,
            monitoring_enabled=True,
        ))
        self.db.upsert_account(Account.create(
            platform="tiktok",
            username="id.easylegal",
            is_own_brand=True,
            monitoring_enabled=True,
        ))
        _seed_post(self.db, instagram, recent)

        with patch.object(ig_module, "scrape_instagram_profile") as mock_scrape:
            result = self.orch.plan_and_execute(
                self.db,
                "Tampilkan data postingan Instagram akun EasyLegal untuk hari ini dan kemarin",
                [],
            )

        mock_scrape.assert_not_called()
        self.assertEqual(result.matched_account, ("instagram", "id.easylegal"))
        self.assertEqual(len(result.receipts), 1)
        self.assertTrue(result.receipts[0].used_cache)

    def test_competitor_atm_request_does_not_scrape_own_brand_profile(self):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        own = self.db.upsert_account(Account.create(
            platform="instagram",
            username="id.easylegal",
            is_own_brand=True,
            monitoring_enabled=True,
        ))
        _seed_post(self.db, own, recent)

        with patch.object(ig_module, "scrape_instagram_profile") as mock_scrape:
            result = self.orch.plan_and_execute(
                self.db,
                "apa konten kompetitor id.easylegal yang views nya besar dan bisa diamati tiru dan dimodifikasi",
                [],
            )

        mock_scrape.assert_not_called()
        self.assertEqual(result.receipts, [])
        self.assertIsNone(result.matched_account)

    def test_competitor_analysis_intent_accepts_natural_language_variants(self):
        positive = [
            "Bandingkan performa kompetitor dengan EasyTax",
            "ATM postingan competitor yang likes-nya paling tinggi",
            "Konten kompetitor mana yang views terbesar?",
            "Buat komparasi dan modifikasi ide dari kompetitor EasyOffice",
        ]
        for message in positive:
            with self.subTest(message=message):
                self.assertTrue(is_competitor_analysis_intent(message))

        self.assertFalse(is_competitor_analysis_intent("monitor akun kompetitor baru"))
        self.assertFalse(is_competitor_analysis_intent("ganti akun kompetitor menjadi @baru"))

    def test_generic_pronoun_after_akun_platform_does_not_misfire(self):
        # "akun tiktok kami" — no real username present, must not scrape a literal
        # account named "kami".
        with patch.object(tt_module, "scrape_tiktok_profile") as mock_scrape:
            result = self.orch.plan_and_execute(self.db, "akun tiktok kami gimana performanya", [])
        mock_scrape.assert_not_called()
        self.assertEqual(result.receipts, [])
        self.assertIsNone(self.db.get_account_by_username("tiktok", "kami"))


class TestMonitoring(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.orch = ChatActionOrchestrator(ttl_hours=6)

    def tearDown(self):
        self.db.close()

    def test_monitor_account_enrolls_in_scheduling(self):
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(5, None, "bright_data")):
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

        with patch.object(ig_module, "scrape_instagram_profile", return_value=(4, None, "bright_data")) as mock_scrape:
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

        with patch.object(ig_module, "scrape_instagram_profile", return_value=(1, None, "bright_data")):
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

        with patch.object(ig_module, "scrape_instagram_profile", return_value=(0, "network error", "bright_data")):
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
                return (2, None, "bright_data")
            return (0, "private account", "bright_data")

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
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(1, None, "bright_data")):
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


class TestBareKnownAccountMention(unittest.TestCase):
    """Regression test: "coba riset tentang akun id.easytax dan id.easyoffice" — no "@",
    no "instagram"/"tiktok" word — previously matched nothing in parse_deterministic, so
    chat_actions never triggered a scrape at all and the AI just described whatever
    (often empty) data already happened to be in the DB. A bare mention of an ALREADY
    REGISTERED account username is now recognized without requiring "@" or a platform
    word, since it can only match real, existing usernames — not arbitrary text."""

    def setUp(self):
        self.db = Database(":memory:")
        self.orch = ChatActionOrchestrator(ttl_hours=6)
        self.db.upsert_account(Account.create(platform="instagram", username="id.easytax", is_own_brand=True))
        self.db.upsert_account(Account.create(platform="instagram", username="id.easyoffice", is_own_brand=True))

    def tearDown(self):
        self.db.close()

    def test_single_known_account_bare_mention_triggers_scrape(self):
        self.db.upsert_account(Account.create(platform="instagram", username="id.easylegal", is_own_brand=True))
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(2, None, "bright_data")) as mock_scrape:
            result = self.orch.plan_and_execute(self.db, "coba riset tentang akun id.easylegal", [])

        mock_scrape.assert_called_once()
        self.assertEqual(len(result.receipts), 1)
        self.assertTrue(result.receipts[0].success)

    def test_two_known_accounts_bare_mention_triggers_scrape_for_both(self):
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(1, None, "bright_data")) as mock_scrape:
            result = self.orch.plan_and_execute(
                self.db, "coba riset tentang akun id.easytax dan id.easyoffice", [],
            )

        self.assertEqual(mock_scrape.call_count, 2)
        self.assertEqual(len(result.receipts), 2)
        self.assertTrue(all(r.success for r in result.receipts))

    def test_unregistered_bare_word_does_not_falsely_trigger_scrape(self):
        with patch.object(ig_module, "scrape_instagram_profile") as mock_scrape:
            self.orch.plan_and_execute(self.db, "coba riset tentang topik legalitas umkm", [])
        mock_scrape.assert_not_called()

    def test_prefers_instagram_for_id_handles_even_if_tiktok_account_exists_in_db(self):
        # Simulate collision: id.easytax registered on tiktok first
        self.db.upsert_account(Account.create(platform="tiktok", username="id.easytax", is_own_brand=True))
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(5, None, "bright_data")) as mock_ig, \
             patch.object(tt_module, "scrape_tiktok_profile") as mock_tt:
            result = self.orch.plan_and_execute(self.db, "coba riset tentang akun id.easytax", [])

        # Must have routed to Instagram, NOT TikTok
        mock_ig.assert_called_once()
        mock_tt.assert_not_called()
        self.assertEqual(result.receipts[0].platform, "instagram")

    def test_tiktok_zero_posts_falls_back_to_instagram_for_id_handles(self):
        from src.chat_actions import _run_profile_scrape
        # Explicitly pass a TikTok account object with an id.* handle
        tt_acc = Account.create(platform="tiktok", username="id.easytax", is_own_brand=True)
        with patch.object(tt_module, "scrape_tiktok_profile", return_value=(0, "no videos", "bright_data")) as mock_tt, \
             patch.object(ig_module, "scrape_instagram_profile", return_value=(47, None, "bright_data")) as mock_ig:
            count, err, backend = _run_profile_scrape(self.db, tt_acc, max_posts=10)

        mock_tt.assert_called_once()
        # Must have fallen back to Instagram automatically
        mock_ig.assert_called_once()
        self.assertEqual(count, 47)
        self.assertIsNone(err)

    def test_seed_default_accounts_adds_missing_to_populated_db(self):
        from src.scrapers.runner import seed_default_accounts_if_empty
        fresh_db = Database(":memory:")
        # Simulate existing DB with just one unrelated account
        fresh_db.upsert_account(Account.create(platform="instagram", username="random_account"))
        self.assertEqual(len(fresh_db.list_accounts()), 1)

        # Run seeder
        seed_default_accounts_if_empty(fresh_db)
        # Must have inserted all missing seeds (id.easylegal, id.easytax, id.easyoffice, etc.)
        self.assertIsNotNone(fresh_db.get_account_by_username("instagram", "id.easytax"))
        self.assertIsNotNone(fresh_db.get_account_by_username("instagram", "id.easyoffice"))
        self.assertIsNotNone(fresh_db.get_account_by_username("instagram", "id.easylegal"))
        fresh_db.close()

if __name__ == "__main__":
    unittest.main()

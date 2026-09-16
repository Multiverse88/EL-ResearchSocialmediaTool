import os
import unittest
from unittest.mock import MagicMock, patch

from src.db import Database
from src.models import Account
from src.scrapers import instagram as ig_module
from src.scrapers import tiktok as tt_module
from src.scrapers import keyword_scraper as kw_module
from src.scrapers.apify_client import run_actor_sync
from src.scrapers.tiktok import _apify_item_to_raw_post


FAKE_IG_ITEMS = [
    {
        "shortCode": "ABC123",
        "id": "999888777",
        "caption": "Panduan legalitas UMKM lengkap #legalitas",
        "displayUrl": "https://cdn.example/ig1.jpg",
        "likesCount": 4200,
        "commentsCount": 150,
        "videoViewCount": None,
        "timestamp": "2026-02-01T10:00:00.000Z",
        "ownerUsername": "easylegal_id",
    },
    {"error": "This account is private"},
]

FAKE_TT_ITEMS = [
    {
        "id": "7301234567890",
        "text": "Tips cepat urus izin OSS #oss #bisnis",
        "diggCount": 8800,
        "commentCount": 340,
        "playCount": 210000,
        "createTime": 1769900000,
        "webVideoUrl": "https://www.tiktok.com/@legalku_tiktok/video/7301234567890",
        "videoMeta": {"downloadAddr": "https://cdn.example/tt1.mp4"},
        "authorMeta": {"name": "legalku_tiktok"},
    }
]


class TestApifyClient(unittest.TestCase):
    def setUp(self):
        os.environ.pop("APIFY_API_TOKEN", None)

    def tearDown(self):
        os.environ.pop("APIFY_API_TOKEN", None)

    def test_run_actor_sync_requires_token(self):
        with self.assertRaises(RuntimeError):
            run_actor_sync("apify~instagram-scraper", {})

    def test_run_actor_sync_success(self):
        os.environ["APIFY_API_TOKEN"] = "fake-token"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"id": "1"}]

        with patch("src.scrapers.apify_client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            items = run_actor_sync("apify~instagram-scraper", {"foo": "bar"})
            self.assertEqual(items, [{"id": "1"}])

    def test_run_actor_sync_raises_on_error_status(self):
        os.environ["APIFY_API_TOKEN"] = "fake-token"
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "actor crashed"

        with patch("src.scrapers.apify_client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            with self.assertRaises(RuntimeError):
                run_actor_sync("apify~instagram-scraper", {})


class TestTikTokAdapter(unittest.TestCase):
    def test_apify_item_to_raw_post_maps_fields_correctly(self):
        raw = _apify_item_to_raw_post(FAKE_TT_ITEMS[0])
        self.assertEqual(raw["id"], "7301234567890")
        self.assertEqual(raw["desc"], "Tips cepat urus izin OSS #oss #bisnis")
        self.assertEqual(raw["stats"]["diggCount"], 8800)
        self.assertEqual(raw["stats"]["commentCount"], 340)
        self.assertEqual(raw["stats"]["playCount"], 210000)
        self.assertEqual(raw["video"]["downloadAddr"], "https://cdn.example/tt1.mp4")

    def test_apify_item_to_raw_post_skips_items_without_id(self):
        self.assertIsNone(_apify_item_to_raw_post({"text": "no id here"}))


class TestApifyProfileScraping(unittest.TestCase):
    def setUp(self):
        os.environ["APIFY_API_TOKEN"] = "fake-token"
        self.db = Database(":memory:")

    def tearDown(self):
        os.environ.pop("APIFY_API_TOKEN", None)
        self.db.close()

    def test_instagram_profile_scrape_uses_apify_and_ingests_posts(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="easylegal_id", is_own_brand=True))

        with patch.object(ig_module, "run_actor_sync", return_value=FAKE_IG_ITEMS) as mock_run:
            count, err, backend = ig_module.scrape_instagram_profile(self.db, acc, max_posts=10)

        mock_run.assert_called_once()
        self.assertIsNone(err)
        self.assertEqual(backend, "apify")
        self.assertEqual(count, 1)  # the "error" item is filtered out

        posts = self.db.query_posts(account_id=acc.id)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["likes"], 4200)
        self.assertEqual(posts[0]["comments"], 150)
        self.assertIn("legalitas", posts[0]["caption"])

    def test_tiktok_profile_scrape_uses_apify_and_ingests_posts(self):
        acc = self.db.upsert_account(Account.create(platform="tiktok", username="legalku_tiktok", is_own_brand=False))

        with patch.object(tt_module, "run_actor_sync", return_value=FAKE_TT_ITEMS) as mock_run:
            count, err, backend = tt_module.scrape_tiktok_profile(self.db, acc, max_posts=10)

        mock_run.assert_called_once()
        self.assertIsNone(err)
        self.assertEqual(backend, "apify")
        self.assertEqual(count, 1)

        posts = self.db.query_posts(account_id=acc.id)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["likes"], 8800)
        self.assertEqual(posts[0]["views"], 210000)

    def test_dispatcher_falls_back_to_free_scraper_when_no_token(self):
        os.environ.pop("APIFY_API_TOKEN", None)
        acc = self.db.upsert_account(Account.create(platform="instagram", username="fallback_test", is_own_brand=True))

        with patch.object(ig_module, "_scrape_instagram_profile_apify") as mock_apify, \
             patch.object(ig_module, "_scrape_instagram_profile_instaloader", return_value=(0, None)) as mock_free:
            ig_module.scrape_instagram_profile(self.db, acc, max_posts=5)

        mock_apify.assert_not_called()
        mock_free.assert_called_once()

    def test_dispatcher_uses_apify_when_token_present(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="apify_test", is_own_brand=True))

        with patch.object(ig_module, "_scrape_instagram_profile_apify", return_value=(0, None)) as mock_apify, \
             patch.object(ig_module, "_scrape_instagram_profile_instaloader") as mock_free:
            ig_module.scrape_instagram_profile(self.db, acc, max_posts=5)

        mock_apify.assert_called_once()
        mock_free.assert_not_called()

    def test_apify_failure_reason_surfaced_when_instaloader_fallback_also_fails(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="both_fail_test", is_own_brand=True))

        with patch.object(ig_module, "_scrape_instagram_profile_apify", return_value=(0, "quota exceeded")), \
             patch.object(ig_module, "_scrape_instagram_profile_instaloader", return_value=(0, "rate limit (429)")):
            count, err, backend = ig_module.scrape_instagram_profile(self.db, acc, max_posts=5)

        self.assertEqual(count, 0)
        self.assertEqual(backend, "instaloader")
        self.assertIn("quota exceeded", err)
        self.assertIn("rate limit (429)", err)


class TestApifyHashtagScraping(unittest.TestCase):
    def setUp(self):
        os.environ["APIFY_API_TOKEN"] = "fake-token"
        self.db = Database(":memory:")

    def tearDown(self):
        os.environ.pop("APIFY_API_TOKEN", None)
        self.db.close()

    def test_instagram_hashtag_scrape_attributes_real_author(self):
        with patch.object(kw_module, "run_actor_sync", return_value=FAKE_IG_ITEMS):
            count, err = kw_module.scrape_instagram_hashtag(self.db, "legalitas umkm", max_posts=10)

        self.assertIsNone(err)
        self.assertEqual(count, 1)

        acc = self.db.get_account_by_username("instagram", "easylegal_id")
        self.assertIsNotNone(acc)
        posts = self.db.query_posts(account_id=acc.id)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["topic"], "legalitas umkm")

    def test_tiktok_hashtag_scrape_attributes_real_author(self):
        with patch.object(kw_module, "run_actor_sync", return_value=FAKE_TT_ITEMS):
            count, err = kw_module.scrape_tiktok_topic(self.db, "izin oss", max_posts=10)

        self.assertIsNone(err)
        self.assertEqual(count, 1)

        acc = self.db.get_account_by_username("tiktok", "legalku_tiktok")
        self.assertIsNotNone(acc)
        posts = self.db.query_posts(account_id=acc.id)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["topic"], "izin oss")


if __name__ == "__main__":
    unittest.main()

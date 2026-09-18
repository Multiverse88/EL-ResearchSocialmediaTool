import os
import unittest
from unittest.mock import patch

from src.db import Database
from src.models import Account
from src.scrapers import tiktok as tt_module
from src.scrapers import keyword_scraper as kw_module
from src.scrapers.tiktokapi_client import (
    _tiktokapi_item_author_username,
    _tiktokapi_item_to_raw_post,
)


FAKE_TIKTOKAPI_ITEMS = [
    {
        "id": "7001",
        "desc": "Panduan izin usaha #legalitas",
        "video": {"downloadAddr": "https://cdn.tiktok.com/7001.mp4", "playAddr": ""},
        "stats": {"diggCount": 3300, "commentCount": 90, "playCount": 120000},
        "createTime": 1700000000,
        "author": {"uniqueId": "legalku_tiktok", "nickname": "Legalku"},
    },
    {"desc": "missing id, should be dropped"},
]


class TestTikTokApiAdapter(unittest.TestCase):
    def test_item_to_raw_post_maps_fields_correctly(self):
        raw = _tiktokapi_item_to_raw_post(FAKE_TIKTOKAPI_ITEMS[0])
        self.assertEqual(raw["id"], "7001")
        self.assertEqual(raw["desc"], "Panduan izin usaha #legalitas")
        self.assertEqual(raw["stats"]["diggCount"], 3300)
        self.assertEqual(raw["stats"]["commentCount"], 90)
        self.assertEqual(raw["stats"]["playCount"], 120000)
        self.assertEqual(raw["video"]["downloadAddr"], "https://cdn.tiktok.com/7001.mp4")

    def test_item_to_raw_post_returns_none_without_id(self):
        self.assertIsNone(_tiktokapi_item_to_raw_post(FAKE_TIKTOKAPI_ITEMS[1]))

    def test_item_author_username_extracted_from_uniqueid(self):
        self.assertEqual(_tiktokapi_item_author_username(FAKE_TIKTOKAPI_ITEMS[0]), "legalku_tiktok")

    def test_item_author_username_none_when_missing(self):
        self.assertIsNone(_tiktokapi_item_author_username({}))


class TestTikTokApiDispatch(unittest.TestCase):
    def setUp(self):
        os.environ.pop("BRIGHT_DATA_API_TOKEN", None)
        self.db = Database(":memory:")

    def tearDown(self):
        os.environ.pop("BRIGHT_DATA_API_TOKEN", None)
        self.db.close()

    def test_profile_dispatcher_prefers_bright_data_when_token_present(self):
        os.environ["BRIGHT_DATA_API_TOKEN"] = "fake-token"
        acc = self.db.upsert_account(Account.create(platform="tiktok", username="brand", is_own_brand=True))

        with patch.object(tt_module, "_scrape_tiktok_profile_bright_data", return_value=(0, None)) as mock_bright_data, \
             patch.object(tt_module, "is_tiktokapi_available", return_value=True), \
             patch.object(tt_module, "_scrape_tiktok_profile_playwright") as mock_pw, \
             patch.object(tt_module, "_scrape_tiktok_profile_html") as mock_html:
            tt_module.scrape_tiktok_profile(self.db, acc, max_posts=5)

        mock_bright_data.assert_called_once()
        mock_pw.assert_not_called()
        mock_html.assert_not_called()

    def test_profile_dispatcher_uses_playwright_without_bright_data_but_tiktokapi_available(self):
        acc = self.db.upsert_account(Account.create(platform="tiktok", username="brand2", is_own_brand=True))

        with patch.object(tt_module, "is_tiktokapi_available", return_value=True), \
             patch.object(tt_module, "_scrape_tiktok_profile_playwright", return_value=(0, None)) as mock_pw, \
             patch.object(tt_module, "_scrape_tiktok_profile_html") as mock_html:
            tt_module.scrape_tiktok_profile(self.db, acc, max_posts=5)

        mock_pw.assert_called_once()
        mock_html.assert_not_called()

    def test_profile_dispatcher_falls_back_to_html_when_tiktokapi_unavailable(self):
        acc = self.db.upsert_account(Account.create(platform="tiktok", username="brand3", is_own_brand=True))

        with patch.object(tt_module, "is_tiktokapi_available", return_value=False), \
             patch.object(tt_module, "_scrape_tiktok_profile_playwright") as mock_pw, \
             patch.object(tt_module, "_scrape_tiktok_profile_html", return_value=(0, None)) as mock_html:
            tt_module.scrape_tiktok_profile(self.db, acc, max_posts=5)

        mock_pw.assert_not_called()
        mock_html.assert_called_once()

    def test_profile_scrape_via_playwright_ingests_posts(self):
        acc = self.db.upsert_account(Account.create(platform="tiktok", username="legalku_tiktok", is_own_brand=False))

        with patch.object(tt_module, "is_tiktokapi_available", return_value=True), \
             patch.object(tt_module, "fetch_user_videos", return_value=FAKE_TIKTOKAPI_ITEMS) as mock_fetch:
            count, err, backend = tt_module.scrape_tiktok_profile(self.db, acc, max_posts=10)

        mock_fetch.assert_called_once()
        self.assertIsNone(err)
        self.assertEqual(backend, "playwright")

        self.assertEqual(count, 1)  # the id-less item is dropped

        posts = self.db.query_posts(account_id=acc.id)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["likes"], 3300)
        self.assertEqual(posts[0]["views"], 120000)

    def test_hashtag_dispatcher_uses_playwright_and_attributes_real_author(self):
        with patch.object(kw_module, "is_tiktokapi_available", return_value=True), \
             patch.object(kw_module, "fetch_hashtag_videos", return_value=FAKE_TIKTOKAPI_ITEMS) as mock_fetch:
            count, err = kw_module.scrape_tiktok_topic(self.db, "izin oss", max_posts=10)

        mock_fetch.assert_called_once()
        self.assertIsNone(err)
        self.assertEqual(count, 1)

        acc = self.db.get_account_by_username("tiktok", "legalku_tiktok")
        self.assertIsNotNone(acc)
        posts = self.db.query_posts(account_id=acc.id)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["topic"], "izin oss")

    def test_hashtag_dispatcher_falls_back_to_html_when_tiktokapi_unavailable(self):
        with patch.object(kw_module, "is_tiktokapi_available", return_value=False), \
             patch.object(kw_module, "_scrape_tiktok_topic_playwright") as mock_pw, \
             patch.object(kw_module, "_scrape_tiktok_topic_html", return_value=(0, None)) as mock_html:
            kw_module.scrape_tiktok_topic(self.db, "izin oss", max_posts=10)

        mock_pw.assert_not_called()
        mock_html.assert_called_once()


if __name__ == "__main__":
    unittest.main()

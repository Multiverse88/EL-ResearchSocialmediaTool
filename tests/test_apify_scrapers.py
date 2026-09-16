import os
from datetime import datetime
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

    def test_run_actor_sync_accepts_201_created_as_success(self):
        # Regression test for a real production incident: Apify's
        # run-sync-get-dataset-items endpoint returns HTTP 201 (not 200) on a
        # synchronously-completed run. The old strict "== 200" check treated this as a
        # failure and silently discarded valid scrape results, falling back to the
        # free/rate-limited scrapers even though Apify had actually succeeded.
        os.environ["APIFY_API_TOKEN"] = "fake-token"
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = [{"id": "1", "caption": "real post"}]

        with patch("src.scrapers.apify_client.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__.return_value = mock_client
            mock_client.post.return_value = mock_resp
            mock_client_cls.return_value = mock_client

            items = run_actor_sync("apify~instagram-scraper", {"foo": "bar"})
            self.assertEqual(items, [{"id": "1", "caption": "real post"}])

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

    def test_apify_item_to_raw_post_clamps_negative_stats(self):
        raw = _apify_item_to_raw_post({"id": "1", "diggCount": -1, "commentCount": -1, "playCount": -1})
        self.assertEqual(raw["stats"]["diggCount"], 0)
        self.assertEqual(raw["stats"]["commentCount"], 0)
        self.assertEqual(raw["stats"]["playCount"], 0)


class TestApifyProfileScraping(unittest.TestCase):
    def setUp(self):
        os.environ["APIFY_API_TOKEN"] = "fake-token"
        self.db = Database(":memory:")

    def tearDown(self):
        os.environ.pop("APIFY_API_TOKEN", None)
        self.db.close()

    def test_instagram_profile_scrape_uses_apify_and_ingests_posts(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="easylegal_id", is_own_brand=True))

        with patch.object(ig_module, "run_actor_sync", side_effect=[FAKE_IG_ITEMS, []]) as mock_run:
            count, err, backend = ig_module.scrape_instagram_profile(self.db, acc, max_posts=10)

        self.assertEqual(mock_run.call_count, 2)
        self.assertEqual(
            [call.args[1]["resultsType"] for call in mock_run.call_args_list],
            ["posts", "reels"],
        )
        self.assertIsNone(err)
        self.assertEqual(backend, "apify")
        self.assertEqual(count, 1)  # the "error" item is filtered out

        posts = self.db.query_posts(account_id=acc.id)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["likes"], 4200)
        self.assertEqual(posts[0]["comments"], 150)
        self.assertEqual(posts[0]["content_type"], "feed")
        self.assertIn("legalitas", posts[0]["caption"])


    def test_instagram_profile_merges_feed_and_reels_deduplicates_and_applies_shared_limit(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="mixed_content", is_own_brand=True))
        feed_items = [
            {
                "shortCode": "FEED_NEW",
                "id": "1",
                "caption": "new feed",
                "displayUrl": "https://cdn.example/feed-new.jpg",
                "likesCount": 10,
                "commentsCount": 1,
                "timestamp": "2026-03-04T10:00:00.000Z",
            },
            {
                "shortCode": "SHARED",
                "id": "2",
                "caption": "shared feed copy",
                "displayUrl": "https://cdn.example/shared.jpg",
                "likesCount": 20,
                "commentsCount": 2,
                "timestamp": "2026-03-03T10:00:00.000Z",
            },
            {
                "shortCode": "FEED_OLD",
                "id": "3",
                "caption": "old feed",
                "displayUrl": "https://cdn.example/feed-old.jpg",
                "likesCount": 30,
                "commentsCount": 3,
                "timestamp": "2026-03-01T10:00:00.000Z",
            },
        ]
        reel_items = [
            {
                "shortCode": "SHARED",
                "id": "2",
                "caption": "shared reel copy",
                "displayUrl": "https://cdn.example/shared-cover.jpg",
                "videoUrl": "https://cdn.example/shared.mp4",
                "likesCount": 25,
                "commentsCount": 4,
                "videoPlayCount": 900,
                "timestamp": "2026-03-03T10:00:00.000Z",
            },
            {
                "shortCode": "REEL_MID",
                "id": "4",
                "caption": "middle reel",
                "displayUrl": "https://cdn.example/reel-cover.jpg",
                "videoUrl": "https://cdn.example/reel.mp4",
                "likesCount": 40,
                "commentsCount": 5,
                "videoPlayCount": 1200,
                "timestamp": "2026-03-02T10:00:00.000Z",
            },
        ]
        progress = []

        with patch.object(ig_module, "run_actor_sync", side_effect=[feed_items, reel_items]):
            count, err, backend = ig_module.scrape_instagram_profile(
                self.db, acc, max_posts=3, progress_callback=progress.append,
            )

        self.assertIsNone(err)
        self.assertEqual(backend, "apify")
        self.assertEqual(count, 3)
        posts = self.db.query_posts(account_id=acc.id, limit=10)
        self.assertEqual([post["platform_post_id"] for post in posts], ["FEED_NEW", "SHARED", "REEL_MID"])
        self.assertEqual([post["content_type"] for post in posts], ["feed", "reel", "reel"])
        self.assertEqual(posts[1]["views"], 900)
        self.assertEqual(posts[1]["media_url"], "https://cdn.example/shared.mp4")
        self.assertEqual(
            progress,
            [
                "Mengambil postingan Feed @mixed_content…",
                "Mengambil postingan Reels @mixed_content…",
                "Menggabungkan Feed dan Reels, menghapus duplikasi, lalu menyimpan data…",
                "Selesai: 3 postingan Feed/Reels tersimpan",
            ],
        )

    def test_instaloader_profile_merges_feed_and_reels(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="free_mixed", is_own_brand=True))

        def fake_post(shortcode, date_utc, *, is_video=False, views=None):
            post = MagicMock()
            post.shortcode = shortcode
            post.mediaid = shortcode
            post.caption = shortcode
            post.url = f"https://cdn.example/{shortcode}.jpg"
            post.video_url = f"https://cdn.example/{shortcode}.mp4" if is_video else None
            post.likes = 10
            post.comments = 1
            post.is_video = is_video
            post.video_view_count = views
            post.date_utc = datetime.fromisoformat(date_utc)
            return post

        shared_feed = fake_post("SHARED_FREE", "2026-03-02T10:00:00")
        shared_reel = fake_post("SHARED_FREE", "2026-03-02T10:00:00", is_video=True, views=700)
        profile = MagicMock()
        profile.get_posts.return_value = [
            fake_post("FEED_FREE", "2026-03-03T10:00:00"),
            shared_feed,
        ]
        profile.get_reels.return_value = [
            shared_reel,
            fake_post("REEL_FREE", "2026-03-01T10:00:00", is_video=True, views=500),
        ]

        with patch.object(ig_module, "create_instaloader_instance") as mock_loader, \
             patch.object(ig_module.instaloader.Profile, "from_username", return_value=profile):
            count, err = ig_module._scrape_instagram_profile_instaloader(
                self.db, acc, max_posts=3, delay_between_requests=0,
            )

        mock_loader.assert_called_once()
        self.assertIsNone(err)
        self.assertEqual(count, 3)
        posts = self.db.query_posts(account_id=acc.id)
        self.assertEqual([post["platform_post_id"] for post in posts], ["FEED_FREE", "SHARED_FREE", "REEL_FREE"])
        self.assertEqual([post["content_type"] for post in posts], ["feed", "reel", "reel"])
        self.assertEqual(posts[1]["views"], 700)

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

    def test_apify_hidden_like_count_sentinel_clamped_to_zero(self):
        # Regression test for a real production incident: Apify's Instagram actor
        # returns likesCount=-1 (not None/0) when the like count is hidden. `-1 or 0`
        # is a no-op in Python (-1 is truthy), so this sentinel was stored as a real
        # negative like count, corrupting averages and "most viral" rankings.
        acc = self.db.upsert_account(Account.create(platform="instagram", username="hidden_likes_test", is_own_brand=True))
        hidden_like_item = {
            "shortCode": "XYZ999",
            "id": "111222333",
            "caption": "post with hidden like count",
            "displayUrl": "https://cdn.example/hidden.jpg",
            "likesCount": -1,
            "commentsCount": -1,
            "videoViewCount": None,
            "timestamp": "2026-03-01T10:00:00.000Z",
        }
        with patch.object(ig_module, "run_actor_sync", return_value=[hidden_like_item]):
            count, err, backend = ig_module.scrape_instagram_profile(self.db, acc, max_posts=5)

        self.assertIsNone(err)
        self.assertEqual(count, 1)
        posts = self.db.query_posts(account_id=acc.id)
        self.assertEqual(posts[0]["likes"], 0)
        self.assertEqual(posts[0]["comments"], 0)


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

import os
import unittest
from unittest.mock import patch

from src.db import Database
from src.models import Account
from src.scrapers import instagram as ig_module
from src.scrapers import keyword_scraper as kw_module
from src.scrapers import tiktok as tt_module


IG_POST_DISCOVERY = [{
    "account": "legalbrand",
    "posts": [{
        "id": "ig-post-1",
        "caption": "Cara mengurus izin usaha",
        "comments": 12,
        "datetime": "2026-09-10T09:00:00.000Z",
        "image_url": "https://cdn.example/ig-post.jpg",
        "likes": 250,
        "url": "https://www.instagram.com/p/POST1/",
    }],
}]

IG_REELS = [{
    "post_id": "ig-reel-1",
    "shortcode": "REEL1",
    "url": "https://www.instagram.com/reel/REEL1/",
    "user_posted": "legalbrand",
    "description": "Reels izin OSS",
    "num_comments": 20,
    "date_posted": "2026-09-11T09:00:00.000Z",
    "likes": 500,
    "views": 3000,
    "video_play_count": 4500,
    "video_url": "https://cdn.example/reel.mp4",
}]

TT_POSTS = [{
    "post_id": "tt-1",
    "description": "Tips izin OSS",
    "create_time": "2026-09-12T09:00:00.000Z",
    "digg_count": 800,
    "comment_count": 30,
    "play_count": 12000,
    "video_url": "https://cdn.example/tt.mp4",
    "profile_username": "legaltiktok",
}]


class TestBrightDataAdapters(unittest.TestCase):
    def test_instagram_reel_maps_metrics_and_video(self):
        raw = ig_module._bright_data_item_to_raw_post(IG_REELS[0], "reel")
        self.assertEqual(raw["shortcode"], "REEL1")
        self.assertEqual(raw["caption"], "Reels izin OSS")
        self.assertEqual(raw["comments"], 20)
        self.assertEqual(raw["video_view_count"], 4500)
        self.assertEqual(raw["display_url"], "https://cdn.example/reel.mp4")

    def test_instagram_discovery_expands_nested_posts(self):
        raw = ig_module._expand_bright_data_records(IG_POST_DISCOVERY, "feed")
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["shortcode"], "ig-post-1")
        self.assertEqual(raw[0]["date_utc"], "2026-09-10T09:00:00.000Z")

    def test_tiktok_post_maps_metrics(self):
        raw = tt_module._bright_data_item_to_raw_post(TT_POSTS[0], "brandacc")
        self.assertEqual(raw["id"], "tt-1")
        self.assertEqual(raw["stats"]["diggCount"], 800)
        self.assertEqual(raw["stats"]["commentCount"], 30)
        self.assertEqual(raw["stats"]["playCount"], 12000)
        self.assertEqual(raw["post_url"], "https://www.tiktok.com/@brandacc/video/tt-1")

    def test_provider_error_and_missing_id_are_rejected(self):
        self.assertIsNone(ig_module._bright_data_item_to_raw_post({"error": "private"}, "feed"))
        self.assertIsNone(tt_module._bright_data_item_to_raw_post({"description": "missing id"}, "brandacc"))


class TestBrightDataProfileScrapers(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_instagram_profile_merges_feed_and_reels(self):
        account = self.db.upsert_account(Account.create(platform="instagram", username="legalbrand"))
        with patch.object(ig_module, "run_dataset", side_effect=[IG_POST_DISCOVERY, IG_REELS]) as run:
            count, error = ig_module._scrape_instagram_profile_bright_data(
                self.db, account, max_posts=10,
            )

        self.assertIsNone(error)
        self.assertEqual(count, 2)
        self.assertEqual(run.call_count, 2)
        posts = self.db.query_posts(account_id=account.id, order_by="posted_at", limit=10)
        self.assertEqual([post["content_type"] for post in posts], ["reel", "feed"])

    def test_instagram_dispatch_reports_bright_data_backend(self):
        account = self.db.upsert_account(Account.create(platform="instagram", username="legalbrand"))
        with patch.object(ig_module, "is_bright_data_configured", return_value=True), \
             patch.object(ig_module, "_scrape_instagram_profile_bright_data", return_value=(2, None)), \
             patch.object(ig_module, "_scrape_instagram_profile_instaloader") as fallback:
            result = ig_module.scrape_instagram_profile(self.db, account)

        self.assertEqual(result, (2, None, "bright_data"))
        fallback.assert_not_called()

    def test_instagram_falls_back_after_bright_data_failure(self):
        account = self.db.upsert_account(Account.create(platform="instagram", username="legalbrand"))
        with patch.object(ig_module, "is_bright_data_configured", return_value=True), \
             patch.object(ig_module, "_scrape_instagram_profile_bright_data", return_value=(0, "denied")), \
             patch.object(ig_module, "_scrape_instagram_profile_instaloader", return_value=(1, None)) as fallback:
            result = ig_module.scrape_instagram_profile(self.db, account)

        self.assertEqual(result, (1, None, "instaloader"))
        fallback.assert_called_once()

    def test_tiktok_profile_attributes_posts_to_requested_account(self):
        account = self.db.upsert_account(Account.create(platform="tiktok", username="legaltiktok"))
        with patch.object(tt_module, "run_dataset", return_value=TT_POSTS):
            count, error = tt_module._scrape_tiktok_profile_bright_data(
                self.db, account, max_posts=10,
            )

        self.assertIsNone(error)
        self.assertEqual(count, 1)
        posts = self.db.query_posts(account_id=account.id)
        self.assertEqual(posts[0]["views"], 12000)

    def test_instagram_profile_captures_follower_count_from_response(self):
        account = self.db.upsert_account(Account.create(platform="instagram", username="legalbrand"))
        ig_reel_with_followers = [{**IG_REELS[0], "followers": 13136}]
        with patch.object(ig_module, "run_dataset", side_effect=[IG_POST_DISCOVERY, ig_reel_with_followers]):
            ig_module._scrape_instagram_profile_bright_data(self.db, account, max_posts=10)

        updated = self.db.get_account(account.id)
        self.assertEqual(updated.follower_count, 13136)

    def test_instagram_post_permalink_prefers_bright_data_url(self):
        account = self.db.upsert_account(Account.create(platform="instagram", username="legalbrand"))
        with patch.object(ig_module, "run_dataset", side_effect=[IG_POST_DISCOVERY, IG_REELS]):
            ig_module._scrape_instagram_profile_bright_data(self.db, account, max_posts=10)

        posts = self.db.query_posts(account_id=account.id, order_by="posted_at", limit=10)
        self.assertEqual(posts[0]["post_url"], "https://www.instagram.com/reel/REEL1/")
        self.assertEqual(posts[1]["post_url"], "https://www.instagram.com/p/POST1/")

    def test_tiktok_profile_captures_follower_count_via_profiles_dataset(self):
        account = self.db.upsert_account(Account.create(platform="tiktok", username="legaltiktok"))
        with patch.object(tt_module, "run_dataset", side_effect=[
            TT_POSTS,
            [{"followers": 85600000}],
        ]) as run:
            tt_module._scrape_tiktok_profile_bright_data(self.db, account, max_posts=10)

        updated = self.db.get_account(account.id)
        self.assertEqual(updated.follower_count, 85600000)
        self.assertEqual(run.call_args_list[1].args[0], "gd_l1villgoiiidt09ci")

    def test_tiktok_post_permalink_constructed_from_username(self):
        account = self.db.upsert_account(Account.create(platform="tiktok", username="legaltiktok"))
        with patch.object(tt_module, "run_dataset", side_effect=[TT_POSTS, []]):
            tt_module._scrape_tiktok_profile_bright_data(self.db, account, max_posts=10)

        posts = self.db.query_posts(account_id=account.id)
        self.assertEqual(posts[0]["post_url"], "https://www.tiktok.com/@legaltiktok/video/tt-1")


class TestBrightDataTopicScrapers(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_instagram_broad_topic_deduplicates_serp_urls_and_keeps_canonical_topic(self):
        discovered = [
            "https://www.instagram.com/p/POST1/?utm_source=google",
            "https://instagram.com/p/POST1/",
            "https://www.instagram.com/reel/REEL1/",
            "https://example.com/not-instagram",
        ]
        collected = [{
            "post_id": "post-1",
            "shortcode": "POST1",
            "url": "https://www.instagram.com/p/POST1/",
            "user_posted": "competitor",
            "description": "Legalitas UMKM",
            "num_comments": 4,
            "date_posted": "2026-09-10T09:00:00.000Z",
            "likes": 50,
            "thumbnail": "https://cdn.example/post.jpg",
        }]
        with patch.object(kw_module, "search_instagram_urls", return_value=discovered), \
             patch.object(kw_module, "run_dataset", return_value=collected) as run:
            count, error = kw_module._scrape_instagram_topic_bright_data(
                self.db,
                ["legalitas bisnis", "izin usaha"],
                max_posts=10,
                topic_label="legalitas bisnis",
            )

        self.assertIsNone(error)
        self.assertEqual(count, 1)
        requested_urls = [row["url"] for row in run.call_args.kwargs.get("inputs", [])]
        if not requested_urls:
            requested_urls = [row["url"] for row in run.call_args.args[1]]
        self.assertEqual(len(set(requested_urls)), len(requested_urls))
        posts = self.db.query_posts(topic="legalitas bisnis")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["username"], "competitor")

    def test_tiktok_keyword_discovery_filters_old_posts(self):
        old = dict(TT_POSTS[0], post_id="tt-old", create_time="2026-08-01T00:00:00.000Z")
        with patch.object(kw_module, "run_dataset", return_value=[old, TT_POSTS[0]]) as run:
            count, error = kw_module._scrape_tiktok_topic_bright_data(
                self.db,
                "izin usaha",
                max_posts=10,
                since="2026-09-01T00:00:00+00:00",
                topic_label="legalitas bisnis",
            )

        self.assertIsNone(error)
        self.assertEqual(count, 1)
        self.assertEqual(run.call_args.kwargs["query"]["discover_by"], "keyword")
        posts = self.db.query_posts(topic="legalitas bisnis")
        self.assertEqual([post["platform_post_id"] for post in posts], ["tt-1"])


if __name__ == "__main__":
    unittest.main()

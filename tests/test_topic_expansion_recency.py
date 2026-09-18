import os
import unittest
from unittest.mock import MagicMock, patch
from src.db import Database
from src.models import Account, Post, Topic, TopicScrape
from src.scrapers.keyword_scraper import (
    expand_topic_queries,
    scrape_topic_content,
    _scrape_instagram_hashtag_bright_data,
    _scrape_tiktok_topic_bright_data,
)
from src.scrapers.runner import refresh_stale_topics


class TestTopicScraperExpansionAndRecency(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_expand_topic_queries(self):
        queries = expand_topic_queries("pendirian pt")
        self.assertIn("pendirianpt", queries)
        self.assertIn("ptperorangan", queries)

    @patch("src.scrapers.keyword_scraper.run_dataset")
    @patch("src.scrapers.keyword_scraper.search_instagram_urls")
    def test_instagram_bright_data_uses_serp_then_collects_urls(self, mock_search, mock_dataset):
        mock_search.return_value = ["https://www.instagram.com/p/POST1/"]
        mock_dataset.return_value = [{
            "post_id": "1",
            "shortcode": "POST1",
            "url": "https://www.instagram.com/p/POST1/",
            "user_posted": "legalbrand",
            "description": "Pendirian PT",
            "date_posted": "2026-09-01T00:00:00Z",
        }]
        count, error = _scrape_instagram_hashtag_bright_data(
            self.db,
            "pendirian pt",
            max_posts=15,
            since="2026-08-01",
        )
        self.assertIsNone(error)
        self.assertEqual(count, 1)
        self.assertIn("site:instagram.com", mock_search.call_args.args[0])
        self.assertEqual(mock_dataset.call_args.args[1], [{"url": "https://www.instagram.com/p/POST1/"}])

    @patch("src.scrapers.keyword_scraper.run_dataset")
    def test_tiktok_bright_data_passes_keyword_discovery_contract(self, mock_dataset):
        mock_dataset.return_value = [{
            "post_id": "1",
            "description": "Pendirian PT",
            "create_time": "2026-09-01T00:00:00Z",
            "profile_username": "legalbrand",
        }]
        count, error = _scrape_tiktok_topic_bright_data(
            self.db,
            "pendirian pt",
            max_posts=10,
            since="2026-08-01",
        )
        self.assertIsNone(error)
        self.assertEqual(count, 1)
        self.assertEqual(
            mock_dataset.call_args.kwargs["query"],
            {"type": "discover_new", "discover_by": "keyword"},
        )
        self.assertEqual(
            mock_dataset.call_args.args[1],
            [{"search_keyword": "pendirian pt", "num_of_posts": 10}],
        )

    @patch("src.scrapers.keyword_scraper.scrape_instagram_hashtag")
    @patch("src.scrapers.keyword_scraper.scrape_tiktok_topic")
    def test_scrape_topic_content_records_audit_and_expands(self, mock_tt, mock_ig):
        mock_ig.return_value = (5, None)
        mock_tt.return_value = (3, None)

        res = scrape_topic_content(self.db, "pendirian pt", max_posts_per_platform=20)
        self.assertEqual(res["status"], "success")
        self.assertGreater(len(res["queries_used"]), 1)
        self.assertGreater(mock_ig.call_count, 1)
        self.assertGreater(mock_tt.call_count, 1)

        # Audit logs recorded
        last_scrape = self.db.get_topic_last_scraped("pendirian pt")
        self.assertIsNotNone(last_scrape)

    @patch("src.scrapers.keyword_scraper.scrape_topic_content")
    def test_refresh_stale_topics_skips_fresh(self, mock_scrape):
        # Register two topics, one fresh, one never scraped
        self.db.upsert_topic(Topic.create(keyword="fresh topic"))
        self.db.record_topic_scrape(TopicScrape.create(
            keyword="fresh topic",
            platform="instagram",
            posts_found=10,
            status="success",
        ))

        self.db.upsert_topic(Topic.create(keyword="stale topic"))

        mock_scrape.return_value = {"total_posts_added": 5}

        res = refresh_stale_topics(self.db, staleness_hours=6.0)
        self.assertEqual(res["topics_refreshed"], 1)
        self.assertEqual(res["topics_skipped"], 1)
        mock_scrape.assert_called_once()
        self.assertEqual(mock_scrape.call_args[0][1], "stale topic")


if __name__ == "__main__":
    unittest.main()

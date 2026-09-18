import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from src.claude_client import ClaudeChatHandler
from src.db import Database
from src.models import Account, Post


class TestTopicLastScraped(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_returns_none_when_no_posts_for_topic(self):
        self.assertIsNone(self.db.get_topic_last_scraped("izin usaha"))

    def test_returns_latest_scraped_at_for_matching_topic(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="brand", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc.id, platform_post_id="1", caption="soal izin usaha",
                        media_url="", likes=1, comments=0, views=0,
                        scraped_at="2026-01-01T00:00:00+00:00", topic="izin usaha"),
            Post.create(account_id=acc.id, platform_post_id="2", caption="soal izin usaha lagi",
                        media_url="", likes=1, comments=0, views=0,
                        scraped_at="2026-01-05T00:00:00+00:00", topic="izin usaha"),
        ])
        self.assertEqual(self.db.get_topic_last_scraped("izin usaha"), "2026-01-05T00:00:00+00:00")


class TestResolveMatchedTopicConfidence(unittest.TestCase):
    """Regression test: a filler/test prompt with no recognizable topic (e.g. "coba dong",
    "cek dulu ya") must never be treated as a real research topic. Before this fix,
    `_resolve_matched_topic`'s fallback picked the first non-stopword out of the sentence
    and callers fed it straight into `_ensure_topic_freshness`, which registers a new row
    in `topics` and burns Bright Data quota searching for the leftover word.

    The current flow has the AI actually read the prompt first (`_classify_topic_intent`)
    before any unmatched message can count as a confident topic — these tests mock that
    router call directly so no real network request happens.
    """

    def setUp(self):
        self.db = Database(":memory:")
        self.handler = ClaudeChatHandler(api_key="fake-router-key", base_url="https://router.example/v1")

    def tearDown(self):
        self.db.close()

    @staticmethod
    def _mock_router_client(payload: dict):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {"choices": [{"message": {"content": json.dumps(payload)}}]}
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = response
        return client

    def test_filler_only_message_is_not_confident(self):
        client = self._mock_router_client({"is_topic_research": False, "topic": None})
        with patch("httpx.Client", return_value=client):
            topic, is_confident = self.handler._resolve_matched_topic(self.db, "coba dong", [])
        self.assertFalse(is_confident)

    def test_known_seed_topic_is_confident_without_calling_ai(self):
        with patch("httpx.Client") as mock_client_cls:
            topic, is_confident = self.handler._resolve_matched_topic(
                self.db, "cari info izin usaha oss dong", [],
            )
        mock_client_cls.assert_not_called()
        self.assertTrue(is_confident)
        self.assertEqual(topic, "izin usaha oss")

    def test_ai_confirms_genuine_topic_intent_marks_confident(self):
        client = self._mock_router_client({"is_topic_research": True, "topic": "sewa virtual office"})
        with patch("httpx.Client", return_value=client):
            topic, is_confident = self.handler._resolve_matched_topic(
                self.db, "ada rekomendasi kantor buat startup baru gak?", [],
            )
        self.assertTrue(is_confident)
        self.assertEqual(topic, "sewa virtual office")

    def test_ai_classifier_unreachable_falls_back_to_not_confident(self):
        with patch("httpx.Client") as mock_client_cls:
            mock_client_cls.side_effect = RuntimeError("no network in test")
            topic, is_confident = self.handler._resolve_matched_topic(self.db, "coba dong", [])
        self.assertFalse(is_confident)

    def test_filler_message_never_reaches_bright_data_or_registers_topic(self):
        with patch("src.scrapers.keyword_scraper.scrape_topic_content") as mock_scrape, \
             patch.object(self.handler, "_build_router_context",
                           return_value=("http://fake/v1/chat/completions", {}, "Thinking", [], "coba", {})), \
             patch("httpx.Client") as mock_client_cls:
            mock_client_cls.side_effect = RuntimeError("no network in test")
            list(self.handler.stream_router_chat(self.db, "coba dong", []))

        mock_scrape.assert_not_called()
        self.assertIsNone(self.db.get_topic_last_scraped("coba"))


class TestEnsureTopicFreshness(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.handler = ClaudeChatHandler(api_key="fake-router-key", base_url="https://router.example/v1")
        os.environ.pop("ENABLE_LIVE_SCRAPE_ON_CHAT", None)
        os.environ.pop("TOPIC_STALENESS_HOURS", None)

    def tearDown(self):
        self.db.close()
        os.environ.pop("ENABLE_LIVE_SCRAPE_ON_CHAT", None)
        os.environ.pop("TOPIC_STALENESS_HOURS", None)

    def test_triggers_scrape_when_topic_never_scraped(self):
        with patch("src.scrapers.keyword_scraper.scrape_topic_content",
                   return_value={"total_posts_added": 4}) as mock_scrape:
            status = self.handler._ensure_topic_freshness(self.db, "izin usaha baru")

        mock_scrape.assert_called_once()
        self.assertIsNotNone(status)
        self.assertIn("izin usaha baru", status)

    def test_skips_scrape_when_data_is_fresh(self):
        recent = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with patch.object(self.db, "get_topic_last_scraped", return_value=recent), \
             patch("src.scrapers.keyword_scraper.scrape_topic_content") as mock_scrape:
            status = self.handler._ensure_topic_freshness(self.db, "izin usaha")

        mock_scrape.assert_not_called()
        self.assertIsNone(status)

    def test_triggers_scrape_when_data_is_stale(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        with patch.object(self.db, "get_topic_last_scraped", return_value=old), \
             patch("src.scrapers.keyword_scraper.scrape_topic_content",
                   return_value={"total_posts_added": 2}) as mock_scrape:
            status = self.handler._ensure_topic_freshness(self.db, "izin usaha")

        mock_scrape.assert_called_once()
        self.assertIsNotNone(status)

    def test_respects_custom_staleness_window(self):
        os.environ["TOPIC_STALENESS_HOURS"] = "72"
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        with patch.object(self.db, "get_topic_last_scraped", return_value=old), \
             patch("src.scrapers.keyword_scraper.scrape_topic_content") as mock_scrape:
            status = self.handler._ensure_topic_freshness(self.db, "izin usaha")

        mock_scrape.assert_not_called()
        self.assertIsNone(status)

    def test_disabled_via_env_flag(self):
        os.environ["ENABLE_LIVE_SCRAPE_ON_CHAT"] = "false"
        with patch("src.scrapers.keyword_scraper.scrape_topic_content") as mock_scrape:
            status = self.handler._ensure_topic_freshness(self.db, "izin usaha baru")

        mock_scrape.assert_not_called()
        self.assertIsNone(status)

    def test_scrape_failure_is_swallowed_not_raised(self):
        with patch("src.scrapers.keyword_scraper.scrape_topic_content", side_effect=RuntimeError("boom")):
            status = self.handler._ensure_topic_freshness(self.db, "izin usaha baru")
        self.assertIsNone(status)


class TestStreamRouterChatYieldsFreshnessStatus(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.handler = ClaudeChatHandler(api_key="fake-router-key", base_url="https://router.example/v1")

    def tearDown(self):
        self.db.close()

    def test_yields_freshness_status_before_router_call(self):
        fake_status = "🔍 Mengambil data terbaru...\n\n"
        with patch.object(self.handler, "_resolve_matched_topic", return_value=("izin usaha", True)), \
             patch.object(self.handler, "_ensure_topic_freshness", return_value=fake_status), \
             patch.object(self.handler, "_build_router_context",
                           return_value=("http://fake/v1/chat/completions", {}, "Thinking", [], "izin usaha", {})), \
             patch("httpx.Client") as mock_client_cls:
            mock_client_cls.side_effect = RuntimeError("no network in test")
            chunks = list(self.handler.stream_router_chat(self.db, "cari info izin usaha", []))

        self.assertEqual(chunks[0], {"type": "reasoning", "text": fake_status})


if __name__ == "__main__":
    unittest.main()

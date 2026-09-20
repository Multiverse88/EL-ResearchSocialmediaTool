import unittest
from unittest.mock import patch

from src.db import Database
from src.models import Account
import src.scrapers.threads as th_module


THREADS_POSTS = [{
    "post_id": "th-1",
    "post_content": "Tips lapor SPT tahunan badan",
    "date_posted": "2026-09-12T09:00:00.000Z",
    "likes": 40,
    "comments": 5,
    "url": "https://www.threads.net/@legalthreads/post/th-1",
}]


class TestThreadsAdapter(unittest.TestCase):
    def test_maps_metrics_and_permalink(self):
        raw = th_module._bright_data_item_to_raw_post(THREADS_POSTS[0], "legalthreads")
        self.assertEqual(raw["id"], "th-1")
        self.assertEqual(raw["likes"], 40)
        self.assertEqual(raw["comments"], 5)
        self.assertEqual(raw["post_url"], "https://www.threads.net/@legalthreads/post/th-1")

    def test_constructs_permalink_when_url_missing(self):
        item = {k: v for k, v in THREADS_POSTS[0].items() if k != "url"}
        raw = th_module._bright_data_item_to_raw_post(item, "legalthreads")
        self.assertEqual(raw["post_url"], "https://www.threads.com/@legalthreads/post/th-1")

    def test_provider_error_and_missing_id_are_rejected(self):
        self.assertIsNone(th_module._bright_data_item_to_raw_post({"error": "private"}, "legalthreads"))
        self.assertIsNone(th_module._bright_data_item_to_raw_post({"post_content": "no id"}, "legalthreads"))


class TestThreadsProfileScraper(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_requires_bright_data_configured(self):
        account = self.db.upsert_account(Account.create(platform="threads", username="legalthreads"))
        with patch.object(th_module, "is_bright_data_configured", return_value=False):
            count, err, backend = th_module.scrape_threads_profile(self.db, account, max_posts=10)
        self.assertEqual(count, 0)
        self.assertIn("Bright Data", err)
        self.assertEqual(backend, "bright_data")

    def test_successful_scrape_stores_posts_and_follower_count(self):
        account = self.db.upsert_account(Account.create(platform="threads", username="legalthreads"))
        with patch.object(th_module, "is_bright_data_configured", return_value=True), \
             patch.object(th_module, "run_dataset", side_effect=[THREADS_POSTS, [{"followers": 999}]]) as run:
            count, err, backend = th_module.scrape_threads_profile(self.db, account, max_posts=10)

        self.assertIsNone(err)
        self.assertEqual(count, 1)
        self.assertEqual(backend, "bright_data")
        self.assertEqual(run.call_args_list[0].args[0], "gd_md75myxy14rihbjksa")
        self.assertEqual(run.call_args_list[0].kwargs.get("query"), {"type": "discover_new", "discover_by": "profile"})
        self.assertEqual(run.call_args_list[0].args[1], [{"profile_url": "https://www.threads.com/@legalthreads"}])
        self.assertEqual(run.call_args_list[0].kwargs.get("timeout"), 300.0)
        self.assertEqual(run.call_args_list[1].args[0], "gd_mde7jg3ld2h3hnnf2")
        self.assertEqual(run.call_args_list[1].args[1], [{"url": "https://www.threads.com/@legalthreads"}])

        posts = self.db.query_posts(account_id=account.id)
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["platform"], "threads")
        self.assertEqual(posts[0]["post_url"], "https://www.threads.net/@legalthreads/post/th-1")

        updated_account = self.db.get_account(account.id)
        self.assertEqual(updated_account.follower_count, 999)

    def test_empty_result_reports_failure_without_fabricating_data(self):
        account = self.db.upsert_account(Account.create(platform="threads", username="legalthreads"))
        with patch.object(th_module, "is_bright_data_configured", return_value=True), \
             patch.object(th_module, "run_dataset", return_value=[]):
            count, err, backend = th_module.scrape_threads_profile(self.db, account, max_posts=10)

        self.assertEqual(count, 0)
        self.assertIn("no usable Threads posts", err)


if __name__ == "__main__":
    unittest.main()

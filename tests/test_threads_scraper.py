import unittest
from unittest.mock import patch

from src.db import Database
from src.models import Account
import src.scrapers.threads as th_module


# Real Bright Data Profiles dataset response shape, confirmed live 2026-09-21 against
# @id.easylegal — one call returns profile info, follower count, and an embedded
# `threads` list of recent posts together.
THREADS_PROFILE_RESPONSE = {
    "url": "https://www.threads.com/@legalthreads",
    "profile_name": "LEGALTHREADS",
    "profile_id": "63277215135",
    "number_of_followers": 313,
    "threads": [
        {
            "profile_id": "63277215135",
            "post_date": "2026-09-20T11:00:09.000Z",
            "post_content_formatted": "Tips lapor SPT tahunan badan",
            "likes": 40,
            "comments_amount": 5,
            "reshare_amount": None,
            "share_amount": None,
        },
        {
            "profile_id": "63277215135",
            "post_date": "2026-09-19T05:00:11.000Z",
            "post_content_formatted": "Post tanpa metrik interaksi",
            "likes": None,
            "comments_amount": None,
            "reshare_amount": None,
            "share_amount": None,
        },
    ],
}


class TestThreadsAdapter(unittest.TestCase):
    def test_maps_confirmed_real_fields(self):
        item = THREADS_PROFILE_RESPONSE["threads"][0]
        raw = th_module._bright_data_item_to_raw_post(
            item, "legalthreads", "https://www.threads.com/@legalthreads"
        )
        self.assertEqual(raw["id"], "63277215135_2026-09-20T11:00:09.000Z")
        self.assertEqual(raw["caption"], "Tips lapor SPT tahunan badan")
        self.assertEqual(raw["likes"], 40)
        self.assertEqual(raw["comments"], 5)
        self.assertEqual(raw["posted_at"], "2026-09-20T11:00:09.000Z")
        self.assertEqual(raw["post_url"], "https://www.threads.com/@legalthreads")

    def test_null_engagement_reported_as_zero_not_fabricated(self):
        item = THREADS_PROFILE_RESPONSE["threads"][1]
        raw = th_module._bright_data_item_to_raw_post(
            item, "legalthreads", "https://www.threads.com/@legalthreads"
        )
        self.assertEqual(raw["likes"], 0)
        self.assertEqual(raw["comments"], 0)

    def test_provider_error_and_missing_date_are_rejected(self):
        self.assertIsNone(th_module._bright_data_item_to_raw_post({"error": "private"}, "legalthreads", "u"))
        self.assertIsNone(th_module._bright_data_item_to_raw_post({"post_content_formatted": "no date"}, "legalthreads", "u"))


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

    def test_successful_scrape_stores_posts_and_follower_count_from_one_call(self):
        account = self.db.upsert_account(Account.create(platform="threads", username="legalthreads"))
        with patch.object(th_module, "is_bright_data_configured", return_value=True), \
             patch.object(th_module, "run_dataset", return_value=[THREADS_PROFILE_RESPONSE]) as run:
            count, err, backend = th_module.scrape_threads_profile(self.db, account, max_posts=10)

        self.assertIsNone(err)
        self.assertEqual(count, 2)
        self.assertEqual(backend, "bright_data")

        # Single synchronous call — no separate follower-count request needed anymore.
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0], "gd_mde7jg3ld2h3hnnf2")
        self.assertEqual(run.call_args.args[1], [{"url": "https://www.threads.com/@legalthreads"}])
        self.assertEqual(run.call_args.kwargs.get("query"), {"notify": "false"})

        posts = self.db.query_posts(account_id=account.id)
        self.assertEqual(len(posts), 2)
        self.assertEqual(posts[0]["platform"], "threads")

        updated_account = self.db.get_account(account.id)
        self.assertEqual(updated_account.follower_count, 313)

    def test_max_posts_limits_how_many_are_stored(self):
        account = self.db.upsert_account(Account.create(platform="threads", username="legalthreads"))
        with patch.object(th_module, "is_bright_data_configured", return_value=True), \
             patch.object(th_module, "run_dataset", return_value=[THREADS_PROFILE_RESPONSE]):
            count, err, _ = th_module.scrape_threads_profile(self.db, account, max_posts=1)
        self.assertIsNone(err)
        self.assertEqual(count, 1)

    def test_empty_result_reports_failure_without_fabricating_data(self):
        account = self.db.upsert_account(Account.create(platform="threads", username="legalthreads"))
        with patch.object(th_module, "is_bright_data_configured", return_value=True), \
             patch.object(th_module, "run_dataset", return_value=[{"url": "x", "threads": []}]):
            count, err, backend = th_module.scrape_threads_profile(self.db, account, max_posts=10)

        self.assertEqual(count, 0)
        self.assertIn("no usable Threads posts", err)

    def test_missing_profile_reports_failure(self):
        account = self.db.upsert_account(Account.create(platform="threads", username="legalthreads"))
        with patch.object(th_module, "is_bright_data_configured", return_value=True), \
             patch.object(th_module, "run_dataset", return_value=[]):
            count, err, backend = th_module.scrape_threads_profile(self.db, account, max_posts=10)

        self.assertEqual(count, 0)
        self.assertIn("no usable Threads profile", err)


if __name__ == "__main__":
    unittest.main()

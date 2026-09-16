import os
import unittest
import uuid

from unittest.mock import patch

from fastapi.testclient import TestClient

import src.scrapers.instagram as ig_module
from src.server import app, get_db


class TestChatActionAuthorization(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        os.environ.pop("CHAT_ACTION_API_KEY", None)

    def tearDown(self):
        os.environ.pop("CHAT_ACTION_API_KEY", None)

    def test_chat_open_when_no_key_configured(self):
        res = self.client.post("/chat", json={"message": "riset topik pendirian PT"})
        self.assertEqual(res.status_code, 200)

    def test_chat_rejects_missing_key_when_configured(self):
        os.environ["CHAT_ACTION_API_KEY"] = "internal-secret"
        res = self.client.post("/chat", json={"message": "riset topik pendirian PT"})
        self.assertEqual(res.status_code, 401)

    def test_chat_rejects_wrong_key(self):
        os.environ["CHAT_ACTION_API_KEY"] = "internal-secret"
        res = self.client.post(
            "/chat", json={"message": "riset topik pendirian PT"},
            headers={"X-API-Key": "wrong"},
        )
        self.assertEqual(res.status_code, 401)

    def test_chat_accepts_bearer_key(self):
        os.environ["CHAT_ACTION_API_KEY"] = "internal-secret"
        res = self.client.post(
            "/chat", json={"message": "riset topik pendirian PT"},
            headers={"Authorization": "Bearer internal-secret"},
        )
        self.assertEqual(res.status_code, 200)

    def test_unauthorized_request_never_reaches_planner_or_scraper(self):
        os.environ["CHAT_ACTION_API_KEY"] = "internal-secret"
        with patch.object(ig_module, "scrape_instagram_profile") as mock_scrape:
            res = self.client.post(
                "/chat", json={"message": "Scrape @should_not_run_e2e"},
            )
        self.assertEqual(res.status_code, 401)
        mock_scrape.assert_not_called()


class TestChatActionEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        os.environ.pop("CHAT_ACTION_API_KEY", None)
        # DATABASE_PATH defaults to a real file shared by the whole process (see
        # src/server.py get_db()), so fixed usernames would collide with leftover rows
        # from earlier test runs. A per-test unique suffix keeps this isolated.
        self.suffix = uuid.uuid4().hex[:8]

    def test_scrape_command_through_chat_endpoint_persists_posts(self):
        username = f"e2e_scrape_target_{self.suffix}"
        with patch.object(ig_module, "scrape_instagram_profile", return_value=(2, None, "apify")) as mock_scrape:
            res = self.client.post(
                "/chat", json={"message": f"Cari 15 post terbaru @{username}"},
            )
        self.assertEqual(res.status_code, 200)
        mock_scrape.assert_called_once()
        data = res.json()
        self.assertEqual(len(data["action_receipts"]), 1)
        self.assertTrue(data["action_receipts"][0]["success"])

        db = get_db()
        acc = db.get_account_by_username("instagram", username)
        self.assertIsNotNone(acc)
        self.assertFalse(acc.monitoring_enabled)

    def test_replace_monitored_account_through_chat_endpoint(self):
        old_username = f"e2e_old_brand_{self.suffix}"
        new_username = f"e2e_new_brand_{self.suffix}"
        db = get_db()
        from src.models import Account
        old = db.upsert_account(Account.create(platform="instagram", username=old_username, monitoring_enabled=True))

        with patch.object(ig_module, "scrape_instagram_profile", return_value=(1, None, "apify")) as mock_scrape:
            res = self.client.post(
                "/chat",
                json={"message": f"Ganti akun @{old_username} menjadi @{new_username}"},
            )

        self.assertEqual(res.status_code, 200)
        mock_scrape.assert_called_once()
        renamed = db.get_account(old.id)
        self.assertEqual(renamed.username, new_username)
        self.assertTrue(renamed.monitoring_enabled)

    def test_ambiguous_bug_report_message_does_not_mutate_or_misfire(self):
        target_username = f"e2e_should_not_exist_{self.suffix}"
        db = get_db()
        before_count = len(db.list_accounts())
        with patch.object(ig_module, "scrape_instagram_profile") as mock_scrape:
            res = self.client.post(
                "/chat", json={"message": f"rekam datanya ganti jadi {target_username}"},
            )
        self.assertEqual(res.status_code, 200)
        mock_scrape.assert_not_called()
        self.assertEqual(len(db.list_accounts()), before_count)
        self.assertIsNone(db.get_account_by_username("instagram", target_username))


if __name__ == "__main__":
    unittest.main()

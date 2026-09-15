import os
import unittest

from fastapi.testclient import TestClient

import src.server as server
from src.server import app


class TestSecurity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        # Ensure a clean slate between tests regardless of ordering.
        os.environ.pop("API_SECRET_KEY", None)
        os.environ.pop("CHAT_RATE_LIMIT_PER_MINUTE", None)

    def tearDown(self):
        os.environ.pop("API_SECRET_KEY", None)
        os.environ.pop("CHAT_RATE_LIMIT_PER_MINUTE", None)

    def test_write_endpoint_open_when_no_secret_configured(self):
        res = self.client.post(
            "/accounts",
            json={"platform": "instagram", "username": "sec_test_open", "is_own_brand": False},
        )
        self.assertEqual(res.status_code, 200)

    def test_write_endpoint_rejects_missing_or_wrong_key(self):
        os.environ["API_SECRET_KEY"] = "secret-abc"
        payload = {"platform": "instagram", "username": "sec_test_locked", "is_own_brand": False}

        res_missing = self.client.post("/accounts", json=payload)
        self.assertEqual(res_missing.status_code, 401)

        res_wrong = self.client.post("/accounts", json=payload, headers={"X-API-Key": "nope"})
        self.assertEqual(res_wrong.status_code, 401)

        res_ok = self.client.post("/accounts", json=payload, headers={"X-API-Key": "secret-abc"})
        self.assertEqual(res_ok.status_code, 200)

    def test_write_endpoint_accepts_bearer_authorization_header(self):
        os.environ["API_SECRET_KEY"] = "secret-abc"
        payload = {"platform": "tiktok", "username": "sec_test_bearer", "is_own_brand": False}
        res = self.client.post(
            "/accounts", json=payload, headers={"Authorization": "Bearer secret-abc"}
        )
        self.assertEqual(res.status_code, 200)

    def test_read_endpoints_stay_open_regardless_of_secret(self):
        os.environ["API_SECRET_KEY"] = "secret-abc"
        res = self.client.get("/accounts")

    def test_chat_rate_limit_blocks_after_threshold(self):
        os.environ["CHAT_RATE_LIMIT_PER_MINUTE"] = "3"
        # Other tests across the suite share this process-wide client IP bucket;
        # reset it so this test is deterministic regardless of execution order.
        server._chat_request_log.clear()
        statuses = []
        for _ in range(4):
            res = self.client.post("/chat", json={"message": "riset topik pendirian PT"})
            statuses.append(res.status_code)
        self.assertEqual(statuses[:3], [200, 200, 200])
        self.assertEqual(statuses[3], 429)


if __name__ == "__main__":
    unittest.main()

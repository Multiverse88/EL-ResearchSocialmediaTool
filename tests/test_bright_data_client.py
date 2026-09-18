import os
import unittest
from unittest.mock import MagicMock, patch

from src.scrapers.bright_data_client import (
    BrightDataError,
    run_dataset,
    search_instagram_urls,
)


class TestBrightDataClient(unittest.TestCase):
    def setUp(self):
        os.environ["BRIGHT_DATA_API_TOKEN"] = "test-token"
        os.environ["BRIGHT_DATA_SERP_ZONE"] = "test-serp-zone"

    def tearDown(self):
        os.environ.pop("BRIGHT_DATA_API_TOKEN", None)
        os.environ.pop("BRIGHT_DATA_SERP_ZONE", None)

    def _client(self, responses):
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        if responses.get("post"):
            client.post.side_effect = responses["post"]
        if responses.get("get"):
            client.get.side_effect = responses["get"]
        return client

    @staticmethod
    def _response(status, payload, headers=None, text=""):
        response = MagicMock()
        response.status_code = status
        response.json.return_value = payload
        response.headers = headers or {}
        response.text = text
        return response

    def test_run_dataset_returns_immediate_records(self):
        response = self._response(200, [{"post_id": "1"}])
        client = self._client({"post": [response]})

        with patch("src.scrapers.bright_data_client.httpx.Client", return_value=client):
            records = run_dataset("dataset", [{"url": "https://example.test"}])

        self.assertEqual(records, [{"post_id": "1"}])
        request = client.post.call_args
        self.assertEqual(request.kwargs["params"]["dataset_id"], "dataset")
        self.assertEqual(request.kwargs["json"], {"input": [{"url": "https://example.test"}]})
        self.assertEqual(request.kwargs["headers"]["Authorization"], "Bearer test-token")

    def test_run_dataset_accepts_single_record_response(self):
        response = self._response(200, {"post_id": "single"})
        client = self._client({"post": [response]})

        with patch("src.scrapers.bright_data_client.httpx.Client", return_value=client):
            records = run_dataset("dataset", [{"url": "https://example.test"}])

        self.assertEqual(records, [{"post_id": "single"}])

    def test_run_dataset_waits_for_async_snapshot(self):
        trigger = self._response(200, {"snapshot_id": "sd_1"})
        running = self._response(200, {"status": "running"})
        ready = self._response(200, {"status": "ready"})
        result = self._response(200, [{"post_id": "2"}])
        client = self._client({"post": [trigger], "get": [running, ready, result]})

        with patch("src.scrapers.bright_data_client.httpx.Client", return_value=client), \
             patch("src.scrapers.bright_data_client.time.sleep"):
            records = run_dataset(
                "dataset",
                [{"search_keyword": "izin usaha"}],
                timeout=10,
                poll_interval=0.01,
            )

        self.assertEqual(records, [{"post_id": "2"}])
        self.assertIn("/datasets/v3/scrape", client.post.call_args.args[0])
        self.assertEqual(client.get.call_count, 3)
        self.assertIn("/progress/sd_1", client.get.call_args_list[0].args[0])
        self.assertIn("/snapshot/sd_1", client.get.call_args_list[2].args[0])

    def test_authentication_error_is_actionable_and_does_not_expose_token(self):
        response = self._response(401, {"message": "bad credentials"})
        client = self._client({"post": [response]})

        with patch("src.scrapers.bright_data_client.httpx.Client", return_value=client):
            with self.assertRaises(BrightDataError) as context:
                run_dataset("dataset", [{"url": "https://example.test"}])

        message = str(context.exception)
        self.assertIn("BRIGHT_DATA_API_TOKEN", message)
        self.assertNotIn("test-token", message)

    def test_rate_limit_retries_once_after_retry_after(self):
        limited = self._response(429, {"message": "slow down"}, headers={"Retry-After": "0"})
        success = self._response(200, [{"post_id": "3"}])
        client = self._client({"post": [limited, success]})

        with patch("src.scrapers.bright_data_client.httpx.Client", return_value=client), \
             patch("src.scrapers.bright_data_client.time.sleep") as sleep:
            records = run_dataset("dataset", [{"url": "https://example.test"}])

        self.assertEqual(records, [{"post_id": "3"}])
        self.assertEqual(client.post.call_count, 2)
        sleep.assert_called_once_with(0.0)

    def test_failed_snapshot_is_not_reported_as_empty_data(self):
        trigger = self._response(200, {"snapshot_id": "sd_failed"})
        failed = self._response(200, {"status": "failed", "error": "collector unavailable"})
        client = self._client({"post": [trigger], "get": [failed]})

        with patch("src.scrapers.bright_data_client.httpx.Client", return_value=client):
            with self.assertRaisesRegex(BrightDataError, "collector unavailable"):
                run_dataset("dataset", [{"url": "https://example.test"}])

    def test_serp_returns_only_organic_links_up_to_limit(self):
        response = self._response(
            200,
            {
                "organic": [
                    {"link": "https://www.instagram.com/p/one/"},
                    {"link": "https://www.instagram.com/reel/two/"},
                    {"link": "https://example.com/three"},
                ]
            },
        )
        client = self._client({"post": [response]})

        with patch("src.scrapers.bright_data_client.httpx.Client", return_value=client):
            links = search_instagram_urls('site:instagram.com "izin usaha"', limit=2)

        self.assertEqual(
            links,
            [
                "https://www.instagram.com/p/one/",
                "https://www.instagram.com/reel/two/",
            ],
        )
        payload = client.post.call_args.kwargs["json"]
        self.assertEqual(payload["zone"], "test-serp-zone")
        self.assertIn("google.com/search?", payload["url"])


if __name__ == "__main__":
    unittest.main()

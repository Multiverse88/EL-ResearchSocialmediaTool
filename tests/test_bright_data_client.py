import os
import unittest
from unittest.mock import MagicMock, patch

from src.scrapers.bright_data_client import (
    BrightDataError,
    get_bright_data_tokens,
    get_bright_data_serp_zones,
    _get_rotating_tokens,
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



class TestBrightDataMultiToken(unittest.TestCase):
    def setUp(self):
        os.environ["BRIGHT_DATA_API_TOKEN"] = "token1, token2, token3"
        os.environ["BRIGHT_DATA_SERP_ZONE"] = "zone1, zone2"

    def tearDown(self):
        os.environ.pop("BRIGHT_DATA_API_TOKEN", None)
        os.environ.pop("BRIGHT_DATA_SERP_ZONE", None)

    @staticmethod
    def _response(status, payload):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = payload
        resp.headers = {}
        resp.text = ""
        return resp

    def test_parses_comma_separated_tokens_and_zones(self):
        tokens = get_bright_data_tokens()
        self.assertEqual(tokens, ["token1", "token2", "token3"])
        zones = get_bright_data_serp_zones()
        self.assertEqual(zones, ["zone1", "zone2"])

    def test_run_dataset_rotates_and_distributes_across_tokens(self):
        success_resp = self._response(200, [{"id": "1"}])
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False
        client.post.return_value = success_resp

        used_auth_headers = []

        def record_post(*args, **kwargs):
            used_auth_headers.append(kwargs.get("headers", {}).get("Authorization"))
            return success_resp

        client.post.side_effect = record_post

        with patch("src.scrapers.bright_data_client.httpx.Client", return_value=client):
            for _ in range(3):
                run_dataset("dataset", [{"url": "https://example.com"}])

        # Verify that all 3 tokens were used across 3 calls
        used_tokens = [h.replace("Bearer ", "") for h in used_auth_headers]
        self.assertEqual(set(used_tokens), {"token1", "token2", "token3"})

    def test_run_dataset_fails_over_to_next_token_when_first_fails(self):
        fail_resp = self._response(429, {"message": "rate limit exceeded"})
        success_resp = self._response(200, [{"id": "recovered"}])

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        auth_attempts = []

        def side_effect(*args, **kwargs):
            auth = kwargs.get("headers", {}).get("Authorization", "").replace("Bearer ", "")
            auth_attempts.append(auth)
            # Fail all calls made with the initial token
            if auth == auth_attempts[0]:
                return fail_resp
            return success_resp

        client.post.side_effect = side_effect

        with patch("src.scrapers.bright_data_client.httpx.Client", return_value=client), \
             patch("src.scrapers.bright_data_client.time.sleep"):
            records = run_dataset("dataset", [{"url": "https://example.com"}])

        self.assertEqual(records, [{"id": "recovered"}])
        # Must have rotated to a different token
        distinct_tokens = list(dict.fromkeys(auth_attempts))
        self.assertGreaterEqual(len(distinct_tokens), 2)
        self.assertNotEqual(distinct_tokens[0], distinct_tokens[1])
    def test_search_instagram_urls_fails_over_to_next_token_and_zone(self):
        fail_resp = self._response(500, {"message": "server error"})
        success_resp = self._response(200, {"organic": [{"link": "https://www.instagram.com/p/abc/"}]})

        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = False

        call_pairs = []

        def side_effect(*args, **kwargs):
            auth = kwargs.get("headers", {}).get("Authorization", "").replace("Bearer ", "")
            zone = kwargs.get("json", {}).get("zone", "")
            call_pairs.append((auth, zone))
            if len(call_pairs) == 1:
                return fail_resp
            return success_resp

        client.post.side_effect = side_effect

        with patch("src.scrapers.bright_data_client.httpx.Client", return_value=client):
            links = search_instagram_urls('site:instagram.com "test"', limit=1)

        self.assertEqual(links, ["https://www.instagram.com/p/abc/"])
        self.assertGreaterEqual(len(call_pairs), 2)
        self.assertNotEqual(call_pairs[0][0], call_pairs[1][0])

if __name__ == "__main__":
    unittest.main()

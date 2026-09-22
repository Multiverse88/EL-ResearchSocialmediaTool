import unittest
import threading
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.claude_client import ClaudeChatHandler
from src.db import Database
from src.server import app
from src.chat_actions import ActionExecutionResult, ActionReceipt
from src.models import Account, Post


class TestChatActionOrchestrationNeverCrashesChat(unittest.TestCase):
    """Regression test for a real production incident: an exception raised anywhere
    inside chat-action orchestration (deterministic parser, AI planner, or executor —
    e.g. a SQLite lock, an unexpected provider response shape) propagated uncaught through
    process_chat/stream_chat, turning the entire /chat endpoint into a raw HTTP 500 even
    for ordinary research questions that requested no action at all."""

    def setUp(self):
        self.db = Database(":memory:")
        self.handler = ClaudeChatHandler(api_key="")

    def tearDown(self):
        self.db.close()

    def test_process_chat_degrades_gracefully_when_orchestration_raises(self):
        with patch(
            "src.chat_actions.ChatActionOrchestrator.plan_and_execute",
            side_effect=RuntimeError("simulated database is locked"),
        ):
            result = self.handler.process_chat(self.db, "riset topik pendirian PT", [])

        self.assertEqual(result["status"], "success")
        self.assertTrue(result["reply"])
        self.assertEqual(result["action_receipts"], [])

    def test_stream_chat_degrades_gracefully_when_orchestration_raises(self):
        with patch(
            "src.chat_actions.ChatActionOrchestrator.plan_and_execute",
            side_effect=RuntimeError("simulated database is locked"),
        ):
            chunks = list(self.handler.stream_chat(self.db, "riset topik pendirian PT", []))

        self.assertTrue(any(c.get("type") == "content" and c.get("text") for c in chunks))

    def test_stream_chat_yields_loading_before_action_execution_finishes(self):
        action_started = threading.Event()
        release_action = threading.Event()
        first_chunk_ready = threading.Event()
        chunks = []

        def blocking_action(*args, **kwargs):
            action_started.set()
            release_action.wait(timeout=2)
            return ActionExecutionResult()

        stream = self.handler.stream_chat(self.db, "coba scrape ulang @id.easylegal", [])

        def consume_first_chunk():
            chunks.append(next(stream))
            first_chunk_ready.set()

        consumer = threading.Thread(target=consume_first_chunk)
        with patch.object(self.handler, "_run_chat_actions", side_effect=blocking_action):
            consumer.start()
            self.assertTrue(action_started.wait(timeout=1))
            arrived_before_action_finished = first_chunk_ready.wait(timeout=0.2)
            release_action.set()
            consumer.join(timeout=2)

        self.assertTrue(arrived_before_action_finished)
        self.assertEqual(chunks[0]["type"], "reasoning")
        self.assertIn("Menyiapkan", chunks[0]["text"])


class TestChatEndpointNeverReturns500(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_chat_endpoint_returns_200_even_if_process_chat_raises(self):
        with patch(
            "src.server.ClaudeChatHandler.process_chat",
            side_effect=RuntimeError("simulated unexpected failure"),
        ):
            res = self.client.post("/chat", json={"message": "riset topik pendirian PT"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("reply", res.json())

    def test_buffered_completions_endpoint_returns_200_even_if_process_chat_raises(self):
        with patch(
            "src.server.ClaudeChatHandler.process_chat",
            side_effect=RuntimeError("simulated unexpected failure"),
        ):
            res = self.client.post(
                "/v1/chat/completions",
                json={"message": "riset topik pendirian PT", "stream": False},
            )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["choices"][0]["message"]["content"])


class TestActionReceiptsSurviveDegradedProviderPaths(unittest.TestCase):
    """Spec §7: action receipts must survive a router failure, an empty stream, a
    malformed chunk, and a deterministic fallback -- an action that already executed
    (e.g. a scrape) must never be silently dropped just because the answering
    provider degraded afterward."""

    def setUp(self):
        self.db = Database(":memory:")
        self.acc = self.db.upsert_account(Account.create(platform="instagram", username="id.easylegal", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=self.acc.id, platform_post_id="1", caption="tips legalitas",
                        media_url="", likes=10, comments=1, views=None, platform="instagram"),
        ])

    def tearDown(self):
        self.db.close()

    def _action_result(self):
        receipt = ActionReceipt(
            action_type="scrape_profile", platform="instagram", target="id.easylegal",
            backend="bright_data", success=True, posts_collected=5, detail="Scraped 5 posts",
        )
        return ActionExecutionResult(receipts=[receipt], matched_account=("instagram", "id.easylegal"), status_lines=["Scraped 5 posts"])

    def test_receipts_survive_router_http_failure(self):
        handler = ClaudeChatHandler(api_key="fake-router-key", base_url="https://router.example/v1")
        with patch.object(handler, "_run_chat_actions", return_value=self._action_result()), \
             patch("httpx.Client") as mock_client_cls:
            mock_client_cls.side_effect = RuntimeError("router unreachable")
            result = handler.process_chat(self.db, "bagaimana performa akun id.easylegal?", [])

        self.assertEqual(result["action_receipts"][0]["target"], "id.easylegal")
        self.assertTrue(result["action_receipts"][0]["success"])
        self.assertTrue(result["reply"])

    def test_receipts_survive_empty_router_stream(self):
        handler = ClaudeChatHandler(api_key="fake-router-key", base_url="https://router.example/v1")

        class _EmptyStreamCtx:
            status_code = 200

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def iter_lines(self_inner):
                return iter(["data: [DONE]"])

        class _EmptyHttpxClient:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def stream(self_inner, method, url, headers=None, json=None):
                return _EmptyStreamCtx()

        with patch.object(handler, "_run_chat_actions", return_value=self._action_result()), \
             patch("httpx.Client", return_value=_EmptyHttpxClient()):
            result = handler.process_chat(self.db, "bagaimana performa akun id.easylegal?", [])

        self.assertEqual(result["action_receipts"][0]["target"], "id.easylegal")
        self.assertTrue(result["reply"])

    def test_receipts_survive_malformed_router_chunk(self):
        handler = ClaudeChatHandler(api_key="fake-router-key", base_url="https://router.example/v1")

        class _MalformedStreamCtx:
            status_code = 200

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def iter_lines(self_inner):
                return iter(["data: {not valid json", "data: [DONE]"])

        class _MalformedHttpxClient:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

            def stream(self_inner, method, url, headers=None, json=None):
                return _MalformedStreamCtx()

        with patch.object(handler, "_run_chat_actions", return_value=self._action_result()), \
             patch("httpx.Client", return_value=_MalformedHttpxClient()):
            result = handler.process_chat(self.db, "bagaimana performa akun id.easylegal?", [])

        self.assertEqual(result["action_receipts"][0]["target"], "id.easylegal")
        self.assertTrue(result["reply"])

    def test_receipts_survive_deterministic_local_fallback(self):
        handler = ClaudeChatHandler(api_key="")
        with patch.object(handler, "_run_chat_actions", return_value=self._action_result()):
            result = handler.process_chat(self.db, "bagaimana performa akun id.easylegal?", [])

        self.assertEqual(result["action_receipts"][0]["target"], "id.easylegal")
        self.assertTrue(result["action_receipts"][0]["success"])
        self.assertTrue(result["reply"])


if __name__ == "__main__":
    unittest.main()

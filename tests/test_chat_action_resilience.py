import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.claude_client import ClaudeChatHandler
from src.db import Database
from src.server import app


class TestChatActionOrchestrationNeverCrashesChat(unittest.TestCase):
    """Regression test for a real production incident: an exception raised anywhere
    inside chat-action orchestration (deterministic parser, AI planner, or executor —
    e.g. a SQLite lock, an unexpected Apify response shape) propagated uncaught through
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


if __name__ == "__main__":
    unittest.main()

import json
import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from src.claude_client import ClaudeChatHandler
from src.chat_actions import ActionExecutionResult, ActionReceipt
from src.db import Database
from src.models import Account, Post
from src.server import app
from src.tools import get_engagement_summary, compare_accounts


class TestExplicitProviderMode(unittest.TestCase):
    """P0 fix 1: provider routing must be resolved once into `self.provider_mode`,
    with env-var override and key/base_url inference only as an unset-env fallback."""

    def test_zero_config_infers_anthropic_from_key_prefix(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHAT_PROVIDER_MODE", None)
            handler = ClaudeChatHandler(api_key="sk-ant-faketestkey")
        self.assertEqual(handler.provider_mode, "anthropic")

    def test_zero_config_infers_openai_compatible_from_non_anthropic_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CHAT_PROVIDER_MODE", None)
            handler = ClaudeChatHandler(api_key="router-key-123", base_url="https://router.example/v1")
        self.assertEqual(handler.provider_mode, "openai_compatible")

    def test_env_var_overrides_key_based_inference(self):
        # A native sk-ant- key would normally infer "anthropic" — the env var must win.
        with patch.dict(os.environ, {"CHAT_PROVIDER_MODE": "openai_compatible"}):
            handler = ClaudeChatHandler(api_key="sk-ant-faketestkey")
        self.assertEqual(handler.provider_mode, "openai_compatible")

    def test_env_var_anthropic_overrides_router_style_key(self):
        with patch.dict(os.environ, {"CHAT_PROVIDER_MODE": "anthropic"}):
            handler = ClaudeChatHandler(api_key="router-key-123", base_url="https://router.example/v1")
        self.assertEqual(handler.provider_mode, "anthropic")

    def test_invalid_env_value_falls_back_to_inference(self):
        with patch.dict(os.environ, {"CHAT_PROVIDER_MODE": "not-a-real-mode"}):
            handler = ClaudeChatHandler(api_key="sk-ant-faketestkey")
        self.assertEqual(handler.provider_mode, "anthropic")

    def test_process_chat_routes_to_router_only_when_provider_mode_is_openai_compatible(self):
        with patch.dict(os.environ, {"CHAT_PROVIDER_MODE": "anthropic"}):
            handler = ClaudeChatHandler(api_key="router-key-123", base_url="https://router.example/v1")
        db = Database(":memory:")
        try:
            with patch.object(handler, "_call_openai_router") as router, \
                 patch.object(handler, "_claude_tool_use_loop") as claude_loop:
                # provider_mode="anthropic" forces the native path even though
                # self.client is None for a non sk-ant- key, exercising the local
                # fallback rather than the router — proving is_openai_router is now
                # driven by provider_mode, not re-derived from the key/base_url.
                handler.process_chat(db, "riset topik pendirian PT", [])
            router.assert_not_called()
        finally:
            db.close()


class TestEngagementMetricRenaming(unittest.TestCase):
    """P0 fix 2: the fabricated (likes+comments)/post_count fallback must be exposed
    as `engagements_per_post` (no "%"), never mislabeled as `engagement_rate`."""

    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_get_topic_summary_engagement_rate_is_none_without_views(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="brandacc", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc.id, platform_post_id="1", caption="topikunik satu",
                        media_url="", likes=100, comments=10, views=None, platform="instagram"),
            Post.create(account_id=acc.id, platform_post_id="2", caption="topikunik dua",
                        media_url="", likes=50, comments=5, views=None, platform="instagram"),
        ])
        summary = self.db.get_topic_summary("topikunik")
        self.assertIsNone(summary["engagement_rate"])
        self.assertEqual(summary["engagements_per_post"], round((150 + 15) / 2, 1))

    def test_get_topic_summary_engagement_rate_is_percent_with_views(self):
        acc = self.db.upsert_account(Account.create(platform="tiktok", username="videoacc", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc.id, platform_post_id="1", caption="topikvideo satu",
                        media_url="", likes=100, comments=10, views=1000, platform="tiktok"),
        ])
        summary = self.db.get_topic_summary("topikvideo")
        self.assertEqual(summary["engagement_rate"], round((110 / 1000) * 100, 2))
        self.assertIn("engagements_per_post", summary)

    def test_get_engagement_summary_tool_distinguishes_percent_from_per_post(self):
        acc_no_views = self.db.upsert_account(Account.create(platform="instagram", username="noviewsacc", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc_no_views.id, platform_post_id="1", caption="post a",
                        media_url="", likes=40, comments=4, views=None, platform="instagram"),
        ])
        res_no_views = get_engagement_summary(self.db, "noviewsacc", "instagram")
        self.assertIsNone(res_no_views["summary"]["engagement_rate"])
        self.assertEqual(res_no_views["summary"]["engagements_per_post"], 44.0)

        acc_views = self.db.upsert_account(Account.create(platform="tiktok", username="viewsacc", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc_views.id, platform_post_id="1", caption="post b",
                        media_url="", likes=40, comments=4, views=440, platform="tiktok"),
        ])
        res_views = get_engagement_summary(self.db, "viewsacc", "tiktok")
        self.assertEqual(res_views["summary"]["engagement_rate"], 10.0)

    def test_compare_accounts_distinguishes_percent_from_per_post(self):
        acc_no_views = self.db.upsert_account(Account.create(platform="instagram", username="noviewscmp", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc_no_views.id, platform_post_id="1", caption="post a",
                        media_url="", likes=20, comments=2, views=None, platform="instagram"),
        ])
        acc_views = self.db.upsert_account(Account.create(platform="tiktok", username="viewscmp", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc_views.id, platform_post_id="1", caption="post b",
                        media_url="", likes=40, comments=4, views=440, platform="tiktok"),
        ])
        comp = compare_accounts(self.db, ["noviewscmp", "viewscmp"])
        by_username = {r["username"]: r for r in comp["accounts"]}
        self.assertIsNone(by_username["noviewscmp"]["engagement_rate"])
        self.assertEqual(by_username["noviewscmp"]["engagements_per_post"], 22.0)
        self.assertEqual(by_username["viewscmp"]["engagement_rate"], 10.0)

    def test_local_fallback_reply_never_appends_percent_without_views(self):
        handler = ClaudeChatHandler(api_key="")
        acc = self.db.upsert_account(Account.create(platform="instagram", username="brandacc2", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc.id, platform_post_id="1", caption="tips pendirian pt yang benar",
                        media_url="", likes=40, comments=4, views=None, platform="instagram"),
        ])
        result = handler.process_chat(self.db, "riset topik pendirian PT dong", [])
        self.assertEqual(result["tool_used"], "research_topic")
        self.assertNotIn("%", result["reply"])
        self.assertIn("interaksi per post", result["reply"])

    def test_local_fallback_reply_appends_percent_with_views(self):
        handler = ClaudeChatHandler(api_key="")
        acc = self.db.upsert_account(Account.create(platform="tiktok", username="videoacc2", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc.id, platform_post_id="1", caption="tips pendirian pt di tiktok",
                        media_url="", likes=100, comments=10, views=1000, platform="tiktok"),
        ])
        result = handler.process_chat(self.db, "riset topik pendirian PT dong", [])
        self.assertEqual(result["tool_used"], "research_topic")
        self.assertIn("Engagement rate: **11.0%**", result["reply"])


class TestUsageNeverFabricated(unittest.TestCase):
    """P0 fix 3: token usage must be real provider-reported numbers or None — never a
    len(text)//4 character-count estimate."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_buffered_endpoint_usage_is_none_without_real_provider_usage(self):
        with patch(
            "src.server.ClaudeChatHandler.process_chat",
            return_value={"status": "success", "reply": "Jawaban singkat tanpa data usage."},
        ):
            res = self.client.post("/v1/chat/completions", json={"message": "halo", "stream": False})
        self.assertEqual(res.status_code, 200)
        self.assertIsNone(res.json()["usage"])

    def test_buffered_endpoint_passes_through_real_usage_untouched(self):
        real_usage = {"prompt_tokens": 512, "completion_tokens": 77, "total_tokens": 589}
        with patch(
            "src.server.ClaudeChatHandler.process_chat",
            return_value={"status": "success", "reply": "Jawaban dengan usage asli.", "usage": real_usage},
        ):
            res = self.client.post("/v1/chat/completions", json={"message": "halo", "stream": False})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["usage"], real_usage)

    def test_call_openai_router_threads_real_usage_from_router_response(self):
        handler = ClaudeChatHandler(api_key="fake-router-key", base_url="https://router.example/v1")
        db = Database(":memory:")
        try:
            lines = [
                'data: {"choices":[{"delta":{"content":"Halo dunia"}}]}',
                'data: {"choices":[],"usage":{"prompt_tokens":120,"completion_tokens":40,"total_tokens":160}}',
                'data: [DONE]',
            ]

            class _FakeStreamCtx:
                status_code = 200

                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

                def iter_lines(self_inner):
                    return iter(lines)

            class _FakeHttpxClient:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

                def stream(self_inner, method, url, headers=None, json=None):
                    return _FakeStreamCtx()

            action_result = ActionExecutionResult(matched_topic="pendirian pt")
            with patch("httpx.Client", return_value=_FakeHttpxClient()):
                result = handler._call_openai_router(db, "riset topik pendirian PT", [], action_result=action_result)
        finally:
            db.close()

        self.assertEqual(result["usage"], {"prompt_tokens": 120, "completion_tokens": 40, "total_tokens": 160})

    def test_claude_tool_use_loop_reports_real_anthropic_usage(self):
        handler = ClaudeChatHandler(api_key="sk-ant-faketestkey")
        db = Database(":memory:")
        try:
            text_block = MagicMock(type="text", text="Jawaban dari Claude native.")
            fake_response = MagicMock()
            fake_response.stop_reason = "end_turn"
            fake_response.content = [text_block]
            fake_response.usage = MagicMock(input_tokens=250, output_tokens=80)

            with patch.object(handler.client, "messages") as messages_mock:
                messages_mock.create.return_value = fake_response
                result = handler._claude_tool_use_loop(db, "halo, apa kabar", [])
        finally:
            db.close()

        self.assertEqual(
            result["usage"],
            {"prompt_tokens": 250, "completion_tokens": 80, "total_tokens": 330},
        )


class TestCompetitorAnalysisUnifiedAcrossProviders(unittest.TestCase):
    """P0 fix 4: the native Anthropic tool-use path must short-circuit competitor
    questions to the same deterministic template as the router paths, instead of
    letting the model free-form them via tool calls."""

    def setUp(self):
        self.db = Database(":memory:")
        self.handler = ClaudeChatHandler(api_key="sk-ant-faketestkey")
        self.own = self.db.upsert_account(Account.create(platform="instagram", username="id.easylegal", is_own_brand=True))
        self.competitor = self.db.upsert_account(Account.create(platform="instagram", username="smartlegalid", is_own_brand=False))
        self.db.upsert_posts([
            Post.create(account_id=self.own.id, platform_post_id="own-1", caption="Cara mudah bikin PT",
                        media_url="", likes=50, comments=5, views=1000, platform="instagram"),
            Post.create(account_id=self.competitor.id, platform_post_id="comp-1",
                        caption="Jangan tunggu izin usaha bermasalah sebelum cek dokumen ini",
                        media_url="", likes=450, comments=35, views=12000, platform="instagram",
                        post_url="https://instagram.com/p/competitor-viral"),
        ])

    def tearDown(self):
        self.db.close()

    def test_native_claude_path_never_calls_the_model_for_competitor_questions(self):
        message = "apa konten kompetitor id.easylegal yang views nya besar dan bisa diamati tiru dan dimodifikasi"
        with patch.object(self.handler.client, "messages") as messages_mock:
            result = self.handler._claude_tool_use_loop(self.db, message, [])
        messages_mock.create.assert_not_called()
        self.assertEqual(result["tool_used"], "competitor_analysis")
        self.assertEqual(result["tools_used"], ["competitor_analysis"])
        self.assertIn("**AMATI**", result["reply"])
        self.assertIn("**TIRU**", result["reply"])

    def test_native_claude_path_matches_router_fallback_template(self):
        message = "apa konten kompetitor id.easylegal yang views nya besar dan bisa diamati tiru dan dimodifikasi"
        with patch.object(self.handler.client, "messages"):
            native_result = self.handler._claude_tool_use_loop(self.db, message, [])
        expected = self.handler._build_competitor_analysis_fallback_result(self.db, message, [])
        self.assertEqual(native_result["reply"], expected["reply"])


class TestActionReceiptsPreservedOnEveryStreamingPath(unittest.TestCase):
    """P0 fix 5: action receipts must be merged into the result on the native Anthropic
    streaming path too, not just the buffered/router paths."""

    def setUp(self):
        self.db = Database(":memory:")
        self.handler = ClaudeChatHandler(api_key="sk-ant-faketestkey")

    def tearDown(self):
        self.db.close()

    def test_stream_chat_merges_action_context_on_native_claude_success_path(self):
        receipt = ActionReceipt(
            action_type="scrape_profile", platform="instagram", target="id.easylegal",
            backend="bright_data", success=True, posts_collected=5, detail="Scraped 5 posts",
        )
        action_result = ActionExecutionResult(receipts=[receipt], status_lines=["Scraped 5 posts"])

        captured = {}
        real_merge = self.handler._merge_action_context

        def spy_merge(result, ar, already_grounded=False):
            merged = real_merge(result, ar, already_grounded=already_grounded)
            captured["result"] = merged
            captured["already_grounded"] = already_grounded
            return merged

        with patch.object(self.handler, "_run_chat_actions", return_value=action_result), \
             patch.object(self.handler, "_claude_tool_use_loop",
                           return_value={"status": "success", "reply": "Jawaban asli dari Claude.",
                                         "tool_used": None, "tools_used": [], "tool_results": []}), \
             patch.object(self.handler, "_merge_action_context", side_effect=spy_merge) as merge_spy:
            chunks = list(self.handler.stream_chat(self.db, "bagaimana performa akun id.easylegal?", []))

        merge_spy.assert_called_once()
        self.assertTrue(captured["already_grounded"])
        self.assertEqual(captured["result"]["action_receipts"][0]["target"], "id.easylegal")
        content_text = "".join(c["text"] for c in chunks if c.get("type") == "content")
        self.assertIn("Jawaban asli dari Claude.", content_text)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch
from src.claude_client import ClaudeChatHandler
from src.db import Database
from src.models import Account, Post


class TestTopicAccountBreakdown(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_ranks_accounts_by_post_count_then_avg_likes(self):
        acc_a = self.db.upsert_account(Account.create(platform="instagram", username="akun_a", is_own_brand=False))
        acc_b = self.db.upsert_account(Account.create(platform="tiktok", username="akun_b", is_own_brand=False))
        self.db.upsert_posts([
            Post.create(account_id=acc_a.id, platform_post_id="1", caption="sewa virtual office jakarta",
                        media_url="", likes=100, comments=1, views=None, platform="instagram",
                        topic="sewa virtual office"),
            Post.create(account_id=acc_a.id, platform_post_id="2", caption="sewa virtual office murah",
                        media_url="", likes=200, comments=2, views=None, platform="instagram",
                        topic="sewa virtual office"),
            Post.create(account_id=acc_b.id, platform_post_id="1", caption="sewa virtual office review",
                        media_url="", likes=50, comments=1, views=1000, platform="tiktok",
                        topic="sewa virtual office"),
        ])
        breakdown = self.db.get_topic_account_breakdown("sewa virtual office")
        self.assertEqual(len(breakdown), 2)
        self.assertEqual(breakdown[0]["username"], "akun_a")
        self.assertEqual(breakdown[0]["post_count"], 2)
        self.assertEqual(breakdown[0]["avg_likes"], 150.0)
        self.assertEqual(breakdown[1]["username"], "akun_b")
        self.assertEqual(breakdown[1]["post_count"], 1)

    def test_returns_empty_list_when_no_posts_match_topic(self):
        self.assertEqual(self.db.get_topic_account_breakdown("topik tidak ada"), [])

    def test_filters_by_platform(self):
        acc_a = self.db.upsert_account(Account.create(platform="instagram", username="akun_a", is_own_brand=False))
        acc_b = self.db.upsert_account(Account.create(platform="tiktok", username="akun_b", is_own_brand=False))
        self.db.upsert_posts([
            Post.create(account_id=acc_a.id, platform_post_id="1", caption="izin usaha oss",
                        media_url="", likes=10, comments=0, views=None, platform="instagram",
                        topic="izin usaha"),
            Post.create(account_id=acc_b.id, platform_post_id="1", caption="izin usaha oss",
                        media_url="", likes=10, comments=0, views=100, platform="tiktok",
                        topic="izin usaha"),
        ])
        breakdown = self.db.get_topic_account_breakdown("izin usaha", platform="tiktok")
        self.assertEqual(len(breakdown), 1)
        self.assertEqual(breakdown[0]["username"], "akun_b")


class TestBuildRouterContextEnrichment(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.handler = ClaudeChatHandler(api_key="fake-router-key", base_url="https://router.example/v1")
        acc = self.db.upsert_account(Account.create(platform="instagram", username="easylegal_id", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc.id, platform_post_id="1", caption="konsultasi pajak umkm",
                        media_url="", likes=500, comments=10, views=None, platform="instagram",
                        topic="konsultasi pajak"),
        ])

    def tearDown(self):
        self.db.close()

    def test_context_includes_account_breakdown_and_known_gaps(self):
        _, _, _, messages, matched_topic, _ = self.handler._build_router_context(
            self.db, "siapa saja akun yang bahas konsultasi pajak?", [], matched_topic="konsultasi pajak"
        )
        system_content = messages[0]["content"]
        self.assertIn("Akun Paling Aktif Membahas Topik Ini", system_content)
        self.assertIn("easylegal_id", system_content)
        self.assertIn("[KETERBATASAN DATA SAAT INI]", system_content)
        self.assertIn("Breakdown per Platform", system_content)

    def test_system_prompt_forbids_scratchpad_notation(self):
        _, _, _, messages, _, _ = self.handler._build_router_context(
            self.db, "test", [], matched_topic="konsultasi pajak"
        )
        self.assertIn("JANGAN PERNAH memakai notasi internal", messages[0]["content"])


class TestBuildRouterContextAccountMode(unittest.TestCase):
    """Regression tests for a real production incident: a message like 'saya mau riset
    soal akun instagram id.easylegal' got misrouted through topic-keyword matching,
    which extracted the stray word 'soal' from the sentence and silently filtered the
    account's own posts down to only the ones containing that substring. account_ref
    mode grounds the answer on the account's FULL post history instead."""

    def setUp(self):
        self.db = Database(":memory:")
        self.handler = ClaudeChatHandler(api_key="fake-router-key", base_url="https://router.example/v1")
        acc = self.db.upsert_account(Account.create(platform="instagram", username="id.easylegal", is_own_brand=True))
        posts = [
            Post.create(account_id=acc.id, platform_post_id="1", caption="Ditanya calon investor soal ISO",
                        media_url="", likes=25, comments=2, views=174, platform="instagram"),
            Post.create(account_id=acc.id, platform_post_id="2", caption="Aturan baru tarif pengumuman badan hukum",
                        media_url="", likes=29, comments=1, views=None, platform="instagram"),
        ]
        # 28 more posts with captions that do NOT contain "soal" at all — this is the
        # exact class of data that was silently dropped by keyword-matched context.
        for i in range(3, 31):
            posts.append(Post.create(
                account_id=acc.id, platform_post_id=str(i),
                caption=f"Tips legalitas usaha bagian {i}",
                media_url="", likes=10 + i, comments=0, views=None, platform="instagram",
            ))
        self.db.upsert_posts(posts)
        self.acc = acc

    def tearDown(self):
        self.db.close()

    def test_account_mode_reflects_full_post_count_not_keyword_filtered(self):
        _, _, _, messages, _, subject_data = self.handler._build_router_context(
            self.db, "saya mau riset soal akun instagram id.easylegal", [],
            account_ref=("instagram", "id.easylegal"),
        )
        system_content = messages[0]["content"]
        # All 30 posts must be reflected in the summary total, not just the 2 whose
        # captions happen to contain "soal".
        self.assertEqual(subject_data["total_posts"], 30)
        self.assertIn("Total Postingan Tersimpan (SEMUA post akun ini, TANPA filter kata kunci apa pun): 30 post", system_content)

    def test_account_mode_does_not_invoke_topic_keyword_resolution(self):
        with patch.object(self.handler, "_resolve_matched_topic") as mock_resolve, \
             patch.object(self.handler, "_ensure_topic_freshness") as mock_freshness:
            self.handler._build_router_context(
                self.db, "saya mau riset soal akun instagram id.easylegal", [],
                account_ref=("instagram", "id.easylegal"),
            )
        mock_resolve.assert_not_called()
        mock_freshness.assert_not_called()

    def test_account_mode_missing_account_reports_no_data_honestly(self):
        _, _, _, messages, _, _ = self.handler._build_router_context(
            self.db, "riset akun instagram belum_ada_di_db", [],
            account_ref=("instagram", "belum_ada_di_db"),
        )
        system_content = messages[0]["content"]
        self.assertIn("belum memiliki data tersimpan", system_content)


if __name__ == "__main__":
    unittest.main()

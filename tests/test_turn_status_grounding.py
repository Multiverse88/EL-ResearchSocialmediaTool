import unittest

from src.claude_client import ClaudeChatHandler
from src.db import Database
from src.models import Account, Post


class TestTurnStatusReflectsGroundingCompleteness(unittest.TestCase):
    """Blocking review fix (spec §3): `TurnResult.status` must be `partial` whenever the
    evidence set is empty or a cited `EvidenceRecord.missing_fields` shows the answer's
    own evidence is materially incomplete -- not just on stale/refresh_failed freshness
    or provider degradation. The globally-unavailable `reach`/`impressions` metrics in
    `AnswerConstraints.unavailable_metrics` must never, by themselves, force `partial`
    on an otherwise-complete answer."""

    def setUp(self):
        self.db = Database(":memory:")
        self.handler = ClaudeChatHandler(api_key="")

    def tearDown(self):
        self.db.close()

    def test_no_data_topic_returns_partial_not_success(self):
        turn = self.handler._prepare_turn(self.db, "req-1", "riset topik topik yang belum pernah ada di sistem", [], None)
        self.assertEqual(turn.evidence, [])
        result = self.handler._run_turn(self.db, turn)
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.grounding.evidence_count, 0)

    def test_missing_metric_on_cited_evidence_returns_partial(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="id.easylegal", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc.id, platform_post_id="1", caption="izin usaha tanpa views",
                        media_url="", likes=40, comments=4, views=None, platform="instagram",
                        topic="izin usaha"),
        ])
        turn = self.handler._prepare_turn(self.db, "req-1", "riset topik izin usaha", [], None)
        post_evidence = [e for e in turn.evidence if e.source_kind == "post"]
        self.assertTrue(any(e.missing_fields for e in post_evidence))

        result = self.handler._run_turn(self.db, turn)
        self.assertEqual(result.status, "partial")

    def test_fully_complete_evidence_returns_success_despite_globally_unavailable_reach(self):
        acc = self.db.upsert_account(Account.create(platform="tiktok", username="id.easylegal", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc.id, platform_post_id="1", caption="izin usaha lengkap dengan views",
                        media_url="", likes=100, comments=10, views=1000, platform="tiktok",
                        topic="izin usaha"),
        ])
        turn = self.handler._prepare_turn(self.db, "req-1", "riset topik izin usaha", [], None)
        post_evidence = [e for e in turn.evidence if e.source_kind == "post"]
        self.assertTrue(post_evidence)
        self.assertEqual(post_evidence[0].missing_fields, [])
        # Reach/impressions are always globally unavailable -- that alone must never
        # force this otherwise-complete answer to "partial".
        self.assertIn("reach", turn.constraints.unavailable_metrics)
        self.assertIn("impressions", turn.constraints.unavailable_metrics)

        result = self.handler._run_turn(self.db, turn)
        self.assertEqual(result.status, "success")

    def test_unsupported_dropped_claim_marks_partial(self):
        acc = self.db.upsert_account(Account.create(platform="instagram", username="id.easylegal", is_own_brand=True))
        self.db.upsert_posts([
            Post.create(account_id=acc.id, platform_post_id="1", caption="izin usaha",
                        media_url="", likes=40, comments=4, views=100, platform="instagram",
                        topic="izin usaha"),
        ])
        turn = self.handler._prepare_turn(self.db, "req-1", "riset topik izin usaha", [], None)
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(
            "Post ini punya 99999 shares yang fantastis.", turn.evidence,
        )
        self.assertEqual(unsupported, 1)


if __name__ == "__main__":
    unittest.main()

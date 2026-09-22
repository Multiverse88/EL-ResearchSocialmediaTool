import dataclasses
import unittest

from src.models import (
    AnswerConstraints,
    ChatMessage,
    Citation,
    EvidenceRecord,
    FreshnessInfo,
    GroundingInfo,
    PreparedTurn,
    ProviderAndModel,
    Subject,
    ToolCallRecord,
    TurnResult,
    UsageInfo,
)


class TestGroundedTurnContracts(unittest.TestCase):
    @staticmethod
    def _field_names(cls):
        return [f.name for f in dataclasses.fields(cls)]

    def test_prepared_turn_is_immutable_and_preserves_typed_evidence(self):
        subject = Subject("topic", ["izin usaha"], 0.9, "classifier")
        freshness = FreshnessInfo("fresh", "2026-09-22T00:00:00+00:00", 24, None)
        evidence = EvidenceRecord(
            source_id="post:42",
            source_kind="post",
            platform="instagram",
            account="easylegal_id",
            topic="izin usaha",
            metrics={"likes": 40, "comments": 3, "views": None},
            posted_at="2026-09-01T00:00:00+00:00",
            scraped_at="2026-09-22T00:00:00+00:00",
            url="https://instagram.test/p/42",
            missing_fields=["views"],
        )
        turn = PreparedTurn(
            request_id="req-1",
            query="Riset izin usaha",
            history=[ChatMessage(role="user", content="Riset izin usaha")],
            subject=subject,
            action_result=None,
            freshness=freshness,
            evidence=[evidence],
            constraints=AnswerConstraints(["impressions"], ["missing follower counts"]),
        )

        self.assertTrue(dataclasses.is_dataclass(turn))
        self.assertTrue(PreparedTurn.__dataclass_params__.frozen)
        self.assertEqual(turn.evidence[0].metrics["views"], None)
        self.assertEqual(turn.evidence[0].missing_fields, ["views"])
        self.assertEqual(turn.evidence[0].posted_at, "2026-09-01T00:00:00+00:00")
        self.assertEqual(turn.evidence[0].scraped_at, "2026-09-22T00:00:00+00:00")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            turn.query = "changed"  # type: ignore[misc]

    def test_turn_result_models_citations_grounding_and_real_usage(self):
        subject = Subject("account", ["easylegal_id"], 1.0, "explicit")
        result = TurnResult(
            status="partial",
            reply="40 likes [post:42]",
            provider_and_model=ProviderAndModel("openai_compatible", "Thinking"),
            subject=subject,
            citations=[Citation("post:42", "https://instagram.test/p/42")],
            tool_calls=[ToolCallRecord("research_topic", {"keyword": "izin usaha"}, "success", 1, 5.5)],
            action_receipts=[{"action_type": "research_topic", "success": True}],
            grounding=GroundingInfo("refresh_failed", None, 1, ["views"], 0),
            fallback_reason="router_unavailable",
            usage=UsageInfo(10, 20, 30),
        )

        self.assertTrue(TurnResult.__dataclass_params__.frozen)
        self.assertEqual(result.provider_and_model.provider, "openai_compatible")
        self.assertEqual(result.citations[0].source_id, "post:42")
        self.assertEqual(result.grounding.unsupported_claim_count, 0)
        self.assertEqual(result.usage.total_tokens, 30)

    def test_contract_field_names_cover_spec_fields(self):
        self.assertEqual(
            self._field_names(PreparedTurn),
            ["request_id", "query", "history", "subject", "action_result", "freshness", "evidence", "constraints"],
        )
        self.assertEqual(
            self._field_names(TurnResult),
            ["status", "reply", "provider_and_model", "subject", "citations", "tool_calls", "action_receipts", "grounding", "fallback_reason", "usage"],
        )
        self.assertEqual(
            self._field_names(EvidenceRecord),
            ["source_id", "source_kind", "platform", "account", "topic", "metrics", "posted_at", "scraped_at", "url", "missing_fields"],
        )
        self.assertEqual(
            self._field_names(GroundingInfo),
            ["freshness_status", "data_as_of", "evidence_count", "missing_fields", "unsupported_claim_count"],
        )


if __name__ == "__main__":
    unittest.main()

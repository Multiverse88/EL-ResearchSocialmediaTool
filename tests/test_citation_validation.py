import unittest

from src.claude_client import ClaudeChatHandler
from src.models import EvidenceRecord


def _post_evidence(source_id="post:real-1", likes=420, comments=31, views=9500, url=None, posted_at=None):
    return EvidenceRecord(
        source_id=source_id,
        source_kind="post",
        platform="instagram",
        account="instagram:id.easylegal",
        topic="izin usaha",
        metrics={"likes": likes, "comments": comments, "views": views},
        posted_at=posted_at,
        scraped_at="2026-09-20T00:00:00+00:00",
        url=url,
        missing_fields=[],
    )


class TestCitationValidatorNeverFabricates(unittest.TestCase):
    """Blocking review fix: `_validate_and_correct_citations` must never invent a
    citation. A claim-requiring line with zero *supporting* citations is dropped and
    counted unsupported -- never anchored to an arbitrary evidence record."""

    def setUp(self):
        self.handler = ClaudeChatHandler(api_key="")

    def test_fabricated_number_not_in_any_evidence_is_dropped_not_anchored(self):
        evidence = [_post_evidence(likes=420, comments=31, views=9500)]
        reply = "Post ini punya 999 views yang sangat tinggi [post:real-1]."
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        # The citation was present but does not actually support "999" (not one of the
        # cited record's metric values) -- the whole line must be dropped, not kept
        # with a fabricated/mismatched anchor.
        self.assertEqual(unsupported, 1)
        self.assertNotIn("999", corrected)
        self.assertEqual(citations, [])

    def test_fabricated_number_with_nonempty_unrelated_evidence_still_dropped(self):
        # Evidence exists (non-empty), but none of it supports this specific claim --
        # the old bug anchored an arbitrary evidence[0] here regardless of relevance.
        evidence = [_post_evidence(source_id="post:unrelated", likes=10, comments=1, views=50)]
        reply = "Akun ini meraih 999 likes bulan lalu."
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        self.assertEqual(unsupported, 1)
        self.assertEqual(corrected.strip(), "")
        self.assertEqual(citations, [])

    def test_valid_metric_with_matching_citation_is_kept(self):
        evidence = [_post_evidence(likes=420, comments=31, views=9500)]
        reply = "Post terbaik meraih 420 likes [post:real-1]."
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        self.assertEqual(unsupported, 0)
        self.assertIn("420 likes", corrected)
        self.assertIn("[post:real-1]", corrected)
        self.assertEqual([c.source_id for c in citations], ["post:real-1"])

    def test_citation_to_nonexistent_source_id_is_dropped(self):
        evidence = [_post_evidence(likes=420, comments=31, views=9500)]
        reply = "Post ini meraih 420 likes [post:does-not-exist]."
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        # The invalid marker is stripped; since no valid citation remains to support the
        # numeric claim "420", the whole line is dropped.
        self.assertNotIn("post:does-not-exist", corrected)
        self.assertEqual(unsupported, 1)
        self.assertEqual(citations, [])

    def test_url_claim_without_matching_source_is_dropped(self):
        evidence = [_post_evidence(url="https://instagram.com/p/real")]
        reply = "Lihat postingan ini: https://instagram.com/p/fabricated-link"
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        self.assertEqual(unsupported, 1)
        self.assertNotIn("fabricated-link", corrected)

    def test_url_claim_with_matching_cited_source_is_kept(self):
        evidence = [_post_evidence(url="https://instagram.com/p/real")]
        reply = "Lihat postingan ini: https://instagram.com/p/real [post:real-1]"
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        self.assertEqual(unsupported, 0)
        self.assertIn("https://instagram.com/p/real", corrected)
        self.assertEqual([c.source_id for c in citations], ["post:real-1"])

    def test_date_claim_without_matching_source_is_dropped(self):
        evidence = [_post_evidence(posted_at="2026-01-01T00:00:00+00:00")]
        reply = "Postingan ini naik pada 2026-05-05."
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        self.assertEqual(unsupported, 1)
        self.assertNotIn("2026-05-05", corrected)

    def test_ranking_claim_without_any_citation_is_dropped(self):
        evidence = [_post_evidence()]
        reply = "Post ini adalah nomor 1 dari 50 postingan yang tersimpan."
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        self.assertEqual(unsupported, 1)
        self.assertNotIn("50", corrected)

    def test_plain_non_factual_prose_is_preserved_untouched(self):
        evidence = [_post_evidence()]
        reply = "Gunakan hook yang menarik perhatian audiens sejak detik pertama."
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        self.assertEqual(corrected, reply)
        self.assertEqual(unsupported, 0)
        self.assertEqual(citations, [])

    def test_numbered_list_marker_alone_is_not_treated_as_a_numeric_claim(self):
        evidence = [_post_evidence()]
        reply = "1. Buat hook yang kuat\n2. Sertakan CTA yang jelas"
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        self.assertEqual(corrected, reply)
        self.assertEqual(unsupported, 0)

    def test_multiple_valid_citations_keeps_only_the_supporting_one(self):
        evidence = [
            _post_evidence(source_id="post:a", likes=420, comments=31, views=9500),
            _post_evidence(source_id="post:b", likes=100, comments=5, views=2000),
        ]
        reply = "Post ini meraih 420 likes [post:a] [post:b]."
        corrected, citations, unsupported = self.handler._validate_and_correct_citations(reply, evidence)

        self.assertEqual(unsupported, 0)
        self.assertEqual([c.source_id for c in citations], ["post:a"])
        self.assertNotIn("[post:b]", corrected)


if __name__ == "__main__":
    unittest.main()

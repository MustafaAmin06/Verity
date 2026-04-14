import unittest

import verity_extractor as ve


def make_scraped_source(
    *,
    domain: str,
    author: str | None = None,
    date: str = "2025",
    word_count: int = 180,
    body_text: str | None = None,
    scrape_note: str | None = None,
    scrape_success: bool = True,
    live: bool = True,
    paywalled: bool = False,
):
    text = body_text
    if text is None and word_count > 0:
        text = "Detailed medical guidance about symptoms, risks, and treatment. " * 8
    return ve.ScrapedSource(
        url=f"https://{domain}/article",
        label="Example article",
        context="This source supports the medical claim.",
        domain=domain,
        live=live,
        http_status=200 if live else 404,
        title="Example article",
        description="Example description",
        body_text=text,
        date=date,
        author=author,
        doi=None,
        paywalled=paywalled,
        is_pdf=False,
        json_ld=None,
        keywords=["medical", "guidance"],
        word_count=word_count,
        scrape_method="beautifulsoup",
        scrape_note=scrape_note,
        scrape_success=scrape_success,
    )


def make_llm_result(
    *,
    topic_relevance: int = 90,
    claim_support: int = 90,
    support_class: str = "direct_support",
    evidence_specificity: str = "direct",
    contradiction_strength: str = "none",
) -> dict:
    return {
        "topic_relevance_score": topic_relevance,
        "claim_support_score": claim_support,
        "support_class": support_class,
        "evidence_specificity": evidence_specificity,
        "contradiction_strength": contradiction_strength,
        "claim_aligned": support_class in {"direct_support", "qualified_support"},
        "reason": "The source directly covers the medical topic in the claim.",
        "implication": "This source is a good fit for the claim.",
        "matched_terms": ["medical", "claim"],
    }


class MultiAxisScoringTests(unittest.TestCase):
    def test_supported_source_exposes_multi_axis_scores(self):
        scraped = make_scraped_source(domain="my.clevelandclinic.org", author=None, word_count=180)
        scored = ve.build_scored_source(scraped, make_llm_result(), {}, topic="health")

        self.assertEqual(scored.verdict, "supported")
        self.assertEqual(scored.signals.domain_tier, "medical_authority")
        self.assertEqual(scored.authorship_type, "institutional")
        self.assertEqual(scored.author_label, "Institutional page")
        self.assertGreaterEqual(scored.signals.retrieval_integrity_score, 70)
        self.assertGreaterEqual(scored.signals.source_credibility_score, 75)
        self.assertGreaterEqual(scored.signals.claim_support_score, 85)
        self.assertGreaterEqual(scored.signals.decision_confidence_score, 75)
        self.assertEqual(scored.signals.decision_confidence_level, "high")
        self.assertEqual(scored.overall_score, scored.composite_score)

    def test_low_retrieval_source_is_inaccessible_even_with_strong_claim_match(self):
        scraped = make_scraped_source(
            domain="example.com",
            author="Dr. Jane Doe",
            word_count=0,
            body_text="",
            scrape_note="partial_content",
            scrape_success=False,
        )
        scored = ve.build_scored_source(scraped, make_llm_result(), {}, topic="health")

        self.assertEqual(scored.verdict, "inaccessible")
        self.assertLess(scored.signals.retrieval_integrity_score, 35)
        self.assertTrue(scored.signals.metadata_only)
        self.assertIn("retrieval_limited", scored.flags)

    def test_unknown_domain_support_is_cautious_not_supported(self):
        scored = ve.build_scored_source(
            make_scraped_source(domain="example.com", author=None, word_count=220),
            make_llm_result(
                topic_relevance=92,
                claim_support=88,
                support_class="qualified_support",
                evidence_specificity="paraphrased",
            ),
            {},
            topic="health",
        )

        self.assertEqual(scored.signals.domain_tier, "unknown")
        self.assertLess(scored.signals.source_credibility_score, 65)
        self.assertEqual(scored.verdict, "cautious_support")
        self.assertNotEqual(scored.verdict, "supported")

    def test_topic_relevant_unverified_maps_to_relevant_unverified(self):
        scored = ve.build_scored_source(
            make_scraped_source(domain="cancer.org", author=None, word_count=180),
            make_llm_result(
                topic_relevance=94,
                claim_support=62,
                support_class="topic_relevant_unverified",
                evidence_specificity="weak",
            ),
            {},
            topic="health",
        )

        self.assertEqual(scored.verdict, "relevant_unverified")
        self.assertEqual(scored.signals.support_class, "topic_relevant_unverified")
        self.assertTrue(scored.signals.retrieval_limited is False)
        self.assertGreaterEqual(scored.signals.relevance_score, 90)
        self.assertLess(scored.signals.claim_support_score, 70)

    def test_strong_contradiction_yields_contradicted(self):
        scored = ve.build_scored_source(
            make_scraped_source(domain="cancer.org", author="Dr. Jane Doe", word_count=220),
            make_llm_result(
                topic_relevance=90,
                claim_support=8,
                support_class="contradicted",
                evidence_specificity="none",
                contradiction_strength="strong",
            ),
            {},
            topic="health",
        )

        self.assertEqual(scored.verdict, "contradicted")
        self.assertEqual(scored.signals.contradiction_strength, "strong")
        self.assertEqual(scored.signals.claim_aligned, False)
        self.assertIn("claim_contradicted", scored.flags)
        self.assertLessEqual(scored.composite_score, 24)

    def test_authority_profile_upgrades_unknown_domain_to_academic_journal(self):
        scraped = make_scraped_source(domain="unseen-journal.example", author=None, word_count=260)
        scored = ve.build_scored_source(
            scraped,
            make_llm_result(
                topic_relevance=88,
                claim_support=82,
                support_class="qualified_support",
                evidence_specificity="paraphrased",
            ),
            {
                "authority_profile": {
                    "authority_kind": "academic_journal",
                    "authority_name": "Journal of Testing",
                    "authority_source": "openalex",
                    "confidence": "high",
                    "is_peer_reviewed": True,
                    "is_institutional": True,
                    "matched_ids": {"doi": "10.1234/example"},
                    "evidence": ["openalex:work"],
                }
            },
            topic="health",
        )

        self.assertEqual(scored.signals.domain_tier, "academic_journal")
        self.assertEqual(scored.authority_source, "openalex")
        self.assertEqual(scored.authority_confidence, "high")
        self.assertEqual(scored.signals.authority_label, "Journal of Testing")
        self.assertEqual(scored.authorship_type, "institutional")
        self.assertGreaterEqual(scored.signals.source_credibility_score, 80)


if __name__ == "__main__":
    unittest.main()

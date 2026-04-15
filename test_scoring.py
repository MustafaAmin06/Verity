import unittest

import verity_extractor as ve


def make_scraped_source(
    *,
    url: str | None = None,
    domain: str,
    title: str = "Example article",
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
        url=url or f"https://{domain}/article",
        label="Example article",
        context="This source supports the medical claim.",
        domain=domain,
        live=live,
        http_status=200 if live else 404,
        title=title,
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

    def test_first_party_docs_are_top_authority_for_owned_product_api_claims(self):
        scraped = make_scraped_source(
            domain="learn.microsoft.com",
            url="https://learn.microsoft.com/en-us/azure/search/retrieval-augmented-generation-overview",
            title="Azure AI Search and retrieval augmented generation",
            author=None,
            word_count=240,
            body_text="Azure AI Search documentation describing retrieval augmented generation patterns. " * 8,
        )
        scored = ve.build_scored_source(
            scraped,
            make_llm_result(
                topic_relevance=95,
                claim_support=91,
                support_class="direct_support",
                evidence_specificity="direct",
            ),
            {},
            authority_context={
                "claim_type": "product_api",
                "main_entity": "Azure AI Search",
                "source_role": "first_party_docs",
                "does_source_own_entity": True,
                "classifier_confidence": "high",
                "authority_reason": "Official first-party product documentation.",
            },
            topic="technical",
        )

        self.assertEqual(scored.source_role, "first_party_docs")
        self.assertTrue(scored.does_source_own_entity)
        self.assertEqual(scored.signals.domain_tier, "technical_primary_source")
        self.assertEqual(scored.signals.authority_fit_score, 100)
        self.assertEqual(scored.signals.source_type_confidence_score, 90)
        self.assertEqual(scored.verdict, "supported")

    def test_vendor_explainer_cannot_be_supported_for_scholarly_claims(self):
        scraped = make_scraped_source(
            domain="ibm.com",
            url="https://www.ibm.com/think/topics/retrieval-augmented-generation",
            title="What is retrieval-augmented generation?",
            author=None,
            word_count=260,
            body_text="IBM technical explainer discussing retrieval augmented generation and enterprise use cases. " * 8,
        )
        scored = ve.build_scored_source(
            scraped,
            make_llm_result(
                topic_relevance=94,
                claim_support=89,
                support_class="qualified_support",
                evidence_specificity="paraphrased",
            ),
            {},
            authority_context={
                "claim_type": "scholarly_empirical",
                "main_entity": "retrieval-augmented generation",
                "source_role": "vendor_explainer",
                "does_source_own_entity": False,
                "classifier_confidence": "high",
                "authority_reason": "Credible explainer, but not a primary scholarly source.",
            },
            topic="technical",
        )

        self.assertEqual(scored.source_role, "vendor_explainer")
        self.assertEqual(scored.signals.authority_fit_score, 25)
        self.assertNotEqual(scored.verdict, "supported")
        self.assertIn(scored.verdict, {"cautious_support", "relevant_unverified"})

    def test_marketing_landing_pages_are_capped_below_supported(self):
        scraped = make_scraped_source(
            domain="example.com",
            title="AI Platform Pricing",
            word_count=220,
            body_text="Start free trial today. Contact sales. Book a demo to learn more about our AI platform. " * 8,
        )
        scored = ve.build_scored_source(
            scraped,
            make_llm_result(
                topic_relevance=90,
                claim_support=86,
                support_class="qualified_support",
                evidence_specificity="paraphrased",
            ),
            {},
            authority_context={
                "claim_type": "general_information",
                "main_entity": "AI platform",
                "source_role": "marketing_landing",
                "does_source_own_entity": False,
                "classifier_confidence": "high",
                "authority_reason": "Marketing landing page rather than evidence-bearing documentation.",
            },
            topic="technical",
        )

        self.assertEqual(scored.source_role, "marketing_landing")
        self.assertEqual(scored.signals.domain_tier, "marketing_landing")
        self.assertEqual(scored.signals.source_type_confidence_score, 20)
        self.assertNotEqual(scored.verdict, "supported")

    def test_unknown_authority_profile_with_confident_classification_is_flagged_as_bug(self):
        scraped = make_scraped_source(
            domain="ibm.com",
            url="https://www.ibm.com/think/topics/retrieval-augmented-generation",
            title="What is retrieval-augmented generation?",
            author=None,
            word_count=260,
            body_text="IBM technical explainer discussing retrieval augmented generation and enterprise use cases. " * 8,
        )
        scored = ve.build_scored_source(
            scraped,
            make_llm_result(
                topic_relevance=94,
                claim_support=89,
                support_class="qualified_support",
                evidence_specificity="paraphrased",
            ),
            {
                "authority_profile": {
                    "authority_kind": "unknown",
                    "authority_name": "ibm.com",
                    "authority_source": "registry",
                    "confidence": "low",
                    "is_peer_reviewed": False,
                    "is_institutional": False,
                    "matched_ids": {"domain": "ibm.com"},
                    "evidence": ["registry:unknown"],
                }
            },
            authority_context={
                "claim_type": "technical_explainer",
                "main_entity": "retrieval-augmented generation",
                "source_role": "vendor_explainer",
                "does_source_own_entity": False,
                "classifier_confidence": "high",
                "authority_reason": "Credible vendor explainer for a technical concept.",
            },
            topic="technical",
        )

        self.assertIn("authority_profile_bug", scored.flags)


if __name__ == "__main__":
    unittest.main()

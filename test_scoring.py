import unittest

import verity_extractor as ve


def make_scraped_source(
    *,
    domain: str,
    author: str | None = None,
    date: str = "2024",
):
    return ve.ScrapedSource(
        url=f"https://{domain}/article",
        label="Example article",
        context="This source supports the medical claim.",
        domain=domain,
        live=True,
        http_status=200,
        title="Example article",
        description="Example description",
        body_text="Detailed medical guidance about symptoms, risks, and treatment.",
        date=date,
        author=author,
        doi=None,
        paywalled=False,
        is_pdf=False,
        json_ld=None,
        keywords=["medical", "guidance"],
        word_count=80,
        scrape_method="beautifulsoup",
        scrape_note=None,
        scrape_success=True,
    )


def make_llm_result(relevance: int = 90, alignment: int = 90) -> dict:
    return {
        "relevance_score": relevance,
        "alignment_score": alignment,
        "claim_aligned": alignment >= 70,
        "reason": "The source directly covers the medical topic in the claim.",
        "implication": "This source is a good fit for the claim.",
        "matched_terms": ["medical", "claim"],
    }


class MedicalAuthorityScoringTests(unittest.TestCase):
    def test_medical_authority_subdomain_gets_institutional_authorship(self):
        scraped = make_scraped_source(domain="my.clevelandclinic.org", author=None)
        scored = ve.build_scored_source(scraped, make_llm_result(), {})

        self.assertEqual(scored.signals.domain_tier, "medical_authority")
        self.assertEqual(scored.authorship_type, "institutional")
        self.assertEqual(scored.author_label, "Institutional page")
        self.assertEqual(scored.signals.author_score, 80)
        self.assertGreaterEqual(scored.composite_score, 85)

    def test_unknown_domain_without_author_stays_unknown_and_scores_lower(self):
        institutional = ve.build_scored_source(
            make_scraped_source(domain="cancer.org", author=None),
            make_llm_result(),
            {},
        )
        unknown = ve.build_scored_source(
            make_scraped_source(domain="example.com", author=None),
            make_llm_result(),
            {},
        )

        self.assertEqual(unknown.signals.domain_tier, "unknown")
        self.assertEqual(unknown.authorship_type, "unknown")
        self.assertEqual(unknown.author_label, "Unknown")
        self.assertEqual(unknown.signals.author_score, 40)
        self.assertGreater(institutional.composite_score, unknown.composite_score)
        self.assertGreaterEqual(institutional.composite_score - unknown.composite_score, 10)

    def test_named_author_path_is_preserved(self):
        scraped = make_scraped_source(domain="cancer.org", author="Dr. Jane Doe")
        scored = ve.build_scored_source(scraped, make_llm_result(), {})

        self.assertEqual(scored.signals.domain_tier, "medical_authority")
        self.assertEqual(scored.authorship_type, "named")
        self.assertEqual(scored.author_label, "Dr. Jane Doe")
        self.assertEqual(scored.signals.author_score, 80)

    def test_authority_profile_upgrades_unknown_domain_to_academic_journal(self):
        scraped = make_scraped_source(domain="unseen-journal.example", author=None)
        scored = ve.build_scored_source(
            scraped,
            make_llm_result(),
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
        )

        self.assertEqual(scored.signals.domain_tier, "academic_journal")
        self.assertEqual(scored.authority_source, "openalex")
        self.assertEqual(scored.authority_confidence, "high")
        self.assertEqual(scored.signals.authority_label, "Journal of Testing")
        self.assertEqual(scored.authorship_type, "institutional")


if __name__ == "__main__":
    unittest.main()

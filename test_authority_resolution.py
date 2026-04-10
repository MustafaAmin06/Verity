import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import verity_extractor as ve


def make_scraped_source(
    *,
    domain="example.com",
    doi=None,
    organization_hint=None,
    site_name=None,
    issn=None,
    journal_name=None,
):
    return ve.ScrapedSource(
        url=f"https://{domain}/article",
        label="Example article",
        context="Authority resolver test context.",
        domain=domain,
        live=True,
        http_status=200,
        title="Example article",
        description="Example description",
        body_text="Detailed article body for authority resolver testing.",
        date="2024",
        author=None,
        doi=doi,
        issn=issn,
        journal_name=journal_name,
        publisher_hint=organization_hint,
        organization_hint=organization_hint,
        site_name=site_name,
        paywalled=False,
        is_pdf=False,
        json_ld=None,
        keywords=["example"],
        word_count=60,
        scrape_method="beautifulsoup",
        scrape_note=None,
        scrape_success=True,
    )


class AuthorityResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.authority_db_path = Path(self.tempdir.name) / "authority_cache.db"

        self.authority_db_patch = patch.object(ve, "_AUTHORITY_DB_PATH", self.authority_db_path)
        self.authority_db_patch.start()
        ve._authority_db = None

    async def asyncTearDown(self):
        if ve._authority_db is not None:
            ve._authority_db.close()
            ve._authority_db = None
        self.authority_db_patch.stop()
        self.tempdir.cleanup()

    async def test_resolve_authority_uses_openalex_for_scholarly_match(self):
        scraped = make_scraped_source(domain="unseen-journal.example", doi="10.1234/example")

        with patch.object(
            ve,
            "enrich_with_openalex",
            AsyncMock(
                return_value={
                    "oa_work_id": "https://openalex.org/W123",
                    "oa_work_type": "journal-article",
                    "oa_source_id": "https://openalex.org/S123",
                    "oa_source_display_name": "Journal of Testing",
                    "oa_source_type": "journal",
                    "oa_source_h_index": 80,
                }
            ),
        ):
            result = await ve.resolve_authority(scraped)

        profile = ve.AuthorityProfile.model_validate(result["authority_profile"])
        self.assertEqual(profile.authority_kind, "academic_journal")
        self.assertEqual(profile.authority_source, "openalex")
        self.assertEqual(profile.confidence, "high")
        self.assertEqual(profile.matched_ids["doi"], "10.1234/example")

    async def test_resolve_authority_uses_ror_for_unknown_institutional_page(self):
        scraped = make_scraped_source(
            domain="examplehealth.org",
            organization_hint="Example Health System",
            site_name="Example Health System",
        )

        ror_payload = {
            "items": [
                {
                    "organization": {
                        "id": "https://ror.org/12345",
                        "name": "Example Health System",
                        "types": ["Healthcare"],
                        "links": [{"value": "https://examplehealth.org"}],
                        "aliases": ["Example Health"],
                        "acronyms": [],
                    }
                }
            ]
        }

        with patch.object(ve, "_ror_search", AsyncMock(return_value=ror_payload)), patch.object(
            ve, "_wikidata_search", AsyncMock(return_value={})
        ):
            result = await ve.resolve_authority(scraped)

        profile = ve.AuthorityProfile.model_validate(result["authority_profile"])
        self.assertEqual(profile.authority_kind, "medical_authority")
        self.assertEqual(profile.authority_source, "ror")
        self.assertEqual(profile.confidence, "high")
        self.assertEqual(profile.matched_ids["domain"], "examplehealth.org")

    async def test_resolve_authority_keeps_protected_registry_tiers(self):
        scraped = make_scraped_source(domain="naturalnews.com")

        with patch.object(ve, "_ror_search", AsyncMock(return_value={})):
            result = await ve.resolve_authority(scraped)

        profile = ve.AuthorityProfile.model_validate(result["authority_profile"])
        self.assertEqual(profile.authority_kind, "flagged")
        self.assertEqual(profile.authority_source, "registry")


if __name__ == "__main__":
    unittest.main()

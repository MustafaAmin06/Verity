import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import devtools.bench_dashboard as dash
import devtools.triage_catalog as tc
import verity_extractor as ve


def make_scraped_source(
    *,
    url="https://example.com/article",
    domain="example.com",
    word_count=0,
    body_text="",
    scrape_note=None,
    scrape_success=False,
    live=True,
    scrape_method="beautifulsoup",
):
    return ve.ScrapedSource(
        url=url,
        label="Example article",
        context="The cited article supports the claim.",
        domain=domain,
        live=live,
        http_status=200 if live else 404,
        title="Example article",
        description="Example description",
        body_text=body_text,
        date="2024",
        author="Example Author",
        doi=None,
        paywalled=False,
        is_pdf=False,
        json_ld=None,
        keywords=["example"],
        word_count=word_count,
        scrape_method=scrape_method,
        scrape_note=scrape_note,
        scrape_success=scrape_success,
    )


class TriageCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "triage.db"
        self.db = tc.get_db(self.db_path)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def test_record_observation_deduplicates_canonical_urls(self):
        first = make_scraped_source(url="https://www.example.com/article/?utm_source=test")
        second = make_scraped_source(url="https://example.com/article")

        case_id_1 = tc.record_observation(
            self.db,
            source_kind="bench_test",
            source_run_id="run-1",
            prompt="Prompt A",
            response="Response A",
            topic="science",
            scraped=first,
        )
        case_id_2 = tc.record_observation(
            self.db,
            source_kind="bench_test",
            source_run_id="run-2",
            prompt="Prompt B",
            response="Response B",
            topic="science",
            scraped=second,
        )

        self.assertEqual(case_id_1, case_id_2)
        case = self.db.execute("SELECT * FROM triage_cases").fetchone()
        self.assertEqual(case["canonical_url"], "https://example.com/article")
        self.assertEqual(case["times_seen"], 2)
        self.assertEqual(
            self.db.execute("SELECT COUNT(*) AS cnt FROM triage_case_events").fetchone()["cnt"],
            2,
        )

    def test_short_live_scrapes_become_partial_content_candidates(self):
        scraped = make_scraped_source(
            word_count=42,
            body_text="short body " * 6,
            scrape_success=True,
        )

        case_id = tc.record_observation(
            self.db,
            source_kind="bench_test",
            source_run_id="run-1",
            prompt="Prompt A",
            response="Response A",
            topic="health",
            scraped=scraped,
        )

        self.assertIsNotNone(case_id)
        case = self.db.execute(
            "SELECT latest_failure_category, likely_scrapable, likely_scrapable_reason, review_status FROM triage_cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        self.assertEqual(case["latest_failure_category"], "partial_content")
        self.assertEqual(case["likely_scrapable"], 1)
        self.assertEqual(case["likely_scrapable_reason"], "partial_content")
        self.assertEqual(case["review_status"], "new")

    def test_review_updates_increment_confirmed_fixable_once(self):
        scraped = make_scraped_source()
        case_id = tc.record_observation(
            self.db,
            source_kind="bench_test",
            source_run_id="run-1",
            prompt="Prompt A",
            response="Response A",
            topic="policy",
            scraped=scraped,
        )

        tc.update_case_review(
            self.db,
            case_id=case_id,
            review_status="confirmed_fixable",
            note="Browser rendering should work here.",
        )
        tc.update_case_review(
            self.db,
            case_id=case_id,
            review_status="confirmed_fixable",
            note="Still confirmed.",
        )

        case = self.db.execute(
            "SELECT review_status, times_confirmed_fixable FROM triage_cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        self.assertEqual(case["review_status"], "confirmed_fixable")
        self.assertEqual(case["times_confirmed_fixable"], 1)

    def test_case_stays_likely_scrapable_after_later_hard_blocker(self):
        case_id = tc.record_observation(
            self.db,
            source_kind="bench_test",
            source_run_id="run-1",
            prompt="Prompt A",
            response="Response A",
            topic="science",
            scraped=make_scraped_source(word_count=0, body_text=""),
        )

        tc.record_observation(
            self.db,
            source_kind="bench_test",
            source_run_id="run-2",
            prompt="Prompt B",
            response="Response B",
            topic="science",
            scraped=make_scraped_source(
                scrape_note="blocked_403_waf",
                live=False,
                word_count=0,
                body_text="",
            ),
        )

        case = self.db.execute(
            "SELECT likely_scrapable, likely_scrapable_reason, review_status, latest_failure_category, times_seen FROM triage_cases WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        self.assertEqual(case["likely_scrapable"], 1)
        self.assertEqual(case["likely_scrapable_reason"], "empty_content")
        self.assertEqual(case["review_status"], "new")
        self.assertEqual(case["latest_failure_category"], "waf_block")
        self.assertEqual(case["times_seen"], 2)

    def test_export_cases_respects_domain_filter(self):
        tc.record_observation(
            self.db,
            source_kind="bench_test",
            source_run_id="run-1",
            prompt="Prompt A",
            response="Response A",
            topic="science",
            scraped=make_scraped_source(url="https://example.com/article-a", domain="example.com"),
        )
        tc.record_observation(
            self.db,
            source_kind="bench_test",
            source_run_id="run-2",
            prompt="Prompt B",
            response="Response B",
            topic="science",
            scraped=make_scraped_source(url="https://news.example.org/article-b", domain="news.example.org"),
        )

        export_path = Path(self.tempdir.name) / "triage.json"
        tc.export_cases(
            self.db,
            output_path=export_path,
            domain="example.com",
            likely_only=True,
        )

        exported = json.loads(export_path.read_text(encoding="utf-8"))
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["normalized_domain"], "example.com")


class LiveCaptureAndDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_triage_batch_persists_capture_run(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "triage.db"
            scraped = make_scraped_source(word_count=0, body_text="")

            with patch.object(ve, "TRIAGE_DB_PATH", str(db_path)), patch.object(
                ve, "TRIAGE_CAPTURE_ENABLED", True
            ):
                await ve._record_live_triage_batch(
                    source_kind="live_stream",
                    prompt="Prompt A",
                    response="Response A",
                    topic="science",
                    llm_enabled=False,
                    observations=[
                        {
                            "scraped": scraped,
                            "llm": {},
                            "scored": None,
                            "playwright_attempted": False,
                            "playwright_improved": False,
                        }
                    ],
                )

            db = tc.get_db(db_path)
            try:
                capture_count = db.execute("SELECT COUNT(*) AS cnt FROM capture_runs").fetchone()["cnt"]
                case_count = db.execute("SELECT COUNT(*) AS cnt FROM triage_cases").fetchone()["cnt"]
                event = db.execute(
                    "SELECT source_kind, failure_category FROM triage_case_events"
                ).fetchone()
            finally:
                db.close()

            self.assertEqual(capture_count, 1)
            self.assertEqual(case_count, 1)
            self.assertEqual(event["source_kind"], "live_stream")
            self.assertEqual(event["failure_category"], "empty_content")

    async def test_dashboard_triage_endpoints_return_catalog_data(self):
        with tempfile.TemporaryDirectory() as tempdir:
            db_path = Path(tempdir) / "triage.db"
            db = tc.get_db(db_path)
            try:
                tc.record_observation(
                    db,
                    source_kind="bench_test",
                    source_run_id="run-1",
                    prompt="Prompt A",
                    response="Response A",
                    topic="science",
                    scraped=make_scraped_source(),
                )
            finally:
                db.close()

            with patch.object(dash, "_DB_PATH", db_path):
                summary_response = await dash.api_triage_summary()
                cases_response = await dash.api_triage_cases()

            summary = json.loads(summary_response.body)
            cases = json.loads(cases_response.body)

            self.assertEqual(summary["open_cases"], 1)
            self.assertEqual(summary["domains"][0]["normalized_domain"], "example.com")
            self.assertEqual(cases[0]["normalized_domain"], "example.com")
            self.assertEqual(cases[0]["latest_failure_category"], "empty_content")


if __name__ == "__main__":
    unittest.main()

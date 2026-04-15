import unittest
from unittest.mock import AsyncMock, patch

from scraping.extractors import extract_page
from scraping.models import BrowserRenderResult, FetchResult, PipelineConfig, SourceInput
from scraping.orchestrator import ScrapeOrchestrator


def make_config() -> PipelineConfig:
    return PipelineConfig(
        request_timeout_seconds=4,
        browser_timeout_seconds=8,
        max_response_bytes=2_000_000,
        max_body_text_chars=8000,
        max_redirects=5,
        enable_playwright_fallback=True,
        browser_user_agent="VerityTest/1.0",
        browser_headers={"User-Agent": "VerityTest/1.0"},
    )


def extract_domain(url: str) -> str:
    return url.split("/")[2]


def get_domain_info(domain: str) -> dict:
    return {"tier": "academic_journal" if "ncbi.nlm.nih.gov" in domain else "unknown", "paywalled": False}


def make_orchestrator() -> ScrapeOrchestrator:
    return ScrapeOrchestrator(
        config=make_config(),
        logger=__import__("logging"),
        extract_domain=extract_domain,
        get_domain_info=get_domain_info,
    )


def long_article(title: str, body_sentence: str, *, paragraphs: int = 18) -> str:
    body = " ".join([body_sentence] * paragraphs)
    return f"""
    <html>
      <head>
        <title>{title}</title>
        <meta name="author" content="Jane Example" />
        <meta name="description" content="{body_sentence}" />
      </head>
      <body>
        <main>
          <article>
            <h1>{title}</h1>
            <p>{body}</p>
          </article>
        </main>
      </body>
    </html>
    """


class ScrapePipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_high_confidence_http_without_browser(self):
        orchestrator = make_orchestrator()
        source = SourceInput(
            url="https://example.com/story",
            label="Mars water study",
            context="The article describes a Mars water study.",
        )
        html = long_article(
            "Mars water study",
            "Mars water study findings describe geological evidence for ancient water on Mars.",
            paragraphs=40,
        )

        with patch("scraping.orchestrator.fetch_url", AsyncMock(return_value=FetchResult(kind="ok", url=source.url, http_status=200, html=html))), patch.object(
            orchestrator.pool,
            "render",
            AsyncMock(side_effect=AssertionError("browser render should not run")),
        ):
            result = await orchestrator.scrape_source(source)

        self.assertEqual(result.extraction_stage, "http")
        self.assertTrue(result.scrape_success)
        self.assertGreaterEqual(result.extraction_confidence or 0, 60)
        self.assertNotEqual(result.extraction_strategy, "none")

    async def test_escalates_to_browser_for_js_heavy_http_snapshot(self):
        orchestrator = make_orchestrator()
        source = SourceInput(
            url="https://example.com/dynamic",
            label="Dynamic article",
            context="The article supports the cited claim.",
        )
        http_html = """
        <html>
          <head>
            <title>Dynamic article</title>
            <script>window.__NUXT__ = {};</script>
            <script>window.__NEXT_DATA__ = {};</script>
          </head>
          <body><div id="app"></div></body>
        </html>
        """
        browser_html = long_article(
            "Dynamic article",
            "Dynamic article evidence directly supports the cited claim with detailed findings and methodology.",
            paragraphs=30,
        )

        with patch("scraping.orchestrator.fetch_url", AsyncMock(return_value=FetchResult(kind="ok", url=source.url, http_status=200, html=http_html))), patch(
            "scraping.orchestrator.PLAYWRIGHT_AVAILABLE",
            True,
        ), patch.object(
            orchestrator.pool,
            "render",
            AsyncMock(return_value=BrowserRenderResult(kind="ok", http_status=200, html=browser_html)),
        ) as render_mock:
            result = await orchestrator.scrape_source(source)

        render_mock.assert_awaited_once()
        self.assertEqual(result.extraction_stage, "browser")
        self.assertEqual(result.scrape_method, "playwright")
        self.assertTrue(result.scrape_success)

    async def test_hard_waf_block_skips_browser(self):
        orchestrator = make_orchestrator()
        source = SourceInput(
            url="https://guarded.example.com/article",
            label="Guarded page",
            context="Guarded page context.",
        )

        with patch(
            "scraping.orchestrator.fetch_url",
            AsyncMock(return_value=FetchResult(kind="blocked_403_waf", url=source.url, http_status=403)),
        ), patch.object(
            orchestrator.pool,
            "render",
            AsyncMock(side_effect=AssertionError("browser render should not run")),
        ):
            result = await orchestrator.scrape_source(source)

        self.assertFalse(result.live)
        self.assertEqual(result.scrape_note, "blocked_403_waf")
        self.assertEqual(result.extraction_stage, "failure")

    async def test_scholarly_adapter_marks_abstract_only(self):
        orchestrator = make_orchestrator()
        source = SourceInput(
            url="https://pubmed.ncbi.nlm.nih.gov/12345678/",
            label="PubMed abstract",
            context="PubMed abstract context.",
        )
        html = """
        <html>
          <head>
            <title>PubMed abstract</title>
            <meta name="description" content="Abstract: This clinical abstract explains the study design, patient cohort, outcomes, and conclusions for the cited intervention in substantial detail." />
          </head>
          <body>
            <div id="enc-abstract">Abstract: This clinical abstract explains the study design, patient cohort, outcomes, and conclusions for the cited intervention in substantial detail. Additional summary sentences make the abstract long enough to evaluate safely.</div>
          </body>
        </html>
        """

        with patch("scraping.orchestrator.fetch_url", AsyncMock(return_value=FetchResult(kind="ok", url=source.url, http_status=200, html=html))), patch.object(
            orchestrator.pool,
            "render",
            AsyncMock(side_effect=AssertionError("browser render should not run for abstract-only pages")),
        ):
            result = await orchestrator.scrape_source(source)

        self.assertEqual(result.extraction_strategy, "scholarly_adapter")
        self.assertIn("abstract_only", result.retrieval_flags)
        self.assertTrue(result.scrape_success)


class ExtractionHeuristicsTests(unittest.TestCase):
    def test_consent_text_candidate_is_flagged_and_low_confidence(self):
        html = """
        <html>
          <head><title>Cookie settings</title></head>
          <body>
            <article>
              We use cookies to improve your experience. Manage cookie preferences and cookie settings.
              This site uses cookies and lets you save preferences for performance cookies, analytics cookies,
              and functional cookies.
            </article>
          </body>
        </html>
        """

        page = extract_page(
            html,
            label="Cookie settings",
            url="https://example.com/cookies",
            domain_info={"tier": "unknown", "paywalled": False},
            max_body_text_chars=8000,
        )

        self.assertTrue(page.candidates)
        self.assertIn("consent_text", page.candidates[0].flags)
        self.assertLess(page.candidates[0].confidence, 40)


if __name__ == "__main__":
    unittest.main()

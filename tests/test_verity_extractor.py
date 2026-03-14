import unittest
from unittest.mock import AsyncMock, patch

IMPORT_ERROR = None

try:
    import httpx
    from bs4 import BeautifulSoup

    import verity_extractor as ve
except ModuleNotFoundError as exc:
    IMPORT_ERROR = exc


if IMPORT_ERROR is None:
    def make_response(
        method: str,
        url: str,
        status_code: int,
        *,
        headers=None,
        text: str = "",
    ):
        request = httpx.Request(method, url)
        return httpx.Response(status_code, headers=headers or {}, text=text, request=request)


    def make_scraped_source(**overrides) -> ve.ScrapedSource:
        data = {
            "url": "https://example.com/article",
            "label": "",
            "context": "",
            "domain": "example.com",
            "live": True,
            "http_status": 200,
            "title": None,
            "description": None,
            "body_text": None,
            "date": None,
            "author": None,
            "doi": None,
            "paywalled": False,
            "is_pdf": False,
            "json_ld": None,
            "keywords": [],
            "word_count": 0,
            "scrape_method": "beautifulsoup",
            "scrape_note": None,
            "scrape_success": False,
        }
        data.update(overrides)
        return ve.ScrapedSource(**data)


    LONG_HTML = """
    <html>
      <body>
        <article>
          <h1>Example Story</h1>
          <p>This is the first paragraph of the article and it is intentionally long enough to count as meaningful readable content.</p>
          <p>This is the second paragraph of the article and it adds enough extra text to pass the scraper success threshold comfortably.</p>
        </article>
      </body>
    </html>
    """


    class VerityExtractorAsyncTests(unittest.IsolatedAsyncioTestCase):
        async def asyncTearDown(self):
            try:
                await ve.close_shared_resources()
            except Exception:
                pass
            ve._SHARED_HTTP_CLIENT = None
            ve._SHARED_BROWSER = None
            ve._SHARED_PLAYWRIGHT = None

        async def test_get_http_client_reuses_singleton(self):
            client = AsyncMock()
            client.is_closed = False

            with patch.object(ve.httpx, "AsyncClient", return_value=client) as client_ctor:
                first = await ve.get_http_client()
                second = await ve.get_http_client()

            self.assertIs(first, second)
            client_ctor.assert_called_once()

        async def test_get_playwright_browser_reuses_singleton(self):
            class FakeBrowser:
                def __init__(self):
                    self.closed = False

                def is_connected(self):
                    return not self.closed

                async def close(self):
                    self.closed = True

            class FakeChromium:
                def __init__(self, browser):
                    self.browser = browser
                    self.launch_calls = 0

                async def launch(self, headless=True):
                    self.launch_calls += 1
                    return self.browser

            class FakeRuntime:
                def __init__(self, browser):
                    self.chromium = FakeChromium(browser)
                    self.stopped = False

                async def stop(self):
                    self.stopped = True

            class FakeStarter:
                def __init__(self, runtime):
                    self.runtime = runtime
                    self.start_calls = 0

                async def start(self):
                    self.start_calls += 1
                    return self.runtime

            browser = FakeBrowser()
            runtime = FakeRuntime(browser)
            starter = FakeStarter(runtime)

            def fake_async_playwright():
                return starter

            with patch.object(ve, "PLAYWRIGHT_AVAILABLE", True), patch.object(
                ve, "ENABLE_PLAYWRIGHT_FALLBACK", True
            ), patch.object(ve, "async_playwright", new=fake_async_playwright, create=True):
                first = await ve.get_playwright_browser()
                second = await ve.get_playwright_browser()

            self.assertIs(first, second)
            self.assertEqual(starter.start_calls, 1)
            self.assertEqual(runtime.chromium.launch_calls, 1)

        async def test_head_response_does_not_decide_liveness(self):
            source = ve.SourceInput(
                url="https://example.com/article",
                label="Example",
                context="Context",
            )

            async def fake_request(_client, method, _url):
                if method == "HEAD":
                    return make_response("HEAD", source.url, 404)
                return make_response(
                    "GET",
                    source.url,
                    200,
                    headers={"content-type": "text/html"},
                    text=LONG_HTML,
                )

            with patch.object(ve, "get_http_client", AsyncMock(return_value=object())), patch.object(
                ve, "request_with_retries", side_effect=fake_request
            ):
                result = await ve.scrape_with_beautifulsoup(source)

            self.assertTrue(result.live)
            self.assertEqual(result.http_status, 200)
            self.assertEqual(result.scrape_method, "beautifulsoup")
            self.assertIsNotNone(result.body_text)

        async def test_get_timeout_marks_source_not_live(self):
            source = ve.SourceInput(
                url="https://example.com/article",
                label="Example",
                context="Context",
            )

            async def fake_request(_client, method, _url):
                if method == "HEAD":
                    return make_response(
                        "HEAD",
                        source.url,
                        200,
                        headers={"content-type": "text/html"},
                    )
                raise httpx.ReadTimeout("timeout", request=httpx.Request("GET", source.url))

            with patch.object(ve, "get_http_client", AsyncMock(return_value=object())), patch.object(
                ve, "request_with_retries", side_effect=fake_request
            ):
                result = await ve.scrape_with_beautifulsoup(source)

            self.assertFalse(result.live)
            self.assertEqual(result.scrape_note, "timeout")
            self.assertIsNone(result.http_status)

        async def test_deduplicated_scrape_reuses_single_fetch_and_preserves_inputs(self):
            sources = [
                ve.SourceInput(
                    url="https://example.com/article",
                    label="First Label",
                    context="First Context",
                ),
                ve.SourceInput(
                    url="https://example.com/article",
                    label="Second Label",
                    context="Second Context",
                ),
            ]
            mock_scrape = AsyncMock(return_value=make_scraped_source())

            with patch.object(ve, "scrape_source", mock_scrape):
                results = await ve.scrape_sources_deduplicated(sources)

            self.assertEqual(mock_scrape.await_count, 1)
            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].label, "First Label")
            self.assertEqual(results[1].label, "Second Label")
            self.assertEqual(results[0].context, "First Context")
            self.assertEqual(results[1].context, "Second Context")
            self.assertEqual(results[0].title, "First Label")
            self.assertEqual(results[1].title, "Second Label")

        async def test_request_with_retries_retries_retryable_status_and_logs(self):
            class FakeClient:
                def __init__(self):
                    self.calls = 0

                async def request(self, method, url):
                    self.calls += 1
                    if self.calls == 1:
                        return make_response(method, url, 503)
                    return make_response(method, url, 200, text="ok")

            client = FakeClient()

            with patch.object(ve, "RETRY_BACKOFF_SECONDS", 0):
                with self.assertLogs("verity_extractor", level="WARNING") as logs:
                    response = await ve.request_with_retries(client, "GET", "https://example.com")

            self.assertEqual(client.calls, 2)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(
                any("Retrying GET https://example.com" in message for message in logs.output)
            )


    class VerityExtractorParsingTests(unittest.TestCase):
        def test_merge_prefers_richer_playwright_fields(self):
            source = ve.SourceInput(
                url="https://example.com/article",
                label="Example Label",
                context="Context",
            )
            baseline = make_scraped_source(
                title="Short title",
                description="Short description",
                body_text="This is a short body.",
                author="J. Doe",
                keywords=["ai"],
                scrape_note="partial_content",
                scrape_success=False,
            )
            rendered = make_scraped_source(
                title="A much richer rendered title",
                description="A significantly more complete rendered description for the article.",
                body_text=" ".join(["Rendered article body with plenty of useful text."] * 8),
                date="2024",
                author="Jane Doe",
                doi="10.1234/example-doi",
                json_ld={"headline": "Rendered headline", "author": "Jane Doe"},
                keywords=["scraping", "ai"],
                scrape_method="playwright",
                scrape_note="js_rendered",
                scrape_success=True,
            )

            merged = ve._merge_scrape_results(source, baseline, rendered)

            self.assertEqual(merged.scrape_method, "playwright")
            self.assertEqual(merged.doi, "10.1234/example-doi")
            self.assertEqual(merged.date, "2024")
            self.assertIn("scraping", merged.keywords)
            self.assertEqual(merged.scrape_note, "js_rendered")
            self.assertGreater(len(merged.body_text), len(baseline.body_text))

        def test_extract_json_ld_selects_best_article_node(self):
            html = """
            <html>
              <head>
                <script type="application/ld+json">
                  {"@type":"WebPage","name":"Landing Page"}
                </script>
                <script type="application/ld+json">
                  {
                    "@type":"Article",
                    "headline":"Best Article",
                    "author":{"@type":"Person","name":"Jane Doe"},
                    "datePublished":"2024-02-10",
                    "url":"https://example.com/story"
                  }
                </script>
              </head>
            </html>
            """
            soup = BeautifulSoup(html, "lxml")

            result = ve.extract_json_ld(soup, page_url="https://example.com/story")

            self.assertIsNotNone(result)
            self.assertEqual(result["headline"], "Best Article")
            self.assertEqual(result["author"], "Jane Doe")

        def test_extract_body_text_prefers_article_over_related_content(self):
            html = """
            <html>
              <body>
                <div class="related-links">
                  <p>Related stories and recommended links that should not be treated as the main article body.</p>
                </div>
                <article>
                  <p>This is the real first paragraph of the story and it contains enough detail to qualify as meaningful article text.</p>
                  <p>This is the real second paragraph of the story and it should be retained by the extractor as part of the body.</p>
                </article>
                <div class="comments">
                  <p>Comment from a reader that should not appear in the main extracted body text.</p>
                </div>
              </body>
            </html>
            """
            soup = BeautifulSoup(html, "lxml")

            body_text = ve.extract_body_text(soup)

            self.assertIn("real first paragraph", body_text)
            self.assertIn("real second paragraph", body_text)
            self.assertNotIn("Related stories", body_text)
            self.assertNotIn("Comment from a reader", body_text)

        def test_extract_date_and_author_use_tighter_heuristics(self):
            html = """
            <html>
              <body>
                <article>
                  <div class="byline">By Jane Doe | Updated March 5, 2024</div>
                  <time datetime="2024-03-05T10:00:00Z">March 5, 2024</time>
                </article>
              </body>
            </html>
            """
            soup = BeautifulSoup(html, "lxml")

            author = ve.extract_author(soup)
            date = ve.extract_date(soup)

            self.assertEqual(author, "Jane Doe")
            self.assertEqual(date, "2024")


else:
    class VerityExtractorDependencyTests(unittest.TestCase):
        @unittest.skip(f"Missing test dependency: {IMPORT_ERROR}")
        def test_dependencies_available(self):
            pass

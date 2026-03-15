import unittest
from unittest.mock import patch

try:
    import httpx
    from bs4 import BeautifulSoup

    import verity_extractor as extractor
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    extractor = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@unittest.skipIf(extractor is None, f"dependency missing: {IMPORT_ERROR}")
class ExtractorHelperTests(unittest.TestCase):
    def test_extract_json_ld_prefers_richer_article_node(self):
        html = """
        <html>
          <head>
            <script type="application/ld+json">
              {
                "@graph": [
                  {
                    "@type": "WebPage",
                    "name": "Landing page",
                    "url": "https://example.com/articles/test"
                  },
                  {
                    "@type": "NewsArticle",
                    "headline": "Real article headline",
                    "datePublished": "2024-05-01T10:00:00Z",
                    "author": {"@type": "Person", "name": "Casey Reporter"},
                    "publisher": {"@type": "Organization", "name": "Example News"},
                    "description": "Important context about the article.",
                    "keywords": ["science", "climate"],
                    "wordCount": 1450,
                    "url": "https://example.com/articles/test"
                  }
                ]
              }
            </script>
          </head>
          <body></body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")

        json_ld = extractor.extract_json_ld(
            soup,
            page_url="https://example.com/articles/test",
        )

        self.assertIsNotNone(json_ld)
        self.assertEqual(json_ld["type"], "NewsArticle")
        self.assertEqual(json_ld["headline"], "Real article headline")
        self.assertEqual(json_ld["author"], "Casey Reporter")
        self.assertEqual(json_ld["publisher"], "Example News")

    def test_extract_body_text_prefers_article_over_boilerplate(self):
        html = """
        <html>
          <body>
            <div class="newsletter-subscribe">
              <p>Subscribe to our newsletter for unlimited updates and offers.</p>
            </div>
            <article class="article-body">
              <p>This is the first substantial paragraph of the article with enough detail to count as real content and not page chrome.</p>
              <p>This is the second substantial paragraph, expanding on the story with context, evidence, and complete sentences for extraction.</p>
            </article>
            <div class="related-links">
              <a href="#">Related story one with lots of teaser text and calls to click through immediately.</a>
              <a href="#">Related story two with more teaser text and another prompt to continue reading elsewhere.</a>
            </div>
          </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")

        body_text = extractor.extract_body_text(soup)

        self.assertIsNotNone(body_text)
        self.assertIn("first substantial paragraph", body_text)
        self.assertIn("second substantial paragraph", body_text)
        self.assertNotIn("Subscribe to our newsletter", body_text)
        self.assertNotIn("Related story one", body_text)

    def test_extract_author_and_date_prefer_metadata(self):
        html = """
        <html>
          <head>
            <meta name="author" content="Jordan Analyst" />
            <meta name="date" content="2024-02-03T09:30:00Z" />
          </head>
          <body>
            <div class="byline">By Subscribe Team</div>
            <time datetime="2022-01-01">January 1, 2022</time>
          </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")

        self.assertEqual(extractor.extract_author(soup), "Jordan Analyst")
        self.assertEqual(extractor.extract_date(soup), "2024")

    def test_merge_scrape_results_keeps_best_fields(self):
        source = extractor.SourceInput(
            url="https://example.com/story",
            label="Example Story",
            context="Example context",
        )
        baseline = extractor.ScrapedSource(
            url=source.url,
            label=source.label,
            context=source.context,
            domain="example.com",
            live=True,
            http_status=200,
            title="Short title",
            description="Short description",
            body_text="Short body text only.",
            date="2023",
            author=None,
            doi="10.1234/example",
            paywalled=False,
            is_pdf=False,
            json_ld={"type": "WebPage"},
            keywords=["science"],
            word_count=4,
            scrape_method="beautifulsoup",
            scrape_note="partial_content",
            scrape_success=False,
        )
        rendered = extractor.ScrapedSource(
            url=source.url,
            label=source.label,
            context=source.context,
            domain="example.com",
            live=True,
            http_status=200,
            title="A more complete story title",
            description="A more complete description with more context for readers.",
            body_text="Rendered body text with enough additional content to be clearly better than the original baseline output.",
            date="2024",
            author="Taylor Writer",
            doi=None,
            paywalled=False,
            is_pdf=False,
            json_ld={"type": "NewsArticle", "headline": "A more complete story title"},
            keywords=["science", "climate"],
            word_count=16,
            scrape_method="playwright",
            scrape_note="js_rendered",
            scrape_success=True,
        )

        merged = extractor._merge_scrape_results(source, baseline, rendered)

        self.assertEqual(merged.body_text, rendered.body_text)
        self.assertEqual(merged.title, rendered.title)
        self.assertEqual(merged.author, rendered.author)
        self.assertEqual(merged.date, "2023")
        self.assertEqual(merged.doi, "10.1234/example")
        self.assertEqual(merged.scrape_method, "playwright")
        self.assertEqual(merged.scrape_note, "js_rendered")
        self.assertIn("climate", merged.keywords)


@unittest.skipIf(extractor is None, f"dependency missing: {IMPORT_ERROR}")
class ExtractorAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_with_retries_retries_once_after_timeout(self):
        calls = []

        class FakeClient:
            async def request(self, method, url):
                calls.append((method, url))
                if len(calls) == 1:
                    raise httpx.TimeoutException("first timeout")
                return type("Response", (), {"status_code": 200})()

        response = await extractor.request_with_retries(
            FakeClient(),
            "GET",
            "https://example.com",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 2)

    async def test_scrape_sources_deduplicated_fetches_each_url_once(self):
        source_a = extractor.SourceInput(
            url="https://example.com/reused",
            label="First Label",
            context="First context",
        )
        source_b = extractor.SourceInput(
            url="https://example.com/reused",
            label="Second Label",
            context="Second context",
        )
        calls = []

        async def fake_scrape_source(source):
            calls.append(source.url)
            return extractor.ScrapedSource(
                url=source.url,
                label=source.label,
                context=source.context,
                domain="example.com",
                live=True,
                http_status=200,
                title=None,
                description="Description",
                body_text="A body of text that is long enough to count as a successful scrape result.",
                date="2024",
                author="Alex Example",
                doi=None,
                paywalled=False,
                is_pdf=False,
                json_ld=None,
                keywords=["example"],
                word_count=14,
                scrape_method="beautifulsoup",
                scrape_note=None,
                scrape_success=True,
            )

        with patch.object(extractor, "scrape_source", side_effect=fake_scrape_source):
            results = await extractor.scrape_sources_deduplicated([source_a, source_b])

        self.assertEqual(calls, ["https://example.com/reused"])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].label, "First Label")
        self.assertEqual(results[1].label, "Second Label")
        self.assertEqual(results[0].context, "First context")
        self.assertEqual(results[1].context, "Second context")
        self.assertEqual(results[0].title, "First Label")
        self.assertEqual(results[1].title, "Second Label")

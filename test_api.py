import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse
from starlette.requests import Request

import verity_extractor as ve


def make_scraped_source(
    *,
    url="https://example.com/article",
    label="Example article",
    context="The cited article supports the claim.",
    domain="example.com",
):
    return ve.ScrapedSource(
        url=url,
        label=label,
        context=context,
        domain=domain,
        live=True,
        http_status=200,
        title="Example article",
        description="Example description",
        body_text="Example body text that is long enough to be considered a successful scrape.",
        date="2024",
        author="Example Author",
        doi=None,
        paywalled=False,
        is_pdf=False,
        json_ld=None,
        keywords=["example"],
        word_count=24,
        scrape_method="beautifulsoup",
        scrape_note=None,
        scrape_success=True,
    )


def make_request(path, method="GET", headers=None):
    normalized_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": normalized_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "app": ve.app,
    }
    return Request(scope, receive)


class VerityApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_reports_backend_capabilities(self):
        original_model = ve.GITHUB_MODEL
        try:
            ve.GITHUB_MODEL = "gpt-test"
            with patch.object(ve, "_check_llm_available", AsyncMock(return_value=True)):
                payload = await ve.healthcheck(make_request("/health"))
        finally:
            ve.GITHUB_MODEL = original_model

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["llm_enabled"])
        self.assertEqual(payload["llm_model"], "gpt-test")
        self.assertEqual(payload["llm_backend"], "github_models")

    async def test_extract_returns_raw_shape_when_llm_disabled(self):
        scraped = make_scraped_source()
        body = ve.ExtractRequest(
            sources=[
                ve.SourceInput(url=scraped.url, label=scraped.label, context=scraped.context)
            ],
            original_prompt="What does the article say?",
            full_ai_response="It says the article supports the claim.",
        )

        with patch.object(ve, "_check_llm_available", AsyncMock(return_value=False)), patch.object(
            ve, "scrape_source", AsyncMock(return_value=scraped)
        ), patch.object(
            ve, "resolve_authority", AsyncMock(return_value={})
        ):
            extract_handler = getattr(ve.extract, "__wrapped__", ve.extract)
            response = await extract_handler(make_request("/extract", method="POST"), body)

        self.assertIsInstance(response, ve.ExtractResponse)
        self.assertEqual(response.source_count, 1)
        self.assertEqual(response.scraped_sources[0].url, scraped.url)

    async def test_extract_stream_emits_progress_and_scored_result(self):
        scraped = make_scraped_source()
        body = ve.ExtractRequest(
            sources=[
                ve.SourceInput(url=scraped.url, label=scraped.label, context=scraped.context)
            ],
            original_prompt="What does the article say?",
            full_ai_response="It says the article supports the claim.",
        )

        llm_result = {
            "relevance_score": 92,
            "alignment_score": 88,
            "claim_aligned": True,
            "reason": "The article directly supports the claim.",
            "implication": "Safe to treat this source as supportive.",
            "matched_terms": ["article", "claim"],
        }
        authority_result = {
            "oa_cited_by_count": 15,
            "oa_work_type": "journal-article",
            "oa_source_h_index": 50,
            "oa_author_h_index": 12,
            "oa_topics": ["Testing"],
            "authority_profile": {
                "authority_kind": "academic_journal",
                "authority_name": "Testing Journal",
                "authority_source": "openalex",
                "confidence": "high",
                "is_peer_reviewed": True,
                "is_institutional": True,
                "matched_ids": {"doi": "10.1234/example"},
                "evidence": ["openalex:work"],
            },
        }

        with patch.object(ve, "_check_llm_available", AsyncMock(return_value=True)), patch.object(
            ve, "scrape_source", AsyncMock(return_value=scraped)
        ), patch.object(
            ve, "score_source_with_llm", AsyncMock(return_value=llm_result)
        ), patch.object(
            ve, "resolve_authority", AsyncMock(return_value=authority_result)
        ):
            extract_stream_handler = getattr(ve.extract_stream, "__wrapped__", ve.extract_stream)
            response = await extract_stream_handler(make_request("/extract-stream", method="POST"), body)
            self.assertEqual(response.media_type, "text/event-stream")

            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
            stream_body = "".join(chunks)

        self.assertIn("event: progress", stream_body)
        self.assertIn("event: result", stream_body)

        result_payload = None
        for part in stream_body.split("\n\n"):
            if not part.startswith("event: result"):
                continue
            for line in part.splitlines():
                if line.startswith("data: "):
                    result_payload = json.loads(line[6:])
                    break

        self.assertIsNotNone(result_payload)
        self.assertEqual(result_payload["source_count"], 1)
        self.assertEqual(result_payload["sources"][0]["verdict"], "reliable")
        self.assertEqual(result_payload["sources"][0]["signals"]["alignment_score"], 88)
        self.assertEqual(result_payload["sources"][0]["authorship_type"], "named")
        self.assertEqual(result_payload["sources"][0]["author_label"], "Example Author")
        self.assertEqual(result_payload["sources"][0]["authority_source"], "openalex")
        self.assertEqual(result_payload["sources"][0]["signals"]["authority_confidence"], "high")

    async def test_verify_api_key_rejects_missing_bearer_token(self):
        async def call_next(_request):
            return JSONResponse({"ok": True})

        original_key = ve.VERITY_API_KEY
        ve.VERITY_API_KEY = "secret"
        try:
            response = await ve.verify_api_key(
                make_request("/extract", method="POST"),
                call_next,
            )
        finally:
            ve.VERITY_API_KEY = original_key

        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()

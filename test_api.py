import asyncio
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
        body_text="Example body text that is long enough to be considered a successful scrape. " * 8,
        date="2024",
        author="Example Author",
        doi=None,
        paywalled=False,
        is_pdf=False,
        json_ld=None,
        keywords=["example"],
        word_count=160,
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
        original_strict = ve.VERITY_STRICT_EXTENSION_LOCKDOWN
        try:
            ve.GITHUB_MODEL = "gpt-test"
            ve.VERITY_STRICT_EXTENSION_LOCKDOWN = False
            with patch.object(ve, "_check_llm_available", AsyncMock(return_value=True)):
                payload = await ve.healthcheck(make_request("/health"))
        finally:
            ve.GITHUB_MODEL = original_model
            ve.VERITY_STRICT_EXTENSION_LOCKDOWN = original_strict

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["llm_enabled"])
        self.assertEqual(payload["llm_model"], "gpt-test")
        self.assertEqual(payload["llm_backend"], "github_models")

    async def test_health_is_minimal_when_strict_mode_blocks_public_request(self):
        original_strict = ve.VERITY_STRICT_EXTENSION_LOCKDOWN
        original_extension_id = ve.VERITY_EXTENSION_ID
        try:
            ve.VERITY_STRICT_EXTENSION_LOCKDOWN = True
            ve.VERITY_EXTENSION_ID = "abc123"
            payload = await ve.healthcheck(make_request("/health"))
        finally:
            ve.VERITY_STRICT_EXTENSION_LOCKDOWN = original_strict
            ve.VERITY_EXTENSION_ID = original_extension_id

        self.assertEqual(payload, {"status": "ok"})

    async def test_health_is_detailed_for_allowed_extension_origin_in_strict_mode(self):
        original_model = ve.GITHUB_MODEL
        original_strict = ve.VERITY_STRICT_EXTENSION_LOCKDOWN
        original_extension_id = ve.VERITY_EXTENSION_ID
        try:
            ve.GITHUB_MODEL = "gpt-test"
            ve.VERITY_STRICT_EXTENSION_LOCKDOWN = True
            ve.VERITY_EXTENSION_ID = "abc123"
            with patch.object(ve, "_check_llm_available", AsyncMock(return_value=True)):
                payload = await ve.healthcheck(
                    make_request("/health", headers={"Origin": "chrome-extension://abc123"})
                )
        finally:
            ve.GITHUB_MODEL = original_model
            ve.VERITY_STRICT_EXTENSION_LOCKDOWN = original_strict
            ve.VERITY_EXTENSION_ID = original_extension_id

        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["strict_extension_lockdown"])
        self.assertTrue(payload["extension_origin_configured"])
        self.assertEqual(payload["request_policy_mode"], "strict_extension_origin")

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
        ), patch.object(
            ve, "classify_source_authority", AsyncMock(return_value={})
        ):
            extract_handler = getattr(ve.extract, "__wrapped__", ve.extract)
            response = await extract_handler(make_request("/extract", method="POST"), body)

        self.assertIsInstance(response, ve.ExtractResponse)
        self.assertEqual(response.source_count, 1)
        self.assertEqual(response.scraped_sources[0].url, scraped.url)
        self.assertEqual(response.scraped_sources[0].context, scraped.context)

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
        authority_classification = {
            "claim_type": "scholarly_empirical",
            "main_entity": "Testing claim",
            "source_role": "scholarly_primary",
            "does_source_own_entity": False,
            "classifier_confidence": "high",
            "authority_reason": "This source is a scholarly primary source.",
        }

        with patch.object(ve, "_check_llm_available", AsyncMock(return_value=True)), patch.object(
            ve, "scrape_source", AsyncMock(return_value=scraped)
        ), patch.object(
            ve, "score_source_with_llm", AsyncMock(return_value=llm_result)
        ), patch.object(
            ve, "resolve_authority", AsyncMock(return_value=authority_result)
        ), patch.object(
            ve, "classify_source_authority", AsyncMock(return_value=authority_classification)
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
        self.assertEqual(result_payload["sources"][0]["verdict"], "supported")
        self.assertEqual(result_payload["sources"][0]["context"], scraped.context)
        self.assertIn("extraction_stage", result_payload["sources"][0])
        self.assertIn("extraction_strategy", result_payload["sources"][0])
        self.assertIn("retrieval_flags", result_payload["sources"][0])
        self.assertEqual(result_payload["sources"][0]["signals"]["alignment_score"], 88)
        self.assertEqual(result_payload["sources"][0]["signals"]["support_class"], "qualified_support")
        self.assertGreaterEqual(result_payload["sources"][0]["signals"]["retrieval_integrity_score"], 70)
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

    async def test_verify_api_key_allows_extension_origin_in_strict_mode(self):
        async def call_next(_request):
            return JSONResponse({"ok": True})

        original_strict = ve.VERITY_STRICT_EXTENSION_LOCKDOWN
        original_extension_id = ve.VERITY_EXTENSION_ID
        original_key = ve.VERITY_API_KEY
        try:
            ve.VERITY_STRICT_EXTENSION_LOCKDOWN = True
            ve.VERITY_EXTENSION_ID = "abc123"
            ve.VERITY_API_KEY = ""
            response = await ve.verify_api_key(
                make_request("/extract", method="POST", headers={"Origin": "chrome-extension://abc123"}),
                call_next,
            )
        finally:
            ve.VERITY_STRICT_EXTENSION_LOCKDOWN = original_strict
            ve.VERITY_EXTENSION_ID = original_extension_id
            ve.VERITY_API_KEY = original_key

        self.assertEqual(response.status_code, 200)

    async def test_verify_api_key_rejects_public_extract_in_strict_mode_without_api_key(self):
        async def call_next(_request):
            return JSONResponse({"ok": True})

        original_strict = ve.VERITY_STRICT_EXTENSION_LOCKDOWN
        original_extension_id = ve.VERITY_EXTENSION_ID
        original_key = ve.VERITY_API_KEY
        try:
            ve.VERITY_STRICT_EXTENSION_LOCKDOWN = True
            ve.VERITY_EXTENSION_ID = "abc123"
            ve.VERITY_API_KEY = ""
            response = await ve.verify_api_key(
                make_request("/extract", method="POST"),
                call_next,
            )
        finally:
            ve.VERITY_STRICT_EXTENSION_LOCKDOWN = original_strict
            ve.VERITY_EXTENSION_ID = original_extension_id
            ve.VERITY_API_KEY = original_key

        self.assertEqual(response.status_code, 403)


class VerityLlmPayloadTests(unittest.TestCase):
    def test_build_llm_payload_uses_json_mode_and_configured_budget(self):
        original_model = ve.GITHUB_MODEL
        original_budget = ve.LLM_MAX_OUTPUT_TOKENS
        try:
            ve.GITHUB_MODEL = "gpt-4o-mini"
            ve.LLM_MAX_OUTPUT_TOKENS = 400
            payload = ve._build_llm_payload([{"role": "user", "content": "hi"}])
        finally:
            ve.GITHUB_MODEL = original_model
            ve.LLM_MAX_OUTPUT_TOKENS = original_budget

        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["max_tokens"], 400)
        self.assertNotIn("max_completion_tokens", payload)

    def test_build_score_prompt_uses_extended_limits(self):
        original_context = ve.LLM_MAX_CONTEXT_CHARS
        original_prompt = ve.LLM_MAX_PROMPT_CHARS
        original_body = ve.LLM_MAX_BODY_CHARS
        try:
            ve.LLM_MAX_CONTEXT_CHARS = 12
            ve.LLM_MAX_PROMPT_CHARS = 10
            ve.LLM_MAX_BODY_CHARS = 20
            prompt = ve._build_score_prompt(
                context="context-abcdefghijklmnopqrstuvwxyz",
                prompt="prompt-abcdefghijklmnopqrstuvwxyz",
                body="body-abcdefghijklmnopqrstuvwxyz",
            )
        finally:
            ve.LLM_MAX_CONTEXT_CHARS = original_context
            ve.LLM_MAX_PROMPT_CHARS = original_prompt
            ve.LLM_MAX_BODY_CHARS = original_body

        self.assertIn("context-abcd", prompt)
        self.assertIn("prompt-abc", prompt)
        self.assertIn("body-abcdefghijklmno", prompt)
        self.assertIn("first 20 chars", prompt)

    def test_call_llm_retries_http_429_then_succeeds(self):
        class FakeResponse:
            def __init__(self, status_code, payload=None):
                self.status_code = status_code
                self._payload = payload or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise RuntimeError(f"HTTP {self.status_code}")

            def json(self):
                return self._payload

        class FakeClient:
            def __init__(self):
                self.calls = 0

            async def post(self, *_args, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return FakeResponse(429)
                return FakeResponse(
                    200,
                    {
                        "choices": [
                            {
                                "finish_reason": "stop",
                                "message": {"content": '{"ok": true}'},
                            }
                        ]
                    },
                )

        fake_client = FakeClient()
        original_token = ve.GITHUB_TOKEN
        original_retries = ve.LLM_429_MAX_RETRIES
        with patch.object(ve, "_get_llm_client", return_value=fake_client), patch(
            "verity_extractor.asyncio.sleep",
            AsyncMock(),
        ) as sleep_mock:
            try:
                ve.GITHUB_TOKEN = "test-token"
                ve.LLM_429_MAX_RETRIES = 3
                result = asyncio.run(ve._call_llm("hello"))
            finally:
                ve.GITHUB_TOKEN = original_token
                ve.LLM_429_MAX_RETRIES = original_retries

        self.assertEqual(result, '{"ok": true}')
        self.assertEqual(fake_client.calls, 2)
        sleep_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

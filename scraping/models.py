from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SourceInput(BaseModel):
    url: str
    label: str
    context: str

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("URL must use http or https protocol")
        return value


class ScrapedSource(BaseModel):
    url: str
    label: str
    context: str
    domain: str
    live: bool
    http_status: int | None
    title: str | None
    description: str | None
    body_text: str | None
    date: str | None
    author: str | None
    doi: str | None
    pmid: str | None = None
    pmcid: str | None = None
    issn: str | None = None
    journal_name: str | None = None
    publisher_hint: str | None = None
    organization_hint: str | None = None
    site_name: str | None = None
    paywalled: bool
    is_pdf: bool
    json_ld: dict | None
    keywords: list[str]
    word_count: int
    scrape_method: str | None
    scrape_note: str | None
    scrape_success: bool
    extraction_stage: str | None = None
    extraction_strategy: str | None = None
    extraction_confidence: int | None = None
    retrieval_flags: list[str] = Field(default_factory=list)
    candidate_count: int = 0


@dataclass(slots=True)
class PipelineConfig:
    request_timeout_seconds: int
    browser_timeout_seconds: int
    max_response_bytes: int
    max_body_text_chars: int
    max_redirects: int
    enable_playwright_fallback: bool
    browser_user_agent: str
    browser_headers: dict[str, str]
    cache_ttl_seconds: int = 3600
    browser_concurrency: int = 2
    accept_http_confidence: int = 70
    accept_http_word_count: int = 250
    escalate_confidence: int = 60
    escalate_word_count: int = 250
    browser_wait_after_load_ms: int = 750


@dataclass(slots=True)
class FetchResult:
    kind: str
    url: str
    http_status: int | None = None
    html: str | None = None
    content_type: str = ""
    final_url: str | None = None
    error: str | None = None


@dataclass(slots=True)
class BrowserRenderResult:
    kind: str
    html: str | None = None
    http_status: int | None = None
    final_url: str | None = None
    error: str | None = None


@dataclass(slots=True)
class ScrapeCandidate:
    strategy: str
    body_text: str | None
    word_count: int
    confidence: int
    flags: list[str] = field(default_factory=list)
    title: str | None = None
    description: str | None = None
    metadata_only: bool = False
    abstract_only: bool = False


@dataclass(slots=True)
class PageExtraction:
    metadata: dict[str, Any]
    candidates: list[ScrapeCandidate]
    page_flags: list[str]

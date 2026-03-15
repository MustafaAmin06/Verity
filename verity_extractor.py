"""
Verity source extraction and scraping module.
"""

import asyncio
import json
import logging
import os
import re
import time
from urllib.parse import urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:2b")
GEMINI_AVAILABLE = False  # replaced by local Ollama


REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "5"))
MAX_BODY_TEXT_CHARS = int(os.getenv("MAX_BODY_TEXT_CHARS", "2000"))
PLAYWRIGHT_TIMEOUT_SECONDS = int(os.getenv("PLAYWRIGHT_TIMEOUT_SECONDS", "10"))
ENABLE_PLAYWRIGHT_FALLBACK = (
    os.getenv("ENABLE_PLAYWRIGHT_FALLBACK", "true").lower() == "true"
)
EXTRACTOR_PORT = int(os.getenv("EXTRACTOR_PORT", "8001"))
MAX_CONCURRENT_SCRAPES = int(os.getenv("MAX_CONCURRENT_SCRAPES", "5"))
MAX_CONCURRENT_PLAYWRIGHT = int(os.getenv("MAX_CONCURRENT_PLAYWRIGHT", "2"))
MAX_REQUEST_RETRIES = int(os.getenv("MAX_REQUEST_RETRIES", "1"))
RETRY_BACKOFF_SECONDS = float(os.getenv("RETRY_BACKOFF_SECONDS", "0.5"))
OLLAMA_STATUS_TTL_SECONDS = int(os.getenv("OLLAMA_STATUS_TTL_SECONDS", "10"))

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "User-Agent": BROWSER_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

DOI_PATTERN = re.compile(r"10\.\d{4,}/[^\s\"<>&]+", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"(19|20)\d{2}")
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
PLAYWRIGHT_TRIGGER_CHARS = 200
SCRAPE_SUCCESS_MIN_CHARS = 100
PARTIAL_CONTENT_CHARS = 200
logger = logging.getLogger("verity_extractor")


DOMAIN_REGISTRY = {
    "nature.com": {"tier": "academic_journal", "paywalled": True},
    "science.org": {"tier": "academic_journal", "paywalled": True},
    "cell.com": {"tier": "academic_journal", "paywalled": True},
    "thelancet.com": {"tier": "academic_journal", "paywalled": True},
    "nejm.org": {"tier": "academic_journal", "paywalled": True},
    "bmj.com": {"tier": "academic_journal", "paywalled": True},
    "jamanetwork.com": {"tier": "academic_journal", "paywalled": True},
    "pnas.org": {"tier": "academic_journal", "paywalled": False},
    "journals.plos.org": {"tier": "academic_journal", "paywalled": False},
    "plosone.org": {"tier": "academic_journal", "paywalled": False},
    "pubmed.ncbi.nlm.nih.gov": {"tier": "academic_journal", "paywalled": False},
    "ncbi.nlm.nih.gov": {"tier": "academic_journal", "paywalled": False},
    "arxiv.org": {"tier": "academic_journal", "paywalled": False},
    "biorxiv.org": {"tier": "academic_journal", "paywalled": False},
    "medrxiv.org": {"tier": "academic_journal", "paywalled": False},
    "scholar.google.com": {"tier": "academic_journal", "paywalled": False},
    "researchgate.net": {"tier": "academic_journal", "paywalled": False},
    "semanticscholar.org": {"tier": "academic_journal", "paywalled": False},
    "springer.com": {"tier": "academic_journal", "paywalled": True},
    "link.springer.com": {"tier": "academic_journal", "paywalled": True},
    "wiley.com": {"tier": "academic_journal", "paywalled": True},
    "onlinelibrary.wiley.com": {"tier": "academic_journal", "paywalled": True},
    "tandfonline.com": {"tier": "academic_journal", "paywalled": True},
    "journals.sagepub.com": {"tier": "academic_journal", "paywalled": True},
    "sciencedirect.com": {"tier": "academic_journal", "paywalled": True},
    "elsevier.com": {"tier": "academic_journal", "paywalled": True},
    "oxfordacademic.com": {"tier": "academic_journal", "paywalled": True},
    "academic.oup.com": {"tier": "academic_journal", "paywalled": True},
    "who.int": {"tier": "official_body", "paywalled": False},
    "cdc.gov": {"tier": "official_body", "paywalled": False},
    "nih.gov": {"tier": "official_body", "paywalled": False},
    "fda.gov": {"tier": "official_body", "paywalled": False},
    "nhs.uk": {"tier": "official_body", "paywalled": False},
    "gov.uk": {"tier": "official_body", "paywalled": False},
    "nasa.gov": {"tier": "official_body", "paywalled": False},
    "noaa.gov": {"tier": "official_body", "paywalled": False},
    "epa.gov": {"tier": "official_body", "paywalled": False},
    "un.org": {"tier": "official_body", "paywalled": False},
    "ipcc.ch": {"tier": "official_body", "paywalled": False},
    "iea.org": {"tier": "official_body", "paywalled": False},
    "worldbank.org": {"tier": "official_body", "paywalled": False},
    "imf.org": {"tier": "official_body", "paywalled": False},
    "oecd.org": {"tier": "official_body", "paywalled": False},
    "europa.eu": {"tier": "official_body", "paywalled": False},
    "ec.europa.eu": {"tier": "official_body", "paywalled": False},
    "federalreserve.gov": {"tier": "official_body", "paywalled": False},
    "bis.org": {"tier": "official_body", "paywalled": False},
    "stats.oecd.org": {"tier": "official_body", "paywalled": False},
    "bbc.com": {"tier": "established_news", "paywalled": False},
    "bbc.co.uk": {"tier": "established_news", "paywalled": False},
    "reuters.com": {"tier": "established_news", "paywalled": False},
    "apnews.com": {"tier": "established_news", "paywalled": False},
    "nytimes.com": {"tier": "established_news", "paywalled": True},
    "theguardian.com": {"tier": "established_news", "paywalled": False},
    "washingtonpost.com": {"tier": "established_news", "paywalled": True},
    "wsj.com": {"tier": "established_news", "paywalled": True},
    "ft.com": {"tier": "established_news", "paywalled": True},
    "economist.com": {"tier": "established_news", "paywalled": True},
    "bloomberg.com": {"tier": "established_news", "paywalled": True},
    "npr.org": {"tier": "established_news", "paywalled": False},
    "theatlantic.com": {"tier": "established_news", "paywalled": True},
    "newyorker.com": {"tier": "established_news", "paywalled": True},
    "politico.com": {"tier": "established_news", "paywalled": False},
    "foreignaffairs.com": {"tier": "established_news", "paywalled": True},
    "nationalgeographic.com": {"tier": "established_news", "paywalled": False},
    "scientificamerican.com": {"tier": "established_news", "paywalled": True},
    "newscientist.com": {"tier": "established_news", "paywalled": True},
    "technologyreview.com": {"tier": "established_news", "paywalled": True},
    "wired.com": {"tier": "established_news", "paywalled": False},
    "arstechnica.com": {"tier": "established_news", "paywalled": False},
    "theconversation.com": {"tier": "established_news", "paywalled": False},
    "vox.com": {"tier": "established_news", "paywalled": False},
    "fivethirtyeight.com": {"tier": "established_news", "paywalled": False},
    "statista.com": {"tier": "established_news", "paywalled": True},
    "medium.com": {"tier": "independent_blog", "paywalled": False},
    "substack.com": {"tier": "independent_blog", "paywalled": False},
    "wordpress.com": {"tier": "independent_blog", "paywalled": False},
    "blogspot.com": {"tier": "independent_blog", "paywalled": False},
    "tumblr.com": {"tier": "independent_blog", "paywalled": False},
    "reddit.com": {"tier": "independent_blog", "paywalled": False},
    "quora.com": {"tier": "independent_blog", "paywalled": False},
    "wikipedia.org": {"tier": "independent_blog", "paywalled": False},
    "en.wikipedia.org": {"tier": "independent_blog", "paywalled": False},
    "naturalnews.com": {"tier": "flagged", "paywalled": False},
    "infowars.com": {"tier": "flagged", "paywalled": False},
    "breitbart.com": {"tier": "flagged", "paywalled": False},
    "theonion.com": {"tier": "flagged", "paywalled": False},
    "babylonbee.com": {"tier": "flagged", "paywalled": False},
}


def get_domain_info(domain: str) -> dict:
    clean = domain.lower().replace("www.", "").strip("/")
    return DOMAIN_REGISTRY.get(clean, {"tier": "unknown", "paywalled": False})


TOPIC_KEYWORDS = {
    "climate": [
        "climate",
        "carbon",
        "emissions",
        "temperature",
        "greenhouse",
        "warming",
        "tipping",
        "ipcc",
        "fossil",
        "renewable",
        "sea level",
        "arctic",
        "deforestation",
        "methane",
        "net zero",
        "paris agreement",
        "carbon dioxide",
        "co2",
        "atmosphere",
        "glacier",
    ],
    "vaccines": [
        "vaccine",
        "mrna",
        "immunization",
        "efficacy",
        "antibody",
        "clinical trial",
        "pfizer",
        "moderna",
        "immunity",
        "dose",
        "booster",
        "pathogen",
        "herd immunity",
        "vaccination",
        "inoculation",
        "astrazeneca",
        "johnson",
        "viral vector",
        "spike protein",
    ],
    "ai": [
        "artificial intelligence",
        "machine learning",
        "neural network",
        "llm",
        "language model",
        "deep learning",
        "training data",
        "chatgpt",
        "algorithm",
        "bias",
        "hallucination",
        "openai",
        "anthropic",
        "generative ai",
        "transformer",
        "reinforcement learning",
        "large language model",
        "gpt",
        "claude",
        "gemini",
    ],
    "economics": [
        "gdp",
        "inflation",
        "monetary policy",
        "recession",
        "interest rate",
        "fiscal",
        "federal reserve",
        "unemployment",
        "trade",
        "tariff",
        "deficit",
        "debt",
        "supply chain",
        "quantitative easing",
        "central bank",
        "bond yield",
        "economic growth",
        "stagflation",
    ],
    "health": [
        "cancer",
        "diabetes",
        "cardiovascular",
        "mental health",
        "depression",
        "obesity",
        "nutrition",
        "exercise",
        "therapy",
        "surgery",
        "diagnosis",
        "treatment",
        "mortality",
        "clinical",
        "patient",
        "chronic disease",
        "public health",
        "epidemiology",
        "pandemic",
        "outbreak",
    ],
    "geopolitics": [
        "nato",
        "sanctions",
        "sovereignty",
        "diplomatic",
        "geopolitical",
        "conflict",
        "alliance",
        "treaty",
        "foreign policy",
        "warfare",
        "terrorism",
        "nuclear",
        "military",
        "security council",
        "un resolution",
    ],
}


class SourceInput(BaseModel):
    url: str
    label: str
    context: str


class ExtractRequest(BaseModel):
    sources: list[SourceInput]
    original_prompt: str
    full_ai_response: str


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
    paywalled: bool
    is_pdf: bool
    json_ld: dict | None
    keywords: list[str]
    word_count: int
    scrape_method: str | None
    scrape_note: str | None
    scrape_success: bool


class ExtractResponse(BaseModel):
    scraped_sources: list[ScrapedSource]
    original_prompt: str
    full_ai_response: str
    source_count: int
    live_count: int
    dead_count: int
    extraction_time_ms: int


_SHARED_HTTP_CLIENT: httpx.AsyncClient | None = None
_SHARED_PLAYWRIGHT = None
_SHARED_BROWSER = None
_HTTP_CLIENT_LOCK = asyncio.Lock()
_PLAYWRIGHT_LOCK = asyncio.Lock()
_SCRAPE_SEMAPHORE = asyncio.Semaphore(max(1, MAX_CONCURRENT_SCRAPES))
_PLAYWRIGHT_SEMAPHORE = asyncio.Semaphore(max(1, MAX_CONCURRENT_PLAYWRIGHT))
_OLLAMA_STATUS_CACHE = {"checked_at": 0.0, "available": False}


async def get_http_client() -> httpx.AsyncClient:
    global _SHARED_HTTP_CLIENT

    if _SHARED_HTTP_CLIENT is not None and not _SHARED_HTTP_CLIENT.is_closed:
        return _SHARED_HTTP_CLIENT

    async with _HTTP_CLIENT_LOCK:
        if _SHARED_HTTP_CLIENT is None or _SHARED_HTTP_CLIENT.is_closed:
            limits = httpx.Limits(
                max_connections=max(10, MAX_CONCURRENT_SCRAPES * 4),
                max_keepalive_connections=max(5, MAX_CONCURRENT_SCRAPES * 2),
            )
            _SHARED_HTTP_CLIENT = httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS,
                headers=BROWSER_HEADERS,
                follow_redirects=True,
                limits=limits,
            )
            logger.info("Created shared HTTP client")
    return _SHARED_HTTP_CLIENT


async def get_playwright_browser():
    global _SHARED_PLAYWRIGHT, _SHARED_BROWSER

    if not ENABLE_PLAYWRIGHT_FALLBACK or not PLAYWRIGHT_AVAILABLE:
        return None

    if _SHARED_BROWSER is not None and _SHARED_BROWSER.is_connected():
        return _SHARED_BROWSER

    async with _PLAYWRIGHT_LOCK:
        if _SHARED_BROWSER is None or not _SHARED_BROWSER.is_connected():
            _SHARED_PLAYWRIGHT = await async_playwright().start()
            _SHARED_BROWSER = await _SHARED_PLAYWRIGHT.chromium.launch(headless=True)
            logger.info("Created shared Playwright browser")
    return _SHARED_BROWSER


async def close_shared_resources() -> None:
    global _SHARED_HTTP_CLIENT, _SHARED_PLAYWRIGHT, _SHARED_BROWSER

    if _SHARED_HTTP_CLIENT is not None:
        await _SHARED_HTTP_CLIENT.aclose()
        _SHARED_HTTP_CLIENT = None
        logger.info("Closed shared HTTP client")

    if _SHARED_BROWSER is not None:
        await _SHARED_BROWSER.close()
        _SHARED_BROWSER = None
        logger.info("Closed shared Playwright browser")

    if _SHARED_PLAYWRIGHT is not None:
        await _SHARED_PLAYWRIGHT.stop()
        _SHARED_PLAYWRIGHT = None
        logger.info("Stopped Playwright runtime")


# ── Scored response models (returned by /extract when Ollama is available) ──

class SourceSignals(BaseModel):
    domain_tier: str
    domain_score: int
    recency_score: int
    author_score: int
    relevance_score: int
    alignment_score: int
    is_peer_reviewed: bool
    claim_aligned: bool | None
    matched_terms: list[str]


class ScoredSource(BaseModel):
    url: str
    domain: str
    title: str | None
    verdict: str
    verdict_label: str
    color: str
    composite_score: int
    reason: str
    implication: str
    flags: list[str]
    date: str | None
    author: str | None
    paywalled: bool
    signals: SourceSignals


class FurtherReadingItem(BaseModel):
    url: str
    domain: str
    title: str
    date: str | None
    tier: str
    verdict: str


class ScoredResponse(BaseModel):
    sources: list[ScoredSource]
    further_reading: list[FurtherReadingItem]
    topic_detected: str
    source_count: int
    reliable_count: int
    flagged_count: int


# ── Scoring helpers ──

DOMAIN_TIER_SCORES: dict[str, int] = {
    "academic_journal": 100,
    "official_body":    95,
    "established_news": 80,
    "independent_blog": 50,
    "flagged":          10,
    "unknown":          30,
}

TIER_PEER_REVIEWED: set[str] = {"academic_journal"}

VERDICT_MAP = [
    (75, "reliable",   "Looks reliable",     "green"),
    (50, "caution",    "Treat with caution", "amber"),
    (25, "skeptical",  "Be skeptical",       "red"),
    (0,  "unverified", "Couldn't verify",    "gray"),
]


def _compute_domain_score(domain_info: dict) -> int:
    return DOMAIN_TIER_SCORES.get(domain_info.get("tier", "unknown"), 30)


def _compute_recency_score(year: str | None) -> int:
    if not year:
        return 40
    try:
        age = time.gmtime().tm_year - int(year)
        if age <= 1:  return 100
        if age <= 3:  return 90
        if age <= 5:  return 75
        if age <= 10: return 60
        if age <= 20: return 45
        return 30
    except (ValueError, TypeError):
        return 40


def _compute_author_score(author: str | None) -> int:
    return 80 if author else 40


def _verdict_from_score(score: int) -> tuple[str, str, str]:
    for threshold, verdict, label, color in VERDICT_MAP:
        if score >= threshold:
            return verdict, label, color
    return "unverified", "Couldn't verify", "gray"


def _composite_score(domain: int, recency: int, author: int, relevance: int, alignment: int) -> int:
    return int(
        domain    * 0.25 +
        recency   * 0.15 +
        author    * 0.10 +
        relevance * 0.20 +
        alignment * 0.30
    )


def _build_flags(scraped: ScrapedSource) -> list[str]:
    flags: list[str] = []
    if not scraped.live:
        flags.append("url_dead")
    if scraped.scrape_note == "blocked_403":
        flags.append("access_blocked")
    if scraped.paywalled:
        flags.append("paywalled")
    domain_info = get_domain_info(scraped.domain)
    if domain_info.get("tier") == "flagged":
        flags.append("low_credibility_domain")
    return flags


def _detect_topic(text: str) -> str:
    lower = text.lower()
    best_topic, best_count = "general", 0
    for topic, keywords in TOPIC_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in lower)
        if count > best_count:
            best_count, best_topic = count, topic
    return best_topic


# ── Ollama scoring ──

_SCORE_PROMPT = """\
You are a source credibility analyst. Given a scraped web page and the claim an AI made when citing it, \
score the source and explain your assessment.

CLAIM (what the AI said when citing this source):
{context}

ORIGINAL QUESTION:
{prompt}

SOURCE CONTENT (truncated):
{body}

Respond with ONLY valid JSON matching this exact schema:
{{
  "relevance_score": <0-100, how relevant is this source to the original question>,
  "alignment_score": <0-100, how well does the source content support the specific claim above>,
  "claim_aligned": <true if source supports claim, false if it contradicts, null if can't verify>,
  "reason": "<1-2 sentence plain-English explanation of the score>",
  "implication": "<1 sentence telling the user what to do with this source>",
  "matched_terms": [<up to 5 key terms found in both the claim and the source content>]
}}"""

_FURTHER_READING_PROMPT = """\
Suggest exactly 3 high-quality, authoritative sources a reader should consult on this topic.
Prefer academic journals, official bodies, or established news outlets. Use only real URLs.

Topic: {topic}
Original question: {prompt}

Respond with ONLY valid JSON as a list:
[
  {{"url": "...", "title": "...", "domain": "...", "date": "<year or null>", "tier": "<academic_journal|official_body|established_news|independent_blog>", "verdict": "reliable"}},
  ...
]"""


async def _call_llm(prompt: str, timeout: int = 30) -> str | None:
    """Call local Ollama model and return the response text."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.1, "num_ctx": 1024, "num_predict": 256},
                },
            )
            response.raise_for_status()
            data = response.json()
            # Thinking models (qwen3) put output in "thinking" when response is empty
            return data.get("response") or data.get("thinking") or None
    except Exception as exc:
        logger.warning("Ollama call failed: %s", str(exc)[:120])
        return None


async def _check_ollama_available() -> bool:
    now = time.monotonic()
    if now - _OLLAMA_STATUS_CACHE["checked_at"] < OLLAMA_STATUS_TTL_SECONDS:
        return bool(_OLLAMA_STATUS_CACHE["available"])

    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            available = OLLAMA_MODEL in models
    except Exception:
        available = False

    _OLLAMA_STATUS_CACHE["checked_at"] = now
    _OLLAMA_STATUS_CACHE["available"] = available
    return available


def _parse_json_response(text: str | None, fallback):
    if not text:
        return fallback
    try:
        # Strip markdown code fences if present
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return fallback


async def score_source_with_llm(scraped: ScrapedSource, prompt: str) -> dict:
    """Call local LLM to get relevance, alignment and reasoning for one source."""
    body_snippet = (scraped.body_text or scraped.description or "")[:800]
    llm_prompt = _SCORE_PROMPT.format(
        context=scraped.context[:400],
        prompt=prompt[:300],
        body=body_snippet or "(no content retrieved)",
    )
    raw = await _call_llm(llm_prompt, timeout=60)
    fallback = {
        "relevance_score": 50,
        "alignment_score": 50,
        "claim_aligned": None,
        "reason": "Could not assess — LLM unavailable or content restricted.",
        "implication": "Verify this source manually before citing.",
        "matched_terms": [],
    }
    return _parse_json_response(raw, fallback)


async def get_further_reading(topic: str, prompt: str) -> list[FurtherReadingItem]:
    """Ask local LLM for 3 further reading suggestions on the topic."""
    llm_prompt = _FURTHER_READING_PROMPT.format(topic=topic, prompt=prompt[:300])
    raw = await _call_llm(llm_prompt, timeout=10)
    items = _parse_json_response(raw, [])
    # Model sometimes returns {"sources": [...]} instead of a bare list
    if isinstance(items, dict):
        items = next((v for v in items.values() if isinstance(v, list)), [])
    result = []
    for item in items[:3]:
        try:
            result.append(FurtherReadingItem(
                url=item.get("url", ""),
                domain=item.get("domain", "") or extract_domain(item.get("url", "")),
                title=item.get("title", ""),
                date=item.get("date"),
                tier=item.get("tier", "unknown"),
                verdict=item.get("verdict", "reliable"),
            ))
        except Exception:
            continue
    return result


def build_scored_source(scraped: ScrapedSource, llm: dict) -> ScoredSource:
    """Combine scraped metadata + Ollama LLM output into a ScoredSource."""
    domain_info = get_domain_info(scraped.domain)

    domain_score  = _compute_domain_score(domain_info)
    recency_score = _compute_recency_score(scraped.date)
    author_score  = _compute_author_score(scraped.author)
    relevance_score  = int(llm.get("relevance_score", 50))
    alignment_score  = int(llm.get("alignment_score", 50))

    if not scraped.live:
        composite = 0
        verdict, verdict_label, color = "unverified", "Couldn't verify", "gray"
    else:
        composite = _composite_score(domain_score, recency_score, author_score, relevance_score, alignment_score)
        # Flagged domains are capped at skeptical
        if domain_info.get("tier") == "flagged":
            composite = min(composite, 24)
        verdict, verdict_label, color = _verdict_from_score(composite)

    signals = SourceSignals(
        domain_tier=domain_info.get("tier", "unknown"),
        domain_score=domain_score,
        recency_score=recency_score,
        author_score=author_score,
        relevance_score=relevance_score,
        alignment_score=alignment_score,
        is_peer_reviewed=domain_info.get("tier") in TIER_PEER_REVIEWED,
        claim_aligned=llm.get("claim_aligned"),
        matched_terms=llm.get("matched_terms", [])[:5],
    )

    return ScoredSource(
        url=scraped.url,
        domain=scraped.domain,
        title=scraped.title,
        verdict=verdict,
        verdict_label=verdict_label,
        color=color,
        composite_score=composite,
        reason=llm.get("reason", ""),
        implication=llm.get("implication", ""),
        flags=_build_flags(scraped),
        date=scraped.date,
        author=scraped.author,
        paywalled=scraped.paywalled,
        signals=signals,
    )


def extract_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def is_pdf_url(url: str, content_type: str = "") -> bool:
    if url.lower().split("?")[0].endswith(".pdf"):
        return True
    if "application/pdf" in content_type.lower():
        return True
    return False


def build_failure_result(
    source: SourceInput, note: str, http_status: int | None = None
) -> ScrapedSource:
    domain = extract_domain(source.url)
    domain_info = get_domain_info(domain)
    logger.warning(
        "  SCRAPE FAIL  %-40s  status=%-4s  reason=%s",
        domain, http_status or "-", note,
    )

    return ScrapedSource(
        url=source.url,
        label=source.label,
        context=source.context,
        domain=domain,
        live=False,
        http_status=http_status,
        title=None,
        description=None,
        body_text=None,
        date=None,
        author=None,
        doi=None,
        paywalled=domain_info["paywalled"],
        is_pdf=False,
        json_ld=None,
        keywords=[],
        word_count=0,
        scrape_method=None,
        scrape_note=note,
        scrape_success=False,
    )


def _model_dump(model) -> dict:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _clone_result_for_source(
    result: ScrapedSource, source: SourceInput, *, fallback_title: bool = True
) -> ScrapedSource:
    data = _model_dump(result)
    data["url"] = source.url
    data["label"] = source.label
    data["context"] = source.context
    if fallback_title and not data.get("title") and source.label:
        data["title"] = _normalize_whitespace(source.label)
    return ScrapedSource(**data)


def _normalize_whitespace(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized or None


def _flatten_attr_value(value) -> str:
    if isinstance(value, list):
        return " ".join(str(item) for item in value if item)
    return str(value or "")


def _tag_has_marker(tag, markers: tuple[str, ...]) -> bool:
    if not getattr(tag, "attrs", None):
        return False
    haystack = " ".join(
        _flatten_attr_value(tag.get(attr))
        for attr in ("class", "id", "role", "itemprop")
    ).lower()
    return any(marker in haystack for marker in markers)


def _get_meta_content(
    soup: BeautifulSoup, *, names: tuple[str, ...] = (), properties: tuple[str, ...] = ()
) -> str | None:
    name_set = {name.lower() for name in names}
    property_set = {prop.lower() for prop in properties}
    for meta in soup.find_all("meta"):
        name = str(meta.get("name", "")).lower()
        prop = str(meta.get("property", "")).lower()
        if name in name_set or prop in property_set:
            content = _normalize_whitespace(meta.get("content"))
            if content:
                return content
    return None


def _extract_year(value: str | None) -> str | None:
    if not value:
        return None
    match = YEAR_PATTERN.search(str(value))
    return match.group(0) if match else None


def _truncate(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value[:limit].strip() or None


def _trim_text(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    normalized = _normalize_whitespace(value)
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    cutoff = normalized.rfind(" ", 0, limit + 1)
    if cutoff < max(50, int(limit * 0.6)):
        cutoff = limit
    return normalized[:cutoff].rstrip(" ,;:")


def _normalize_keywords(items: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for item in items:
        keyword = _normalize_whitespace(str(item).lower())
        if keyword and keyword not in seen:
            seen.add(keyword)
            normalized.append(keyword)
        if len(normalized) >= 20:
            break
    return normalized


def _coerce_json_ld_name(value) -> str | None:
    if isinstance(value, str):
        return _normalize_whitespace(value)
    if isinstance(value, dict):
        return _normalize_whitespace(value.get("name"))
    if isinstance(value, list):
        parts = []
        for item in value:
            name = _coerce_json_ld_name(item)
            if name:
                parts.append(name)
        return _normalize_whitespace(", ".join(parts))
    return None


def _extract_keywords_from_json_ld(json_ld: dict | None) -> list[str]:
    if not json_ld:
        return []
    raw_keywords = json_ld.get("keywords")
    if isinstance(raw_keywords, str):
        return _normalize_keywords(raw_keywords.split(","))
    if isinstance(raw_keywords, list):
        return _normalize_keywords([str(item) for item in raw_keywords])
    return []


def _iter_json_ld_nodes(payload):
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_json_ld_nodes(item)
        return
    if isinstance(payload, dict):
        if payload.get("@type") or payload.get("type"):
            yield payload
        graph = payload.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _iter_json_ld_nodes(item)


def _score_json_ld_node(node: dict, page_url: str | None = None) -> int:
    node_type = node.get("@type") or node.get("type")
    if isinstance(node_type, list):
        node_type = next((item for item in node_type if isinstance(item, str)), None)
    normalized_type = str(node_type or "").split("/")[-1].lower()

    type_scores = {
        "scholarlyarticle": 70,
        "newsarticle": 65,
        "article": 60,
        "blogposting": 50,
        "webpage": 35,
    }
    score = type_scores.get(normalized_type, 0)

    for field, field_score in (
        ("headline", 12),
        ("name", 10),
        ("datePublished", 10),
        ("author", 10),
        ("description", 8),
        ("keywords", 6),
        ("wordCount", 4),
        ("publisher", 4),
    ):
        if node.get(field):
            score += field_score

    node_url = str(node.get("url") or "")
    if page_url and node_url and node_url.rstrip("/") == page_url.rstrip("/"):
        score += 15

    main_entity = node.get("mainEntityOfPage")
    if isinstance(main_entity, str) and page_url and main_entity.rstrip("/") == page_url.rstrip("/"):
        score += 12
    elif isinstance(main_entity, dict):
        main_entity_id = str(main_entity.get("@id") or main_entity.get("url") or "")
        if page_url and main_entity_id.rstrip("/") == page_url.rstrip("/"):
            score += 12

    return score


def _clean_author_candidate(text: str | None) -> str | None:
    value = _normalize_whitespace(text)
    if not value:
        return None

    value = re.sub(r"^\s*(written by|by|author)\s*[:\-]?\s+", "", value, flags=re.IGNORECASE)
    value = re.split(r"\s*(?:\||/)\s*", value)[0]
    value = re.sub(
        r"\s*(updated|published|last updated|posted)\b.*$",
        "",
        value,
        flags=re.IGNORECASE,
    )
    return value.strip(" ,-:") or None


def _looks_like_author_candidate(text: str | None) -> bool:
    value = _clean_author_candidate(text)
    if not value or len(value) > 150:
        return False

    lower = value.lower()
    noise_markers = (
        "read time",
        "minute read",
        "minutes read",
        "comment",
        "subscribe",
        "newsletter",
        "updated",
        "published",
    )
    if any(marker in lower for marker in noise_markers):
        return False

    if sum(char.isdigit() for char in value) > 3:
        return False

    return bool(re.search(r"[A-Za-z]", value))


def _body_marker_score(tag) -> int:
    positive_markers = (
        "article",
        "article-body",
        "story-body",
        "entry-content",
        "post-content",
        "main-content",
        "content-body",
        "content",
        "post",
        "story",
        "main",
        "body",
    )
    negative_markers = (
        "comment",
        "footer",
        "header",
        "nav",
        "sidebar",
        "share",
        "social",
        "related",
        "recommend",
        "promo",
        "advert",
        "ad-",
        "ads",
        "cookie",
        "consent",
        "newsletter",
        "subscribe",
        "breadcrumb",
        "toolbar",
        "menu",
        "modal",
        "popup",
        "outbrain",
        "taboola",
    )
    haystack = " ".join(
        _flatten_attr_value(tag.get(attr))
        for attr in ("class", "id", "role", "itemprop")
    ).lower()
    score = 0
    if any(marker in haystack for marker in positive_markers):
        score += 250
    if any(marker in haystack for marker in negative_markers):
        score -= 400
    return score


def _score_body_candidate(tag) -> tuple[int, str]:
    text = _normalize_whitespace(tag.get_text(" ", strip=True))
    if not text:
        return (-10_000, "")

    paragraph_texts = [
        _normalize_whitespace(paragraph.get_text(" ", strip=True))
        for paragraph in tag.find_all("p")
    ]
    paragraph_texts = [paragraph for paragraph in paragraph_texts if paragraph]
    paragraph_count = len([paragraph for paragraph in paragraph_texts if len(paragraph) >= 80])
    sentence_count = len(re.findall(r"[.!?]", text))
    link_text_length = 0
    for link in tag.find_all("a"):
        link_text = _normalize_whitespace(link.get_text(" ", strip=True))
        if link_text:
            link_text_length += len(link_text)
    link_density = link_text_length / max(len(text), 1)

    score = len(text)
    score += paragraph_count * 220
    score += sentence_count * 12
    score += _body_marker_score(tag)
    score -= int(link_density * 500)

    return (score, text)


def extract_title(soup: BeautifulSoup, label: str = "") -> str | None:
    og_title = _get_meta_content(soup, properties=("og:title",), names=("og:title",))
    if og_title:
        return og_title

    title_tag = soup.find("title")
    title_text = _normalize_whitespace(title_tag.get_text(" ", strip=True) if title_tag else None)
    if title_text:
        return title_text

    h1 = soup.find("h1")
    h1_text = _normalize_whitespace(h1.get_text(" ", strip=True) if h1 else None)
    if h1_text:
        return h1_text

    return _normalize_whitespace(label)


def extract_description(soup: BeautifulSoup) -> str | None:
    og_description = _get_meta_content(
        soup, properties=("og:description",), names=("og:description",)
    )
    if og_description:
        return og_description

    meta_description = _get_meta_content(soup, names=("description",))
    if meta_description:
        return meta_description

    return None


def extract_date(soup: BeautifulSoup) -> str | None:
    for names, properties in (
        ((), ("article:published_time",)),
        (("date",), ()),
        (("dc.date",), ()),
        (("citation_date",), ()),
        (("citation_publication_date",), ()),
    ):
        year = _extract_year(_get_meta_content(soup, names=names, properties=properties))
        if year:
            return year

    for time_tag in soup.find_all("time"):
        for candidate in (
            time_tag.get("datetime"),
            time_tag.get("content"),
            time_tag.get_text(" ", strip=True),
        ):
            year = _extract_year(candidate)
            if year:
                return year

    candidate_tags = soup.find_all(
        lambda tag: _tag_has_marker(
            tag,
            (
                "date",
                "publish",
                "published",
                "timestamp",
                "dateline",
                "article-meta",
                "article-info",
            ),
        )
    )
    for tag in candidate_tags[:25]:
        text = _normalize_whitespace(tag.get_text(" ", strip=True))
        if not text or len(text) > 120:
            continue
        year = _extract_year(text)
        if year:
            return year

    return None


def extract_author(soup: BeautifulSoup) -> str | None:
    author = _get_meta_content(soup, names=("author",))
    if author:
        return _truncate(author, 150)

    article_author = _get_meta_content(
        soup, names=("article:author",), properties=("article:author",)
    )
    if article_author:
        return _truncate(article_author, 150)

    citation_authors = []
    for meta in soup.find_all("meta"):
        if str(meta.get("name", "")).lower() == "citation_author":
            content = _normalize_whitespace(meta.get("content"))
            if content:
                citation_authors.append(content)
    if citation_authors:
        return _truncate(", ".join(citation_authors), 150)

    candidate_tags = soup.find_all(
        lambda tag: _tag_has_marker(tag, ("author", "byline", "written-by"))
        or str(tag.get("itemprop", "")).lower() == "author"
        or "author" in _flatten_attr_value(tag.get("rel")).lower()
    )
    for candidate in candidate_tags[:25]:
        text = _clean_author_candidate(candidate.get_text(" ", strip=True))
        if _looks_like_author_candidate(text):
            return _truncate(text, 150)

    return None


def extract_doi(soup: BeautifulSoup, html: str) -> str | None:
    for meta_name in ("citation_doi", "dc.identifier"):
        content = _get_meta_content(soup, names=(meta_name,))
        if content:
            match = DOI_PATTERN.search(content)
            if match:
                return match.group(0).rstrip(").,;]")

    match = DOI_PATTERN.search(html or "")
    if match:
        return match.group(0).rstrip(").,;]")

    return None


def extract_body_text(soup: BeautifulSoup) -> str | None:
    working_soup = BeautifulSoup(str(soup), "lxml")

    for tag in working_soup(
        [
            "script",
            "style",
            "nav",
            "header",
            "footer",
            "aside",
            "form",
            "noscript",
            "svg",
            "canvas",
            "iframe",
            "button",
            "input",
        ]
    ):
        tag.decompose()

    noisy_elements = working_soup.find_all(
        lambda tag: _body_marker_score(tag) < -200 and tag.name not in {"body", "html"}
    )
    for tag in noisy_elements:
        tag.decompose()

    candidates = []
    for selector_tag in ("article", "main"):
        candidates.extend(working_soup.find_all(selector_tag))

    candidates.extend(
        working_soup.find_all(
            lambda tag: (
                str(tag.get("role", "")).lower() == "main"
                or _body_marker_score(tag) > 0
            )
            and tag.name in {"section", "div", "article", "main", "body"}
        )
    )
    candidates.extend(working_soup.find_all(["section", "div", "body"]))

    best_tag = None
    best_score = -10_000
    best_text = ""
    seen_ids = set()
    for tag in candidates:
        tag_id = id(tag)
        if tag_id in seen_ids:
            continue
        seen_ids.add(tag_id)

        score, text = _score_body_candidate(tag)
        if score > best_score:
            best_score = score
            best_tag = tag
            best_text = text

    candidate = best_tag or working_soup.body

    if candidate is None:
        return None

    paragraphs = [
        _normalize_whitespace(paragraph.get_text(" ", strip=True))
        for paragraph in candidate.find_all("p")
    ]
    paragraphs = [paragraph for paragraph in paragraphs if paragraph and len(paragraph) >= 40]
    if len(paragraphs) >= 2:
        text = _normalize_whitespace(" ".join(paragraphs))
    else:
        text = best_text or _normalize_whitespace(candidate.get_text(separator=" ", strip=True))
    if not text:
        return None

    text = _trim_text(text, MAX_BODY_TEXT_CHARS)
    return text or None


def extract_keywords(soup: BeautifulSoup) -> list[str]:
    keywords: list[str] = []

    meta_keywords = _get_meta_content(soup, names=("keywords",))
    if meta_keywords:
        keywords.extend(meta_keywords.split(","))

    for meta in soup.find_all("meta"):
        if str(meta.get("property", "")).lower() == "article:tag":
            content = _normalize_whitespace(meta.get("content"))
            if content:
                keywords.append(content)

    return _normalize_keywords(keywords)


def extract_json_ld(soup: BeautifulSoup, page_url: str | None = None) -> dict | None:
    allowed_types = {
        "article",
        "newsarticle",
        "scholarlyarticle",
        "blogposting",
        "webpage",
    }
    best_node = None
    best_score = -1

    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text()
        raw = raw.strip() if raw else ""
        if not raw:
            continue

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue

        for node in _iter_json_ld_nodes(payload):
            node_type = node.get("@type") or node.get("type")
            if isinstance(node_type, list):
                node_type = next((item for item in node_type if isinstance(item, str)), None)
            if not isinstance(node_type, str):
                continue
            normalized_type = node_type.split("/")[-1].lower()
            if normalized_type not in allowed_types:
                continue
            score = _score_json_ld_node(node, page_url=page_url)
            if score > best_score:
                best_score = score
                best_node = node

    if not best_node:
        return None

    node_type = best_node.get("@type") or best_node.get("type")
    if isinstance(node_type, list):
        node_type = next((item for item in node_type if isinstance(item, str)), None)

    simplified: dict = {"type": node_type}

    headline = _normalize_whitespace(best_node.get("headline") or best_node.get("name"))
    if headline:
        simplified["headline"] = headline

    if best_node.get("datePublished"):
        simplified["datePublished"] = str(best_node["datePublished"])

    author = _coerce_json_ld_name(best_node.get("author"))
    if author:
        simplified["author"] = author

    publisher = _coerce_json_ld_name(best_node.get("publisher"))
    if publisher:
        simplified["publisher"] = publisher

    description = _normalize_whitespace(best_node.get("description"))
    if description:
        simplified["description"] = description

    if best_node.get("keywords") is not None:
        simplified["keywords"] = best_node.get("keywords")

    if best_node.get("wordCount") is not None:
        try:
            simplified["wordCount"] = int(best_node.get("wordCount"))
        except (TypeError, ValueError):
            pass

    if best_node.get("isAccessibleForFree") is not None:
        value = best_node.get("isAccessibleForFree")
        if isinstance(value, str):
            simplified["isAccessibleForFree"] = value.strip().lower() == "true"
        else:
            simplified["isAccessibleForFree"] = bool(value)

    return simplified


def detect_paywall(soup: BeautifulSoup, body_text: str | None, domain_info: dict) -> bool:
    if soup.find(
        lambda tag: _tag_has_marker(tag, ("paywall", "subscribe", "premium-content", "locked"))
    ):
        return True

    text_to_scan = (body_text or _normalize_whitespace(soup.get_text(" ", strip=True)) or "").lower()
    phrases = (
        "subscribe to continue",
        "sign in to read",
        "become a member to access",
        "this article is for subscribers",
        "create a free account to read",
        "unlock this article",
        "this content is for subscribers",
    )
    if any(phrase in text_to_scan for phrase in phrases):
        return True

    if domain_info.get("paywalled") and (not body_text or len(body_text) < 300):
        return True

    return False


def _extract_page_fields(
    html: str, label: str, domain_info: dict, page_url: str | None = None
) -> dict:
    soup = BeautifulSoup(html, "lxml")

    json_ld = extract_json_ld(soup, page_url=page_url)
    title = extract_title(soup)
    description = extract_description(soup)
    body_text = extract_body_text(soup)
    date = extract_date(soup)
    author = extract_author(soup)
    doi = extract_doi(soup, html)
    keywords = extract_keywords(soup)
    paywalled = detect_paywall(soup, body_text, domain_info)

    if json_ld:
        if not title and json_ld.get("headline"):
            title = _normalize_whitespace(str(json_ld["headline"]))
        if not description and json_ld.get("description"):
            description = _normalize_whitespace(str(json_ld["description"]))
        if not date and json_ld.get("datePublished"):
            date = _extract_year(str(json_ld["datePublished"]))
        if not author and json_ld.get("author"):
            author = _normalize_whitespace(str(json_ld["author"]))
        if not keywords:
            keywords = _extract_keywords_from_json_ld(json_ld)
        if json_ld.get("isAccessibleForFree") is False:
            paywalled = True

    title = title or _normalize_whitespace(label)
    if not description and body_text:
        description = _trim_text(body_text, 200)

    author = _truncate(author, 150)
    keywords = _normalize_keywords(keywords)
    word_count = len(body_text.split()) if body_text else 0

    return {
        "title": title,
        "description": description,
        "body_text": body_text,
        "date": date,
        "author": author,
        "doi": doi,
        "paywalled": paywalled,
        "json_ld": json_ld,
        "keywords": keywords,
        "word_count": word_count,
    }


def _json_ld_richness_score(json_ld: dict | None) -> int:
    if not json_ld:
        return -1
    score = 0
    for key, value_score in (
        ("headline", 10),
        ("datePublished", 8),
        ("author", 8),
        ("description", 6),
        ("keywords", 4),
        ("wordCount", 2),
        ("publisher", 2),
    ):
        if json_ld.get(key):
            score += value_score
    return score


def _prefer_longer_text(
    current: str | None, candidate: str | None, *, min_gain: int = 10
) -> str | None:
    current_text = _normalize_whitespace(current)
    candidate_text = _normalize_whitespace(candidate)
    if not current_text:
        return candidate_text
    if not candidate_text:
        return current_text
    if len(candidate_text) > len(current_text) + min_gain:
        return candidate_text
    return current_text


def _compute_scrape_note(
    *, body_text: str | None, paywalled: bool, used_playwright: bool = False
) -> str | None:
    if paywalled:
        return "paywall_detected"
    if used_playwright:
        return "js_rendered"
    if body_text and len(body_text) < PARTIAL_CONTENT_CHARS:
        return "partial_content"
    return None


def _merge_scrape_results(
    source: SourceInput, baseline: ScrapedSource, rendered: ScrapedSource
) -> ScrapedSource:
    baseline_data = _model_dump(baseline)
    rendered_data = _model_dump(rendered)

    body_text = baseline.body_text
    used_playwright = False
    if len(rendered.body_text or "") > len(baseline.body_text or ""):
        body_text = rendered.body_text
        used_playwright = True

    title = _prefer_longer_text(baseline.title, rendered.title, min_gain=5)
    description = _prefer_longer_text(baseline.description, rendered.description, min_gain=20)
    author = _prefer_longer_text(baseline.author, rendered.author, min_gain=5)
    doi = baseline.doi or rendered.doi
    date = baseline.date or rendered.date
    json_ld = (
        rendered.json_ld
        if _json_ld_richness_score(rendered.json_ld) > _json_ld_richness_score(baseline.json_ld)
        else baseline.json_ld
    )
    keywords = _normalize_keywords((baseline.keywords or []) + (rendered.keywords or []))
    paywalled = bool(baseline.paywalled or rendered.paywalled)

    for field_name in ("title", "description", "author"):
        if baseline_data.get(field_name) != locals()[field_name] and rendered_data.get(field_name):
            used_playwright = True
    if baseline.doi != doi and rendered.doi:
        used_playwright = True
    if baseline.date != date and rendered.date:
        used_playwright = True
    if baseline.json_ld != json_ld and rendered.json_ld:
        used_playwright = True
    if keywords != (baseline.keywords or []) and rendered.keywords:
        used_playwright = True

    if not title and source.label:
        title = _normalize_whitespace(source.label)

    word_count = len(body_text.split()) if body_text else 0
    scrape_note = _compute_scrape_note(
        body_text=body_text,
        paywalled=paywalled,
        used_playwright=used_playwright,
    )

    return ScrapedSource(
        url=source.url,
        label=source.label,
        context=source.context,
        domain=baseline.domain or rendered.domain or extract_domain(source.url),
        live=baseline.live or rendered.live,
        http_status=rendered.http_status or baseline.http_status,
        title=title,
        description=description,
        body_text=body_text,
        date=date,
        author=_truncate(author, 150),
        doi=doi,
        paywalled=paywalled,
        is_pdf=baseline.is_pdf or rendered.is_pdf,
        json_ld=json_ld,
        keywords=keywords,
        word_count=word_count,
        scrape_method="playwright" if used_playwright else baseline.scrape_method,
        scrape_note=scrape_note,
        scrape_success=bool(body_text and len(body_text) > SCRAPE_SUCCESS_MIN_CHARS),
    )


async def request_with_retries(
    client: httpx.AsyncClient, method: str, url: str
) -> httpx.Response:
    attempts = max(1, MAX_REQUEST_RETRIES + 1)
    last_exception = None

    for attempt in range(1, attempts + 1):
        try:
            response = await client.request(method, url)
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < attempts:
                logger.warning(
                    "Retrying %s %s after status %s (%s/%s)",
                    method,
                    url,
                    response.status_code,
                    attempt,
                    attempts,
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            return response
        except httpx.TimeoutException as exc:
            last_exception = exc
            if attempt < attempts:
                logger.warning(
                    "Retrying %s %s after timeout (%s/%s)",
                    method,
                    url,
                    attempt,
                    attempts,
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise
        except httpx.TransportError as exc:
            last_exception = exc
            if attempt < attempts:
                logger.warning(
                    "Retrying %s %s after transport error %s (%s/%s)",
                    method,
                    url,
                    exc.__class__.__name__,
                    attempt,
                    attempts,
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            raise

    if last_exception is not None:
        raise last_exception
    raise RuntimeError(f"Failed request for {method} {url}")


async def scrape_with_beautifulsoup(source: SourceInput) -> ScrapedSource:
    domain = extract_domain(source.url)
    domain_info = get_domain_info(domain)
    http_status = None
    content_type = ""

    try:
        client = await get_http_client()

        try:
            head_response = await request_with_retries(client, "HEAD", source.url)
            head_status = head_response.status_code
            content_type = head_response.headers.get("content-type", "")
            if head_status < 400 and is_pdf_url(source.url, content_type):
                return ScrapedSource(
                    url=source.url,
                    label=source.label,
                    context=source.context,
                    domain=domain,
                    live=True,
                    http_status=head_status,
                    title=_normalize_whitespace(source.label),
                    description=None,
                    body_text=None,
                    date=None,
                    author=None,
                    doi=None,
                    paywalled=False,
                    is_pdf=True,
                    json_ld=None,
                    keywords=[],
                    word_count=0,
                    scrape_method="head_only",
                    scrape_note="pdf_skipped",
                    scrape_success=False,
                )
            if head_status >= 400:
                logger.info(
                    "HEAD returned %s for %s; continuing with GET",
                    head_status,
                    source.url,
                )
        except httpx.TimeoutException:
            logger.warning("HEAD timed out for %s; continuing with GET", source.url)
        except httpx.TransportError as exc:
            logger.warning(
                "HEAD failed for %s with %s; continuing with GET",
                source.url,
                exc.__class__.__name__,
            )

        get_response = await request_with_retries(client, "GET", source.url)
        http_status = get_response.status_code
        content_type = get_response.headers.get("content-type", content_type)

        if http_status == 403:
            return build_failure_result(source, "blocked_403", http_status=http_status)
        if http_status >= 400:
            return build_failure_result(source, "url_dead", http_status=http_status)
        if is_pdf_url(source.url, content_type):
            return ScrapedSource(
                url=source.url,
                label=source.label,
                context=source.context,
                domain=domain,
                live=True,
                http_status=http_status,
                title=_normalize_whitespace(source.label),
                description=None,
                body_text=None,
                date=None,
                author=None,
                doi=None,
                paywalled=False,
                is_pdf=True,
                json_ld=None,
                keywords=[],
                word_count=0,
                scrape_method="head_only",
                scrape_note="pdf_skipped",
                scrape_success=False,
            )
        html = get_response.text
    except httpx.TimeoutException:
        return build_failure_result(source, "timeout")
    except httpx.TransportError as exc:
        logger.warning("GET transport error for %s: %s", source.url, exc.__class__.__name__)
        return build_failure_result(source, "url_dead")
    except Exception as exc:
        logger.warning("CLIENT fail  %-40s  %s: %s", source.url, type(exc).__name__, exc)
        return build_failure_result(source, "scrape_failed")

    try:
        extracted = _extract_page_fields(
            html,
            source.label,
            domain_info,
            page_url=source.url,
        )
        scrape_note = _compute_scrape_note(
            body_text=extracted["body_text"],
            paywalled=extracted["paywalled"],
        )

        logger.info(
            "SCRAPED OK  %-40s  status=%s  words=%s  method=beautifulsoup  note=%s",
            domain,
            http_status,
            extracted["word_count"],
            scrape_note or "ok",
        )
        return ScrapedSource(
            url=source.url,
            label=source.label,
            context=source.context,
            domain=domain,
            live=True,
            http_status=http_status,
            title=extracted["title"],
            description=extracted["description"],
            body_text=extracted["body_text"],
            date=extracted["date"],
            author=extracted["author"],
            doi=extracted["doi"],
            paywalled=extracted["paywalled"],
            is_pdf=False,
            json_ld=extracted["json_ld"],
            keywords=extracted["keywords"],
            word_count=extracted["word_count"],
            scrape_method="beautifulsoup",
            scrape_note=scrape_note,
            scrape_success=bool(
                extracted["body_text"] and len(extracted["body_text"]) > SCRAPE_SUCCESS_MIN_CHARS
            ),
        )
    except Exception as exc:
        logger.warning("PARSE fail   %-40s  %s: %s", source.url, type(exc).__name__, exc)
        return build_failure_result(source, "scrape_failed", http_status=http_status)


async def scrape_with_playwright(
    source: SourceInput, baseline: ScrapedSource
) -> ScrapedSource:
    if not PLAYWRIGHT_AVAILABLE:
        return baseline

    domain = extract_domain(source.url)
    domain_info = get_domain_info(domain)
    page = None
    context = None
    browser = None

    try:
        browser = await get_playwright_browser()
        if browser is None:
            return baseline

        async with _PLAYWRIGHT_SEMAPHORE:
            context = await browser.new_context(user_agent=BROWSER_USER_AGENT)
            page = await context.new_page()
            response = await page.goto(
                source.url,
                wait_until="domcontentloaded",
                timeout=PLAYWRIGHT_TIMEOUT_SECONDS * 1000,
            )
            try:
                await page.wait_for_load_state(
                    "networkidle", timeout=min(5000, PLAYWRIGHT_TIMEOUT_SECONDS * 1000)
                )
            except Exception:
                pass

            html = await page.content()
            http_status = response.status if response else baseline.http_status
            if http_status == 403:
                return baseline
            if http_status and http_status >= 400:
                return baseline

            extracted = _extract_page_fields(
                html,
                source.label,
                domain_info,
                page_url=source.url,
            )

            playwright_result = ScrapedSource(
                url=source.url,
                label=source.label,
                context=source.context,
                domain=domain,
                live=True,
                http_status=http_status,
                title=extracted["title"],
                description=extracted["description"],
                body_text=extracted["body_text"],
                date=extracted["date"],
                author=extracted["author"],
                doi=extracted["doi"],
                paywalled=extracted["paywalled"],
                is_pdf=False,
                json_ld=extracted["json_ld"],
                keywords=extracted["keywords"],
                word_count=extracted["word_count"],
                scrape_method="playwright",
                scrape_note="js_rendered",
                scrape_success=bool(
                    extracted["body_text"] and len(extracted["body_text"]) > SCRAPE_SUCCESS_MIN_CHARS
                ),
            )
            merged = _merge_scrape_results(source, baseline, playwright_result)
            logger.info("PLAYWRIGHT OK  %-40s  status=%s", domain, http_status)
            return merged
    except Exception as exc:
        logger.warning("PLAYWRIGHT    %-40s  %s: %s", source.url, type(exc).__name__, exc)
        return baseline
    finally:
        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass


async def _scrape_source_impl(source: SourceInput) -> ScrapedSource:
    result = await scrape_with_beautifulsoup(source)
    should_use_playwright = (
        ENABLE_PLAYWRIGHT_FALLBACK
        and result.live
        and not result.is_pdf
        and (result.body_text is None or len(result.body_text) < PLAYWRIGHT_TRIGGER_CHARS)
    )
    if not should_use_playwright:
        return result
    return await scrape_with_playwright(source, result)


async def scrape_source(source: SourceInput) -> ScrapedSource:
    async with _SCRAPE_SEMAPHORE:
        return await _scrape_source_impl(source)


async def scrape_sources_deduplicated(sources: list[SourceInput]) -> list[ScrapedSource]:
    tasks: dict[str, asyncio.Task] = {}
    unique_urls: list[str] = []

    for source in sources:
        if source.url not in tasks:
            canonical_source = SourceInput(url=source.url, label="", context="")
            tasks[source.url] = asyncio.create_task(scrape_source(canonical_source))
            unique_urls.append(source.url)

    unique_results = await asyncio.gather(*(tasks[url] for url in unique_urls))
    results_by_url = {url: result for url, result in zip(unique_urls, unique_results)}

    return [
        _clone_result_for_source(results_by_url[source.url], source)
        for source in sources
    ]


app = FastAPI(title="Verity Extractor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await close_shared_resources()


@app.get("/health")
async def healthcheck() -> dict:
    ollama_ok = await _check_ollama_available()
    return {
        "status": "ok",
        "playwright_enabled": ENABLE_PLAYWRIGHT_FALLBACK and PLAYWRIGHT_AVAILABLE,
        "llm_enabled": ollama_ok,
        "llm_model": OLLAMA_MODEL,
        "llm_backend": "ollama",
    }


@app.post("/extract")
async def extract(request: ExtractRequest):
    start = time.perf_counter()

    logger.info("-" * 60)
    ollama_ok = await _check_ollama_available()
    logger.info(
        "Extract request: %d source(s)  |  Ollama: %s",
        len(request.sources),
        "on" if ollama_ok else "off",
    )
    logger.info("Prompt: %s", request.original_prompt[:120] or "(none)")
    for i, s in enumerate(request.sources, 1):
        logger.info("  [%d] %s  |  label: %s", i, s.url, s.label[:60])

    scraped_sources = await scrape_sources_deduplicated(request.sources)

    scrape_ms = int((time.perf_counter() - start) * 1000)
    live_count = sum(1 for s in scraped_sources if s.live)
    dead_count = len(scraped_sources) - live_count
    logger.info("Scrape done (%dms): %d live, %d dead", scrape_ms, live_count, dead_count)

    # Step 2: if Ollama is available, score all sources + get further reading
    if ollama_ok:
        topic = _detect_topic(request.full_ai_response + " " + request.original_prompt)
        logger.info("Scoring with Ollama (topic: %s)...", topic)

        further_reading_task = asyncio.create_task(
            get_further_reading(topic, request.original_prompt)
        )
        logger.info("  Scoring %d sources in parallel...", len(scraped_sources))
        llm_results = await asyncio.gather(
            *(score_source_with_llm(s, request.original_prompt) for s in scraped_sources)
        )
        further_reading = await further_reading_task

        scored = [build_scored_source(s, llm) for s, llm in zip(scraped_sources, llm_results)]

        # Sort: reliable first, unverified last
        order = {"reliable": 0, "caution": 1, "skeptical": 2, "unverified": 3}
        scored.sort(key=lambda s: order.get(s.verdict, 4))

        reliable_count = sum(1 for s in scored if s.verdict == "reliable")
        flagged_count  = sum(1 for s in scored if s.verdict in ("skeptical", "unverified"))

        total_ms = int((time.perf_counter() - start) * 1000)
        logger.info("Scoring done (%dms total)", total_ms)
        for s in scored:
            logger.info(
                "  %-12s  score=%-3s  %-40s  %s",
                s.verdict, s.composite_score, s.domain, s.reason[:60],
            )
        logger.info("-" * 60)

        return ScoredResponse(
            sources=scored,
            further_reading=further_reading,
            topic_detected=topic,
            source_count=len(scored),
            reliable_count=reliable_count,
            flagged_count=flagged_count,
        )

    # Fallback: return raw scraped data if Ollama is not configured
    for s in scraped_sources:
        status = "live" if s.live else "dead"
        logger.info(
            "  %-4s  %-40s  method=%-14s  words=%s",
            status,
            s.domain,
            s.scrape_method or "-",
            s.word_count or 0,
        )
    logger.info("-" * 60)

    extraction_time_ms = int((time.perf_counter() - start) * 1000)
    return ExtractResponse(
        scraped_sources=list(scraped_sources),
        original_prompt=request.original_prompt,
        full_ai_response=request.full_ai_response,
        source_count=len(scraped_sources),
        live_count=live_count,
        dead_count=dead_count,
        extraction_time_ms=extraction_time_ms,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("verity_extractor:app", host="0.0.0.0", port=EXTRACTOR_PORT, reload=True)

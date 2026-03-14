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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen3.5:9b")
GEMINI_AVAILABLE = False  # replaced by local Ollama


REQUEST_TIMEOUT_SECONDS = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "5"))
MAX_BODY_TEXT_CHARS = int(os.getenv("MAX_BODY_TEXT_CHARS", "2000"))
PLAYWRIGHT_TIMEOUT_SECONDS = int(os.getenv("PLAYWRIGHT_TIMEOUT_SECONDS", "10"))
ENABLE_PLAYWRIGHT_FALLBACK = (
    os.getenv("ENABLE_PLAYWRIGHT_FALLBACK", "true").lower() == "true"
)
EXTRACTOR_PORT = int(os.getenv("EXTRACTOR_PORT", "8001"))

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

DOI_PATTERN = re.compile(r"10\.\d{4,}/[^\s\"<>&]+", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"(19|20)\d{2}")


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


# ── Scored response models (returned by /extract when Gemini is available) ──

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
        age = 2025 - int(year)
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


# ── Gemini scoring ──

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


async def _call_llm(prompt: str) -> str | None:
    """Call local Ollama model and return the response text."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.1},
                },
            )
            response.raise_for_status()
            return response.json().get("response")
    except Exception as exc:
        logging.warning("Ollama call failed: %s", str(exc)[:120])
        return None


async def _check_ollama_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            models = [m["name"] for m in r.json().get("models", [])]
            return OLLAMA_MODEL in models
    except Exception:
        return False


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
    raw = await _call_llm(llm_prompt)
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
    raw = await _call_llm(llm_prompt)
    items = _parse_json_response(raw, [])
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
    """Combine scraped metadata + Gemini LLM output into a ScoredSource."""
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
            tag, ("byline", "author", "date", "publish", "published", "timestamp")
        )
    )
    for tag in candidate_tags[:20]:
        year = _extract_year(tag.get_text(" ", strip=True))
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

    candidate = soup.find(lambda tag: _tag_has_marker(tag, ("author", "byline")))
    if candidate:
        text = _normalize_whitespace(candidate.get_text(" ", strip=True))
        if text:
            text = re.sub(r"^(by|author)\s*[:\-]?\s+", "", text, flags=re.IGNORECASE)
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
        ["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]
    ):
        tag.decompose()

    candidate = working_soup.find("article")
    if candidate is None:
        candidate = working_soup.find("main")
    if candidate is None:
        candidate = working_soup.find(
            attrs={"role": lambda value: value and str(value).lower() == "main"}
        )
    if candidate is None:
        candidate = working_soup.find(
            lambda tag: bool(tag.get("id"))
            and any(
                token in _flatten_attr_value(tag.get("id")).lower()
                for token in ("content", "article")
            )
        )
    if candidate is None:
        candidate = working_soup.find(
            lambda tag: _tag_has_marker(
                tag,
                (
                    "article-body",
                    "article__body",
                    "story-body",
                    "entry-content",
                    "post-content",
                    "main-content",
                    "content",
                ),
            )
        )
    if candidate is None:
        best_tag = None
        best_length = 0
        for tag in working_soup.find_all(["section", "div", "body"]):
            text = _normalize_whitespace(tag.get_text(" ", strip=True))
            if text and len(text) > best_length:
                best_length = len(text)
                best_tag = tag
        candidate = best_tag or working_soup.body

    if candidate is None:
        return None

    text = _normalize_whitespace(candidate.get_text(separator=" ", strip=True))
    if not text:
        return None

    if len(text) > MAX_BODY_TEXT_CHARS:
        text = text[:MAX_BODY_TEXT_CHARS].rstrip()
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


def extract_json_ld(soup: BeautifulSoup) -> dict | None:
    allowed_types = {
        "article",
        "newsarticle",
        "scholarlyarticle",
        "blogposting",
        "webpage",
    }

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

            simplified: dict = {"type": node_type}

            headline = _normalize_whitespace(node.get("headline") or node.get("name"))
            if headline:
                simplified["headline"] = headline

            if node.get("datePublished"):
                simplified["datePublished"] = str(node["datePublished"])

            author = _coerce_json_ld_name(node.get("author"))
            if author:
                simplified["author"] = author

            publisher = _coerce_json_ld_name(node.get("publisher"))
            if publisher:
                simplified["publisher"] = publisher

            description = _normalize_whitespace(node.get("description"))
            if description:
                simplified["description"] = description

            if node.get("keywords") is not None:
                simplified["keywords"] = node.get("keywords")

            if node.get("wordCount") is not None:
                try:
                    simplified["wordCount"] = int(node.get("wordCount"))
                except (TypeError, ValueError):
                    pass

            if node.get("isAccessibleForFree") is not None:
                value = node.get("isAccessibleForFree")
                if isinstance(value, str):
                    simplified["isAccessibleForFree"] = value.strip().lower() == "true"
                else:
                    simplified["isAccessibleForFree"] = bool(value)

            return simplified

    return None


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


def _extract_page_fields(html: str, label: str, domain_info: dict) -> dict:
    soup = BeautifulSoup(html, "lxml")

    json_ld = extract_json_ld(soup)
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
        description = body_text[:200].strip()

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


async def scrape_with_beautifulsoup(source: SourceInput) -> ScrapedSource:
    domain = extract_domain(source.url)
    domain_info = get_domain_info(domain)

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers={"User-Agent": BROWSER_USER_AGENT},
            follow_redirects=True,
        ) as client:
            try:
                head_response = await client.head(source.url)
                http_status = head_response.status_code
                content_type = head_response.headers.get("content-type", "")
            except httpx.TimeoutException:
                return build_failure_result(source, "timeout")
            except Exception:
                return build_failure_result(source, "url_dead")

            if http_status == 403:
                return build_failure_result(source, "blocked_403", http_status=http_status)
            if http_status >= 400 and http_status != 405:
                return build_failure_result(source, "url_dead", http_status=http_status)

            if http_status < 400 and is_pdf_url(source.url, content_type):
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

            try:
                get_response = await client.get(source.url)
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
                    paywalled=domain_info["paywalled"],
                    is_pdf=False,
                    json_ld=None,
                    keywords=[],
                    word_count=0,
                    scrape_method="beautifulsoup",
                    scrape_note="timeout",
                    scrape_success=False,
                )
            except Exception:
                return build_failure_result(source, "scrape_failed", http_status=http_status)

    except Exception:
        return build_failure_result(source, "scrape_failed")

    try:
        extracted = _extract_page_fields(html, source.label, domain_info)
        scrape_note = None
        if extracted["paywalled"]:
            scrape_note = "paywall_detected"
        elif extracted["body_text"] and len(extracted["body_text"]) < 200:
            scrape_note = "partial_content"

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
            scrape_success=bool(extracted["body_text"] and len(extracted["body_text"]) > 100),
        )
    except Exception:
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
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
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

            extracted = _extract_page_fields(html, source.label, domain_info)

            scrape_note = "js_rendered"
            if extracted["paywalled"]:
                scrape_note = "paywall_detected"
            elif extracted["body_text"] and len(extracted["body_text"]) < 200:
                scrape_note = "partial_content"

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
                scrape_note=scrape_note,
                scrape_success=bool(extracted["body_text"] and len(extracted["body_text"]) > 100),
            )

            if len(playwright_result.body_text or "") > len(baseline.body_text or ""):
                return playwright_result
            if playwright_result.scrape_success and not baseline.scrape_success:
                return playwright_result
            return baseline
    except Exception:
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
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass


async def scrape_source(source: SourceInput) -> ScrapedSource:
    result = await scrape_with_beautifulsoup(source)
    should_use_playwright = (
        ENABLE_PLAYWRIGHT_FALLBACK
        and result.live
        and not result.is_pdf
        and (result.body_text is None or len(result.body_text) < 200)
    )
    if not should_use_playwright:
        return result
    return await scrape_with_playwright(source, result)


app = FastAPI(title="Verity Extractor", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

    logging.info("─" * 60)
    ollama_ok = await _check_ollama_available()
    logging.info("Extract request: %d source(s)  |  Ollama: %s", len(request.sources), "on" if ollama_ok else "off")
    logging.info("Prompt: %s", request.original_prompt[:120] or "(none)")
    for i, s in enumerate(request.sources, 1):
        logging.info("  [%d] %s  |  label: %s", i, s.url, s.label[:60])

    # Step 1: scrape all sources concurrently
    scraped_sources = await asyncio.gather(
        *(scrape_source(source) for source in request.sources)
    )

    scrape_ms = int((time.perf_counter() - start) * 1000)
    live_count = sum(1 for s in scraped_sources if s.live)
    dead_count = len(scraped_sources) - live_count
    logging.info("Scrape done (%dms): %d live, %d dead", scrape_ms, live_count, dead_count)

    # Step 2: if Ollama is available, score all sources + get further reading
    if ollama_ok:
        topic = _detect_topic(request.full_ai_response + " " + request.original_prompt)
        logging.info("Scoring with Gemini (topic: %s)...", topic)

        llm_results = []
        for i, s in enumerate(scraped_sources, 1):
            logging.info("  Scoring %d/%d: %s", i, len(scraped_sources), s.domain)
            result = await score_source_with_llm(s, request.original_prompt)
            llm_results.append(result)
        further_reading = await get_further_reading(topic, request.original_prompt)

        scored = [build_scored_source(s, llm) for s, llm in zip(scraped_sources, llm_results)]

        # Sort: reliable first, unverified last
        order = {"reliable": 0, "caution": 1, "skeptical": 2, "unverified": 3}
        scored.sort(key=lambda s: order.get(s.verdict, 4))

        reliable_count = sum(1 for s in scored if s.verdict == "reliable")
        flagged_count  = sum(1 for s in scored if s.verdict in ("skeptical", "unverified"))

        total_ms = int((time.perf_counter() - start) * 1000)
        logging.info("Scoring done (%dms total)", total_ms)
        for s in scored:
            logging.info(
                "  %-12s  score=%-3s  %-40s  %s",
                s.verdict, s.composite_score, s.domain, s.reason[:60],
            )
        logging.info("─" * 60)

        return ScoredResponse(
            sources=scored,
            further_reading=further_reading,
            topic_detected=topic,
            source_count=len(scored),
            reliable_count=reliable_count,
            flagged_count=flagged_count,
        )

    # Fallback: return raw scraped data if Gemini is not configured
    for s in scraped_sources:
        status = "✓ live" if s.live else "✗ dead"
        logging.info("  %s  %-40s  method=%-14s  words=%s", status, s.domain, s.scrape_method or "-", s.word_count or 0)
    logging.info("─" * 60)

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

"""
Verity source extraction and scraping module.
"""

import asyncio
import csv
import hmac
import ipaddress
import json
import logging
import logging.handlers
import os
import pathlib
import re
import socket
import sqlite3
import time
import unicodedata
import uuid
from difflib import SequenceMatcher
from urllib.parse import parse_qs, quote, urlparse

if not logging.root.handlers:
    _log_formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    _stream_handler = logging.StreamHandler()
    _stream_handler.setFormatter(_log_formatter)
    _file_handler = logging.handlers.RotatingFileHandler(
        "verity.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    _file_handler.setFormatter(_log_formatter)
    logging.root.setLevel(logging.INFO)
    logging.root.addHandler(_stream_handler)
    logging.root.addHandler(_file_handler)

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from devtools.triage_catalog import create_capture_run, get_db as get_triage_db, record_observation as triage_record_observation

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

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_MODEL = os.getenv("GITHUB_MODEL", "gpt-4o")
GITHUB_API_URL = "https://models.inference.ai.azure.com/chat/completions"
VERITY_API_KEY = os.getenv("VERITY_API_KEY", "")
VERITY_EXTENSION_ID = os.getenv("VERITY_EXTENSION_ID", "")


def _env_int(name: str, default: str, lo: int = 0, hi: int = 2**31 - 1) -> int:
    raw = os.getenv(name, default)
    try:
        val = int(raw)
    except (ValueError, TypeError):
        logging.warning("Invalid env var %s=%r, using default %s", name, raw, default)
        return int(default)
    return max(lo, min(hi, val))


REQUEST_TIMEOUT_SECONDS = _env_int("REQUEST_TIMEOUT_SECONDS", "5", lo=1, hi=120)
MAX_BODY_TEXT_CHARS = _env_int("MAX_BODY_TEXT_CHARS", "8000", lo=100, hi=500_000)
MAX_RESPONSE_BYTES = _env_int("MAX_RESPONSE_BYTES", str(10 * 1024 * 1024), lo=1024, hi=100 * 1024 * 1024)
MAX_SOURCES_PER_REQUEST = _env_int("MAX_SOURCES_PER_REQUEST", "25", lo=1, hi=100)
PLAYWRIGHT_TIMEOUT_SECONDS = _env_int("PLAYWRIGHT_TIMEOUT_SECONDS", "6", lo=1, hi=60)
VERITY_RELOAD = os.getenv("VERITY_RELOAD", "false").lower() == "true"
ENABLE_PLAYWRIGHT_FALLBACK = (
    os.getenv("ENABLE_PLAYWRIGHT_FALLBACK", "true").lower() == "true"
)
EXTRACTOR_PORT = _env_int("PORT", os.getenv("EXTRACTOR_PORT", "8001"), lo=1, hi=65535)
MAX_REDIRECTS = 5

# ── OpenAlex API configuration ──
OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "")
OPENALEX_API_KEY = os.getenv("OPENALEX_API_KEY", "")
OPENALEX_TIMEOUT_SECONDS = _env_int("OPENALEX_TIMEOUT_SECONDS", "8", lo=1, hi=120)
OPENALEX_CACHE_TTL_SECONDS = _env_int("OPENALEX_CACHE_TTL_SECONDS", "86400", lo=60, hi=604800)
OPENALEX_ENABLED = os.getenv("OPENALEX_ENABLED", "true").lower() == "true"
CROSSREF_MAILTO = os.getenv("CROSSREF_MAILTO", "")
CROSSREF_USER_AGENT = os.getenv("CROSSREF_USER_AGENT", "Verity/1.0")
CROSSREF_TIMEOUT_SECONDS = _env_int("CROSSREF_TIMEOUT_SECONDS", "4", lo=1, hi=120)
CROSSREF_ENABLED = os.getenv("CROSSREF_ENABLED", "true").lower() == "true"
ROR_CLIENT_ID = os.getenv("ROR_CLIENT_ID", "")
ROR_TIMEOUT_SECONDS = _env_int("ROR_TIMEOUT_SECONDS", "3", lo=1, hi=120)
ROR_ENABLED = os.getenv("ROR_ENABLED", "true").lower() == "true"
WIKIDATA_TIMEOUT_SECONDS = _env_int("WIKIDATA_TIMEOUT_SECONDS", "3", lo=1, hi=120)
WIKIDATA_ENABLED = os.getenv("WIKIDATA_ENABLED", "true").lower() == "true"
AUTHORITY_LOOKUP_BUDGET_MS = _env_int("AUTHORITY_LOOKUP_BUDGET_MS", "800", lo=100, hi=10_000)
AUTHORITY_POSITIVE_TTL_SECONDS = _env_int("AUTHORITY_POSITIVE_TTL_SECONDS", str(14 * 86400), lo=300, hi=90 * 86400)
AUTHORITY_SCHOLARLY_TTL_SECONDS = _env_int("AUTHORITY_SCHOLARLY_TTL_SECONDS", str(30 * 86400), lo=300, hi=180 * 86400)
AUTHORITY_NEGATIVE_TTL_SECONDS = _env_int("AUTHORITY_NEGATIVE_TTL_SECONDS", str(3 * 86400), lo=300, hi=30 * 86400)
TRIAGE_CAPTURE_ENABLED = os.getenv("TRIAGE_CAPTURE_ENABLED", "true").lower() == "true"
TRIAGE_DB_PATH = os.getenv(
    "TRIAGE_DB_PATH",
    str(pathlib.Path(__file__).resolve().parent / "devtools" / "verity_bench.db"),
)

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
PMID_PATTERN = re.compile(r"\b(?:pmid[:\s]*)?(\d{5,9})\b", re.IGNORECASE)
PMCID_PATTERN = re.compile(r"\b(PMC\d{4,10})\b", re.IGNORECASE)
ISSN_PATTERN = re.compile(r"\b(\d{4}-?\d{3}[\dxX])\b")
YEAR_PATTERN = re.compile(r"(19|20)\d{2}")
HTML_PARSER = "html.parser"

# ── URL result cache (TTL-based, keyed by URL) ──

_SCRAPE_CACHE: dict[str, tuple[float, "ScrapedSource"]] = {}
_CACHE_TTL_SECONDS = 3600  # 1 hour


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
    "cancer.gov": {"tier": "official_body", "paywalled": False},
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
    "medlineplus.gov": {"tier": "official_body", "paywalled": False},
    "mayoclinic.org": {"tier": "medical_authority", "paywalled": False},
    "clevelandclinic.org": {"tier": "medical_authority", "paywalled": False},
    "cancer.org": {"tier": "medical_authority", "paywalled": False},
    "heart.org": {"tier": "medical_authority", "paywalled": False},
    "diabetes.org": {"tier": "medical_authority", "paywalled": False},
    "hopkinsmedicine.org": {"tier": "medical_authority", "paywalled": False},
    "mdanderson.org": {"tier": "medical_authority", "paywalled": False},
    "merckmanuals.com": {"tier": "medical_authority", "paywalled": False},
    "msdmanuals.com": {"tier": "medical_authority", "paywalled": False},
    "cancerresearchuk.org": {"tier": "medical_authority", "paywalled": False},
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
    "wikipedia.org": {"tier": "reference_tertiary", "paywalled": False},
    "en.wikipedia.org": {"tier": "reference_tertiary", "paywalled": False},
    "naturalnews.com": {"tier": "flagged", "paywalled": False},
    "infowars.com": {"tier": "flagged", "paywalled": False},
    "breitbart.com": {"tier": "flagged", "paywalled": False},
    "theonion.com": {"tier": "flagged", "paywalled": False},
    "babylonbee.com": {"tier": "flagged", "paywalled": False},
}


def get_domain_info(domain: str) -> dict:
    clean = domain.lower().replace("www.", "").strip("/")
    if clean == "wikipedia.org" or clean.endswith(".wikipedia.org"):
        return {"tier": "reference_tertiary", "paywalled": False}
    exact = DOMAIN_REGISTRY.get(clean)
    if exact:
        return exact
    for registered_domain in sorted(DOMAIN_REGISTRY.keys(), key=len, reverse=True):
        if clean.endswith(f".{registered_domain}"):
            return DOMAIN_REGISTRY[registered_domain]
    return {"tier": "unknown", "paywalled": False}


# ── ScimagoJR journal lookup ──

_SCIMAGO_BY_ISSN: dict[str, dict] = {}
_SCIMAGO_BY_TITLE: dict[str, dict] = {}


def _normalize_journal_title(title: str) -> str:
    if not title:
        return ""
    title = title.lower().strip()
    title = unicodedata.normalize("NFD", title)
    title = "".join(c for c in title if unicodedata.category(c) != "Mn")
    title = re.sub(r"[^\w\s]", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _load_scimago_data() -> None:
    csv_path = pathlib.Path(__file__).parent / "scimagojr 2024.csv"
    if not csv_path.exists():
        return
    try:
        with open(csv_path, encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                if row.get("Type", "").lower() != "journal":
                    continue
                quartile = row.get("SJR Best Quartile", "").strip()
                if quartile not in {"Q1", "Q2", "Q3", "Q4"}:
                    quartile = None
                try:
                    sjr = float(row.get("SJR", "0").replace(",", "."))
                except (ValueError, TypeError):
                    sjr = 0.0
                try:
                    h_index = int(row.get("H index", "0"))
                except (ValueError, TypeError):
                    h_index = 0
                open_access = row.get("Open Access", "").strip().lower() == "yes"
                entry = {
                    "quartile": quartile,
                    "sjr": sjr,
                    "h_index": h_index,
                    "open_access": open_access,
                    "journal_title": row.get("Title", "").strip(),
                }
                norm_title = _normalize_journal_title(row.get("Title", ""))
                if norm_title:
                    _SCIMAGO_BY_TITLE[norm_title] = entry
                issn_raw = row.get("Issn", "").strip().strip('"')
                for issn in issn_raw.split(","):
                    issn = issn.strip().replace("-", "")
                    if len(issn) == 8:
                        _SCIMAGO_BY_ISSN[issn] = entry
        logging.info(
            "ScimagoJR loaded: %d ISSNs, %d titles",
            len(_SCIMAGO_BY_ISSN), len(_SCIMAGO_BY_TITLE),
        )
    except Exception as exc:
        logging.warning("ScimagoJR load failed: %s", exc)


def lookup_journal_info(issn: str | None = None, title: str | None = None) -> dict | None:
    """Return ScimagoJR entry for a journal, matched by ISSN then title."""
    if issn:
        clean_issn = issn.strip().replace("-", "")
        if clean_issn in _SCIMAGO_BY_ISSN:
            return _SCIMAGO_BY_ISSN[clean_issn]
    if title:
        norm = _normalize_journal_title(title)
        if norm and norm in _SCIMAGO_BY_TITLE:
            return _SCIMAGO_BY_TITLE[norm]
    return None


_load_scimago_data()


# ── OpenAlex SQLite cache ──────────────────────────────────────────────

_OPENALEX_DB_PATH = pathlib.Path(__file__).parent / "openalex_cache.db"
_openalex_db: sqlite3.Connection | None = None
_AUTHORITY_DB_PATH = pathlib.Path(__file__).parent / "authority_cache.db"
_authority_db: sqlite3.Connection | None = None


def _get_openalex_db() -> sqlite3.Connection:
    global _openalex_db
    if _openalex_db is None:
        _openalex_db = sqlite3.connect(str(_OPENALEX_DB_PATH), check_same_thread=False)
        _openalex_db.execute("PRAGMA journal_mode=WAL")
        _openalex_db.execute("PRAGMA synchronous=NORMAL")
        _openalex_db.executescript("""
            CREATE TABLE IF NOT EXISTS openalex_works (
                lookup_key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                fetched_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS openalex_sources (
                openalex_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                fetched_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS openalex_authors (
                openalex_id TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                fetched_at REAL NOT NULL
            );
        """)
    return _openalex_db


def _get_authority_db() -> sqlite3.Connection:
    global _authority_db
    if _authority_db is None:
        _authority_db = sqlite3.connect(str(_AUTHORITY_DB_PATH), check_same_thread=False)
        _authority_db.execute("PRAGMA journal_mode=WAL")
        _authority_db.execute("PRAGMA synchronous=NORMAL")
        _authority_db.executescript("""
            CREATE TABLE IF NOT EXISTS authority_cache (
                cache_key TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                authority_profile_json TEXT NOT NULL,
                authority_source TEXT,
                confidence TEXT,
                negative INTEGER NOT NULL DEFAULT 0,
                fetched_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );
        """)
    return _authority_db


_OA_ALLOWED_TABLES: dict[str, str] = {
    "openalex_works": "lookup_key",
    "openalex_sources": "openalex_id",
    "openalex_authors": "openalex_id",
}


def _authority_cache_get(cache_key: str) -> dict | None:
    db = _get_authority_db()
    row = db.execute(
        "SELECT authority_profile_json, expires_at FROM authority_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if row is None:
        return None
    payload_json, expires_at = row
    if time.time() > expires_at:
        return None
    return json.loads(payload_json)


def _authority_cache_set(
    cache_key: str,
    scope: str,
    profile: dict,
    *,
    ttl_seconds: int,
    negative: bool = False,
) -> None:
    db = _get_authority_db()
    now = time.time()
    db.execute(
        """INSERT OR REPLACE INTO authority_cache
           (cache_key, scope, authority_profile_json, authority_source, confidence, negative, fetched_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            cache_key,
            scope,
            json.dumps(profile),
            profile.get("authority_source"),
            profile.get("confidence"),
            int(negative),
            now,
            now + ttl_seconds,
        ),
    )
    db.commit()


def _oa_cache_get(table: str, key: str) -> dict | None:
    if table not in _OA_ALLOWED_TABLES:
        raise ValueError(f"Invalid cache table: {table}")
    col = _OA_ALLOWED_TABLES[table]
    db = _get_openalex_db()
    row = db.execute(
        f"SELECT data, fetched_at FROM {table} WHERE {col} = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    data_json, fetched_at = row
    if time.time() - fetched_at > OPENALEX_CACHE_TTL_SECONDS:
        return None
    return json.loads(data_json)


def _oa_cache_set(table: str, key: str, data: dict) -> None:
    if table not in _OA_ALLOWED_TABLES:
        raise ValueError(f"Invalid cache table: {table}")
    col = _OA_ALLOWED_TABLES[table]
    db = _get_openalex_db()
    db.execute(
        f"INSERT OR REPLACE INTO {table} ({col}, data, fetched_at) VALUES (?, ?, ?)",
        (key, json.dumps(data), time.time()),
    )
    db.commit()


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

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must use http or https protocol")
        return v


class ExtractRequest(BaseModel):
    sources: list[SourceInput] = Field(..., max_length=25)
    original_prompt: str = Field(..., max_length=5000)
    full_ai_response: str = Field(..., max_length=50000)


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


class ExtractResponse(BaseModel):
    scraped_sources: list[ScrapedSource]
    original_prompt: str
    full_ai_response: str
    source_count: int
    live_count: int
    dead_count: int
    extraction_time_ms: int


# ── Scored response models (returned by /extract when GitHub Models is available) ──

class SourceSignals(BaseModel):
    domain_tier: str
    domain_score: int
    recency_score: int
    author_score: int
    relevance_score: int
    alignment_score: int
    topic_relevance_score: int
    claim_support_score: int
    retrieval_integrity_score: int
    source_credibility_score: int
    decision_confidence_score: int
    overall_score: int
    support_class: str
    evidence_specificity: str
    contradiction_strength: str
    decision_confidence_level: str
    retrieval_limited: bool = False
    metadata_only: bool = False
    is_peer_reviewed: bool
    claim_aligned: bool | None
    matched_terms: list[str]
    # OpenAlex enrichment signals
    oa_source_h_index: int | None = None
    oa_author_h_index: int | None = None
    oa_cited_by_count: int | None = None
    oa_work_type: str | None = None
    oa_source_type: str | None = None
    authority_source: str | None = None
    authority_confidence: str | None = None
    authority_label: str | None = None


class ScoredSource(BaseModel):
    url: str
    domain: str
    title: str | None
    description: str | None
    context: str
    live: bool
    verdict: str
    verdict_label: str
    color: str
    overall_score: int
    composite_score: int
    reason: str
    implication: str
    flags: list[str]
    date: str | None
    author: str | None
    authorship_type: str = "unknown"
    author_label: str | None = None
    authority_name: str | None = None
    authority_source: str | None = None
    authority_confidence: str | None = None
    matched_ids: dict[str, str] | None = None
    paywalled: bool
    signals: SourceSignals
    # OpenAlex display data
    publisher: str | None = None
    topics: list[str] | None = None
    funders: list[str] | None = None
    author_institution: str | None = None


class ScoredResponse(BaseModel):
    sources: list[ScoredSource]
    topic_detected: str
    source_count: int
    reliable_count: int
    flagged_count: int


class AuthorityProfile(BaseModel):
    authority_kind: str
    authority_name: str | None = None
    authority_source: str
    confidence: str
    is_peer_reviewed: bool = False
    is_institutional: bool = False
    matched_ids: dict[str, str] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


# ── Scoring helpers ──

DOMAIN_TIER_SCORES: dict[str, int] = {
    "academic_journal": 100,
    "official_body":    95,
    "medical_authority": 92,
    "established_news": 80,
    "reference_tertiary": 35,
    "independent_blog": 50,
    "flagged":          10,
    "unknown":          30,
}

SUPPORTED_CLASSES: set[str] = {"direct_support", "qualified_support"}
UNVERIFIED_CLASSES: set[str] = {"topic_relevant_unverified", "mixed_or_ambiguous"}
CONTRADICTION_CLASSES: set[str] = {"contradicted"}
LLM_SUPPORT_CLASSES: set[str] = SUPPORTED_CLASSES | UNVERIFIED_CLASSES | CONTRADICTION_CLASSES
EVIDENCE_SPECIFICITY_VALUES: set[str] = {"direct", "paraphrased", "weak", "none"}
CONTRADICTION_STRENGTH_VALUES: set[str] = {"none", "weak", "moderate", "strong"}
DECISION_CONFIDENCE_LEVELS: tuple[tuple[int, str], ...] = (
    (80, "high"),
    (60, "medium"),
    (0, "low"),
)

TIER_PEER_REVIEWED: set[str] = {"academic_journal"}
INSTITUTIONAL_AUTHORSHIP_TIERS: set[str] = {"academic_journal", "official_body", "medical_authority"}
AUTHORITY_OVERRIDE_PROTECTED_TIERS: set[str] = {"flagged", "reference_tertiary"}
GOVERNMENT_DOMAIN_SUFFIXES: tuple[str, ...] = (
    ".gov",
    ".gov.uk",
    ".gc.ca",
    ".gouv.fr",
    ".gov.au",
    ".europa.eu",
)
REGISTERED_DOMAIN_THIRD_LEVEL_SUFFIXES: tuple[str, ...] = (
    "co.uk",
    "org.uk",
    "ac.uk",
    "gov.uk",
    "com.au",
    "org.au",
    "gov.au",
    "co.jp",
)

VERDICT_MAP = [
    (75, "reliable",   "Looks reliable",     "green"),
    (50, "caution",    "Treat with caution", "amber"),
    (25, "skeptical",  "Be skeptical",       "red"),
    (0,  "unverified", "Couldn't verify",    "gray"),
]


_QUARTILE_SCORES: dict[str, int] = {"Q1": 100, "Q2": 85, "Q3": 70, "Q4": 55}


def _compute_domain_score(domain_info: dict, oa: dict | None = None) -> int:
    quartile = domain_info.get("quartile")
    if quartile and quartile in _QUARTILE_SCORES:
        return _QUARTILE_SCORES[quartile]
    # OpenAlex source h-index fallback when no SCImago quartile
    if oa:
        h = oa.get("oa_source_h_index") or 0
        if h >= 150:
            return 100  # Q1 equivalent
        if h >= 75:
            return 85   # Q2
        if h >= 30:
            return 70   # Q3
        if h > 0:
            return 55   # Q4
    return DOMAIN_TIER_SCORES.get(domain_info.get("tier", "unknown"), 30)


def _compute_recency_score(year: str | None, topic: str = "general") -> int:
    if not year:
        return 35 if topic == "health" else 40
    try:
        age = time.localtime().tm_year - int(year)
        if topic == "health":
            if age <= 1:  return 100
            if age <= 3:  return 85
            if age <= 5:  return 70
            if age <= 10: return 55
            if age <= 20: return 40
            return 25
        if topic == "geopolitics":
            if age <= 1:  return 100
            if age <= 3:  return 90
            if age <= 5:  return 75
            if age <= 10: return 55
            if age <= 20: return 35
            return 20
        if age <= 1:  return 95
        if age <= 3:  return 90
        if age <= 5:  return 80
        if age <= 10: return 65
        if age <= 20: return 50
        return 40
    except (ValueError, TypeError):
        return 35 if topic == "health" else 40


def _classify_authorship(author: str | None, domain_info: dict) -> tuple[str, str]:
    if author:
        return "named", author
    if domain_info.get("tier") in INSTITUTIONAL_AUTHORSHIP_TIERS:
        return "institutional", "Institutional page"
    return "unknown", "Unknown"


def _compute_author_score(author: str | None, domain_info: dict, oa: dict | None = None) -> int:
    if not author:
        if domain_info.get("tier") in INSTITUTIONAL_AUTHORSHIP_TIERS:
            return 80
        return 40
    if oa:
        h = oa.get("oa_author_h_index") or 0
        if h >= 50:
            return 100  # distinguished
        if h >= 20:
            return 95   # established
        if h >= 10:
            return 90   # active
        if h > 0:
            return 85   # published
    return 80


def _clamp_score(value: int | float, *, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(int(round(value)), hi))


def _authority_confidence_score(confidence: str | None) -> int:
    if not confidence:
        return 55
    mapping = {
        "high": 90,
        "medium": 75,
        "low": 60,
    }
    return mapping.get(confidence, 55)


def _compute_retrieval_integrity(scraped: ScrapedSource) -> int:
    if not scraped.live:
        return 0

    note = scraped.scrape_note or ""
    if note == "blocked_403_waf":
        return 0

    if scraped.word_count >= 1200:
        depth_score = 100
    elif scraped.word_count >= 700:
        depth_score = 95
    elif scraped.word_count >= 250:
        depth_score = 90
    elif scraped.word_count >= 100:
        depth_score = 80
    elif scraped.word_count >= 50:
        depth_score = 70
    elif scraped.word_count >= 25:
        depth_score = 55
    elif scraped.word_count > 0:
        depth_score = 35
    else:
        depth_score = 10

    score = depth_score
    if scraped.scrape_success:
        score += 5
    if scraped.scrape_method == "playwright" and scraped.scrape_success:
        score += 5
    if not scraped.body_text and scraped.description:
        score = min(score, 45)

    cap = 100
    if scraped.paywalled or note == "paywall_detected":
        cap = min(cap, 40)
    if scraped.is_pdf or note == "pdf_skipped":
        cap = min(cap, 45)
    if note == "partial_content":
        cap = min(cap, 60)
    if note == "timeout":
        cap = min(cap, 35)
    if note == "consent_only":
        cap = min(cap, 35)
    if not scraped.scrape_success:
        cap = min(cap, 55)

    return _clamp_score(min(score, cap))


def _is_metadata_only(scraped: ScrapedSource, retrieval_integrity_score: int) -> bool:
    if not scraped.body_text:
        return True
    if scraped.word_count < 50:
        return True
    if scraped.paywalled or scraped.is_pdf:
        return True
    return retrieval_integrity_score < 45


def _compute_source_credibility(
    *,
    domain_score: int,
    recency_score: int,
    author_score: int,
    is_peer_reviewed: bool,
    authority_confidence: str | None,
    domain_tier: str,
) -> int:
    authority_score = _authority_confidence_score(authority_confidence)
    peer_evidence_score = 100 if is_peer_reviewed else 70 if authority_score >= 75 else 55
    score = (
        domain_score * 0.50 +
        recency_score * 0.15 +
        author_score * 0.15 +
        authority_score * 0.10 +
        peer_evidence_score * 0.10
    )
    if domain_tier == "flagged":
        score = min(score, 25)
    if domain_tier == "reference_tertiary":
        score = min(score, 74)
    return _clamp_score(score)


def _normalize_llm_result(llm: dict | None) -> dict:
    llm = llm or {}

    topic_relevance_score = _clamp_score(llm.get("topic_relevance_score", llm.get("relevance_score", 50)))
    claim_support_score = _clamp_score(llm.get("claim_support_score", llm.get("alignment_score", 50)))

    support_class = str(llm.get("support_class") or "").strip().lower()
    if support_class not in LLM_SUPPORT_CLASSES:
        if claim_support_score >= 90:
            support_class = "direct_support"
        elif claim_support_score >= 75:
            support_class = "qualified_support"
        elif claim_support_score >= 55 and topic_relevance_score >= 60:
            support_class = "topic_relevant_unverified"
        elif claim_support_score >= 35:
            support_class = "mixed_or_ambiguous"
        else:
            support_class = "contradicted"

    evidence_specificity = str(llm.get("evidence_specificity") or "").strip().lower()
    if evidence_specificity not in EVIDENCE_SPECIFICITY_VALUES:
        if claim_support_score >= 90:
            evidence_specificity = "direct"
        elif claim_support_score >= 75:
            evidence_specificity = "paraphrased"
        elif claim_support_score >= 55:
            evidence_specificity = "weak"
        else:
            evidence_specificity = "none"

    contradiction_strength = str(llm.get("contradiction_strength") or "").strip().lower()
    if contradiction_strength not in CONTRADICTION_STRENGTH_VALUES:
        if support_class != "contradicted":
            contradiction_strength = "none"
        elif claim_support_score < 10:
            contradiction_strength = "strong"
        elif claim_support_score < 25:
            contradiction_strength = "moderate"
        else:
            contradiction_strength = "weak"

    claim_aligned = llm.get("claim_aligned")
    if claim_aligned is None:
        if support_class in SUPPORTED_CLASSES:
            claim_aligned = True
        elif support_class == "contradicted":
            claim_aligned = False

    return {
        "topic_relevance_score": topic_relevance_score,
        "claim_support_score": claim_support_score,
        "relevance_score": topic_relevance_score,
        "alignment_score": claim_support_score,
        "support_class": support_class,
        "evidence_specificity": evidence_specificity,
        "contradiction_strength": contradiction_strength,
        "claim_aligned": claim_aligned,
        "reason": llm.get("reason", ""),
        "implication": llm.get("implication", ""),
        "matched_terms": list(llm.get("matched_terms", []))[:5],
    }


def _compute_claim_support_axis(llm: dict) -> int:
    score = llm["claim_support_score"]
    topic_relevance = llm["topic_relevance_score"]
    support_class = llm["support_class"]
    evidence_specificity = llm["evidence_specificity"]
    contradiction_strength = llm["contradiction_strength"]

    if topic_relevance < 30:
        score = min(score, 35)
    elif topic_relevance < 50:
        score = min(score, 55)

    if support_class == "direct_support":
        if evidence_specificity == "direct":
            score = max(score, 92)
        elif evidence_specificity == "paraphrased":
            score = max(score, 86)
    elif support_class == "qualified_support":
        score = min(max(score, 72), 89)
    elif support_class == "topic_relevant_unverified":
        floor = 55 if topic_relevance >= 70 else 45
        score = min(max(score, floor), 69)
    elif support_class == "mixed_or_ambiguous":
        score = min(max(score, 35), 54)
    elif support_class == "contradicted":
        contradiction_caps = {
            "weak": 39,
            "moderate": 24,
            "strong": 9,
            "none": 29,
        }
        score = min(score, contradiction_caps.get(contradiction_strength, 24))

    return _clamp_score(score)


def _compute_decision_confidence(
    *,
    retrieval_integrity_score: int,
    authority_confidence: str | None,
    support_class: str,
    evidence_specificity: str,
    contradiction_strength: str,
    topic_relevance_score: int,
) -> int:
    authority_score = _authority_confidence_score(authority_confidence)

    clarity_score = 55
    if support_class == "direct_support":
        clarity_score = 95 if evidence_specificity == "direct" else 85
    elif support_class == "qualified_support":
        clarity_score = 75
    elif support_class == "topic_relevant_unverified":
        clarity_score = 60
    elif support_class == "mixed_or_ambiguous":
        clarity_score = 45
    elif support_class == "contradicted":
        contradiction_map = {
            "strong": 85,
            "moderate": 75,
            "weak": 65,
            "none": 60,
        }
        clarity_score = contradiction_map.get(contradiction_strength, 65)

    score = (
        retrieval_integrity_score * 0.50 +
        authority_score * 0.20 +
        clarity_score * 0.30
    )

    if topic_relevance_score < 40 and support_class != "contradicted":
        score = min(score, 60)
    if retrieval_integrity_score < 50:
        score = min(score, 50)
    if evidence_specificity == "none" and support_class in SUPPORTED_CLASSES:
        score = min(score, 65)

    return _clamp_score(score)


def _decision_confidence_level(score: int) -> str:
    for threshold, label in DECISION_CONFIDENCE_LEVELS:
        if score >= threshold:
            return label
    return "low"


def _compute_overall_score(
    *,
    retrieval_integrity_score: int,
    source_credibility_score: int,
    claim_support_score: int,
    decision_confidence_score: int,
    domain_tier: str,
    support_class: str,
) -> int:
    if retrieval_integrity_score == 0:
        return 0

    score = (
        retrieval_integrity_score * 0.20 +
        source_credibility_score * 0.35 +
        claim_support_score * 0.35 +
        decision_confidence_score * 0.10
    )

    if retrieval_integrity_score < 50:
        score = min(score, 59)
    if support_class == "contradicted":
        score = min(score, 24)
    elif support_class in UNVERIFIED_CLASSES:
        score = min(score, 69)
    if domain_tier == "flagged":
        score = min(score, 24)
    if domain_tier == "reference_tertiary" and support_class in SUPPORTED_CLASSES:
        score = min(score, 74)
    if source_credibility_score < 40 and support_class in SUPPORTED_CLASSES:
        score = min(score, 64)

    return _clamp_score(score)


def _verdict_from_matrix(
    *,
    live: bool,
    retrieval_integrity_score: int,
    source_credibility_score: int,
    claim_support_score: int,
    topic_relevance_score: int,
    support_class: str,
    contradiction_strength: str,
) -> tuple[str, str, str]:
    if not live or retrieval_integrity_score < 35:
        return "inaccessible", "Inaccessible or insufficient evidence", "gray"

    if (
        support_class == "contradicted"
        and contradiction_strength in {"moderate", "strong"}
        and retrieval_integrity_score >= 60
    ):
        return "contradicted", "Contradicted by source", "red"

    if (
        source_credibility_score >= 65
        and claim_support_score >= 80
        and retrieval_integrity_score >= 70
        and support_class in SUPPORTED_CLASSES
    ):
        return "supported", "Supported by source", "green"

    if (
        claim_support_score >= 65
        and retrieval_integrity_score >= 55
        and support_class in SUPPORTED_CLASSES
    ):
        return "cautious_support", "Some support, but use caution", "amber"

    if topic_relevance_score >= 40 or support_class in UNVERIFIED_CLASSES or support_class == "contradicted":
        return "relevant_unverified", "Relevant, but not verified", "amber"

    return "inaccessible", "Inaccessible or insufficient evidence", "gray"


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
    if (scraped.scrape_note or "").startswith("blocked_403"):
        flags.append("access_blocked")
    if scraped.paywalled:
        flags.append("paywalled")
    domain_info = get_domain_info(scraped.domain)
    if domain_info.get("tier") == "reference_tertiary":
        flags.append("tertiary_source")
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


# ── LLM scoring ──

_SCORE_SYSTEM_PROMPT = """\
You are a rigorous source credibility analyst. You read web content and score \
how well it supports a specific AI-generated claim. You output ONLY valid JSON — \
no explanations, no markdown, no preamble."""

_SCORE_PROMPT = """\
CLAIM (the statement the AI made when citing this source):
{context}

ORIGINAL QUESTION (what the user asked):
{prompt}

SOURCE CONTENT (truncated to first 2000 chars):
{body}

TASK: Evaluate this source using a real-world verification rubric. Score it, classify the support type, and explain briefly.

SCORING RUBRICS — read these before assigning any number:

topic_relevance_score (0-100): Does the source address the same subject as the original question?
  90-100  The source is entirely about this exact topic with significant depth.
  70-89   The source covers this topic as a primary focus.
  40-69   The source mentions the topic but is mainly about something else.
  10-39   The source is only tangentially related.
  0-9     The source is unrelated to the question.

claim_support_score (0-100): Does the source content support, contradict, or fail to verify the specific claim?
  90-100  Source explicitly states or strongly confirms the claim with direct evidence.
  75-89   Source supports the claim, but the evidence is paraphrased, qualified, or indirect.
  55-74   Source is clearly on-topic but does not provide enough evidence to verify the claim.
  35-54   Source is mixed, ambiguous, or only weakly connected to the claim.
  0-34    Source contradicts the claim or materially undermines it.

support_class:
  direct_support              The claim is directly supported by the source.
  qualified_support           The source supports the claim, but with qualifications or indirect evidence.
  topic_relevant_unverified   The source is relevant, but the specific claim is not verified.
  mixed_or_ambiguous          The source is ambiguous, incomplete, or only weakly related to the claim.
  contradicted                The source contradicts or materially undermines the claim.

evidence_specificity:
  direct       The source directly states the relevant fact or finding.
  paraphrased  The source supports the claim, but not with quote-level specificity.
  weak         The source only loosely suggests the claim.
  none         The source does not provide affirmative evidence for the claim.

contradiction_strength:
  none         No contradiction detected.
  weak         The source creates mild tension with the claim.
  moderate     The source meaningfully undermines the claim.
  strong       The source directly contradicts the claim.

Respond with ONLY valid JSON — no text before or after the JSON object:
{{
  "topic_relevance_score": <integer 0-100>,
  "claim_support_score": <integer 0-100>,
  "support_class": "<direct_support|qualified_support|topic_relevant_unverified|mixed_or_ambiguous|contradicted>",
  "evidence_specificity": "<direct|paraphrased|weak|none>",
  "contradiction_strength": "<none|weak|moderate|strong>",
  "claim_aligned": <true if support_class is direct_support or qualified_support, false if support_class is contradicted, null otherwise>,
  "matched_terms": ["<up to 5 important terms that appeared in the source and informed your judgment>"],
  "reason": "<1-2 sentences: cite specific evidence from the source content that drove your scores>",
  "implication": "<1 sentence: what the user should do given this source's credibility>"
}}"""


_llm_client: httpx.AsyncClient | None = None


def _get_llm_client() -> httpx.AsyncClient:
    """Reuse a single persistent HTTP client for all LLM calls."""
    global _llm_client
    if _llm_client is None or _llm_client.is_closed:
        _llm_client = httpx.AsyncClient(
            timeout=15,
            headers={
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Content-Type": "application/json",
            },
            http2=True,
        )
    return _llm_client


def _build_llm_payload(messages: list[dict[str, str]]) -> dict:
    payload = {
        "model": GITHUB_MODEL,
        "messages": messages,
        "temperature": 0.1,
    }
    # GPT-5 family on GitHub Models expects max_completion_tokens instead of max_tokens.
    if GITHUB_MODEL.startswith("gpt-5"):
        payload["max_completion_tokens"] = 150
    else:
        payload["max_tokens"] = 150
    return payload


async def _call_llm(prompt: str, system: str | None = None) -> str | None:
    """Call GitHub Models API (OpenAI-compatible) and return the response text."""
    if not GITHUB_TOKEN:
        logging.warning("GITHUB_TOKEN not set — skipping LLM call")
        return None

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    client = _get_llm_client()
    for attempt in range(2):
        try:
            response = await client.post(
                GITHUB_API_URL,
                json=_build_llm_payload(messages),
            )
            if response.status_code == 429 and attempt == 0:
                await asyncio.sleep(2)
                continue
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            if attempt == 0 and "429" in str(exc):
                await asyncio.sleep(2)
                continue
            logging.warning("GitHub Models call failed: %s", str(exc)[:120])
            return None
    return None


async def _check_llm_available() -> bool:
    return bool(GITHUB_TOKEN)


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
    """Call local LLM to get support classification and evidence reasoning for one source."""
    body_snippet = (scraped.body_text or scraped.description or "")[:2000]
    llm_prompt = _SCORE_PROMPT.format(
        context=scraped.context[:600],
        prompt=prompt[:500],
        body=body_snippet or "(no content retrieved)",
    )
    raw = await _call_llm(llm_prompt, system=_SCORE_SYSTEM_PROMPT)
    fallback = {
        "topic_relevance_score": 50,
        "claim_support_score": 50,
        "support_class": "mixed_or_ambiguous",
        "evidence_specificity": "none",
        "contradiction_strength": "none",
        "claim_aligned": None,
        "reason": "Could not assess — LLM unavailable or content restricted.",
        "implication": "Verify this source manually before citing.",
        "matched_terms": [],
    }
    return _parse_json_response(raw, fallback)


# ── OpenAlex API client ────────────────────────────────────────────────

_openalex_client: httpx.AsyncClient | None = None
_crossref_client: httpx.AsyncClient | None = None
_ror_client: httpx.AsyncClient | None = None
_wikidata_client: httpx.AsyncClient | None = None


def _get_openalex_client() -> httpx.AsyncClient:
    """Reuse a single persistent HTTP client for all OpenAlex calls."""
    global _openalex_client
    if _openalex_client is None or _openalex_client.is_closed:
        ua = "Verity/1.0 (source verification tool)"
        if OPENALEX_EMAIL:
            ua += f" (mailto:{OPENALEX_EMAIL})"
        _openalex_client = httpx.AsyncClient(
            base_url="https://api.openalex.org",
            timeout=OPENALEX_TIMEOUT_SECONDS,
            headers={"User-Agent": ua, "Accept": "application/json"},
            http2=True,
        )
    return _openalex_client


def _get_crossref_client() -> httpx.AsyncClient:
    global _crossref_client
    if _crossref_client is None or _crossref_client.is_closed:
        ua = CROSSREF_USER_AGENT
        if CROSSREF_MAILTO:
            ua += f" (mailto:{CROSSREF_MAILTO})"
        _crossref_client = httpx.AsyncClient(
            base_url="https://api.crossref.org",
            timeout=CROSSREF_TIMEOUT_SECONDS,
            headers={"User-Agent": ua, "Accept": "application/json"},
            http2=True,
        )
    return _crossref_client


def _get_ror_client() -> httpx.AsyncClient:
    global _ror_client
    if _ror_client is None or _ror_client.is_closed:
        headers = {"Accept": "application/json", "User-Agent": "Verity/1.0"}
        if ROR_CLIENT_ID:
            headers["Client-Id"] = ROR_CLIENT_ID
        _ror_client = httpx.AsyncClient(
            base_url="https://api.ror.org/v2",
            timeout=ROR_TIMEOUT_SECONDS,
            headers=headers,
            http2=True,
        )
    return _ror_client


def _get_wikidata_client() -> httpx.AsyncClient:
    global _wikidata_client
    if _wikidata_client is None or _wikidata_client.is_closed:
        _wikidata_client = httpx.AsyncClient(
            base_url="https://www.wikidata.org",
            timeout=WIKIDATA_TIMEOUT_SECONDS,
            headers={"Accept": "application/json", "User-Agent": "Verity/1.0"},
            http2=True,
        )
    return _wikidata_client


async def _openalex_get(path: str, params: dict | None = None) -> dict | None:
    """GET from OpenAlex with exponential back-off on 429."""
    client = _get_openalex_client()
    merged_params = dict(params or {})
    if OPENALEX_API_KEY:
        merged_params.setdefault("api_key", OPENALEX_API_KEY)
    for attempt in range(3):
        try:
            resp = await client.get(path, params=merged_params)
            if resp.status_code == 429:
                wait = 2 ** attempt
                logging.warning("OpenAlex 429, retry in %ds", wait)
                await asyncio.sleep(wait)
                continue
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            logging.warning("OpenAlex timeout on %s (attempt %d)", path, attempt + 1)
            if attempt < 2:
                await asyncio.sleep(1)
                continue
            return None
        except Exception as exc:
            logging.warning("OpenAlex error: %s", str(exc)[:120])
            return None
    return None


_OA_WORK_SELECT = (
    "id,doi,title,publication_year,cited_by_count,type,ids,"
    "primary_location,best_oa_location,authorships,topics,primary_topic,open_access"
)


async def lookup_openalex_work(
    doi: str | None,
    url: str | None,
    *,
    pmid: str | None = None,
    pmcid: str | None = None,
) -> dict | None:
    """Look up a work by DOI (preferred) or URL fallback.

    Always uses the filter/list endpoint to avoid path-encoding issues with
    DOIs that contain forward slashes (e.g. 10.48550/arXiv.1706.03762).
    """
    if not OPENALEX_ENABLED:
        return None

    lookup_attempts: list[tuple[str, str, str]] = []
    if doi:
        lookup_attempts.append((doi.lower().strip(), "doi", doi))
    if pmid:
        lookup_attempts.append((f"pmid:{pmid}", "ids.pmid", pmid))
    if pmcid:
        lookup_attempts.append((f"pmcid:{pmcid.upper()}", "ids.pmcid", pmcid.upper()))
    if url:
        lookup_attempts.append((url.lower().strip(), "locations.landing_page_url", url))

    for key, filter_name, filter_value in lookup_attempts:
        cached = _oa_cache_get("openalex_works", key)
        if cached:
            return cached
        data = await _openalex_get(
            "/works",
            params={
                "filter": f"{filter_name}:{filter_value}",
                "per_page": "1",
                "select": _OA_WORK_SELECT,
            },
        )
        if data and data.get("results"):
            work = data["results"][0]
            _oa_cache_set("openalex_works", key, work)
            return work

    return None


async def lookup_openalex_source(source_id: str) -> dict | None:
    """Look up an OpenAlex Source (journal/venue) by its OpenAlex ID."""
    if not OPENALEX_ENABLED or not source_id:
        return None
    short_id = source_id.split("/")[-1]
    cached = _oa_cache_get("openalex_sources", short_id)
    if cached:
        return cached
    data = await _openalex_get(
        f"/sources/{short_id}",
        params={"select": "id,display_name,type,issn_l,issn,host_organization_name,is_oa,summary_stats"},
    )
    if data:
        _oa_cache_set("openalex_sources", short_id, data)
    return data


async def lookup_openalex_author(author_id: str) -> dict | None:
    """Look up an OpenAlex Author by their OpenAlex ID."""
    if not OPENALEX_ENABLED or not author_id:
        return None
    short_id = author_id.split("/")[-1]
    cached = _oa_cache_get("openalex_authors", short_id)
    if cached:
        return cached
    data = await _openalex_get(
        f"/authors/{short_id}",
        params={"select": "id,display_name,summary_stats,last_known_institutions"},
    )
    if data:
        _oa_cache_set("openalex_authors", short_id, data)
    return data


async def _noop_coro():
    """Placeholder coroutine that returns None."""
    return None


def _authority_cache_keys(scraped: "ScrapedSource") -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    if scraped.doi:
        keys.append(("work", f"doi:{scraped.doi.lower()}"))
    if scraped.pmid:
        keys.append(("work", f"pmid:{scraped.pmid}"))
    if scraped.pmcid:
        keys.append(("work", f"pmcid:{scraped.pmcid.upper()}"))
    if scraped.issn:
        keys.append(("journal", f"issn:{scraped.issn.lower()}"))
    reg_domain = registered_domain(scraped.domain)
    if reg_domain:
        keys.append(("domain", f"domain:{reg_domain}"))
    return keys


def _bootstrap_authority_profile(scraped: "ScrapedSource") -> AuthorityProfile:
    domain_info = get_domain_info(scraped.domain)
    tier = domain_info.get("tier", "unknown")
    return AuthorityProfile(
        authority_kind=tier,
        authority_name=scraped.publisher_hint or scraped.site_name or registered_domain(scraped.domain) or scraped.domain,
        authority_source="registry",
        confidence="medium" if tier != "unknown" else "low",
        is_peer_reviewed=tier == "academic_journal",
        is_institutional=tier in INSTITUTIONAL_AUTHORSHIP_TIERS,
        matched_ids={"domain": registered_domain(scraped.domain)} if scraped.domain else {},
        evidence=[f"registry:{tier}"],
    )


def _profile_ttl_seconds(profile: AuthorityProfile, scope: str) -> int:
    if scope in {"work", "journal"} and profile.authority_kind == "academic_journal":
        return AUTHORITY_SCHOLARLY_TTL_SECONDS
    if profile.confidence == "low":
        return AUTHORITY_NEGATIVE_TTL_SECONDS
    return AUTHORITY_POSITIVE_TTL_SECONDS


def _time_left_seconds(deadline: float) -> float:
    return max(0.0, deadline - time.perf_counter())


def _remaining_timeout(deadline: float, provider_default: int) -> float | None:
    remaining = _time_left_seconds(deadline)
    if remaining <= 0:
        return None
    return max(0.1, min(float(provider_default), remaining))


async def _crossref_get(path: str, params: dict | None = None, timeout_s: float | None = None) -> dict | None:
    if not CROSSREF_ENABLED:
        return None
    client = _get_crossref_client()
    query = dict(params or {})
    if CROSSREF_MAILTO:
        query.setdefault("mailto", CROSSREF_MAILTO)
    try:
        resp = await client.get(path, params=query, timeout=timeout_s or CROSSREF_TIMEOUT_SECONDS)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logging.warning("Crossref error: %s", str(exc)[:120])
        return None


async def _ror_search(query: str, timeout_s: float | None = None) -> dict | None:
    if not ROR_ENABLED or not query:
        return None
    client = _get_ror_client()
    try:
        resp = await client.get(
            "/organizations",
            params={"query": query, "page": 1, "affiliation": "false"},
            timeout=timeout_s or ROR_TIMEOUT_SECONDS,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logging.warning("ROR error: %s", str(exc)[:120])
        return None


async def _wikidata_search(query: str, timeout_s: float | None = None) -> dict | None:
    if not WIKIDATA_ENABLED or not query:
        return None
    client = _get_wikidata_client()
    try:
        resp = await client.get(
            "/w/api.php",
            params={
                "action": "wbsearchentities",
                "search": query,
                "language": "en",
                "limit": 5,
                "format": "json",
                "origin": "*",
            },
            timeout=timeout_s or WIKIDATA_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logging.warning("Wikidata search error: %s", str(exc)[:120])
        return None


async def _wikidata_entities(ids: list[str], timeout_s: float | None = None) -> dict | None:
    if not WIKIDATA_ENABLED or not ids:
        return None
    client = _get_wikidata_client()
    try:
        resp = await client.get(
            "/w/api.php",
            params={
                "action": "wbgetentities",
                "ids": "|".join(ids),
                "props": "claims|labels|descriptions",
                "languages": "en",
                "format": "json",
                "origin": "*",
            },
            timeout=timeout_s or WIKIDATA_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logging.warning("Wikidata entity error: %s", str(exc)[:120])
        return None


async def lookup_openalex_source_by_issn(issn: str) -> dict | None:
    if not OPENALEX_ENABLED or not issn:
        return None
    clean = issn.strip().replace("-", "")
    cache_key = f"issn:{clean}"
    cached = _oa_cache_get("openalex_sources", cache_key)
    if cached:
        return cached
    data = await _openalex_get(
        "/sources",
        params={
            "filter": f"issn:{issn}",
            "per_page": "1",
            "select": "id,display_name,type,issn_l,issn,host_organization_name,is_oa,summary_stats",
        },
    )
    if data and data.get("results"):
        source = data["results"][0]
        _oa_cache_set("openalex_sources", cache_key, source)
        return source
    return None


async def lookup_crossref_work(doi: str, timeout_s: float | None = None) -> dict | None:
    if not doi:
        return None
    return await _crossref_get(f"/works/{quote(doi, safe='')}", timeout_s=timeout_s)


async def lookup_crossref_journal(issn: str, timeout_s: float | None = None) -> dict | None:
    if not issn:
        return None
    return await _crossref_get(f"/journals/{quote(issn, safe='')}", timeout_s=timeout_s)


def _domain_is_government(domain: str) -> bool:
    reg = registered_domain(domain)
    return any(reg.endswith(suffix.lstrip(".")) or reg == suffix.lstrip(".") for suffix in GOVERNMENT_DOMAIN_SUFFIXES)


def _wikidata_official_website_domain(entity: dict) -> str | None:
    claims = entity.get("claims") or {}
    website_claims = claims.get("P856") or []
    for claim in website_claims:
        try:
            website = claim["mainsnak"]["datavalue"]["value"]
        except Exception:
            continue
        website_domain = extract_domain(website)
        if website_domain:
            return registered_domain(website_domain)
    return None


def _wikidata_description(entity: dict) -> str:
    descriptions = entity.get("descriptions") or {}
    for lang in ("en", "en-gb", "en-us"):
        if lang in descriptions:
            return descriptions[lang].get("value", "")
    return ""


def _wikidata_is_institutional(entity: dict) -> bool:
    description = _wikidata_description(entity).lower()
    return any(
        token in description
        for token in ("hospital", "medical", "health", "clinic", "research institute", "charity", "nonprofit", "government")
    )


def _merge_evidence(*parts: str) -> list[str]:
    return [part for part in parts if part]


def _profile_from_openalex(scraped: "ScrapedSource", enrichment: dict) -> AuthorityProfile | None:
    if not enrichment:
        return None
    matched_ids = {}
    if scraped.doi:
        matched_ids["doi"] = scraped.doi
    if scraped.pmid:
        matched_ids["pmid"] = scraped.pmid
    if scraped.pmcid:
        matched_ids["pmcid"] = scraped.pmcid
    if enrichment.get("oa_work_id"):
        matched_ids["openalex_work_id"] = enrichment["oa_work_id"]
    if enrichment.get("oa_source_id"):
        matched_ids["openalex_source_id"] = enrichment["oa_source_id"]
    if enrichment.get("oa_source_type") == "journal" or enrichment.get("oa_work_type") in {"journal-article", "review"}:
        return AuthorityProfile(
            authority_kind="academic_journal",
            authority_name=enrichment.get("oa_source_display_name") or enrichment.get("oa_publisher") or scraped.journal_name,
            authority_source="openalex",
            confidence="high" if scraped.doi or scraped.pmid or scraped.pmcid else "medium",
            is_peer_reviewed=True,
            is_institutional=True,
            matched_ids=matched_ids,
            evidence=_merge_evidence("openalex:work", enrichment.get("oa_work_type"), enrichment.get("oa_source_type")),
        )
    return None


def _profile_from_crossref(scraped: "ScrapedSource", message: dict) -> AuthorityProfile | None:
    if not message:
        return None
    publisher = message.get("publisher") or scraped.publisher_hint or scraped.site_name
    work_type = message.get("type") or ""
    is_journal = bool(message.get("container-title")) or work_type in {"journal-article", "journal-issue", "journal-volume"}
    if is_journal:
        matched_ids = {}
        if scraped.doi:
            matched_ids["doi"] = scraped.doi
        if scraped.issn:
            matched_ids["issn"] = scraped.issn
        return AuthorityProfile(
            authority_kind="academic_journal",
            authority_name=publisher or scraped.journal_name,
            authority_source="crossref",
            confidence="high" if scraped.doi else "medium",
            is_peer_reviewed=True,
            is_institutional=True,
            matched_ids=matched_ids,
            evidence=_merge_evidence("crossref:work", work_type),
        )
    return None


async def _resolve_institutional_profile(scraped: "ScrapedSource", deadline: float) -> AuthorityProfile | None:
    org_hint = scraped.organization_hint or scraped.publisher_hint or scraped.site_name
    if not org_hint:
        return None

    reg_domain = registered_domain(scraped.domain)

    ror_timeout = _remaining_timeout(deadline, ROR_TIMEOUT_SECONDS)
    if ror_timeout is not None:
        ror_payload = await _ror_search(org_hint, timeout_s=ror_timeout)
        items = (ror_payload or {}).get("items") or []
        best_match = None
        best_score = 0.0
        for item in items:
            org = item.get("organization") or item
            names = [org.get("name", "")] + (org.get("aliases") or []) + (org.get("acronyms") or [])
            similarity = max((_name_similarity(org_hint, name) for name in names if name), default=0.0)
            domains = [
                registered_domain(extract_domain(link.get("value", "")) or link.get("value", ""))
                for link in (org.get("links") or [])
                if link.get("value")
            ]
            domain_match = reg_domain in domains
            score = similarity + (0.5 if domain_match else 0.0)
            if score > best_score:
                best_score = score
                best_match = (org, similarity, domain_match)
        if best_match and best_match[2] and best_match[1] >= 0.72:
            org, similarity, _domain_match = best_match
            type_text = " ".join(org.get("types") or []).lower()
            name_text = (org.get("name", "") or "").lower()
            is_medical = any(token in type_text or token in name_text for token in ("health", "healthcare", "hospital", "medical", "clinic"))
            if not is_medical and not _domain_is_government(reg_domain):
                return None
            authority_kind = "official_body" if _domain_is_government(reg_domain) else "medical_authority"
            return AuthorityProfile(
                authority_kind=authority_kind,
                authority_name=org.get("name") or org_hint,
                authority_source="ror",
                confidence="high",
                is_peer_reviewed=False,
                is_institutional=True,
                matched_ids={"ror": org.get("id", ""), "domain": reg_domain},
                evidence=[f"ror:similarity:{similarity:.2f}", "ror:domain_match"],
            )

    wikidata_timeout = _remaining_timeout(deadline, WIKIDATA_TIMEOUT_SECONDS)
    if wikidata_timeout is not None:
        search_payload = await _wikidata_search(org_hint, timeout_s=wikidata_timeout)
        results = (search_payload or {}).get("search") or []
        ids = [item.get("id") for item in results if item.get("id")]
        if ids:
            entity_payload = await _wikidata_entities(ids[:5], timeout_s=wikidata_timeout)
            entities = (entity_payload or {}).get("entities") or {}
            for item in results:
                entity = entities.get(item.get("id", "")) or {}
                website_domain = _wikidata_official_website_domain(entity)
                similarity = _name_similarity(org_hint, item.get("label"))
                if website_domain and website_domain == reg_domain and similarity >= 0.72 and _wikidata_is_institutional(entity):
                    description = _wikidata_description(entity).lower()
                    is_medical = any(token in description for token in ("hospital", "medical", "health", "clinic"))
                    if not is_medical and not _domain_is_government(reg_domain):
                        continue
                    authority_kind = "official_body" if _domain_is_government(reg_domain) else "medical_authority"
                    return AuthorityProfile(
                        authority_kind=authority_kind,
                        authority_name=item.get("label") or org_hint,
                        authority_source="wikidata",
                        confidence="high",
                        is_peer_reviewed=False,
                        is_institutional=True,
                        matched_ids={"wikidata": item.get("id", ""), "domain": reg_domain},
                        evidence=[f"wikidata:similarity:{similarity:.2f}", "wikidata:website_match"],
                    )

    return None


async def resolve_authority(scraped: "ScrapedSource") -> dict:
    deadline = time.perf_counter() + (AUTHORITY_LOOKUP_BUDGET_MS / 1000.0)
    cache_keys = _authority_cache_keys(scraped)
    for _scope, cache_key in cache_keys:
        cached = _authority_cache_get(cache_key)
        if cached:
            return {"authority_profile": cached}

    bootstrap = _bootstrap_authority_profile(scraped)
    if bootstrap.authority_kind in AUTHORITY_OVERRIDE_PROTECTED_TIERS:
        return {"authority_profile": bootstrap.model_dump()}

    enrichment: dict = {}
    profile: AuthorityProfile | None = None

    scholarly_candidate = bool(
        scraped.doi or scraped.pmid or scraped.pmcid or scraped.issn or scraped.journal_name
        or (scraped.json_ld and str(scraped.json_ld.get("type", "")).lower() == "scholarlyarticle")
    )

    if scholarly_candidate:
        openalex_timeout = _remaining_timeout(deadline, OPENALEX_TIMEOUT_SECONDS)
        if openalex_timeout is not None and OPENALEX_ENABLED:
            enrichment = await enrich_with_openalex(scraped, timeout_s=openalex_timeout)
            profile = _profile_from_openalex(scraped, enrichment)

        if not profile or profile.confidence != "high":
            crossref_timeout = _remaining_timeout(deadline, CROSSREF_TIMEOUT_SECONDS)
            if crossref_timeout is not None and CROSSREF_ENABLED:
                crossref_payload = None
                if scraped.doi:
                    crossref_payload = await lookup_crossref_work(scraped.doi, timeout_s=crossref_timeout)
                    message = (crossref_payload or {}).get("message") or {}
                    if message:
                        enrichment["cr_publisher"] = message.get("publisher")
                        enrichment["cr_work_type"] = message.get("type")
                        funders = [f.get("name") for f in (message.get("funder") or []) if f.get("name")]
                        if funders:
                            enrichment["cr_funders"] = funders[:5]
                        profile = profile or _profile_from_crossref(scraped, message)
                elif scraped.issn:
                    journal_payload = await lookup_crossref_journal(scraped.issn, timeout_s=crossref_timeout)
                    message = (journal_payload or {}).get("message") or {}
                    if message and not profile:
                        profile = AuthorityProfile(
                            authority_kind="academic_journal",
                            authority_name=message.get("title") or scraped.journal_name,
                            authority_source="crossref",
                            confidence="medium",
                            is_peer_reviewed=True,
                            is_institutional=True,
                            matched_ids={"issn": scraped.issn},
                            evidence=["crossref:journal"],
                        )

    if not profile or profile.confidence != "high":
        institutional_profile = await _resolve_institutional_profile(scraped, deadline)
        if institutional_profile:
            profile = institutional_profile

    if not profile:
        profile = bootstrap

    profile_dict = profile.model_dump()
    merged = {**enrichment, "authority_profile": profile_dict}
    for scope, cache_key in cache_keys:
        ttl = _profile_ttl_seconds(profile, scope)
        _authority_cache_set(cache_key, scope, profile_dict, ttl_seconds=ttl, negative=profile.confidence == "low")
    if profile.authority_source in {"ror", "wikidata"}:
        reg_domain = registered_domain(scraped.domain)
        if reg_domain:
            _authority_cache_set(
                f"domain:{reg_domain}",
                "domain",
                profile_dict,
                ttl_seconds=_profile_ttl_seconds(profile, "domain"),
                negative=profile.confidence == "low",
            )
    return merged


async def enrich_with_openalex(scraped: "ScrapedSource", timeout_s: float | None = None) -> dict:
    """Enrich a scraped source with OpenAlex metadata. Returns enrichment dict."""
    if not OPENALEX_ENABLED:
        return {}

    enrichment: dict = {}

    # Step 1: Look up the Work (DOI preferred, URL fallback)
    work = await lookup_openalex_work(
        scraped.doi,
        scraped.url,
        pmid=scraped.pmid,
        pmcid=scraped.pmcid,
    )
    if not work:
        if scraped.issn:
            oa_source = await lookup_openalex_source_by_issn(scraped.issn)
            if oa_source:
                enrichment["oa_source_id"] = oa_source.get("id", "")
                enrichment["oa_source_display_name"] = oa_source.get("display_name", "")
                enrichment["oa_source_type"] = oa_source.get("type", "")
                enrichment["oa_publisher"] = oa_source.get("host_organization_name", "")
                summary = oa_source.get("summary_stats") or {}
                if summary:
                    enrichment["oa_source_h_index"] = summary.get("h_index", 0)
                    enrichment["oa_source_2yr_mean_citedness"] = summary.get("2yr_mean_citedness", 0.0)
        return enrichment

    # Work-level data
    enrichment["oa_work_id"] = work.get("id", "")
    enrichment["oa_cited_by_count"] = work.get("cited_by_count", 0)
    enrichment["oa_work_type"] = work.get("type", "")
    enrichment["oa_publication_year"] = work.get("publication_year")
    work_ids = work.get("ids") or {}
    if work_ids.get("pmid"):
        enrichment["oa_pmid"] = str(work_ids["pmid"]).split("/")[-1]
    if work_ids.get("pmcid"):
        enrichment["oa_pmcid"] = str(work_ids["pmcid"]).split("/")[-1]

    # Topics
    topics = work.get("topics") or []
    if topics:
        enrichment["oa_topics"] = [
            t["display_name"] for t in topics[:5] if t.get("display_name")
        ]
    primary_topic = work.get("primary_topic") or {}
    if primary_topic.get("display_name"):
        enrichment["oa_primary_topic"] = primary_topic["display_name"]

    # Open access
    oa_info = work.get("open_access") or {}
    enrichment["oa_is_open_access"] = oa_info.get("is_oa", False)

    # Step 2: Look up Source + first Author in parallel
    primary_loc = (work.get("primary_location") or {})
    source_ref = (primary_loc.get("source") or {})
    source_id = source_ref.get("id", "")

    authorships = work.get("authorships") or []
    first_author_id = ""
    if authorships:
        first_author_id = (authorships[0].get("author") or {}).get("id", "")

    oa_source, oa_author = await asyncio.gather(
        lookup_openalex_source(source_id) if source_id else _noop_coro(),
        lookup_openalex_author(first_author_id) if first_author_id else _noop_coro(),
    )

    # Source data
    if oa_source:
        enrichment["oa_source_id"] = oa_source.get("id", "")
        enrichment["oa_source_display_name"] = oa_source.get("display_name", "")
        enrichment["oa_source_type"] = oa_source.get("type", "")
        enrichment["oa_publisher"] = oa_source.get("host_organization_name", "")
        summary = oa_source.get("summary_stats") or {}
        if summary:
            enrichment["oa_source_h_index"] = summary.get("h_index", 0)
            enrichment["oa_source_2yr_mean_citedness"] = summary.get("2yr_mean_citedness", 0.0)

    # Author data
    if oa_author:
        author_summary = oa_author.get("summary_stats") or {}
        if author_summary:
            enrichment["oa_author_h_index"] = author_summary.get("h_index", 0)
        institutions = oa_author.get("last_known_institutions") or []
        if institutions:
            enrichment["oa_author_institution"] = institutions[0].get("display_name", "")

    return enrichment


def build_scored_source(
    scraped: ScrapedSource,
    llm: dict,
    oa_enrichment: dict | None = None,
    *,
    topic: str = "general",
) -> ScoredSource:
    """Combine scraped metadata + LLM output + OpenAlex enrichment into a ScoredSource."""
    oa = oa_enrichment or {}
    authority_profile = AuthorityProfile.model_validate(
        oa.get("authority_profile") or _bootstrap_authority_profile(scraped).model_dump()
    )
    domain_info = {
        **get_domain_info(scraped.domain),
        "tier": authority_profile.authority_kind,
    }

    # Enrich with ScimagoJR data when JSON-LD exposes journal metadata
    _journal_meta = lookup_journal_info(
        issn=scraped.issn or (scraped.json_ld.get("journal_issn") if scraped.json_ld else None),
        title=scraped.journal_name or (scraped.json_ld.get("journal_name") if scraped.json_ld else None),
    )
    if _journal_meta:
        domain_info = {**domain_info, **_journal_meta}
        if domain_info.get("tier") == "unknown" and authority_profile.authority_kind == "unknown":
            domain_info = {**domain_info, "tier": "academic_journal"}
        if _journal_meta.get("open_access") and domain_info.get("paywalled"):
            domain_info = {**domain_info, "paywalled": False}

    if authority_profile.authority_kind != "unknown":
        domain_info["tier"] = authority_profile.authority_kind

    domain_score  = _compute_domain_score(domain_info, oa)
    recency_score = _compute_recency_score(scraped.date, topic)
    authorship_type, author_label = _classify_authorship(scraped.author, domain_info)
    author_score  = _compute_author_score(scraped.author, domain_info, oa)
    llm_scores = _normalize_llm_result(llm)
    relevance_score = llm_scores["relevance_score"]
    alignment_score = llm_scores["alignment_score"]

    is_peer_reviewed = (
        authority_profile.is_peer_reviewed
        or domain_info.get("tier") in TIER_PEER_REVIEWED
        or bool(domain_info.get("quartile"))
        or oa.get("oa_source_type") == "journal"
        or oa.get("oa_work_type") in ("journal-article", "review")
    )

    retrieval_integrity_score = _compute_retrieval_integrity(scraped)
    metadata_only = _is_metadata_only(scraped, retrieval_integrity_score)
    retrieval_limited = retrieval_integrity_score < 70
    source_credibility_score = _compute_source_credibility(
        domain_score=domain_score,
        recency_score=recency_score,
        author_score=author_score,
        is_peer_reviewed=is_peer_reviewed,
        authority_confidence=authority_profile.confidence,
        domain_tier=domain_info.get("tier", "unknown"),
    )
    claim_support_score = _compute_claim_support_axis(llm_scores)
    decision_confidence_score = _compute_decision_confidence(
        retrieval_integrity_score=retrieval_integrity_score,
        authority_confidence=authority_profile.confidence,
        support_class=llm_scores["support_class"],
        evidence_specificity=llm_scores["evidence_specificity"],
        contradiction_strength=llm_scores["contradiction_strength"],
        topic_relevance_score=llm_scores["topic_relevance_score"],
    )
    overall_score = _compute_overall_score(
        retrieval_integrity_score=retrieval_integrity_score,
        source_credibility_score=source_credibility_score,
        claim_support_score=claim_support_score,
        decision_confidence_score=decision_confidence_score,
        domain_tier=domain_info.get("tier", "unknown"),
        support_class=llm_scores["support_class"],
    )
    verdict, verdict_label, color = _verdict_from_matrix(
        live=scraped.live,
        retrieval_integrity_score=retrieval_integrity_score,
        source_credibility_score=source_credibility_score,
        claim_support_score=claim_support_score,
        topic_relevance_score=llm_scores["topic_relevance_score"],
        support_class=llm_scores["support_class"],
        contradiction_strength=llm_scores["contradiction_strength"],
    )
    flags = _build_flags(scraped)
    if retrieval_limited:
        flags.append("retrieval_limited")
    if metadata_only:
        flags.append("metadata_only")
    if llm_scores["support_class"] == "contradicted":
        flags.append("claim_contradicted")

    signals = SourceSignals(
        domain_tier=domain_info.get("tier", "unknown"),
        domain_score=domain_score,
        recency_score=recency_score,
        author_score=author_score,
        relevance_score=relevance_score,
        alignment_score=alignment_score,
        topic_relevance_score=llm_scores["topic_relevance_score"],
        claim_support_score=claim_support_score,
        retrieval_integrity_score=retrieval_integrity_score,
        source_credibility_score=source_credibility_score,
        decision_confidence_score=decision_confidence_score,
        overall_score=overall_score,
        support_class=llm_scores["support_class"],
        evidence_specificity=llm_scores["evidence_specificity"],
        contradiction_strength=llm_scores["contradiction_strength"],
        decision_confidence_level=_decision_confidence_level(decision_confidence_score),
        retrieval_limited=retrieval_limited,
        metadata_only=metadata_only,
        is_peer_reviewed=is_peer_reviewed,
        claim_aligned=llm_scores["claim_aligned"],
        matched_terms=llm_scores["matched_terms"],
        oa_source_h_index=oa.get("oa_source_h_index"),
        oa_author_h_index=oa.get("oa_author_h_index"),
        oa_cited_by_count=oa.get("oa_cited_by_count"),
        oa_work_type=oa.get("oa_work_type"),
        oa_source_type=oa.get("oa_source_type"),
        authority_source=authority_profile.authority_source,
        authority_confidence=authority_profile.confidence,
        authority_label=authority_profile.authority_name,
    )

    return ScoredSource(
        url=scraped.url,
        domain=scraped.domain,
        title=scraped.title,
        description=scraped.description,
        context=scraped.context,
        live=scraped.live,
        verdict=verdict,
        verdict_label=verdict_label,
        color=color,
        overall_score=overall_score,
        composite_score=overall_score,
        reason=llm_scores["reason"],
        implication=llm_scores["implication"],
        flags=flags,
        date=scraped.date,
        author=scraped.author,
        authorship_type=authorship_type,
        author_label=author_label,
        authority_name=authority_profile.authority_name,
        authority_source=authority_profile.authority_source,
        authority_confidence=authority_profile.confidence,
        matched_ids=authority_profile.matched_ids or None,
        paywalled=scraped.paywalled,
        signals=signals,
        publisher=oa.get("cr_publisher") or oa.get("oa_publisher") or scraped.publisher_hint or None,
        topics=oa.get("oa_topics") or None,
        funders=oa.get("cr_funders") or oa.get("oa_funders") or None,
        author_institution=oa.get("oa_author_institution") or None,
    )


def extract_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ""


def registered_domain(domain: str) -> str:
    clean = (domain or "").lower().strip().strip(".")
    if not clean:
        return ""
    parts = clean.split(".")
    if len(parts) <= 2:
        return clean
    tail = ".".join(parts[-2:])
    tail3 = ".".join(parts[-3:])
    if tail in REGISTERED_DOMAIN_THIRD_LEVEL_SUFFIXES:
        return tail3
    return tail


def _domain_matches(candidate: str | None, domain: str | None) -> bool:
    if not candidate or not domain:
        return False
    candidate_domain = extract_domain(candidate) if "://" in candidate else candidate
    return registered_domain(candidate_domain) == registered_domain(domain)


def _normalize_entity_name(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    text = re.sub(r"\b(the|inc|llc|ltd|foundation|society|association|hospital|health system)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _name_similarity(a: str | None, b: str | None) -> float:
    na = _normalize_entity_name(a)
    nb = _normalize_entity_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


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
    logging.warning(
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


def _normalize_whitespace(value: str | None) -> str | None:
    if not value:
        return None
    normalized = re.sub(r"\s+", " ", value).strip()
    return normalized or None


def _classify_403_response(response: httpx.Response) -> str:
    """Differentiate generic 403s from hard WAF/CDN blocks.

    Hard blocks are not worth retrying through Playwright from the same machine.
    """
    server = response.headers.get("server", "").lower()
    header_names = {name.lower() for name in response.headers.keys()}
    body_sample = (response.text or "")[:600].lower()

    hard_block_markers = (
        "attention required",
        "access denied",
        "forbidden",
        "please enable cookies",
        "checking your browser",
        "security check",
        "request blocked",
        "captcha",
    )

    if (
        "cf-ray" in header_names
        or "cloudflare" in server
        or "akamai" in server
        or "imperva" in server
        or "sucuri" in server
        or "x-sucuri-id" in header_names
        or "x-iinfo" in header_names
        or any(marker in body_sample for marker in hard_block_markers)
    ):
        return "blocked_403_waf"

    return "blocked_403"


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


_AUTHOR_JUNK_WORDS = frozenset({
    "print", "share", "email", "subscribe", "follow", "save",
    "comment", "comments", "reply", "report", "menu", "search",
    "home", "login", "logout", "register", "admin", "staff",
})

_AUTHOR_JUNK_PHRASES = (
    "min read", "last updated", "date created", "reviewed/revised",
    "you're currently following", "want to unfollow", "unsubscribe",
    "about the creator", "overview of",
)

_AUTHOR_TRAILING_RE = re.compile(
    r"\s+(?:Last\s+Updated|Updated|Published|Posted|Modified|Reviewed|Created)"
    r"|\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[,\s]"
    r"|\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d"
    r"|\s+\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\s+\d{4}-\d{2}-\d{2}"
    r"|\s*[·|]\s*\d+\s*min\s+read",
    re.IGNORECASE,
)


def _validate_author(value: str | None) -> str | None:
    """Return value if it looks like a plausible author name, else None."""
    if not value:
        return None
    text = _normalize_whitespace(value)
    if not text:
        return None

    # Reject URLs
    if text.startswith(("http://", "https://", "//")) or "://" in text:
        return None

    # Reject single junk words
    if text.lower().strip() in _AUTHOR_JUNK_WORDS:
        return None

    # Reject strings that start with timestamp-like prefixes
    if re.match(
        r"^(updated|published|posted|modified|reviewed|created)\s+(on\s+)?",
        text, re.IGNORECASE,
    ):
        return None

    # Strip trailing metadata (dates, day names, "N min read") to salvage the name part
    text = _AUTHOR_TRAILING_RE.split(text, maxsplit=1)[0].rstrip(" ,;:-")
    text = _normalize_whitespace(text)
    if not text:
        return None

    # Reject if too many words (bio text, concatenated paragraphs)
    if len(text.split()) > 12:
        return None

    # Reject strings containing known junk phrases
    text_lower = text.lower()
    if any(phrase in text_lower for phrase in _AUTHOR_JUNK_PHRASES):
        return None

    return text


_AUTHOR_INLINE_TAGS = frozenset({"a", "span", "cite", "em", "strong", "address"})
_AUTHOR_BLOCK_TAGS = frozenset({"p", "li", "td", "h3", "h4", "h5", "h6"})


def _find_author_element(soup: BeautifulSoup):
    """Find the most specific DOM element that likely contains an author name."""
    candidates = soup.find_all(lambda tag: _tag_has_marker(tag, ("author", "byline")))

    best = None
    best_score = -1
    for tag in candidates:
        text = _normalize_whitespace(tag.get_text(" ", strip=True))
        if not text:
            continue

        score = 0
        word_count = len(text.split())

        if word_count <= 8:
            score += 10
        elif word_count <= 15:
            score += 3
        else:
            score -= 5

        if tag.name in _AUTHOR_INLINE_TAGS:
            score += 5
        elif tag.name in _AUTHOR_BLOCK_TAGS:
            score += 2

        if "author" in _flatten_attr_value(tag.get("itemprop")).lower():
            score += 8
        if "author" in _flatten_attr_value(tag.get("role")).lower():
            score += 4

        if score > best_score:
            best_score = score
            best = tag

    return best


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
    # Tier 1: meta name="author"
    author = _validate_author(_truncate(_get_meta_content(soup, names=("author",)), 150))
    if author:
        return author

    # Tier 2: meta article:author (may be a URL on some sites)
    article_author = _validate_author(
        _truncate(
            _get_meta_content(soup, names=("article:author",), properties=("article:author",)),
            150,
        )
    )
    if article_author:
        return article_author

    # Tier 3: citation_author (academic)
    citation_authors = []
    for meta in soup.find_all("meta"):
        if str(meta.get("name", "")).lower() == "citation_author":
            content = _normalize_whitespace(meta.get("content"))
            if content:
                citation_authors.append(content)
    if citation_authors:
        result = _validate_author(_truncate(", ".join(citation_authors), 150))
        if result:
            return result

    # Tier 4: DOM search (scored, prefers specific/small elements)
    candidate = _find_author_element(soup)
    if candidate:
        text = _normalize_whitespace(candidate.get_text(" ", strip=True))
        if text:
            text = re.sub(r"^(by|author)\s*[:\-]?\s+", "", text, flags=re.IGNORECASE)
            result = _validate_author(_truncate(text, 150))
            if result:
                return result

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


def _extract_identifier_from_meta(soup: BeautifulSoup, names: tuple[str, ...], pattern: re.Pattern[str]) -> str | None:
    for meta_name in names:
        content = _get_meta_content(soup, names=(meta_name,), properties=(meta_name,))
        if not content:
            continue
        match = pattern.search(content)
        if match:
            return match.group(1) if match.groups() else match.group(0)
    return None


def extract_pmid(soup: BeautifulSoup, html: str, url: str) -> str | None:
    match = _extract_identifier_from_meta(
        soup,
        ("citation_pmid", "pmid", "dc.identifier"),
        PMID_PATTERN,
    )
    if match:
        return match
    url_match = re.search(r"/(?:pubmed|articles|medgen)/(\d{5,9})(?:[/?#]|$)", url, re.IGNORECASE)
    if url_match:
        return url_match.group(1)
    html_match = re.search(r'"pmid"\s*:\s*"?(?:\D*?)(\d{5,9})"?', html, re.IGNORECASE)
    if html_match:
        return html_match.group(1)
    return None


def extract_pmcid(soup: BeautifulSoup, html: str, url: str) -> str | None:
    match = _extract_identifier_from_meta(
        soup,
        ("citation_pmcid", "pmcid", "dc.identifier"),
        PMCID_PATTERN,
    )
    if match:
        return match.upper()
    url_match = re.search(r"/(?:pmc/articles/)?(PMC\d{4,10})(?:[/?#]|$)", url, re.IGNORECASE)
    if url_match:
        return url_match.group(1).upper()
    html_match = PMCID_PATTERN.search(html or "")
    if html_match:
        return html_match.group(1).upper()
    return None


def extract_issn(soup: BeautifulSoup) -> str | None:
    content = _get_meta_content(soup, names=("citation_issn", "issn", "prism.issn"))
    if content:
        match = ISSN_PATTERN.search(content)
        if match:
            return match.group(1)
    return None


def extract_site_name(soup: BeautifulSoup) -> str | None:
    return _truncate(
        _get_meta_content(
            soup,
            names=("application-name", "twitter:site"),
            properties=("og:site_name",),
        ),
        150,
    )


_SOFT_404_TITLE_PATTERNS = (
    "page not found", "404 not found", "error 404",
    "not found", "page doesn't exist", "page does not exist",
    "no page found", "nothing here", "oops!", "can't find that",
)


def _is_soft_404(title: str | None) -> bool:
    """Detect pages that returned HTTP 200 but are actually error pages."""
    if not title:
        return False
    title_lower = title.lower()
    return any(pat in title_lower for pat in _SOFT_404_TITLE_PATTERNS)



_CONSENT_TEXT_SIGNATURES = (
    "manage cookie", "cookie preferences", "cookie settings",
    "we use cookies", "this site uses cookies", "this website uses cookies",
    "types of cookies", "consent preferences", "privacy preferences",
    "manage your privacy", "manage preferences", "strictly necessary",
    "performance cookies", "functional cookies", "targeting cookies",
    "advertising cookies", "analytics cookies", "third-party cookies",
    "cookie policy", "save preferences",
)


def _is_consent_text(text: str | None) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    # Check first 200 chars for consent language
    prefix = text_lower[:200]
    if sum(1 for sig in _CONSENT_TEXT_SIGNATURES if sig in prefix) >= 2:
        return True
    # High overall density of consent phrases (but skip long articles about privacy)
    total_hits = sum(1 for sig in _CONSENT_TEXT_SIGNATURES if sig in text_lower)
    if total_hits >= 3 and len(text) < 3000:
        return True
    return False


def extract_body_text(soup: BeautifulSoup) -> str | None:
    working_soup = BeautifulSoup(str(soup), HTML_PARSER)
    for tag in working_soup(
        ["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]
    ):
        tag.decompose()

    _CONSENT_MARKERS = (
        "cookie-consent", "cookie-banner", "cookie-notice", "cookie-popup",
        "cookie-overlay", "cookie-modal", "cookie-bar", "cookie-wall",
        "cookie-preferences", "cookie-settings", "cookie-policy",
        "consent-banner", "consent-modal", "consent-overlay", "consent-popup",
        "gdpr-banner", "gdpr-consent", "gdpr-overlay",
        "cc-banner", "cc-window", "cc-overlay",
        "onetrust-consent", "onetrust-banner", "cookiebot",
        "CybotCookiebotDialog",
        # OneTrust SDK (used by Mayo Clinic and many health sites)
        "optanon", "ot-sdk-", "ot-pc-", "ot-floating",
        "privacy-center", "privacy-modal",
    )
    for el in working_soup.find_all(
        lambda tag: _tag_has_marker(tag, _CONSENT_MARKERS)
    ):
        el.decompose()

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

            # Extract journal name + ISSN from isPartOf (ScholarlyArticle)
            is_part_of = node.get("isPartOf")
            if is_part_of and isinstance(is_part_of, dict):
                j_name = is_part_of.get("name") or is_part_of.get("alternateName")
                if j_name and isinstance(j_name, str):
                    simplified["journal_name"] = j_name.strip()
                j_issn = is_part_of.get("issn")
                if j_issn:
                    if isinstance(j_issn, list):
                        j_issn = j_issn[0] if j_issn else None
                    if j_issn and isinstance(j_issn, str):
                        simplified["journal_issn"] = j_issn.strip()

            return simplified

    return None


def detect_paywall(soup: BeautifulSoup, body_text: str | None, domain_info: dict) -> bool:
    if soup.find(
        lambda tag: _tag_has_marker(tag, ("paywall", "premium-content", "locked"))
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


def _extract_page_fields(html: str, label: str, url: str, domain_info: dict) -> dict:
    soup = BeautifulSoup(html, HTML_PARSER)

    json_ld = extract_json_ld(soup)
    title = extract_title(soup)
    description = extract_description(soup)
    body_text = extract_body_text(soup)
    if _is_consent_text(body_text):
        logging.warning("  CONSENT_TEXT  body appears to be cookie consent text, discarding")
        body_text = None
    date = extract_date(soup)
    author = extract_author(soup)
    doi = extract_doi(soup, html)
    pmid = extract_pmid(soup, html, url)
    pmcid = extract_pmcid(soup, html, url)
    issn = extract_issn(soup)
    site_name = extract_site_name(soup)
    keywords = extract_keywords(soup)
    paywalled = detect_paywall(soup, body_text, domain_info)
    publisher_hint = None
    journal_name = None

    if json_ld:
        if not title and json_ld.get("headline"):
            title = _normalize_whitespace(str(json_ld["headline"]))
        if not description and json_ld.get("description"):
            description = _normalize_whitespace(str(json_ld["description"]))
        if not date and json_ld.get("datePublished"):
            date = _extract_year(str(json_ld["datePublished"]))
        if not author and json_ld.get("author"):
            author = _validate_author(_coerce_json_ld_name(json_ld["author"]))
        if not publisher_hint and json_ld.get("publisher"):
            publisher_hint = _truncate(_normalize_whitespace(str(json_ld["publisher"])), 150)
        if not journal_name and json_ld.get("journal_name"):
            journal_name = _truncate(_normalize_whitespace(str(json_ld["journal_name"])), 150)
        if not issn and json_ld.get("journal_issn"):
            match = ISSN_PATTERN.search(str(json_ld["journal_issn"]))
            if match:
                issn = match.group(1)
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
    publisher_hint = publisher_hint or _truncate(
        _get_meta_content(soup, names=("publisher", "citation_publisher", "dc.publisher")),
        150,
    )
    organization_hint = publisher_hint or site_name or title

    return {
        "title": title,
        "description": description,
        "body_text": body_text,
        "date": date,
        "author": author,
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "issn": issn,
        "journal_name": journal_name,
        "publisher_hint": publisher_hint,
        "organization_hint": _truncate(organization_hint, 150),
        "site_name": site_name,
        "paywalled": paywalled,
        "json_ld": json_ld,
        "keywords": keywords,
        "word_count": word_count,
    }


def _is_private_ip(hostname: str) -> bool:
    """Return True if hostname resolves to a private/reserved IP address."""
    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _type, _proto, _canonname, sockaddr in resolved:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_reserved or ip.is_loopback or ip.is_link_local:
                return True
        return False
    except (socket.gaierror, ValueError, OSError):
        return True  # fail-closed: block unresolvable hosts


async def scrape_with_beautifulsoup(source: SourceInput) -> ScrapedSource:
    domain = extract_domain(source.url)
    domain_info = get_domain_info(domain)
    http_status = None

    # SSRF protection: block private/internal IPs
    parsed = urlparse(source.url)
    if not parsed.hostname or _is_private_ip(parsed.hostname):
        return build_failure_result(source, "private_ip_blocked")

    # Check URL cache
    cached = _SCRAPE_CACHE.get(source.url)
    if cached:
        ts, cached_result = cached
        if time.time() - ts < _CACHE_TTL_SECONDS:
            logging.info("  CACHE HIT    %-40s", domain)
            return cached_result
        else:
            del _SCRAPE_CACHE[source.url]

    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT_SECONDS,
            headers=BROWSER_HEADERS,
            follow_redirects=True,
            max_redirects=MAX_REDIRECTS,
            http2=True,
        ) as client:
            try:
                get_response = await client.get(source.url)
                http_status = get_response.status_code
                content_type = get_response.headers.get("content-type", "")
                # Reject oversized responses
                content_length = get_response.headers.get("content-length")
                if content_length and int(content_length) > MAX_RESPONSE_BYTES:
                    return build_failure_result(source, "response_too_large", http_status=http_status)
                if http_status == 403:
                    return build_failure_result(
                        source,
                        _classify_403_response(get_response),
                        http_status=http_status,
                    )
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
            except Exception as exc:
                logging.warning("  GET failed   %-40s  %s: %s", source.url, type(exc).__name__, exc)
                return build_failure_result(source, "scrape_failed", http_status=http_status)

    except Exception as exc:
        logging.warning("  CLIENT fail  %-40s  %s: %s", source.url, type(exc).__name__, exc)
        return build_failure_result(source, "scrape_failed")

    try:
        extracted = _extract_page_fields(html, source.label, source.url, domain_info)
        if _is_soft_404(extracted["title"]):
            logging.warning("  SOFT 404     %-40s  title=%r", domain, extracted["title"])
            return build_failure_result(source, "soft_404", http_status=http_status)
        scrape_note = None
        if extracted["paywalled"]:
            scrape_note = "paywall_detected"
        elif extracted["body_text"] and len(extracted["body_text"]) < 200:
            scrape_note = "partial_content"

        logging.info(
            "  SCRAPED OK   %-40s  status=%s  words=%s  method=beautifulsoup  note=%s",
            domain, http_status, extracted["word_count"], scrape_note or "ok",
        )
        result = ScrapedSource(
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
            pmid=extracted["pmid"],
            pmcid=extracted["pmcid"],
            issn=extracted["issn"],
            journal_name=extracted["journal_name"],
            publisher_hint=extracted["publisher_hint"],
            organization_hint=extracted["organization_hint"],
            site_name=extracted["site_name"],
            paywalled=extracted["paywalled"],
            is_pdf=False,
            json_ld=extracted["json_ld"],
            keywords=extracted["keywords"],
            word_count=extracted["word_count"],
            scrape_method="beautifulsoup",
            scrape_note=scrape_note,
            scrape_success=bool(extracted["body_text"] and len(extracted["body_text"]) > 100),
        )
        # Cache successful results
        _SCRAPE_CACHE[source.url] = (time.time(), result)
        return result
    except Exception as exc:
        logging.warning("  PARSE fail   %-40s  %s: %s", source.url, type(exc).__name__, exc)
        return build_failure_result(source, "scrape_failed", http_status=http_status)


_COOKIE_CONSENT_BUTTON_TEXTS = [
    "Accept all", "Accept All", "Accept cookies", "Accept Cookies",
    "Accept all cookies", "Accept All Cookies",
    "I accept", "I agree", "Agree",
    "Allow all", "Allow All", "Allow all cookies", "Allow cookies",
    "Got it", "OK", "Okay", "Continue", "Consent", "Close",
]

# Pre-build a single CSS selector that matches all consent buttons at once
_COOKIE_SELECTOR = ", ".join(
    f"button:has-text('{t}'), a:has-text('{t}'), [role='button']:has-text('{t}')"
    for t in _COOKIE_CONSENT_BUTTON_TEXTS
)


async def _dismiss_cookie_popup(page) -> bool:
    """Best-effort attempt to dismiss cookie consent popups."""
    try:
        button = page.locator(_COOKIE_SELECTOR).first
        if await button.is_visible(timeout=300):
            await button.click(timeout=1000)
            await page.wait_for_timeout(300)
            logging.info("  COOKIE_POPUP dismissed")
            return True
    except Exception:
        pass
    return False


_CONSENT_REMOVAL_JS = """
() => {
    const SELECTORS = [
        '[aria-modal="true"]',
        '[role="dialog"]',
        '[role="alertdialog"]',
        '[class*="cookie" i]', '[id*="cookie" i]',
        '[class*="consent" i]', '[id*="consent" i]',
        '[class*="gdpr" i]', '[id*="gdpr" i]',
        '[class*="onetrust" i]', '[id*="onetrust" i]',
        '[class*="optanon" i]', '[id*="optanon" i]',
        '[class*="ot-sdk" i]', '[id*="ot-sdk" i]',
        '[class*="cookiebot" i]', '[id*="cookiebot" i]',
        '[class*="truste" i]', '[id*="truste" i]',
        '[class*="evidon" i]', '[id*="evidon" i]',
        '[class*="quantcast" i]', '[id*="quantcast" i]',
        '[class*="sp_veil" i]', '[id*="sp_message" i]',
        '[class*="privacy-banner" i]', '[class*="privacy-modal" i]',
        '[class*="privacy-center" i]',
        '[class*="cc-banner" i]', '[class*="cc-window" i]',
    ];
    const mainContent = document.querySelector(
        'article, main, [role="main"], #content, .article-body, .post-content'
    );
    let removed = 0;
    for (const sel of SELECTORS) {
        try {
            document.querySelectorAll(sel).forEach(el => {
                if (mainContent && (el === mainContent || el.contains(mainContent))) return;
                const tag = el.tagName.toLowerCase();
                if (tag === 'body' || tag === 'html' || tag === 'head') return;
                if (el.innerText && el.innerText.length > 2000) return;
                el.remove();
                removed++;
            });
        } catch (e) {}
    }
    return removed;
}
"""


async def _remove_consent_elements(page) -> int:
    """Remove cookie/consent DOM elements from the page before HTML capture."""
    try:
        removed = await page.evaluate(_CONSENT_REMOVAL_JS)
        if removed:
            logging.info("  CONSENT_DOM  removed %d consent element(s) via JS", removed)
        return removed or 0
    except Exception as exc:
        logging.debug("  CONSENT_DOM  JS removal failed: %s", exc)
        return 0


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
                    "networkidle", timeout=2000
                )
            except Exception:
                pass

            await _dismiss_cookie_popup(page)

            # Layer 2: wait for article content to render after cookie dismissal
            try:
                await page.wait_for_selector(
                    'article, main, [role="main"], #content, .article-body',
                    timeout=1500,
                )
            except Exception:
                pass

            # Layer 3: remove consent/cookie DOM elements via JS
            await _remove_consent_elements(page)

            html = await page.content()
            http_status = response.status if response else baseline.http_status
            if http_status == 403:
                return baseline
            if http_status and http_status >= 400:
                return baseline

            extracted = _extract_page_fields(html, source.label, source.url, domain_info)
            if _is_soft_404(extracted["title"]):
                logging.warning("  SOFT 404     %-40s  title=%r  (playwright)", domain, extracted["title"])
                return build_failure_result(source, "soft_404", http_status=http_status)
            # Re-check soft-404 on body text as well
            if extracted["body_text"] and _is_soft_404(extracted["body_text"][:200]):
                logging.warning("  SOFT 404     %-40s  body=%r  (playwright)", domain, extracted["body_text"][:80])
                return build_failure_result(source, "soft_404", http_status=http_status)

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
                pmid=extracted["pmid"],
                pmcid=extracted["pmcid"],
                issn=extracted["issn"],
                journal_name=extracted["journal_name"],
                publisher_hint=extracted["publisher_hint"],
                organization_hint=extracted["organization_hint"],
                site_name=extracted["site_name"],
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
    except Exception as exc:
        logging.warning("  PLAYWRIGHT   %-40s  %s: %s", source.url, type(exc).__name__, exc)
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
    _recoverable = result.scrape_note in ("scrape_failed", "blocked_403")
    should_use_playwright = (
        ENABLE_PLAYWRIGHT_FALLBACK
        and not result.is_pdf
        and (
            (result.live and result.word_count == 0)
            or (not result.live and _recoverable)
        )
    )
    if not should_use_playwright and result.scrape_note == "blocked_403_waf":
        logging.info(
            "  PLAYWRIGHT   %-40s  skipped after confirmed hard 403/WAF block",
            result.domain,
        )
    if should_use_playwright:
        logging.info("  PLAYWRIGHT   %-40s  words=%s → trying JS render", result.domain, result.word_count)
        result = await scrape_with_playwright(source, result)
    return result


async def _record_live_triage_batch(
    *,
    source_kind: str,
    prompt: str,
    response: str,
    topic: str,
    llm_enabled: bool,
    observations: list[dict],
) -> None:
    if not TRIAGE_CAPTURE_ENABLED or not observations:
        return

    try:
        db = get_triage_db(TRIAGE_DB_PATH)
        try:
            capture_id = str(uuid.uuid4())[:8]
            live_count = sum(1 for item in observations if item["scraped"].live)
            dead_count = len(observations) - live_count
            create_capture_run(
                db,
                capture_id=capture_id,
                source_kind=source_kind,
                prompt=prompt,
                response=response,
                topic=topic,
                source_count=len(observations),
                live_count=live_count,
                dead_count=dead_count,
                llm_enabled=llm_enabled,
            )
            for item in observations:
                triage_record_observation(
                    db,
                    source_kind=source_kind,
                    source_run_id=capture_id,
                    prompt=prompt,
                    response=response,
                    topic=topic,
                    scraped=item["scraped"],
                    llm=item.get("llm"),
                    scored=item.get("scored"),
                    playwright_attempted=item.get("playwright_attempted"),
                    playwright_improved=item.get("playwright_improved"),
                )
        finally:
            db.close()
    except Exception as exc:
        logging.warning("Triage capture write failed: %s", str(exc)[:120])


from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

app = FastAPI(title="Verity Extractor", version="1.0.0")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

_origin_regex = (
    rf"chrome-extension://{re.escape(VERITY_EXTENSION_ID)}"
    if VERITY_EXTENSION_ID
    else r"chrome-extension://.*"
)
if not VERITY_EXTENSION_ID:
    logging.warning("VERITY_EXTENSION_ID not set — CORS allows any Chrome extension")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


def _is_authenticated(request: Request) -> bool:
    if not VERITY_API_KEY:
        return True
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    return hmac.compare_digest(auth[7:], VERITY_API_KEY)


@app.middleware("http")
async def verify_api_key(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    if not _is_authenticated(request):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=()"
    return response


@app.get("/health")
async def healthcheck(request: Request) -> dict: 
    if _is_authenticated(request):
        llm_ok = await _check_llm_available()
        return {
            "status": "ok",
            "port": EXTRACTOR_PORT,
            "api_key_required": bool(VERITY_API_KEY),
            "extension_lockdown_enabled": bool(VERITY_EXTENSION_ID),
            "playwright_enabled": ENABLE_PLAYWRIGHT_FALLBACK and PLAYWRIGHT_AVAILABLE,
            "llm_enabled": llm_ok,
            "llm_model": GITHUB_MODEL,
            "llm_backend": "github_models",
            "openalex_enabled": OPENALEX_ENABLED,
            "openalex_polite": bool(OPENALEX_EMAIL),
            "crossref_enabled": CROSSREF_ENABLED,
            "crossref_polite": bool(CROSSREF_MAILTO),
            "ror_enabled": ROR_ENABLED,
            "ror_identified": bool(ROR_CLIENT_ID),
            "wikidata_enabled": WIKIDATA_ENABLED,
        }
    return {"status": "ok"}


@app.post("/extract")
@limiter.limit("10/minute")
async def extract(request: Request, body: ExtractRequest):
    start = time.perf_counter()

    logging.info("─" * 60)
    llm_ok = await _check_llm_available()
    logging.info("Extract request: %d source(s)  |  LLM: %s", len(body.sources), "on" if llm_ok else "off")
    logging.info("Prompt: [%d chars]", len(body.original_prompt))
    for i, s in enumerate(body.sources, 1):
        logging.info("  [%d] %s  |  label: %s", i, s.url, s.label[:60])

    # Scrape + score overlap: each source starts LLM scoring as soon as it's scraped
    topic = _detect_topic(body.full_ai_response + " " + body.original_prompt)
    async def scrape_and_score(source: SourceInput) -> tuple:
        scraped = await scrape_source(source)
        llm_result, oa_enrichment = await asyncio.gather(
            score_source_with_llm(scraped, body.original_prompt) if llm_ok else _noop_coro(),
            resolve_authority(scraped),
        )
        return scraped, llm_result or {}, oa_enrichment or {}

    results = await asyncio.gather(
        *(scrape_and_score(source) for source in body.sources)
    )
    scraped_sources = [r[0] for r in results]
    llm_results = [r[1] for r in results]
    oa_results = [r[2] for r in results]

    scrape_ms = int((time.perf_counter() - start) * 1000)
    live_count = sum(1 for s in scraped_sources if s.live)
    dead_count = len(scraped_sources) - live_count
    logging.info("Scrape+Score done (%dms): %d live, %d dead", scrape_ms, live_count, dead_count)
    for i, s in enumerate(scraped_sources, 1):
        body_preview = (s.body_text or "").replace("\n", " ").strip()
        logging.info(
            "  [%d/%d] EXTRACTED  %s\n"
            "          title    : %s\n"
            "          date     : %s\n"
            "          author   : %s\n"
            "          words    : %s\n"
            "          paywalled: %s\n"
            "          method   : %s  note=%s\n"
            "          body     : %s",
            i, len(scraped_sources), s.url,
            s.title or "(none)",
            s.date or "(none)",
            s.author or "(none)",
            s.word_count,
            "yes" if s.paywalled else "no",
            s.scrape_method or "-", s.scrape_note or "ok",
            body_preview or "(none)",
        )

    if llm_ok:
        scored_unsorted = [
            build_scored_source(s, llm, oa, topic=topic)
            for s, llm, oa in zip(scraped_sources, llm_results, oa_results)
        ]
        await _record_live_triage_batch(
            source_kind="live_extract",
            prompt=body.original_prompt,
            response=body.full_ai_response,
            topic=topic,
            llm_enabled=True,
            observations=[
                {
                    "scraped": s,
                    "llm": llm,
                    "scored": scored,
                    "playwright_attempted": s.scrape_method == "playwright",
                    "playwright_improved": s.scrape_method == "playwright",
                }
                for s, llm, scored in zip(scraped_sources, llm_results, scored_unsorted)
            ],
        )
        scored = list(scored_unsorted)

        order = {
            "supported": 0,
            "cautious_support": 1,
            "relevant_unverified": 2,
            "contradicted": 3,
            "inaccessible": 4,
        }
        scored.sort(key=lambda s: order.get(s.verdict, 4))

        reliable_count = sum(1 for s in scored if s.verdict == "supported")
        flagged_count  = sum(1 for s in scored if s.verdict in ("relevant_unverified", "contradicted", "inaccessible"))

        total_ms = int((time.perf_counter() - start) * 1000)
        logging.info("Scoring done (%dms total)", total_ms)
        for s in scored:
            logging.info(
                "  %-12s  score=%-3s  %-40s  %s",
                s.verdict, s.composite_score, s.domain, s.reason[:60],
            )
        for i, s in enumerate(scored, 1):
            logging.info(
                "  [%d/%d] SCORED    %s  →  %s (%d/100)\n"
                "          retrieval: %d/100  credibility: %d/100\n"
                "          support  : %d/100  confidence : %d/100 (%s)\n"
                "          domain   : %s  %d/100\n"
                "          recency  : %d/100  (date: %s)\n"
                "          author   : %d/100  (author: %s)\n"
                "          relevance: %d/100  alignment: %d/100  class=%s\n"
                "          reason   : %s\n"
                "          terms    : %s",
                i, len(scored), s.domain, s.verdict, s.composite_score,
                s.signals.retrieval_integrity_score, s.signals.source_credibility_score,
                s.signals.claim_support_score, s.signals.decision_confidence_score,
                s.signals.decision_confidence_level,
                s.signals.domain_tier, s.signals.domain_score,
                s.signals.recency_score, s.date or "none",
                s.signals.author_score, s.author or "none",
                s.signals.relevance_score, s.signals.alignment_score, s.signals.support_class,
                s.reason,
                ", ".join(s.signals.matched_terms) or "(none)",
            )
        logging.info("─" * 60)

        return ScoredResponse(
            sources=scored,
            topic_detected=topic,
            source_count=len(scored),
            reliable_count=reliable_count,
            flagged_count=flagged_count,
        )

    # Fallback: return raw scraped data if GitHub Models is not configured
    for s in scraped_sources:
        status = "✓ live" if s.live else "✗ dead"
        logging.info("  %s  %-40s  method=%-14s  words=%s", status, s.domain, s.scrape_method or "-", s.word_count or 0)
    logging.info("─" * 60)
    await _record_live_triage_batch(
        source_kind="live_extract",
        prompt=body.original_prompt,
        response=body.full_ai_response,
        topic=topic,
        llm_enabled=False,
        observations=[
            {
                "scraped": scraped,
                "llm": {},
                "scored": None,
                "playwright_attempted": scraped.scrape_method == "playwright",
                "playwright_improved": scraped.scrape_method == "playwright",
            }
            for scraped in scraped_sources
        ],
    )

    extraction_time_ms = int((time.perf_counter() - start) * 1000)
    return ExtractResponse(
        scraped_sources=list(scraped_sources),
        original_prompt=body.original_prompt,
        full_ai_response=body.full_ai_response,
        source_count=len(scraped_sources),
        live_count=live_count,
        dead_count=dead_count,
        extraction_time_ms=extraction_time_ms,
    )


@app.post("/extract-stream")
@limiter.limit("10/minute")
async def extract_stream(request: Request, body: ExtractRequest):
    """SSE endpoint that emits progress events as each source finishes scraping."""

    async def event_generator():
        start = time.perf_counter()

        logging.info("─" * 60)
        llm_ok = await _check_llm_available()
        logging.info("Extract-stream request: %d source(s)  |  LLM: %s", len(body.sources), "on" if llm_ok else "off")
        logging.info("Prompt: [%d chars]", len(body.original_prompt))
        for i, s in enumerate(body.sources, 1):
            logging.info("  [%d] %s  |  label: %s", i, s.url, s.label[:60])

        topic = _detect_topic(body.full_ai_response + " " + body.original_prompt)

        total = len(body.sources)

        async def scrape_and_score_indexed(index, source):
            scraped = await scrape_source(source)
            llm_result, oa_enrichment = await asyncio.gather(
                score_source_with_llm(scraped, body.original_prompt) if llm_ok else _noop_coro(),
                resolve_authority(scraped),
            )
            return index, scraped, llm_result or {}, oa_enrichment or {}

        tasks = [
            asyncio.create_task(scrape_and_score_indexed(i, s))
            for i, s in enumerate(body.sources)
        ]

        results = [None] * total
        completed = 0
        for coro in asyncio.as_completed(tasks):
            idx, scraped, llm_result, oa_enrichment = await coro
            results[idx] = (scraped, llm_result, oa_enrichment)
            completed += 1
            progress = {
                "completed": completed,
                "total": total,
                "domain": scraped.domain,
            }
            yield f"event: progress\ndata: {json.dumps(progress)}\n\n"

        scraped_sources = [r[0] for r in results]
        llm_results = [r[1] for r in results]
        oa_results = [r[2] for r in results]

        scrape_ms = int((time.perf_counter() - start) * 1000)
        live_count = sum(1 for s in scraped_sources if s.live)
        dead_count = len(scraped_sources) - live_count
        logging.info("Scrape+Score done (%dms): %d live, %d dead", scrape_ms, live_count, dead_count)

        if llm_ok:
            scored_unsorted = [
                build_scored_source(s, llm, oa, topic=topic)
                for s, llm, oa in zip(scraped_sources, llm_results, oa_results)
            ]
            await _record_live_triage_batch(
                source_kind="live_stream",
                prompt=body.original_prompt,
                response=body.full_ai_response,
                topic=topic,
                llm_enabled=True,
                observations=[
                    {
                        "scraped": s,
                        "llm": llm,
                        "scored": scored,
                        "playwright_attempted": s.scrape_method == "playwright",
                        "playwright_improved": s.scrape_method == "playwright",
                    }
                    for s, llm, scored in zip(scraped_sources, llm_results, scored_unsorted)
                ],
            )
            scored = list(scored_unsorted)

            order = {
                "supported": 0,
                "cautious_support": 1,
                "relevant_unverified": 2,
                "contradicted": 3,
                "inaccessible": 4,
            }
            scored.sort(key=lambda s: order.get(s.verdict, 4))

            reliable_count = sum(1 for s in scored if s.verdict == "supported")
            flagged_count = sum(1 for s in scored if s.verdict in ("relevant_unverified", "contradicted", "inaccessible"))

            total_ms = int((time.perf_counter() - start) * 1000)
            logging.info("Scoring done (%dms total)", total_ms)
            logging.info("─" * 60)

            response_obj = ScoredResponse(
                sources=scored,
                topic_detected=topic,
                source_count=len(scored),
                reliable_count=reliable_count,
                flagged_count=flagged_count,
            )
        else:
            extraction_time_ms = int((time.perf_counter() - start) * 1000)
            logging.info("─" * 60)
            await _record_live_triage_batch(
                source_kind="live_stream",
                prompt=body.original_prompt,
                response=body.full_ai_response,
                topic=topic,
                llm_enabled=False,
                observations=[
                    {
                        "scraped": scraped,
                        "llm": {},
                        "scored": None,
                        "playwright_attempted": scraped.scrape_method == "playwright",
                        "playwright_improved": scraped.scrape_method == "playwright",
                    }
                    for scraped in scraped_sources
                ],
            )
            response_obj = ExtractResponse(
                scraped_sources=list(scraped_sources),
                original_prompt=body.original_prompt,
                full_ai_response=body.full_ai_response,
                source_count=len(scraped_sources),
                live_count=live_count,
                dead_count=dead_count,
                extraction_time_ms=extraction_time_ms,
            )

        yield f"event: result\ndata: {response_obj.model_dump_json()}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("verity_extractor:app", host="0.0.0.0", port=EXTRACTOR_PORT, reload=VERITY_RELOAD)

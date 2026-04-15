from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from bs4 import BeautifulSoup

from .models import PageExtraction, ScrapeCandidate

try:
    import trafilatura
except ImportError:
    trafilatura = None

try:
    from readability import Document
except ImportError:
    Document = None


HTML_PARSER = "html.parser"
DOI_PATTERN = re.compile(r"10\.\d{4,}/[^\s\"<>&]+", re.IGNORECASE)
PMID_PATTERN = re.compile(r"\b(?:pmid[:\s]*)?(\d{5,9})\b", re.IGNORECASE)
PMCID_PATTERN = re.compile(r"\b(PMC\d{4,10})\b", re.IGNORECASE)
ISSN_PATTERN = re.compile(r"\b(\d{4}-?\d{3}[\dxX])\b")
YEAR_PATTERN = re.compile(r"(19|20)\d{2}")

_SOFT_404_TITLE_PATTERNS = (
    "page not found",
    "404 not found",
    "error 404",
    "not found",
    "page doesn't exist",
    "page does not exist",
    "no page found",
    "nothing here",
    "oops!",
    "can't find that",
)

_CONSENT_TEXT_SIGNATURES = (
    "manage cookie",
    "cookie preferences",
    "cookie settings",
    "we use cookies",
    "this site uses cookies",
    "this website uses cookies",
    "types of cookies",
    "consent preferences",
    "privacy preferences",
    "manage your privacy",
    "manage preferences",
    "strictly necessary",
    "performance cookies",
    "functional cookies",
    "targeting cookies",
    "advertising cookies",
    "analytics cookies",
    "third-party cookies",
    "cookie policy",
    "save preferences",
)

_BOILERPLATE_SIGNATURES = (
    "privacy policy",
    "terms of service",
    "all rights reserved",
    "sign up for our newsletter",
    "subscribe to our newsletter",
    "share this article",
    "follow us on",
    "advertisement",
    "continue reading",
    "sign in",
    "log in",
)

_AUTHOR_JUNK_WORDS = frozenset(
    {
        "print",
        "share",
        "email",
        "subscribe",
        "follow",
        "save",
        "comment",
        "comments",
        "reply",
        "report",
        "menu",
        "search",
        "home",
        "login",
        "logout",
        "register",
        "admin",
        "staff",
    }
)

_AUTHOR_JUNK_PHRASES = (
    "min read",
    "last updated",
    "date created",
    "reviewed/revised",
    "you're currently following",
    "want to unfollow",
    "unsubscribe",
    "about the creator",
    "overview of",
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

_AUTHOR_INLINE_TAGS = frozenset({"a", "span", "cite", "em", "strong", "address"})
_AUTHOR_BLOCK_TAGS = frozenset({"p", "li", "td", "h3", "h4", "h5", "h6"})
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "into",
    "about",
    "your",
    "their",
    "have",
    "has",
    "will",
    "was",
    "were",
    "are",
    "but",
    "not",
    "our",
    "you",
}


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


def _truncate(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value[:limit].strip() or None


def _extract_year(value: str | None) -> str | None:
    if not value:
        return None
    match = YEAR_PATTERN.search(str(value))
    return match.group(0) if match else None


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


def _is_soft_404(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(pattern in lowered for pattern in _SOFT_404_TITLE_PATTERNS)


def _is_consent_text(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    prefix = lowered[:200]
    if sum(1 for signature in _CONSENT_TEXT_SIGNATURES if signature in prefix) >= 2:
        return True
    total_hits = sum(1 for signature in _CONSENT_TEXT_SIGNATURES if signature in lowered)
    return total_hits >= 3 and len(text) < 3000


def _boilerplate_hits(text: str | None) -> int:
    if not text:
        return 0
    lowered = text.lower()
    return sum(1 for signature in _BOILERPLATE_SIGNATURES if signature in lowered)


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


def _validate_author(value: str | None) -> str | None:
    if not value:
        return None
    text = _normalize_whitespace(value)
    if not text:
        return None
    if text.startswith(("http://", "https://", "//")) or "://" in text:
        return None
    if text.lower().strip() in _AUTHOR_JUNK_WORDS:
        return None
    if re.match(r"^(updated|published|posted|modified|reviewed|created)\s+(on\s+)?", text, re.IGNORECASE):
        return None
    text = _AUTHOR_TRAILING_RE.split(text, maxsplit=1)[0].rstrip(" ,;:-")
    text = _normalize_whitespace(text)
    if not text:
        return None
    if len(text.split()) > 12:
        return None
    text_lower = text.lower()
    if any(phrase in text_lower for phrase in _AUTHOR_JUNK_PHRASES):
        return None
    return text


def _find_author_element(soup: BeautifulSoup):
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


def extract_json_ld(soup: BeautifulSoup) -> dict | None:
    allowed_types = {
        "article",
        "newsarticle",
        "report",
        "analysisnewsarticle",
        "scholarlyarticle",
        "medicalscholarlyarticle",
        "blogposting",
        "webpage",
    }

    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
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

            simplified: dict[str, Any] = {"type": node_type}
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
                simplified["isAccessibleForFree"] = str(value).strip().lower() == "true" if isinstance(value, str) else bool(value)
            is_part_of = node.get("isPartOf")
            if is_part_of and isinstance(is_part_of, dict):
                journal_name = is_part_of.get("name") or is_part_of.get("alternateName")
                if journal_name and isinstance(journal_name, str):
                    simplified["journal_name"] = journal_name.strip()
                journal_issn = is_part_of.get("issn")
                if isinstance(journal_issn, list):
                    journal_issn = journal_issn[0] if journal_issn else None
                if journal_issn and isinstance(journal_issn, str):
                    simplified["journal_issn"] = journal_issn.strip()
            return simplified
    return None


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
    og_description = _get_meta_content(soup, properties=("og:description",), names=("og:description",))
    if og_description:
        return og_description
    return _get_meta_content(soup, names=("description",))


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
        lambda tag: _tag_has_marker(tag, ("byline", "author", "date", "publish", "published", "timestamp"))
    )
    for tag in candidate_tags[:20]:
        year = _extract_year(tag.get_text(" ", strip=True))
        if year:
            return year
    return None


def extract_author(soup: BeautifulSoup) -> str | None:
    author = _validate_author(_truncate(_get_meta_content(soup, names=("author",)), 150))
    if author:
        return author
    article_author = _validate_author(
        _truncate(_get_meta_content(soup, names=("article:author",), properties=("article:author",)), 150)
    )
    if article_author:
        return article_author
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
    match = _extract_identifier_from_meta(soup, ("citation_pmid", "pmid", "dc.identifier"), PMID_PATTERN)
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
    match = _extract_identifier_from_meta(soup, ("citation_pmcid", "pmcid", "dc.identifier"), PMCID_PATTERN)
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


def _extract_keywords_from_json_ld(json_ld: dict | None) -> list[str]:
    if not json_ld:
        return []
    raw_keywords = json_ld.get("keywords")
    if isinstance(raw_keywords, str):
        return _normalize_keywords(raw_keywords.split(","))
    if isinstance(raw_keywords, list):
        return _normalize_keywords([str(item) for item in raw_keywords])
    return []


def detect_paywall(soup: BeautifulSoup, body_text: str | None, domain_info: dict) -> bool:
    if soup.find(lambda tag: _tag_has_marker(tag, ("paywall", "premium-content", "locked"))):
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


def _sanitize_text(text: str | None, *, max_body_text_chars: int) -> str | None:
    text = _normalize_whitespace(text)
    if not text:
        return None
    if len(text) > max_body_text_chars:
        text = text[:max_body_text_chars].rstrip()
    return text


def extract_body_text(soup: BeautifulSoup, *, max_body_text_chars: int) -> str | None:
    working_soup = BeautifulSoup(str(soup), HTML_PARSER)
    for tag in working_soup(["script", "style", "nav", "header", "footer", "aside", "form", "noscript"]):
        tag.decompose()

    consent_markers = (
        "cookie-consent",
        "cookie-banner",
        "cookie-notice",
        "cookie-popup",
        "cookie-overlay",
        "cookie-modal",
        "cookie-bar",
        "cookie-wall",
        "cookie-preferences",
        "cookie-settings",
        "cookie-policy",
        "consent-banner",
        "consent-modal",
        "consent-overlay",
        "consent-popup",
        "gdpr-banner",
        "gdpr-consent",
        "gdpr-overlay",
        "cc-banner",
        "cc-window",
        "cc-overlay",
        "onetrust-consent",
        "onetrust-banner",
        "cookiebot",
        "CybotCookiebotDialog",
        "optanon",
        "ot-sdk-",
        "ot-pc-",
        "ot-floating",
        "privacy-center",
        "privacy-modal",
    )
    for element in working_soup.find_all(lambda tag: _tag_has_marker(tag, consent_markers)):
        element.decompose()

    candidate = working_soup.find("article")
    if candidate is None:
        candidate = working_soup.find("main")
    if candidate is None:
        candidate = working_soup.find(attrs={"role": lambda value: value and str(value).lower() == "main"})
    if candidate is None:
        candidate = working_soup.find(
            lambda tag: bool(tag.get("id"))
            and any(token in _flatten_attr_value(tag.get("id")).lower() for token in ("content", "article"))
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
    return _sanitize_text(candidate.get_text(separator=" ", strip=True), max_body_text_chars=max_body_text_chars)


def _title_overlap(title: str | None, text: str | None) -> float:
    if not title or not text:
        return 0.0
    title_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", title.lower())
        if len(token) > 2 and token not in _STOPWORDS
    }
    body_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", text[:1000].lower())
        if len(token) > 2 and token not in _STOPWORDS
    }
    if not title_tokens or not body_tokens:
        return 0.0
    return len(title_tokens & body_tokens) / max(1, len(title_tokens))


def _count_sentences(text: str | None) -> int:
    if not text:
        return 0
    return len(re.findall(r"[.!?]+(?:\s|$)", text))


def _looks_like_metadata_only(text: str | None, description: str | None) -> bool:
    if text:
        return False
    return bool(description)


def _estimate_confidence(
    *,
    title: str | None,
    description: str | None,
    text: str | None,
    strategy: str,
    flags: list[str],
    abstract_only: bool,
) -> int:
    if not text:
        return 30 if _looks_like_metadata_only(text, description) else 0

    word_count = len(text.split())
    sentence_count = _count_sentences(text)
    overlap = _title_overlap(title, text)
    score = 0

    if word_count >= 1200:
        score += 45
    elif word_count >= 700:
        score += 38
    elif word_count >= 250:
        score += 30
    elif word_count >= 120:
        score += 20
    elif word_count >= 60:
        score += 10
    else:
        score -= 10

    if sentence_count >= 12:
        score += 18
    elif sentence_count >= 6:
        score += 12
    elif sentence_count >= 3:
        score += 6
    else:
        score -= 6

    if overlap >= 0.5:
        score += 12
    elif overlap >= 0.25:
        score += 8
    elif overlap >= 0.10:
        score += 4

    strategy_bonus = {
        "trafilatura": 8,
        "readability": 6,
        "heuristic_dom": 4,
        "scholarly_adapter": 10,
    }
    score += strategy_bonus.get(strategy, 0)

    if abstract_only:
        score = min(max(score + 6, 48 if word_count >= 20 else 35), 72)
    if "partial_content" in flags:
        score -= 12
    if "metadata_only" in flags:
        score = min(score, 45)
    if "consent_text" in flags:
        score -= 45
    if "soft_404" in flags:
        score -= 70
    if "boilerplate" in flags:
        score -= 25
    if "js_required" in flags:
        score -= 8
    if "paywall" in flags:
        score = min(score, 40)

    return max(0, min(100, score))


def _candidate_from_text(
    *,
    strategy: str,
    title: str | None,
    description: str | None,
    text: str | None,
    max_body_text_chars: int,
    extra_flags: list[str] | None = None,
    abstract_only: bool = False,
) -> ScrapeCandidate | None:
    body_text = _sanitize_text(text, max_body_text_chars=max_body_text_chars)
    if not body_text:
        return None

    flags = list(extra_flags or [])
    if _is_consent_text(body_text):
        flags.append("consent_text")
    if _is_soft_404(body_text[:200]):
        flags.append("soft_404")
    if _boilerplate_hits(body_text[:1500]) >= 3:
        flags.append("boilerplate")
    if len(body_text.split()) < 250 and not abstract_only:
        flags.append("partial_content")
    confidence = _estimate_confidence(
        title=title,
        description=description,
        text=body_text,
        strategy=strategy,
        flags=flags,
        abstract_only=abstract_only,
    )
    return ScrapeCandidate(
        strategy=strategy,
        body_text=body_text,
        word_count=len(body_text.split()),
        confidence=confidence,
        flags=sorted(set(flags)),
        title=title,
        description=description,
        abstract_only=abstract_only,
    )


def _extract_trafilatura_candidate(html: str, url: str, title: str | None, description: str | None, *, max_body_text_chars: int) -> ScrapeCandidate | None:
    if trafilatura is None:
        return None
    try:
        extracted = trafilatura.extract(
            html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
            deduplicate=True,
        )
    except Exception:
        return None
    return _candidate_from_text(
        strategy="trafilatura",
        title=title,
        description=description,
        text=extracted,
        max_body_text_chars=max_body_text_chars,
    )


def _extract_readability_candidate(html: str, title: str | None, description: str | None, *, max_body_text_chars: int) -> ScrapeCandidate | None:
    if Document is None:
        return None
    try:
        document = Document(html)
        summary_html = document.summary(html_partial=True)
    except Exception:
        return None
    soup = BeautifulSoup(summary_html, HTML_PARSER)
    text = _sanitize_text(soup.get_text(" ", strip=True), max_body_text_chars=max_body_text_chars)
    return _candidate_from_text(
        strategy="readability",
        title=title or document.short_title(),
        description=description,
        text=text,
        max_body_text_chars=max_body_text_chars,
    )


def _extract_heuristic_candidate(soup: BeautifulSoup, title: str | None, description: str | None, *, max_body_text_chars: int) -> ScrapeCandidate | None:
    text = extract_body_text(soup, max_body_text_chars=max_body_text_chars)
    return _candidate_from_text(
        strategy="heuristic_dom",
        title=title,
        description=description,
        text=text,
        max_body_text_chars=max_body_text_chars,
    )


def _extract_scholarly_candidate(
    soup: BeautifulSoup,
    html: str,
    url: str,
    title: str | None,
    description: str | None,
    *,
    max_body_text_chars: int,
) -> ScrapeCandidate | None:
    url_lower = url.lower()
    selectors = (
        "[data-testid='abstract']",
        ".abstract",
        ".abstract-content",
        ".article-abstract",
        "#abstract",
    )
    abstract_text = None
    for selector in selectors:
        node = soup.select_one(selector)
        if node:
            abstract_text = _normalize_whitespace(node.get_text(" ", strip=True))
            if abstract_text:
                break
    if not abstract_text and "pubmed.ncbi.nlm.nih.gov" in url_lower:
        description_meta = _get_meta_content(soup, names=("description",))
        if description_meta:
            abstract_text = description_meta
    if not abstract_text and any(token in url_lower for token in ("pubmed.ncbi.nlm.nih.gov", "/pmc/articles/", "ncbi.nlm.nih.gov/pmc")):
        match = re.search(r"abstract\s*[:\-]?\s*(.{120,2000})", html, re.IGNORECASE | re.DOTALL)
        if match:
            abstract_text = _normalize_whitespace(match.group(1))

    if not abstract_text:
        return None

    return _candidate_from_text(
        strategy="scholarly_adapter",
        title=title,
        description=description,
        text=abstract_text,
        max_body_text_chars=max_body_text_chars,
        extra_flags=["abstract_only"],
        abstract_only=True,
    )


def _detect_js_required(soup: BeautifulSoup, html: str, best_candidate: ScrapeCandidate | None) -> bool:
    word_count = best_candidate.word_count if best_candidate else 0
    script_count = len(soup.find_all("script"))
    page_text = _normalize_whitespace(soup.get_text(" ", strip=True)) or ""
    if word_count >= 120:
        return False
    if script_count >= 10 and len(page_text) < 1200:
        return True
    return "__NEXT_DATA__" in html or "window.__NUXT__" in html or "webpackJsonp" in html


def extract_page(
    html: str,
    *,
    label: str,
    url: str,
    domain_info: dict,
    max_body_text_chars: int,
) -> PageExtraction:
    soup = BeautifulSoup(html, HTML_PARSER)
    json_ld = extract_json_ld(soup)
    title = extract_title(soup, label=label)
    description = extract_description(soup)
    date = extract_date(soup)
    author = extract_author(soup)
    doi = extract_doi(soup, html)
    pmid = extract_pmid(soup, html, url)
    pmcid = extract_pmcid(soup, html, url)
    issn = extract_issn(soup)
    site_name = extract_site_name(soup)
    keywords = extract_keywords(soup)
    publisher_hint = _truncate(_get_meta_content(soup, names=("publisher", "citation_publisher", "dc.publisher")), 150)
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

    page_flags: list[str] = []
    if _is_soft_404(title):
        page_flags.append("soft_404")

    candidates = [
        _extract_scholarly_candidate(soup, html, url, title, description, max_body_text_chars=max_body_text_chars),
        _extract_trafilatura_candidate(html, url, title, description, max_body_text_chars=max_body_text_chars),
        _extract_readability_candidate(html, title, description, max_body_text_chars=max_body_text_chars),
        _extract_heuristic_candidate(soup, title, description, max_body_text_chars=max_body_text_chars),
    ]
    candidates = [candidate for candidate in candidates if candidate is not None]
    candidates.sort(key=lambda candidate: candidate.confidence, reverse=True)

    best_candidate = candidates[0] if candidates else None
    best_body = best_candidate.body_text if best_candidate else None
    paywalled = detect_paywall(soup, best_body, domain_info)
    if paywalled:
        page_flags.append("paywall")

    if _detect_js_required(soup, html, best_candidate):
        page_flags.append("js_required")

    if best_candidate is None and description:
        page_flags.append("metadata_only")
    if best_candidate and _is_consent_text(best_candidate.body_text):
        page_flags.append("consent_text")
    if best_candidate and _boilerplate_hits(best_candidate.body_text[:1500] if best_candidate.body_text else "") >= 3:
        page_flags.append("boilerplate")

    if best_candidate:
        flags = sorted(set(best_candidate.flags + [flag for flag in page_flags if flag not in {"js_required"}]))
        best_candidate.flags = flags
        best_candidate.confidence = _estimate_confidence(
            title=title,
            description=description,
            text=best_candidate.body_text,
            strategy=best_candidate.strategy,
            flags=best_candidate.flags,
            abstract_only=best_candidate.abstract_only,
        )

    metadata = {
        "title": title or _normalize_whitespace(label),
        "description": description,
        "date": date,
        "author": _truncate(author, 150),
        "doi": doi,
        "pmid": pmid,
        "pmcid": pmcid,
        "issn": issn,
        "journal_name": journal_name,
        "publisher_hint": publisher_hint,
        "organization_hint": publisher_hint or site_name or title,
        "site_name": site_name,
        "json_ld": json_ld,
        "keywords": keywords,
        "paywalled": paywalled,
    }
    return PageExtraction(metadata=metadata, candidates=candidates, page_flags=sorted(set(page_flags)))

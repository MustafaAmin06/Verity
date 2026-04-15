from __future__ import annotations

import time
from typing import Callable

from .browser import PLAYWRIGHT_AVAILABLE, PlaywrightBrowserPool
from .extractors import extract_page
from .fetch import fetch_url
from .models import PipelineConfig, ScrapeCandidate, ScrapedSource, SourceInput


class ScrapeOrchestrator:
    def __init__(
        self,
        *,
        config: PipelineConfig,
        logger,
        extract_domain: Callable[[str], str],
        get_domain_info: Callable[[str], dict],
    ):
        self.config = config
        self.logger = logger
        self.extract_domain = extract_domain
        self.get_domain_info = get_domain_info
        self.pool = PlaywrightBrowserPool(config, logger)
        self._cache: dict[str, tuple[float, ScrapedSource]] = {}

    def _build_failure_result(
        self,
        source: SourceInput,
        *,
        note: str,
        domain: str,
        paywalled: bool,
        http_status: int | None = None,
        live: bool = False,
    ) -> ScrapedSource:
        self.logger.warning(
            "  SCRAPE FAIL  %-40s  status=%-4s  reason=%s",
            domain,
            http_status or "-",
            note,
        )
        return ScrapedSource(
            url=source.url,
            label=source.label,
            context=source.context,
            domain=domain,
            live=live,
            http_status=http_status,
            title=None if not live else source.label,
            description=None,
            body_text=None,
            date=None,
            author=None,
            doi=None,
            paywalled=paywalled,
            is_pdf=False,
            json_ld=None,
            keywords=[],
            word_count=0,
            scrape_method=None,
            scrape_note=note,
            scrape_success=False,
            extraction_stage="failure",
            extraction_strategy="none",
            extraction_confidence=0,
            retrieval_flags=[note],
            candidate_count=0,
        )

    def _build_pdf_result(
        self,
        source: SourceInput,
        *,
        domain: str,
        http_status: int | None,
    ) -> ScrapedSource:
        return ScrapedSource(
            url=source.url,
            label=source.label,
            context=source.context,
            domain=domain,
            live=True,
            http_status=http_status,
            title=source.label,
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
            extraction_stage="pdf",
            extraction_strategy="none",
            extraction_confidence=20,
            retrieval_flags=["pdf"],
            candidate_count=0,
        )

    def _derive_note(self, *, stage: str, flags: list[str], success: bool) -> str | None:
        if "paywall" in flags:
            return "paywall_detected"
        if "consent_text" in flags and not success:
            return "consent_only"
        if "metadata_only" in flags:
            return "metadata_only"
        if "partial_content" in flags and not success:
            return "partial_content"
        if "abstract_only" in flags:
            return "abstract_only"
        if stage == "browser" and success:
            return "js_rendered"
        return None

    def _materialize_source(
        self,
        source: SourceInput,
        *,
        domain: str,
        http_status: int | None,
        stage: str,
        page,
        candidate: ScrapeCandidate | None,
    ) -> ScrapedSource:
        metadata = page.metadata
        flags = sorted(set(page.page_flags + (candidate.flags if candidate else [])))
        body_text = candidate.body_text if candidate else None
        word_count = candidate.word_count if candidate else 0
        confidence = candidate.confidence if candidate else 30 if metadata.get("description") else 0
        strategy = candidate.strategy if candidate else "none"
        if candidate is None and metadata.get("description"):
            flags.append("metadata_only")
        flags = sorted(set(flags))
        minimum_words = 20 if "abstract_only" in flags else 100
        success = bool(body_text and word_count >= minimum_words and "consent_text" not in flags and "soft_404" not in flags)
        note = self._derive_note(stage=stage, flags=flags, success=success)
        extraction_stage = stage if candidate else "metadata_only"
        scrape_method = "playwright" if stage == "browser" else "http"

        return ScrapedSource(
            url=source.url,
            label=source.label,
            context=source.context,
            domain=domain,
            live=True,
            http_status=http_status,
            title=metadata.get("title"),
            description=metadata.get("description"),
            body_text=body_text,
            date=metadata.get("date"),
            author=metadata.get("author"),
            doi=metadata.get("doi"),
            pmid=metadata.get("pmid"),
            pmcid=metadata.get("pmcid"),
            issn=metadata.get("issn"),
            journal_name=metadata.get("journal_name"),
            publisher_hint=metadata.get("publisher_hint"),
            organization_hint=metadata.get("organization_hint"),
            site_name=metadata.get("site_name"),
            paywalled=bool(metadata.get("paywalled")),
            is_pdf=False,
            json_ld=metadata.get("json_ld"),
            keywords=metadata.get("keywords") or [],
            word_count=word_count,
            scrape_method=scrape_method,
            scrape_note=note,
            scrape_success=success,
            extraction_stage=extraction_stage,
            extraction_strategy=strategy,
            extraction_confidence=confidence,
            retrieval_flags=flags,
            candidate_count=len(page.candidates),
        )

    def _accept_http_result(self, scraped: ScrapedSource) -> bool:
        flags = set(scraped.retrieval_flags)
        if "abstract_only" in flags:
            return (
                (scraped.extraction_confidence or 0) >= 45
                and scraped.word_count >= 20
                and not flags.intersection({"consent_text", "soft_404"})
            )
        return (
            (scraped.extraction_confidence or 0) >= self.config.accept_http_confidence
            and scraped.word_count >= self.config.accept_http_word_count
            and not flags.intersection({"partial_content", "consent_text", "metadata_only", "js_required", "soft_404"})
        )

    def _should_escalate(self, fetched_kind: str, scraped: ScrapedSource | None) -> bool:
        if not self.config.enable_playwright_fallback or not PLAYWRIGHT_AVAILABLE:
            return False
        if fetched_kind in {"blocked_403_waf", "private_ip_blocked", "response_too_large", "pdf_skipped", "url_dead"}:
            return False
        if fetched_kind in {"blocked_403", "scrape_failed", "timeout"}:
            return True
        if scraped is None:
            return False
        flags = set(scraped.retrieval_flags)
        if "abstract_only" in flags and (scraped.extraction_confidence or 0) >= 45:
            return False
        return (
            scraped.word_count < self.config.escalate_word_count
            or (scraped.extraction_confidence or 0) < self.config.escalate_confidence
            or bool(flags.intersection({"partial_content", "consent_text", "metadata_only", "js_required"}))
        )

    def _choose_better(self, current: ScrapedSource | None, candidate: ScrapedSource | None) -> ScrapedSource | None:
        if current is None:
            return candidate
        if candidate is None:
            return current
        current_confidence = current.extraction_confidence or 0
        candidate_confidence = candidate.extraction_confidence or 0
        if candidate.scrape_success and not current.scrape_success:
            return candidate
        if candidate_confidence >= current_confidence + 5:
            return candidate
        if (
            candidate.word_count > current.word_count + 150
            and candidate_confidence >= current_confidence - 5
        ):
            return candidate
        return current

    async def scrape_source(self, source: SourceInput) -> ScrapedSource:
        cached = self._cache.get(source.url)
        if cached and time.time() - cached[0] < self.config.cache_ttl_seconds:
            result = cached[1]
            self.logger.info("  CACHE HIT    %-40s", result.domain)
            return result
        if cached:
            del self._cache[source.url]

        domain = self.extract_domain(source.url)
        domain_info = self.get_domain_info(domain)
        fetch_result = await fetch_url(source.url, self.config, self.logger)

        if fetch_result.kind == "private_ip_blocked":
            return self._build_failure_result(source, note="private_ip_blocked", domain=domain, paywalled=domain_info["paywalled"])
        if fetch_result.kind == "response_too_large":
            return self._build_failure_result(
                source,
                note="response_too_large",
                domain=domain,
                paywalled=domain_info["paywalled"],
                http_status=fetch_result.http_status,
            )
        if fetch_result.kind == "url_dead":
            return self._build_failure_result(
                source,
                note="url_dead",
                domain=domain,
                paywalled=domain_info["paywalled"],
                http_status=fetch_result.http_status,
            )
        if fetch_result.kind == "blocked_403_waf":
            return self._build_failure_result(
                source,
                note="blocked_403_waf",
                domain=domain,
                paywalled=domain_info["paywalled"],
                http_status=fetch_result.http_status,
            )
        if fetch_result.kind == "pdf_skipped":
            result = self._build_pdf_result(source, domain=domain, http_status=fetch_result.http_status)
            self._cache[source.url] = (time.time(), result)
            return result

        current_result: ScrapedSource | None = None
        if fetch_result.kind == "ok" and fetch_result.html:
            http_page = extract_page(
                fetch_result.html,
                label=source.label,
                url=fetch_result.final_url or source.url,
                domain_info=domain_info,
                max_body_text_chars=self.config.max_body_text_chars,
            )
            http_candidate = http_page.candidates[0] if http_page.candidates else None
            if http_candidate is None and http_page.metadata.get("description"):
                http_page.page_flags = sorted(set(http_page.page_flags + ["metadata_only"]))
            current_result = self._materialize_source(
                source,
                domain=domain,
                http_status=fetch_result.http_status,
                stage="http",
                page=http_page,
                candidate=http_candidate,
            )
            self.logger.info(
                "  SCRAPED OK   %-40s  status=%s  words=%s  stage=http  strategy=%s  confidence=%s  note=%s",
                domain,
                fetch_result.http_status,
                current_result.word_count,
                current_result.extraction_strategy or "none",
                current_result.extraction_confidence or 0,
                current_result.scrape_note or "ok",
            )
            if self._accept_http_result(current_result):
                self._cache[source.url] = (time.time(), current_result)
                return current_result

        if not self._should_escalate(fetch_result.kind, current_result):
            if current_result is not None:
                self._cache[source.url] = (time.time(), current_result)
                return current_result
            if fetch_result.kind == "timeout":
                return self._build_failure_result(
                    source,
                    note="timeout",
                    domain=domain,
                    paywalled=domain_info["paywalled"],
                    http_status=fetch_result.http_status,
                    live=True,
                )
            if fetch_result.kind == "blocked_403":
                return self._build_failure_result(
                    source,
                    note="blocked_403",
                    domain=domain,
                    paywalled=domain_info["paywalled"],
                    http_status=fetch_result.http_status,
                )
            return self._build_failure_result(
                source,
                note="scrape_failed",
                domain=domain,
                paywalled=domain_info["paywalled"],
                http_status=fetch_result.http_status,
            )

        self.logger.info(
            "  PLAYWRIGHT   %-40s  stage=%s  confidence=%s → trying browser render",
            domain,
            current_result.extraction_stage if current_result else "failure",
            current_result.extraction_confidence if current_result else 0,
        )
        browser_result = await self.pool.render(source.url)
        if browser_result.kind == "ok" and browser_result.html:
            browser_page = extract_page(
                browser_result.html,
                label=source.label,
                url=browser_result.final_url or source.url,
                domain_info=domain_info,
                max_body_text_chars=self.config.max_body_text_chars,
            )
            browser_candidate = browser_page.candidates[0] if browser_page.candidates else None
            browser_scraped = self._materialize_source(
                source,
                domain=domain,
                http_status=browser_result.http_status,
                stage="browser",
                page=browser_page,
                candidate=browser_candidate,
            )
            current_result = self._choose_better(current_result, browser_scraped)
        elif current_result is None:
            note = "scrape_failed" if browser_result.kind == "unavailable" else browser_result.kind
            current_result = self._build_failure_result(
                source,
                note=note,
                domain=domain,
                paywalled=domain_info["paywalled"],
                http_status=browser_result.http_status,
            )

        if current_result is not None:
            self._cache[source.url] = (time.time(), current_result)
            return current_result
        return self._build_failure_result(
            source,
            note="scrape_failed",
            domain=domain,
            paywalled=domain_info["paywalled"],
        )

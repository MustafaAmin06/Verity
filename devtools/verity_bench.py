"""
Verity Scrape Bench — developer testing & debugging tool for the scraping pipeline.

Usage:
    python -m devtools.verity_bench scrape <url> [url2 ...] [options]
    python -m devtools.verity_bench test   [options]
    python -m devtools.verity_bench compare <label1> <label2> [options]
    python -m devtools.verity_bench failures [options]
    python -m devtools.verity_bench history [options]
    python -m devtools.verity_bench prompt  <save|list|show> [options]
"""

import argparse
import asyncio
import json
import pathlib
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone

# ── Import bridge ──
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from verity_extractor import (  # noqa: E402
    ENABLE_PLAYWRIGHT_FALLBACK,
    _SCRAPE_PIPELINE,
    ScrapedSource,
    SourceInput,
    _detect_topic,
    _call_llm,
    _build_score_prompt,
    _parse_json_response,
    _SCORE_PROMPT,
    _SCORE_SYSTEM_PROMPT,
    _SCRAPE_CACHE,
    build_scored_source,
    enrich_with_openalex,
    scrape_source,
)
from devtools.triage_catalog import (  # noqa: E402
    classify_failure,
    ensure_schema as triage_ensure_schema,
    export_cases as triage_export_cases,
    get_case_detail as triage_get_case_detail,
    get_domain_rollups as triage_get_domain_rollups,
    list_cases as triage_list_cases,
    record_observation as triage_record_observation,
    update_case_review as triage_update_case_review,
)

# ── Paths ──
_DEVTOOLS_DIR = pathlib.Path(__file__).resolve().parent
_DB_PATH = _DEVTOOLS_DIR / "verity_bench.db"
_FIXTURES_DIR = _DEVTOOLS_DIR / "fixtures"

# ── ANSI helpers ──
_COLORS = {
    "green": "\033[32m",
    "amber": "\033[33m",
    "red": "\033[31m",
    "gray": "\033[90m",
    "cyan": "\033[36m",
    "bold": "\033[1m",
    "dim": "\033[2m",
    "reset": "\033[0m",
}


def _c(text: str, color: str) -> str:
    return f"{_COLORS.get(color, '')}{text}{_COLORS['reset']}"


def _header(text: str) -> str:
    return _c(f"--- {text} ---", "bold")


# ── SQLite schema ──

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS test_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT UNIQUE NOT NULL,
    created_at      TEXT NOT NULL,
    label           TEXT,
    original_prompt TEXT NOT NULL,
    full_ai_response TEXT NOT NULL DEFAULT '',
    prompt_variant  TEXT,
    total_sources   INTEGER NOT NULL,
    duration_ms     INTEGER,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS source_results (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL REFERENCES test_runs(run_id),
    url              TEXT NOT NULL,
    label            TEXT NOT NULL,
    context          TEXT NOT NULL,
    domain           TEXT,
    http_status      INTEGER,
    scrape_method    TEXT,
    scrape_note      TEXT,
    scrape_success   INTEGER,
    failure_category TEXT,
    word_count       INTEGER,
    body_text_len    INTEGER,
    title            TEXT,
    author           TEXT,
    date             TEXT,
    doi              TEXT,
    paywalled        INTEGER,
    has_json_ld      INTEGER,
    keywords_count   INTEGER,
    bs_duration_ms   INTEGER,
    pw_duration_ms   INTEGER,
    pw_attempted     INTEGER,
    pw_improved      INTEGER,
    relevance_score  INTEGER,
    alignment_score  INTEGER,
    claim_aligned    INTEGER,
    reason           TEXT,
    implication      TEXT,
    matched_terms    TEXT,
    llm_duration_ms  INTEGER,
    llm_raw_response TEXT,
    oa_cited_by_count INTEGER,
    oa_work_type     TEXT,
    oa_source_h_index INTEGER,
    oa_author_h_index INTEGER,
    oa_topics        TEXT,
    domain_tier      TEXT,
    domain_score     INTEGER,
    recency_score    INTEGER,
    author_score     INTEGER,
    composite_score  INTEGER,
    verdict          TEXT,
    flags            TEXT
);

CREATE TABLE IF NOT EXISTS prompt_variants (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT UNIQUE NOT NULL,
    system_prompt  TEXT NOT NULL,
    score_prompt   TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
"""


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(_DB_PATH))
    db.row_factory = sqlite3.Row
    db.executescript(_SCHEMA_SQL)
    triage_ensure_schema(db)
    # Seed default prompt variant if not present
    existing = db.execute(
        "SELECT 1 FROM prompt_variants WHERE name = 'default'"
    ).fetchone()
    if not existing:
        db.execute(
            "INSERT INTO prompt_variants (name, system_prompt, score_prompt, created_at) VALUES (?, ?, ?, ?)",
            ("default", _SCORE_SYSTEM_PROMPT, _SCORE_PROMPT, _now_iso()),
        )
        db.commit()
    return db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_ACTIONABILITY = {
    "waf_block": "Hard blocker",
    "paywall": "Hard blocker",
    "url_dead": "Hard blocker",
    "soft_404": "Hard blocker",
    "timeout": "Infrastructure",
    "too_large": "Infrastructure",
    "ssrf_blocked": "Infrastructure",
    "empty_content": "Fixable",
    "soft_403": "Fixable",
    "partial_content": "Fixable",
    "consent_only": "Fixable",
    "pdf": "Not a failure",
    "scrape_error": "Needs investigation",
    "unknown": "Needs investigation",
}


# ── Instrumented pipeline ──

async def instrumented_scrape(
    source: SourceInput, *, use_playwright: bool = True
) -> dict:
    """Scrape with diagnostics from the active orchestrator."""
    url_lower = source.url.lower()
    _SCRAPE_CACHE.pop(url_lower, None)

    diag: dict = {"url": source.url, "stages": {}}

    original_playwright_flag = _SCRAPE_PIPELINE.config.enable_playwright_fallback
    _SCRAPE_PIPELINE.config.enable_playwright_fallback = original_playwright_flag and use_playwright
    try:
        t0 = time.perf_counter()
        result = await scrape_source(source)
        total_ms = int((time.perf_counter() - t0) * 1000)
    finally:
        _SCRAPE_PIPELINE.config.enable_playwright_fallback = original_playwright_flag

    used_browser = result.extraction_stage == "browser"
    diag["stages"]["beautifulsoup"] = {
        "duration_ms": total_ms,
        "success": result.scrape_success,
        "word_count": result.word_count,
        "scrape_note": result.scrape_note,
        "http_status": result.http_status,
    }
    diag["stages"]["playwright"] = {
        "attempted": used_browser,
        "improved": used_browser,
        "duration_ms": total_ms if used_browser else None,
        "word_count": result.word_count if used_browser else None,
        "skip_reason": None if used_browser else ("disabled" if not use_playwright else "http_accepted"),
    }
    diag["result"] = result
    diag["bs_duration_ms"] = total_ms
    diag["pw_duration_ms"] = total_ms if used_browser else None
    diag["pw_attempted"] = used_browser
    diag["pw_improved"] = used_browser
    diag["failure_category"] = classify_failure(
        scrape_note=result.scrape_note,
        scrape_success=result.scrape_success,
        live=result.live,
        word_count=result.word_count,
    )
    return diag


async def instrumented_llm_score(
    scraped: ScrapedSource,
    original_prompt: str,
    system_prompt: str = _SCORE_SYSTEM_PROMPT,
    score_prompt_template: str = _SCORE_PROMPT,
) -> dict:
    """Score with custom prompt variant, capturing raw LLM response."""
    llm_prompt = _build_score_prompt(
        context=scraped.context,
        prompt=original_prompt,
        body=scraped.body_text or scraped.description or "",
        template=score_prompt_template,
    )

    t0 = time.perf_counter()
    raw = await _call_llm(llm_prompt, system=system_prompt)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    fallback = {
        "relevance_score": 50,
        "alignment_score": 50,
        "claim_aligned": None,
        "reason": "Could not assess — LLM unavailable or content restricted.",
        "implication": "Verify this source manually before citing.",
        "matched_terms": [],
    }
    parsed = _parse_json_response(raw, fallback)

    return {
        **parsed,
        "_raw_response": raw,
        "_duration_ms": duration_ms,
        "_used_fallback": raw is None,
    }


# ── Output formatting ──

def _print_scrape_result(diag: dict, *, verbose: bool = False) -> None:
    result: ScrapedSource = diag["result"]
    bs = diag["stages"]["beautifulsoup"]
    pw = diag["stages"]["playwright"]
    fc = diag["failure_category"]

    print(_header(result.url))
    print(f"  Domain:         {result.domain}" + (f" ({_c('paywalled', 'amber')})" if result.paywalled else ""))
    print(f"  HTTP Status:    {result.http_status or 'N/A'}")
    print(f"  Scrape Method:  {result.scrape_method or 'N/A'} ({bs['duration_ms']}ms)")
    print(f"  Stage:          {result.extraction_stage or 'N/A'}")
    print(f"  Strategy:       {result.extraction_strategy or 'N/A'}")
    print(f"  Confidence:     {result.extraction_confidence if result.extraction_confidence is not None else 'N/A'}")

    if pw["attempted"]:
        imp = _c("improved", "green") if pw.get("improved") else _c("no improvement", "dim")
        print(f"  Playwright:     attempted ({pw['duration_ms']}ms) — {imp}")
    else:
        print(f"  Playwright:     skipped ({pw.get('skip_reason', 'N/A')})")

    print(f"  Title:          {result.title or _c('(none)', 'dim')}")
    print(f"  Author:         {result.author or _c('(none)', 'dim')}")
    print(f"  Date:           {result.date or _c('(none)', 'dim')}")
    print(f"  DOI:            {result.doi or _c('(none)', 'dim')}")
    print(f"  Word Count:     {result.word_count:,}")
    print(f"  JSON-LD:        {'yes' if result.json_ld else 'no'}")
    print(f"  Keywords:       {len(result.keywords)}")
    print(f"  Scrape Note:    {result.scrape_note or 'ok'}")
    if result.retrieval_flags:
        print(f"  Retrieval:      {result.retrieval_flags}")

    if fc:
        act = _ACTIONABILITY.get(fc, "Unknown")
        color = "red" if act == "Hard blocker" else "amber" if act == "Infrastructure" else "green"
        print(f"  Failure:        {_c(fc, color)} ({act})")
    else:
        print(f"  Result:         {_c('SUCCESS', 'green')}")

    if verbose and result.body_text:
        preview = result.body_text[:300].replace("\n", " ")
        print(f"  Body Preview:   {preview}...")

    print()


def _print_scored_result(diag: dict, llm: dict, scored: object, *, verbose: bool = False) -> None:
    _print_scrape_result(diag, verbose=verbose)

    # LLM scores
    print(f"  {_c('LLM Scoring', 'cyan')} ({llm['_duration_ms']}ms)" + (" [FALLBACK]" if llm["_used_fallback"] else ""))
    print(f"    Relevance:      {llm.get('relevance_score', '?')}/100")
    print(f"    Alignment:      {llm.get('alignment_score', '?')}/100")
    aligned = llm.get("claim_aligned")
    aligned_str = "yes" if aligned is True else "no" if aligned is False else "unclear"
    print(f"    Claim Aligned:  {aligned_str}")
    print(f"    Reason:         {llm.get('reason', 'N/A')}")
    print(f"    Implication:    {llm.get('implication', 'N/A')}")
    terms = llm.get("matched_terms", [])
    if terms:
        print(f"    Matched Terms:  {terms}")

    # Composite
    if scored:
        sigs = scored.signals
        color = scored.color
        print(f"\n  {_c('Composite Score', 'cyan')}")
        print(f"    Domain:       {sigs.domain_score}/100 ({sigs.domain_tier})")
        print(f"    Recency:      {sigs.recency_score}/100")
        print(f"    Author:       {sigs.author_score}/100")
        print(f"    Relevance:    {sigs.relevance_score}/100")
        print(f"    Alignment:    {sigs.alignment_score}/100")
        print(f"    {'─' * 30}")
        print(f"    Composite:    {scored.composite_score}/100")
        print(f"    Verdict:      {_c(scored.verdict_label, color)}")
        if scored.flags:
            print(f"    Flags:        {scored.flags}")

        # OpenAlex
        if sigs.oa_cited_by_count is not None:
            print(f"\n  {_c('OpenAlex', 'cyan')}")
            print(f"    Cited By:       {sigs.oa_cited_by_count}")
            print(f"    Work Type:      {sigs.oa_work_type or 'N/A'}")
            print(f"    Source H-Index: {sigs.oa_source_h_index or 'N/A'}")
            print(f"    Author H-Index: {sigs.oa_author_h_index or 'N/A'}")

    print()


# ── Database persistence ──

def _save_run(db: sqlite3.Connection, run_id: str, label: str | None,
              prompt: str, response: str, variant: str | None,
              total: int, duration_ms: int) -> None:
    db.execute(
        """INSERT INTO test_runs
           (run_id, created_at, label, original_prompt, full_ai_response,
            prompt_variant, total_sources, duration_ms)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (run_id, _now_iso(), label, prompt, response, variant, total, duration_ms),
    )
    db.commit()


def _save_source_result(db: sqlite3.Connection, run_id: str, diag: dict,
                        llm: dict | None = None, scored: object | None = None,
                        oa: dict | None = None) -> None:
    result: ScrapedSource = diag["result"]
    oa = oa or {}

    db.execute(
        """INSERT INTO source_results
           (run_id, url, label, context, domain, http_status, scrape_method,
            scrape_note, scrape_success, failure_category, word_count, body_text_len,
            title, author, date, doi, paywalled, has_json_ld, keywords_count,
            bs_duration_ms, pw_duration_ms, pw_attempted, pw_improved,
            relevance_score, alignment_score, claim_aligned, reason, implication,
            matched_terms, llm_duration_ms, llm_raw_response,
            oa_cited_by_count, oa_work_type, oa_source_h_index, oa_author_h_index, oa_topics,
            domain_tier, domain_score, recency_score, author_score,
            composite_score, verdict, flags)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, result.url, result.label, result.context, result.domain,
            result.http_status, result.scrape_method, result.scrape_note,
            int(result.scrape_success), diag["failure_category"], result.word_count,
            len(result.body_text) if result.body_text else 0,
            result.title, result.author, result.date, result.doi,
            int(result.paywalled), int(bool(result.json_ld)), len(result.keywords),
            diag["bs_duration_ms"], diag["pw_duration_ms"],
            int(diag["pw_attempted"]), int(diag["pw_improved"]),
            # LLM
            llm.get("relevance_score") if llm else None,
            llm.get("alignment_score") if llm else None,
            int(llm["claim_aligned"]) if llm and llm.get("claim_aligned") is not None else None,
            llm.get("reason") if llm else None,
            llm.get("implication") if llm else None,
            json.dumps(llm.get("matched_terms", [])) if llm else None,
            llm.get("_duration_ms") if llm else None,
            llm.get("_raw_response") if llm else None,
            # OpenAlex
            oa.get("oa_cited_by_count"),
            oa.get("oa_work_type"),
            oa.get("oa_source_h_index"),
            oa.get("oa_author_h_index"),
            json.dumps(oa.get("oa_topics", [])) if oa.get("oa_topics") else None,
            # Composite
            scored.signals.domain_tier if scored else None,
            scored.signals.domain_score if scored else None,
            scored.signals.recency_score if scored else None,
            scored.signals.author_score if scored else None,
            scored.composite_score if scored else None,
            scored.verdict if scored else None,
            json.dumps(scored.flags) if scored else None,
        ),
    )
    db.commit()


def _record_triage_from_result(
    db: sqlite3.Connection,
    *,
    source_kind: str,
    source_run_id: str | None,
    prompt: str,
    response: str,
    topic: str | None,
    diag: dict,
    llm: dict | None = None,
    scored: object | None = None,
) -> None:
    triage_record_observation(
        db,
        source_kind=source_kind,
        source_run_id=source_run_id,
        prompt=prompt,
        response=response,
        topic=topic,
        scraped=diag["result"],
        llm=llm,
        scored=scored,
        playwright_attempted=diag.get("pw_attempted"),
        playwright_improved=diag.get("pw_improved"),
    )


# ── Commands ──

async def cmd_scrape(args: argparse.Namespace) -> None:
    """Scrape URLs and display diagnostics (no LLM scoring)."""
    urls = args.urls
    results = []

    sem = asyncio.Semaphore(args.concurrency)

    async def _scrape_one(url: str) -> dict:
        async with sem:
            source = SourceInput(url=url, label=args.label or url, context=args.context or "N/A")
            return await instrumented_scrape(source, use_playwright=not args.no_playwright)

    tasks = [_scrape_one(u) for u in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    db = None
    if args.save:
        db = _get_db()
        run_id = str(uuid.uuid4())[:8]
        _save_run(db, run_id, args.label, "N/A (scrape only)", "", None, len(urls), 0)

    for r in results:
        if isinstance(r, Exception):
            print(_c(f"ERROR: {r}", "red"))
            continue

        if args.json:
            scraped: ScrapedSource = r["result"]
            print(json.dumps({
                "url": scraped.url,
                "domain": scraped.domain,
                "http_status": scraped.http_status,
                "scrape_method": scraped.scrape_method,
                "scrape_note": scraped.scrape_note,
                "scrape_success": scraped.scrape_success,
                "word_count": scraped.word_count,
                "title": scraped.title,
                "author": scraped.author,
                "date": scraped.date,
                "doi": scraped.doi,
                "paywalled": scraped.paywalled,
                "failure_category": r["failure_category"],
                "bs_duration_ms": r["bs_duration_ms"],
                "pw_duration_ms": r["pw_duration_ms"],
                "pw_attempted": r["pw_attempted"],
                "pw_improved": r["pw_improved"],
            }, indent=2))
        else:
            _print_scrape_result(r, verbose=args.verbose)

        if db:
            _save_source_result(db, run_id, r)
            _record_triage_from_result(
                db,
                source_kind="bench_scrape",
                source_run_id=run_id,
                prompt="",
                response="",
                topic=None,
                diag=r,
            )

    # Summary
    if not args.json and len(urls) > 1:
        successes = sum(1 for r in results if not isinstance(r, Exception) and r["failure_category"] is None)
        failures = len(urls) - successes
        print(f"\n{_c('Summary:', 'bold')} {successes}/{len(urls)} succeeded, {failures} failed")


async def cmd_test(args: argparse.Namespace) -> None:
    """Full pipeline test: scrape + LLM score + OpenAlex + composite."""
    # Load sources
    if args.urls_file:
        with open(args.urls_file) as f:
            data = json.load(f)
        sources_raw = data["sources"]
        prompt = args.prompt or data.get("original_prompt", "")
        response = args.response or data.get("full_ai_response", "")
    else:
        if not args.urls:
            print(_c("Error: provide URLs as arguments or use --urls-file", "red"))
            return
        prompt = args.prompt or ""
        response = args.response or ""
        sources_raw = [{"url": u, "label": u, "context": args.context or "N/A"} for u in args.urls]

    if not prompt:
        print(_c("Error: --prompt is required (or set original_prompt in urls file)", "red"))
        return

    # Load prompt variant
    sys_prompt = _SCORE_SYSTEM_PROMPT
    score_tmpl = _SCORE_PROMPT
    variant_name = args.prompt_variant
    if variant_name:
        db = _get_db()
        row = db.execute("SELECT system_prompt, score_prompt FROM prompt_variants WHERE name = ?", (variant_name,)).fetchone()
        if not row:
            print(_c(f"Error: prompt variant '{variant_name}' not found. Use 'prompt list' to see available.", "red"))
            return
        sys_prompt, score_tmpl = row["system_prompt"], row["score_prompt"]
        print(f"Using prompt variant: {_c(variant_name, 'cyan')}")

    sources = [SourceInput(url=s["url"], label=s.get("label", s["url"]), context=s.get("context", "N/A")) for s in sources_raw]
    topic = _detect_topic((response or "") + " " + prompt)

    run_id = str(uuid.uuid4())[:8]
    label = args.label or run_id
    db = _get_db()
    t_start = time.perf_counter()

    scrape_sem = asyncio.Semaphore(args.concurrency)
    llm_sem = asyncio.Semaphore(max(1, args.concurrency // 2))

    async def _process_one(source: SourceInput) -> None:
        # Scrape
        async with scrape_sem:
            diag = await instrumented_scrape(source, use_playwright=not args.no_playwright)

        scraped: ScrapedSource = diag["result"]

        # LLM scoring
        llm = None
        if not args.no_llm:
            async with llm_sem:
                llm = await instrumented_llm_score(scraped, prompt, sys_prompt, score_tmpl)

        # OpenAlex
        oa = {}
        if not args.no_openalex:
            try:
                oa = await enrich_with_openalex(scraped)
            except Exception as e:
                print(_c(f"  OpenAlex error for {scraped.domain}: {e}", "amber"))

        # Composite scoring
        scored = None
        if llm:
            scored = build_scored_source(scraped, llm, oa or None)

        # Display
        if scored:
            _print_scored_result(diag, llm, scored, verbose=args.verbose)
        else:
            _print_scrape_result(diag, verbose=args.verbose)

        # Persist
        _save_source_result(db, run_id, diag, llm=llm, scored=scored, oa=oa)
        _record_triage_from_result(
            db,
            source_kind="bench_test",
            source_run_id=run_id,
            prompt=prompt,
            response=response,
            topic=topic,
            diag=diag,
            llm=llm,
            scored=scored,
        )

    total_ms_start = time.perf_counter()
    for source in sources:
        await _process_one(source)
    total_ms = int((time.perf_counter() - total_ms_start) * 1000)

    _save_run(db, run_id, label, prompt, response, variant_name, len(sources), total_ms)

    print(f"\n{_c('Run saved:', 'bold')} {label} ({run_id}) — {len(sources)} sources in {total_ms}ms")


async def cmd_triage(args: argparse.Namespace) -> None:
    """Inspect and manage triage cases."""
    db = _get_db()

    triage_action = "list" if args.triage_action == "queue" else args.triage_action

    if triage_action == "list":
        try:
            rows = triage_list_cases(
                db,
                review_status=args.status,
                domain=args.domain,
                failure_category=args.category,
                likely_only=not args.all,
                limit=args.limit,
            )
        except ValueError as exc:
            print(_c(str(exc), "red"))
            return
        if not args.status and not args.all:
            rows = [row for row in rows if row["review_status"] in {"new", "reviewing"}]
        if not rows:
            print(_c("No triage cases found.", "dim"))
            return

        print(f"\n{_c('Triage Queue', 'bold')}\n")
        hdr = f"{'Case':<6} | {'Status':<18} | {'Priority':>8} | {'Seen':>4} | {'Domain':<26} | URL"
        print(hdr)
        print("─" * len(hdr))
        for row in rows:
            print(
                f"{row['case_id']:<6} | "
                f"{(row['review_status'] or 'N/A'):<18} | "
                f"{row['priority_score']:>8} | "
                f"{row['times_seen']:>4} | "
                f"{row['normalized_domain'][:26]:<26} | "
                f"{row['canonical_url']}"
            )

        if not args.domain and not args.category:
            rollups = triage_get_domain_rollups(db, limit=10)
            if rollups:
                print(f"\n{_c('Top Domains', 'bold')}")
                for row in rollups:
                    print(
                        f"  {row['normalized_domain']}: "
                        f"{row['open_cases']} open case(s), "
                        f"{row['total_observations']} observation(s)"
                    )
        return

    if triage_action == "show":
        if args.case_id is None:
            print(_c("Error: triage show requires a case_id", "red"))
            return
        detail = triage_get_case_detail(db, args.case_id)
        if not detail:
            print(_c(f"Case not found: {args.case_id}", "red"))
            return

        case = detail["case"]
        case_title = f"Triage Case {case['case_id']}"
        print(f"\n{_c(case_title, 'bold')}")
        print(f"  URL:             {case['canonical_url']}")
        print(f"  Domain:          {case['normalized_domain']}")
        print(f"  Status:          {case['review_status']}")
        print(f"  Priority:        {case['priority_score']}")
        print(f"  Failure:         {case['latest_failure_category']} ({case['likely_scrapable_reason'] or 'not-queued'})")
        print(f"  Latest note:     {case['latest_scrape_note'] or 'ok'}")
        print(f"  Score/Verdict:   {case['latest_composite_score'] if case['latest_composite_score'] is not None else 'N/A'} / {case['latest_verdict'] or 'N/A'}")
        print(f"  Prompt sample:   {case['latest_prompt_snippet'] or 'N/A'}")
        print(f"  Context sample:  {case['latest_context_snippet'] or 'N/A'}")
        print(f"  Seen:            {case['times_seen']} time(s)")
        print(f"  Confirmed fixable: {case['times_confirmed_fixable']}")

        print(f"\n{_c('Recent Events', 'bold')}")
        for event in detail["events"][:10]:
            print(
                f"  {event['observed_at'][:19]} | "
                f"{event['source_kind']:<12} | "
                f"{event['failure_category'] or 'ok':<16} | "
                f"words={event['word_count']:<4} | "
                f"status={event['http_status'] or 'N/A'} | "
                f"method={event['scrape_method'] or 'N/A'} | "
                f"pw={'yes' if event['playwright_attempted'] else 'no'} | "
                f"{event['scrape_note'] or 'ok'}"
            )

        if detail["actions"]:
            print(f"\n{_c('Review History', 'bold')}")
            for action in detail["actions"][:10]:
                print(
                    f"  {action['created_at'][:19]} | "
                    f"{action['review_status'] or action['action_type']}: "
                    f"{action['note'] or '(no note)'}"
                )
        return

    if triage_action == "mark":
        if args.case_id is None or not args.review_status:
            print(_c("Error: triage mark requires case_id and review_status", "red"))
            return
        try:
            ok = triage_update_case_review(
                db,
                case_id=args.case_id,
                review_status=args.review_status,
                note=args.note,
            )
        except ValueError as exc:
            print(_c(str(exc), "red"))
            return
        if not ok:
            print(_c(f"Case not found: {args.case_id}", "red"))
            return
        print(f"Updated case {args.case_id} → {_c(args.review_status, 'green')}")
        return

    if triage_action == "export":
        output_path = args.output
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = _DEVTOOLS_DIR / f"triage_export_{timestamp}.{args.format}"
        try:
            path = triage_export_cases(
                db,
                output_path=output_path,
                format=args.format,
                review_status=args.status,
                domain=args.domain,
                failure_category=args.category,
                likely_only=not args.all,
            )
        except ValueError as exc:
            print(_c(str(exc), "red"))
            return
        print(f"Exported triage cases to {_c(str(path), 'green')}")
        return

    if triage_action == "rerun":
        case_rows = []
        if args.case_id is not None:
            detail = triage_get_case_detail(db, args.case_id)
            if not detail:
                print(_c(f"Case not found: {args.case_id}", "red"))
                return
            case_rows = [detail["case"]]
        else:
            try:
                case_rows = triage_list_cases(
                    db,
                    review_status=args.status,
                    domain=args.domain,
                    failure_category=args.category,
                    likely_only=not args.all,
                    limit=args.limit,
                )
            except ValueError as exc:
                print(_c(str(exc), "red"))
                return
            if not args.status and not args.all:
                case_rows = [row for row in case_rows if row["review_status"] in {"new", "reviewing"}]
            if not case_rows:
                print(_c("No triage cases matched rerun filters.", "dim"))
                return

        print(f"\n{_c('Rerunning Triage Cases', 'bold')} ({len(case_rows)})")
        for case in case_rows:
            case_label = f"Case {case['case_id']}"
            print(f"\n{_c(case_label, 'cyan')}: {case['canonical_url']}")
            source = SourceInput(
                url=case["canonical_url"],
                label=case["latest_title"] or case["canonical_url"],
                context=case["latest_context_snippet"] or "Triage rerun",
            )
            diag = await instrumented_scrape(source, use_playwright=not args.no_playwright)
            _print_scrape_result(diag, verbose=args.verbose)
            rerun_id = str(uuid.uuid4())[:8]
            _record_triage_from_result(
                db,
                source_kind="triage_rerun",
                source_run_id=rerun_id,
                prompt=case["latest_prompt_snippet"] or "",
                response="",
                topic=None,
                diag=diag,
            )
            print(f"Recorded rerun observation under {_c(rerun_id, 'green')}")
        return


async def cmd_compare(args: argparse.Namespace) -> None:
    """Compare two test runs side-by-side."""
    db = _get_db()

    def _find_run(label: str) -> dict | None:
        row = db.execute(
            "SELECT * FROM test_runs WHERE label = ? OR run_id = ? ORDER BY created_at DESC LIMIT 1",
            (label, label),
        ).fetchone()
        return dict(row) if row else None

    run_a = _find_run(args.label_a)
    run_b = _find_run(args.label_b)

    if not run_a:
        print(_c(f"Run not found: {args.label_a}", "red"))
        return
    if not run_b:
        print(_c(f"Run not found: {args.label_b}", "red"))
        return

    results_a = {
        row["url"]: dict(row)
        for row in db.execute("SELECT * FROM source_results WHERE run_id = ?", (run_a["run_id"],))
    }
    results_b = {
        row["url"]: dict(row)
        for row in db.execute("SELECT * FROM source_results WHERE run_id = ?", (run_b["run_id"],))
    }

    common_urls = sorted(set(results_a.keys()) & set(results_b.keys()))
    if not common_urls:
        print(_c("No common URLs between the two runs.", "amber"))
        return

    label_a = run_a["label"] or run_a["run_id"]
    label_b = run_b["label"] or run_b["run_id"]

    print(f"\n{_c('Comparing:', 'bold')} \"{label_a}\" vs \"{label_b}\"")
    print(f"URLs in common: {len(common_urls)}\n")

    # Header
    hdr = f"{'URL':<50} | {label_a:<18} | {label_b:<18} | Delta"
    print(hdr)
    print("─" * len(hdr))

    verdict_changes = 0
    deltas = []

    for url in common_urls:
        a = results_a[url]
        b = results_b[url]
        score_a = a.get("composite_score")
        score_b = b.get("composite_score")
        verdict_a = a.get("verdict", "?")
        verdict_b = b.get("verdict", "?")

        if score_a is not None and score_b is not None:
            delta = score_b - score_a
            deltas.append(delta)
            delta_str = f"{delta:+d}"
            if verdict_a != verdict_b:
                verdict_changes += 1
                delta_str += " <<"
        else:
            delta_str = "N/A"

        short_url = url[:48] + ".." if len(url) > 50 else url
        sa_str = f"{score_a or '?'} {verdict_a}" if score_a else "N/A"
        sb_str = f"{score_b or '?'} {verdict_b}" if score_b else "N/A"
        print(f"{short_url:<50} | {sa_str:<18} | {sb_str:<18} | {delta_str}")

    # Summary
    if deltas:
        avg_a = sum(results_a[u].get("composite_score", 0) for u in common_urls) / len(common_urls)
        avg_b = sum(results_b[u].get("composite_score", 0) for u in common_urls) / len(common_urls)
        print(f"\n{_c('Summary:', 'bold')}")
        print(f"  Avg composite:    {avg_a:.1f} vs {avg_b:.1f} (delta {avg_b - avg_a:+.1f})")
        print(f"  Verdict changes:  {verdict_changes}/{len(common_urls)} URLs")
        print(f"  Avg delta:        {sum(deltas) / len(deltas):+.1f}")


async def cmd_failures(args: argparse.Namespace) -> None:
    """Analyze failure patterns across stored results."""
    db = _get_db()

    where_clauses = ["failure_category IS NOT NULL"]
    params: list = []

    if args.run:
        # Find run_id from label
        row = db.execute(
            "SELECT run_id FROM test_runs WHERE label = ? OR run_id = ?",
            (args.run, args.run),
        ).fetchone()
        if not row:
            print(_c(f"Run not found: {args.run}", "red"))
            return
        where_clauses.append("run_id = ?")
        params.append(row["run_id"])

    if args.category:
        where_clauses.append("failure_category = ?")
        params.append(args.category)

    if args.domain:
        where_clauses.append("domain = ?")
        params.append(args.domain)

    where_sql = " AND ".join(where_clauses)

    # Total tested
    total = db.execute("SELECT COUNT(*) as cnt FROM source_results").fetchone()["cnt"]

    rows = db.execute(
        f"""SELECT failure_category, COUNT(*) as cnt, domain
            FROM source_results
            WHERE {where_sql}
            GROUP BY failure_category
            ORDER BY cnt DESC""",
        params,
    ).fetchall()

    if not rows:
        print(_c("No failures found matching criteria.", "green"))
        return

    total_failures = sum(r["cnt"] for r in rows)
    print(f"\n{_c('Failure Analysis', 'bold')} ({total_failures} failures out of {total} URLs tested)\n")

    hdr = f"{'Category':<20} | {'Count':>5} | {'% of Total':>10} | Actionability"
    print(hdr)
    print("─" * len(hdr))

    fixable = 0
    blockers = 0

    for r in rows:
        cat = r["failure_category"]
        cnt = r["cnt"]
        pct = (cnt / total * 100) if total > 0 else 0
        act = _ACTIONABILITY.get(cat, "Unknown")
        color = "green" if act == "Fixable" else "red" if act == "Hard blocker" else "amber"
        print(f"{cat:<20} | {cnt:>5} | {pct:>9.1f}% | {_c(act, color)}")

        if act == "Fixable":
            fixable += cnt
        elif act == "Hard blocker":
            blockers += cnt

    print(f"\n  Fixable:       {fixable}")
    print(f"  Hard blockers: {blockers}")
    print(f"  Other:         {total_failures - fixable - blockers}")

    # Show example domains per category if not already filtered
    if not args.category and not args.domain:
        print(f"\n{_c('Top failing domains per category:', 'bold')}")
        for r in rows:
            cat = r["failure_category"]
            domains = db.execute(
                f"SELECT domain, COUNT(*) as cnt FROM source_results WHERE failure_category = ? AND {where_sql} GROUP BY domain ORDER BY cnt DESC LIMIT 3",
                [cat] + params,
            ).fetchall()
            domain_list = ", ".join(f"{d['domain']}({d['cnt']})" for d in domains)
            print(f"  {cat}: {domain_list}")


async def cmd_history(args: argparse.Namespace) -> None:
    """List past test runs."""
    db = _get_db()
    rows = db.execute(
        "SELECT * FROM test_runs ORDER BY created_at DESC LIMIT ?",
        (args.limit,),
    ).fetchall()

    if not rows:
        print(_c("No test runs found.", "dim"))
        return

    print(f"\n{_c('Test Run History', 'bold')}\n")
    hdr = f"{'Run ID':<10} | {'Label':<25} | {'Sources':>7} | {'Duration':>10} | {'Variant':<15} | Created"
    print(hdr)
    print("─" * len(hdr))

    for r in rows:
        dur = f"{r['duration_ms']}ms" if r["duration_ms"] else "N/A"
        label = (r["label"] or "")[:25]
        variant = (r["prompt_variant"] or "default")[:15]
        created = r["created_at"][:19]
        print(f"{r['run_id']:<10} | {label:<25} | {r['total_sources']:>7} | {dur:>10} | {variant:<15} | {created}")


async def cmd_prompt(args: argparse.Namespace) -> None:
    """Manage prompt variants."""
    db = _get_db()

    if args.prompt_action == "list":
        rows = db.execute("SELECT name, created_at FROM prompt_variants ORDER BY created_at").fetchall()
        if not rows:
            print(_c("No prompt variants saved.", "dim"))
            return
        print(f"\n{_c('Prompt Variants', 'bold')}\n")
        for r in rows:
            print(f"  {r['name']:<20} (created {r['created_at'][:19]})")

    elif args.prompt_action == "show":
        if not args.name:
            print(_c("Error: provide variant name", "red"))
            return
        row = db.execute("SELECT * FROM prompt_variants WHERE name = ?", (args.name,)).fetchone()
        if not row:
            print(_c(f"Variant '{args.name}' not found.", "red"))
            return
        print(f"\n{_c(f'Prompt Variant: {args.name}', 'bold')}\n")
        print(_c("System Prompt:", "cyan"))
        print(row["system_prompt"])
        print(f"\n{_c('Score Prompt:', 'cyan')}")
        print(row["score_prompt"])

    elif args.prompt_action == "save":
        if not args.name:
            print(_c("Error: provide variant name", "red"))
            return
        if not args.system_file or not args.score_file:
            print(_c("Error: --system-file and --score-file are required", "red"))
            return
        sys_text = pathlib.Path(args.system_file).read_text()
        score_text = pathlib.Path(args.score_file).read_text()
        db.execute(
            """INSERT OR REPLACE INTO prompt_variants (name, system_prompt, score_prompt, created_at)
               VALUES (?, ?, ?, ?)""",
            (args.name, sys_text, score_text, _now_iso()),
        )
        db.commit()
        print(f"Saved prompt variant: {_c(args.name, 'green')}")


# ── Argument parser ──

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verity_bench",
        description="Verity Scrape Bench — developer testing tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # scrape
    p_scrape = sub.add_parser("scrape", help="Quick diagnostic scrape (no LLM)")
    p_scrape.add_argument("urls", nargs="+", help="URLs to scrape")
    p_scrape.add_argument("--label", help="Label for the source")
    p_scrape.add_argument("--context", help="Claim context text")
    p_scrape.add_argument("--no-playwright", action="store_true", help="Skip Playwright fallback")
    p_scrape.add_argument("--save", action="store_true", help="Persist results to database")
    p_scrape.add_argument("--json", action="store_true", help="Output as JSON")
    p_scrape.add_argument("--verbose", action="store_true", help="Show body text preview")
    p_scrape.add_argument("--concurrency", type=int, default=5, help="Max concurrent scrapes")

    # test
    p_test = sub.add_parser("test", help="Full pipeline test")
    p_test.add_argument("urls", nargs="*", help="URLs to test")
    p_test.add_argument("--urls-file", help="JSON file with URLs and prompt")
    p_test.add_argument("--prompt", help="Original user prompt")
    p_test.add_argument("--response", default="", help="Full AI response text")
    p_test.add_argument("--context", help="Claim context (used if no urls-file)")
    p_test.add_argument("--prompt-variant", help="Name of saved prompt variant")
    p_test.add_argument("--label", help="Label for this test run")
    p_test.add_argument("--no-openalex", action="store_true", help="Skip OpenAlex enrichment")
    p_test.add_argument("--no-llm", action="store_true", help="Skip LLM scoring")
    p_test.add_argument("--no-playwright", action="store_true", help="Skip Playwright fallback")
    p_test.add_argument("--verbose", action="store_true", help="Show body text preview")
    p_test.add_argument("--concurrency", type=int, default=5, help="Max concurrent operations")

    # compare
    p_compare = sub.add_parser("compare", help="Compare two test runs")
    p_compare.add_argument("label_a", help="First run label or run_id")
    p_compare.add_argument("label_b", help="Second run label or run_id")
    p_compare.add_argument("--summary", action="store_true", help="Summary only")

    # failures
    p_failures = sub.add_parser("failures", help="Failure pattern analysis")
    p_failures.add_argument("--run", help="Filter to a specific run label")
    p_failures.add_argument("--category", help="Filter by failure category")
    p_failures.add_argument("--domain", help="Filter by domain")

    # history
    p_history = sub.add_parser("history", help="List past test runs")
    p_history.add_argument("--limit", type=int, default=20, help="Number of runs to show")

    # prompt
    p_prompt = sub.add_parser("prompt", help="Manage prompt variants")
    p_prompt.add_argument("prompt_action", choices=["save", "list", "show"], help="Action")
    p_prompt.add_argument("name", nargs="?", help="Variant name")
    p_prompt.add_argument("--system-file", help="Path to system prompt file")
    p_prompt.add_argument("--score-file", help="Path to score prompt file")

    # triage
    p_triage = sub.add_parser("triage", help="Inspect and manage triage cases")
    p_triage.add_argument("triage_action", choices=["list", "queue", "show", "mark", "export", "rerun"], help="Action")
    p_triage.add_argument("case_id", nargs="?", type=int, help="Triage case ID")
    p_triage.add_argument("review_status", nargs="?", help="Review status for mark")
    p_triage.add_argument("--status", help="Filter by review status")
    p_triage.add_argument("--domain", help="Filter by domain")
    p_triage.add_argument("--category", help="Filter by failure category")
    p_triage.add_argument("--all", action="store_true", help="Include non-likely-scrapable cases")
    p_triage.add_argument("--limit", type=int, default=50, help="Max rows to show")
    p_triage.add_argument("--note", help="Review note for mark")
    p_triage.add_argument("--output", help="Output path for export")
    p_triage.add_argument("--format", choices=["json", "csv"], default="json", help="Export format")
    p_triage.add_argument("--no-playwright", action="store_true", help="Skip Playwright fallback on rerun")
    p_triage.add_argument("--verbose", action="store_true", help="Show body text preview on rerun")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    cmd_map = {
        "scrape": cmd_scrape,
        "test": cmd_test,
        "compare": cmd_compare,
        "failures": cmd_failures,
        "history": cmd_history,
        "prompt": cmd_prompt,
        "triage": cmd_triage,
    }

    asyncio.run(cmd_map[args.command](args))


if __name__ == "__main__":
    main()

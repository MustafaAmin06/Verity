"""
Shared triage catalog for scrape failures that are likely fixable.
"""

from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import sqlite3
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEFAULT_DB_PATH = pathlib.Path(__file__).resolve().parent / "verity_bench.db"

TRIAGE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS capture_runs (
    capture_id       TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    source_kind      TEXT NOT NULL,
    prompt_hash      TEXT,
    prompt_snippet   TEXT,
    response_snippet TEXT,
    topic_detected   TEXT,
    source_count     INTEGER NOT NULL DEFAULT 0,
    live_count       INTEGER NOT NULL DEFAULT 0,
    dead_count       INTEGER NOT NULL DEFAULT 0,
    llm_enabled      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS triage_cases (
    case_id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_url               TEXT NOT NULL UNIQUE,
    normalized_domain           TEXT NOT NULL,
    first_seen_at               TEXT NOT NULL,
    last_seen_at                TEXT NOT NULL,
    latest_failure_category     TEXT,
    latest_scrape_note          TEXT,
    latest_http_status          INTEGER,
    latest_word_count           INTEGER NOT NULL DEFAULT 0,
    latest_body_text_len        INTEGER NOT NULL DEFAULT 0,
    latest_scrape_method        TEXT,
    latest_title                TEXT,
    latest_prompt_hash          TEXT,
    latest_prompt_snippet       TEXT,
    latest_context_snippet      TEXT,
    latest_verdict              TEXT,
    latest_composite_score      INTEGER,
    likely_scrapable            INTEGER NOT NULL DEFAULT 0,
    likely_scrapable_reason     TEXT,
    review_status               TEXT NOT NULL DEFAULT 'deferred',
    review_note                 TEXT,
    priority_score              INTEGER NOT NULL DEFAULT 0,
    times_seen                  INTEGER NOT NULL DEFAULT 0,
    times_confirmed_fixable     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS triage_case_events (
    event_id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id                  INTEGER NOT NULL REFERENCES triage_cases(case_id) ON DELETE CASCADE,
    observed_at              TEXT NOT NULL,
    source_kind              TEXT NOT NULL,
    source_run_id            TEXT,
    prompt_hash              TEXT,
    prompt_snippet           TEXT,
    response_snippet         TEXT,
    topic_detected           TEXT,
    source_label             TEXT,
    context_snippet          TEXT,
    title_snippet            TEXT,
    http_status              INTEGER,
    scrape_method            TEXT,
    scrape_note              TEXT,
    scrape_success           INTEGER NOT NULL DEFAULT 0,
    live                     INTEGER NOT NULL DEFAULT 0,
    word_count               INTEGER NOT NULL DEFAULT 0,
    body_text_len            INTEGER NOT NULL DEFAULT 0,
    failure_category         TEXT,
    likely_scrapable         INTEGER NOT NULL DEFAULT 0,
    likely_scrapable_reason  TEXT,
    verdict                  TEXT,
    composite_score          INTEGER,
    oa_work_type             TEXT,
    domain_tier              TEXT,
    playwright_attempted     INTEGER,
    playwright_improved      INTEGER
);

CREATE TABLE IF NOT EXISTS triage_case_actions (
    action_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id        INTEGER NOT NULL REFERENCES triage_cases(case_id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL,
    action_type    TEXT NOT NULL,
    review_status  TEXT,
    note           TEXT
);

CREATE INDEX IF NOT EXISTS idx_triage_cases_domain ON triage_cases(normalized_domain);
CREATE INDEX IF NOT EXISTS idx_triage_cases_review ON triage_cases(review_status, likely_scrapable, priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_triage_case_events_case ON triage_case_events(case_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_triage_case_events_source ON triage_case_events(source_kind, source_run_id);
"""

_TRACKING_PARAMS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "source",
}

REVIEW_STATUSES = {
    "new",
    "reviewing",
    "confirmed_fixable",
    "not_fixable",
    "duplicate",
    "deferred",
}


def now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def ensure_schema(db: sqlite3.Connection) -> None:
    db.executescript(TRIAGE_SCHEMA_SQL)
    db.commit()


def get_db(db_path: str | pathlib.Path | None = None) -> sqlite3.Connection:
    path = pathlib.Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    ensure_schema(db)
    return db


def hash_text(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def make_snippet(value: str | None, limit: int = 240) -> str | None:
    if not value:
        return None
    collapsed = " ".join(value.split()).strip()
    if not collapsed:
        return None
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def canonicalize_url(url: str) -> str:
    try:
        parsed = urlsplit(url.strip())
    except Exception:
        return url.strip()

    scheme = (parsed.scheme or "https").lower()
    hostname = (parsed.hostname or "").lower()
    if hostname.startswith("www."):
        hostname = hostname[4:]

    port = parsed.port
    default_port = (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    netloc = hostname if not port or default_port else f"{hostname}:{port}"

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    filtered_params = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_") or lower in _TRACKING_PARAMS:
            continue
        filtered_params.append((key, value))
    query = urlencode(sorted(filtered_params))

    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_domain(url: str | None = None, domain: str | None = None) -> str:
    if domain:
        clean = domain.lower().strip()
        return clean[4:] if clean.startswith("www.") else clean
    if not url:
        return ""
    try:
        hostname = (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""
    return hostname[4:] if hostname.startswith("www.") else hostname


def classify_failure(
    *,
    scrape_note: str | None,
    scrape_success: bool,
    live: bool,
    word_count: int,
) -> str | None:
    if scrape_success and word_count > 100:
        return None

    note = scrape_note or ""

    if note == "blocked_403_waf":
        return "waf_block"
    if note == "blocked_403":
        return "soft_403"
    if note == "timeout":
        return "timeout"
    if note == "paywall_detected":
        return "paywall"
    if note == "pdf_skipped":
        return "pdf"
    if note == "soft_404":
        return "soft_404"
    if note == "url_dead":
        return "url_dead"
    if note == "private_ip_blocked":
        return "ssrf_blocked"
    if note == "response_too_large":
        return "too_large"
    if note == "scrape_failed":
        return "scrape_error"
    if note == "partial_content":
        return "partial_content"
    if note == "consent_only":
        return "consent_only"
    if live and word_count == 0:
        return "empty_content"
    if live and word_count < 100:
        return "partial_content"
    if not live:
        return "url_dead"
    return "unknown"


def classify_triage_candidate(
    *,
    failure_category: str | None,
    scrape_note: str | None,
    live: bool,
    http_status: int | None,
) -> tuple[bool, str | None]:
    if not failure_category:
        return False, None

    if failure_category in {"empty_content", "partial_content", "consent_only", "soft_403"}:
        return True, failure_category

    if failure_category == "scrape_error" and (
        live or (http_status is not None and 200 <= http_status < 400)
    ):
        return True, "scrape_error_with_live_url"

    return False, None


def compute_priority(
    *,
    likely_scrapable: bool,
    failure_category: str | None,
    http_status: int | None,
    word_count: int,
    verdict: str | None,
    times_seen: int,
    domain_case_count: int,
) -> int:
    score = 0
    if likely_scrapable:
        score += 60
    if failure_category in {"empty_content", "partial_content", "consent_only", "soft_403"}:
        score += 12
    if http_status is not None and 200 <= http_status < 400:
        score += 10
    if word_count == 0:
        score += 10
    elif word_count < 100:
        score += 5
    if verdict in {"unverified", "skeptical"}:
        score += 8
    score += min(max(times_seen - 1, 0) * 8, 24)
    score += min(max(domain_case_count - 1, 0) * 3, 15)
    return score


def create_capture_run(
    db: sqlite3.Connection,
    *,
    capture_id: str,
    source_kind: str,
    prompt: str,
    response: str,
    topic: str | None,
    source_count: int,
    live_count: int,
    dead_count: int,
    llm_enabled: bool,
) -> None:
    db.execute(
        """INSERT OR REPLACE INTO capture_runs
           (capture_id, created_at, source_kind, prompt_hash, prompt_snippet, response_snippet,
            topic_detected, source_count, live_count, dead_count, llm_enabled)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            capture_id,
            now_iso(),
            source_kind,
            hash_text(prompt),
            make_snippet(prompt),
            make_snippet(response),
            topic,
            source_count,
            live_count,
            dead_count,
            int(llm_enabled),
        ),
    )
    db.commit()


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def record_observation(
    db: sqlite3.Connection,
    *,
    source_kind: str,
    source_run_id: str | None,
    prompt: str,
    response: str,
    topic: str | None,
    scraped,
    llm: dict | None = None,
    scored=None,
    playwright_attempted: bool | None = None,
    playwright_improved: bool | None = None,
) -> int | None:
    failure_category = classify_failure(
        scrape_note=getattr(scraped, "scrape_note", None),
        scrape_success=bool(getattr(scraped, "scrape_success", False)),
        live=bool(getattr(scraped, "live", False)),
        word_count=int(getattr(scraped, "word_count", 0) or 0),
    )
    if not failure_category:
        return None

    likely_scrapable, likely_reason = classify_triage_candidate(
        failure_category=failure_category,
        scrape_note=getattr(scraped, "scrape_note", None),
        live=bool(getattr(scraped, "live", False)),
        http_status=getattr(scraped, "http_status", None),
    )

    canonical_url = canonicalize_url(getattr(scraped, "url", ""))
    domain = normalize_domain(canonical_url, getattr(scraped, "domain", None))
    observed_at = now_iso()
    prompt_hash = hash_text(prompt)
    prompt_snippet = make_snippet(prompt)
    response_snippet = make_snippet(response)
    context_snippet = make_snippet(getattr(scraped, "context", None))
    title_snippet = make_snippet(getattr(scraped, "title", None) or getattr(scraped, "label", None))
    word_count = int(getattr(scraped, "word_count", 0) or 0)
    body_text_len = len(getattr(scraped, "body_text", None) or "")
    verdict = getattr(scored, "verdict", None) if scored else None
    composite_score = getattr(scored, "composite_score", None) if scored else None
    domain_tier = getattr(getattr(scored, "signals", None), "domain_tier", None) if scored else None
    oa_work_type = getattr(getattr(scored, "signals", None), "oa_work_type", None) if scored else None

    existing = db.execute(
        "SELECT * FROM triage_cases WHERE canonical_url = ?",
        (canonical_url,),
    ).fetchone()

    if existing:
        case_id = existing["case_id"]
        times_seen = existing["times_seen"] + 1
        review_status = existing["review_status"]
        case_likely_scrapable = likely_scrapable or bool(existing["likely_scrapable"])
        case_likely_reason = likely_reason if likely_scrapable else existing["likely_scrapable_reason"]
        if case_likely_scrapable and review_status == "deferred":
            review_status = "new"
        times_confirmed_fixable = existing["times_confirmed_fixable"]
    else:
        case_id = None
        times_seen = 1
        case_likely_scrapable = likely_scrapable
        case_likely_reason = likely_reason
        review_status = "new" if case_likely_scrapable else "deferred"
        times_confirmed_fixable = 0

    domain_case_count = db.execute(
        "SELECT COUNT(*) AS cnt FROM triage_cases WHERE normalized_domain = ?",
        (domain,),
    ).fetchone()["cnt"]
    if case_id is None:
        domain_case_count += 1

    priority_score = compute_priority(
        likely_scrapable=case_likely_scrapable,
        failure_category=failure_category,
        http_status=getattr(scraped, "http_status", None),
        word_count=word_count,
        verdict=verdict,
        times_seen=times_seen,
        domain_case_count=domain_case_count,
    )

    if case_id is None:
        db.execute(
            """INSERT INTO triage_cases
               (canonical_url, normalized_domain, first_seen_at, last_seen_at, latest_failure_category,
                latest_scrape_note, latest_http_status, latest_word_count, latest_body_text_len,
                latest_scrape_method, latest_title, latest_prompt_hash, latest_prompt_snippet,
                latest_context_snippet, latest_verdict, latest_composite_score, likely_scrapable,
                likely_scrapable_reason, review_status, review_note, priority_score, times_seen,
                times_confirmed_fixable)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                canonical_url,
                domain,
                observed_at,
                observed_at,
                failure_category,
                getattr(scraped, "scrape_note", None),
                getattr(scraped, "http_status", None),
                word_count,
                body_text_len,
                getattr(scraped, "scrape_method", None),
                title_snippet,
                prompt_hash,
                prompt_snippet,
                context_snippet,
                verdict,
                composite_score,
                int(case_likely_scrapable),
                case_likely_reason,
                review_status,
                None,
                priority_score,
                times_seen,
                times_confirmed_fixable,
            ),
        )
        case_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    else:
        db.execute(
            """UPDATE triage_cases
               SET last_seen_at = ?,
                   latest_failure_category = ?,
                   latest_scrape_note = ?,
                   latest_http_status = ?,
                   latest_word_count = ?,
                   latest_body_text_len = ?,
                   latest_scrape_method = ?,
                   latest_title = ?,
                   latest_prompt_hash = ?,
                   latest_prompt_snippet = ?,
                   latest_context_snippet = ?,
                   latest_verdict = ?,
                   latest_composite_score = ?,
                   likely_scrapable = ?,
                   likely_scrapable_reason = ?,
                   review_status = ?,
                   priority_score = ?,
                   times_seen = ?
               WHERE case_id = ?""",
            (
                observed_at,
                failure_category,
                getattr(scraped, "scrape_note", None),
                getattr(scraped, "http_status", None),
                word_count,
                body_text_len,
                getattr(scraped, "scrape_method", None),
                title_snippet,
                prompt_hash,
                prompt_snippet,
                context_snippet,
                verdict,
                composite_score,
                int(case_likely_scrapable),
                case_likely_reason,
                review_status,
                priority_score,
                times_seen,
                case_id,
            ),
        )

    db.execute(
        """INSERT INTO triage_case_events
           (case_id, observed_at, source_kind, source_run_id, prompt_hash, prompt_snippet,
            response_snippet, topic_detected, source_label, context_snippet, title_snippet,
            http_status, scrape_method, scrape_note, scrape_success, live, word_count,
            body_text_len, failure_category, likely_scrapable, likely_scrapable_reason,
            verdict, composite_score, oa_work_type, domain_tier, playwright_attempted,
            playwright_improved)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            case_id,
            observed_at,
            source_kind,
            source_run_id,
            prompt_hash,
            prompt_snippet,
            response_snippet,
            topic,
            make_snippet(getattr(scraped, "label", None)),
            context_snippet,
            title_snippet,
            getattr(scraped, "http_status", None),
            getattr(scraped, "scrape_method", None),
            getattr(scraped, "scrape_note", None),
            int(bool(getattr(scraped, "scrape_success", False))),
            int(bool(getattr(scraped, "live", False))),
            word_count,
            body_text_len,
            failure_category,
            int(likely_scrapable),
            likely_reason,
            verdict,
            composite_score,
            oa_work_type,
            domain_tier,
            int(playwright_attempted) if playwright_attempted is not None else None,
            int(playwright_improved) if playwright_improved is not None else None,
        ),
    )
    db.commit()
    return case_id


def record_observation_batch(
    *,
    source_kind: str,
    source_run_id: str | None,
    prompt: str,
    response: str,
    topic: str | None,
    observations: list[dict[str, Any]],
    db_path: str | pathlib.Path | None = None,
) -> int:
    db = get_db(db_path)
    try:
        inserted = 0
        for observation in observations:
            case_id = record_observation(
                db,
                source_kind=source_kind,
                source_run_id=source_run_id,
                prompt=prompt,
                response=response,
                topic=topic,
                scraped=observation["scraped"],
                llm=observation.get("llm"),
                scored=observation.get("scored"),
                playwright_attempted=observation.get("playwright_attempted"),
                playwright_improved=observation.get("playwright_improved"),
            )
            if case_id is not None:
                inserted += 1
        return inserted
    finally:
        db.close()


def list_cases(
    db: sqlite3.Connection,
    *,
    review_status: str | None = None,
    domain: str | None = None,
    failure_category: str | None = None,
    likely_only: bool = True,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []

    if likely_only:
        clauses.append("likely_scrapable = 1")
    if review_status:
        if review_status not in REVIEW_STATUSES:
            raise ValueError(f"Invalid review status: {review_status}")
        clauses.append("review_status = ?")
        params.append(review_status)
    if domain:
        clauses.append("normalized_domain LIKE ?")
        params.append(f"%{normalize_domain(domain=domain)}%")
    if failure_category:
        clauses.append("latest_failure_category = ?")
        params.append(failure_category)

    rows = db.execute(
        f"""SELECT * FROM triage_cases
            WHERE {' AND '.join(clauses)}
            ORDER BY priority_score DESC, last_seen_at DESC
            LIMIT ?""",
        [*params, limit],
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_case_detail(db: sqlite3.Connection, case_id: int) -> dict[str, Any] | None:
    case = db.execute(
        "SELECT * FROM triage_cases WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    if not case:
        return None

    events = db.execute(
        """SELECT * FROM triage_case_events
           WHERE case_id = ?
           ORDER BY observed_at DESC
           LIMIT 50""",
        (case_id,),
    ).fetchall()
    actions = db.execute(
        """SELECT * FROM triage_case_actions
           WHERE case_id = ?
           ORDER BY created_at DESC
           LIMIT 50""",
        (case_id,),
    ).fetchall()

    return {
        "case": _row_to_dict(case),
        "events": [_row_to_dict(row) for row in events],
        "actions": [_row_to_dict(row) for row in actions],
    }


def get_domain_rollups(
    db: sqlite3.Connection,
    *,
    review_statuses: tuple[str, ...] = ("new", "reviewing"),
    limit: int = 25,
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in review_statuses)
    rows = db.execute(
        f"""SELECT normalized_domain,
                   COUNT(*) AS open_cases,
                   SUM(times_seen) AS total_observations,
                   MAX(priority_score) AS max_priority
            FROM triage_cases
            WHERE likely_scrapable = 1
              AND review_status IN ({placeholders})
            GROUP BY normalized_domain
            ORDER BY open_cases DESC, total_observations DESC, max_priority DESC
            LIMIT ?""",
        [*review_statuses, limit],
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_case_review(
    db: sqlite3.Connection,
    *,
    case_id: int,
    review_status: str,
    note: str | None = None,
) -> bool:
    if review_status not in REVIEW_STATUSES:
        raise ValueError(f"Invalid review status: {review_status}")

    existing = db.execute(
        "SELECT review_status, times_confirmed_fixable FROM triage_cases WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    if not existing:
        return False

    times_confirmed = existing["times_confirmed_fixable"]
    if review_status == "confirmed_fixable" and existing["review_status"] != "confirmed_fixable":
        times_confirmed += 1

    db.execute(
        """UPDATE triage_cases
           SET review_status = ?, review_note = ?, times_confirmed_fixable = ?
           WHERE case_id = ?""",
        (review_status, note, times_confirmed, case_id),
    )
    db.execute(
        """INSERT INTO triage_case_actions (case_id, created_at, action_type, review_status, note)
           VALUES (?, ?, 'status_update', ?, ?)""",
        (case_id, now_iso(), review_status, note),
    )
    db.commit()
    return True


def export_cases(
    db: sqlite3.Connection,
    *,
    output_path: str | pathlib.Path,
    format: str = "json",
    review_status: str | None = None,
    domain: str | None = None,
    failure_category: str | None = None,
    likely_only: bool = True,
) -> pathlib.Path:
    rows = list_cases(
        db,
        review_status=review_status,
        domain=domain,
        failure_category=failure_category,
        likely_only=likely_only,
        limit=10000,
    )
    path = pathlib.Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if format == "csv":
        with path.open("w", newline="", encoding="utf-8") as handle:
            fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else []
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
    else:
        path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return path

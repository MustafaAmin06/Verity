# Verity Scrape Bench — Developer Testing & Debugging Tool

## Context

Verity currently has no way to systematically test the scraping pipeline against diverse websites. When a URL fails to scrape properly, there's no structured way to diagnose *why* (WAF block? paywall? JS-rendered content? timeout?), compare fixes, or track improvements over time. The user needs a local developer tool to:

1. Scrape arbitrary URLs and see detailed per-stage diagnostics
2. Run the full pipeline (scrape + LLM score + OpenAlex + composite) on batches of URLs
3. Test different LLM prompt variants side-by-side to tune scoring
4. Classify and track failure patterns across websites
5. Persist history to measure progress as the pipeline improves

## Approach

A **CLI tool** (`devtools/verity_bench.py`) that **imports functions directly** from `verity_extractor.py` and wraps them with timing/diagnostic instrumentation. Results are stored in SQLite. An optional lightweight web dashboard lets you browse results visually.

No modifications to `verity_extractor.py` — the tool replicates only the thin orchestration logic (~20 lines from `scrape_source()` at line 2210) with diagnostic wrappers around the existing functions.

## File Structure

```
Verity/
  devtools/
    __init__.py              # empty package marker
    verity_bench.py          # CLI tool (main deliverable)
    bench_dashboard.py       # optional read-only web dashboard
    fixtures/
      example_urls.json      # curated test URLs across site categories
```

Storage: `devtools/verity_bench.db` (SQLite, gitignored)

## CLI Commands

### `scrape` — Quick diagnostic scrape (no LLM)
```
python -m devtools.verity_bench scrape <url> [url2 ...] [--no-playwright] [--save] [--json] [--verbose]
```
Shows per-stage timing, HTTP status, scrape method, word count, metadata extracted, failure classification.

### `test` — Full pipeline test
```
python -m devtools.verity_bench test <url_or_file> \
  --prompt "What are the latest CRISPR findings?" \
  --response "Recent studies in Nature show..." \
  [--urls-file fixtures/batch.json] \
  [--prompt-variant strict-v2] \
  [--label "my-test-run"] \
  [--no-openalex] [--no-llm] [--concurrency 5]
```
Runs scrape + LLM scoring + OpenAlex enrichment + composite scoring. Batch mode from JSON file. Results always persisted.

### `compare` — Side-by-side prompt/run comparison
```
python -m devtools.verity_bench compare <run_label_1> <run_label_2> [--summary]
```
Matches URLs across two runs. Shows score deltas, verdict changes, and aggregate stats.

### `failures` — Failure pattern analysis
```
python -m devtools.verity_bench failures [--run <label>] [--category waf_block] [--domain nature.com]
```
Aggregates failures by category (waf_block, paywall, timeout, empty_content, soft_404, url_dead, consent_only, etc.) with actionability classification (fixable vs hard blocker).

### `history` — List past runs
```
python -m devtools.verity_bench history [--limit 20]
```

### `prompt` — Manage LLM prompt variants
```
python -m devtools.verity_bench prompt save <name> --system-file sys.txt --score-file score.txt
python -m devtools.verity_bench prompt list
python -m devtools.verity_bench prompt show <name>
```

## URL Fixture Format (`--urls-file`)
```json
{
  "original_prompt": "What are the latest CRISPR findings?",
  "full_ai_response": "Recent studies in Nature show...",
  "sources": [
    {"url": "https://nature.com/articles/...", "label": "Nature CRISPR", "context": "Studies show CRISPR is effective"}
  ]
}
```

The shipped `fixtures/example_urls.json` will include ~20 URLs spanning: academic journals (Nature, arXiv, PubMed), news (BBC, Reuters), government (WHO, CDC), blogs (Medium, Substack), and known-problematic sites (heavy JS, paywalled, WAF-blocked).

## SQLite Schema (3 tables)

- **`test_runs`** — One row per batch: run_id, label, prompt text, prompt variant used, timestamp, duration, notes
- **`source_results`** — One row per URL per run: all scrape metadata, per-stage timings (bs_duration_ms, pw_duration_ms, llm_duration_ms), LLM scores + raw response, OpenAlex data, composite score, verdict, failure_category
- **`prompt_variants`** — Saved prompt templates for A/B testing. Pre-seeded with current production prompts from `_SCORE_SYSTEM_PROMPT` / `_SCORE_PROMPT` (lines 713-753)

## Failure Classification

Derived from `scrape_note`, `http_status`, `live`, `word_count`:

| Category | Actionability | Source |
|----------|--------------|--------|
| `waf_block` | Hard blocker | `blocked_403_waf` |
| `paywall` | Hard blocker | `paywall_detected` |
| `url_dead` | Hard blocker | HTTP 4xx/5xx, `url_dead` |
| `soft_404` | Hard blocker | `soft_404` |
| `timeout` | Infrastructure | `timeout` |
| `too_large` | Infrastructure | `response_too_large` |
| `empty_content` | **Fixable** | live=true, word_count=0 |
| `soft_403` | **Fixable** | `blocked_403` (playwright may help) |
| `partial_content` | **Fixable** | `partial_content` |
| `consent_only` | **Fixable** | `consent_only` |

## Key Implementation Details

### Direct import from verity_extractor.py
The tool imports and calls existing functions in-process (same pattern as `test_openalex.py` line 146). Functions used:
- `scrape_with_beautifulsoup()` — stage 1 scrape
- `scrape_with_playwright()` — stage 2 fallback
- `score_source_with_llm()` — LLM scoring (reimplemented thinly for custom prompt support)
- `_call_llm()` / `_parse_json_response()` — raw LLM utilities
- `enrich_with_openalex()` — academic enrichment
- `build_scored_source()` — composite scoring + verdict
- `SourceInput`, `ScrapedSource`, `ScoredSource` — data models
- `_SCORE_SYSTEM_PROMPT`, `_SCORE_PROMPT` — default prompts
- `_SCRAPE_CACHE` — cleared before each run to avoid stale results

### Instrumented scrape wrapper
Replicates the 20-line orchestration from `scrape_source()` (line 2210-2229) with `time.perf_counter()` around each stage. Captures BS vs Playwright comparison (did Playwright improve word count?).

### Custom prompt scoring
For `--prompt-variant`, the tool calls `_call_llm()` directly with the variant's system/score templates instead of `score_source_with_llm()`, then parses with `_parse_json_response()`.

### Concurrency
Uses `asyncio.Semaphore` — default 5 for scraping, 3 for LLM calls. Configurable via `--concurrency`.

## Optional Dashboard (`bench_dashboard.py`)

Minimal FastAPI app on port 8099, serves a single self-contained HTML page (inline CSS/JS, no build step). Read-only views:
- Run list with summary stats
- Per-URL detail drilldown
- Failure breakdown
- Two-run comparison mode

## Implementation Phases

1. **Foundation** — `__init__.py`, `verity_bench.py` skeleton: argparse commands, SQLite schema init, import bridge, ANSI color helpers, `classify_failure()`
2. **`scrape` command** — `instrumented_scrape()` with per-stage timing, formatted output, `--save` persistence
3. **`test` command** — Full pipeline orchestration with batch support, concurrency control, prompt variant injection, result persistence
4. **Analysis commands** — `history`, `failures` (aggregate by category), `compare` (join by URL, compute deltas)
5. **Prompt management** — `prompt save/list/show`, pre-seed default
6. **Fixtures** — `example_urls.json` with curated diverse URL set
7. **Dashboard** — `bench_dashboard.py` (optional, last)

## Files to Modify

- **Create:** `devtools/__init__.py`, `devtools/verity_bench.py`, `devtools/fixtures/example_urls.json`, `devtools/bench_dashboard.py`
- **Edit:** `.gitignore` (add `devtools/verity_bench.db`)
- **No changes to:** `verity_extractor.py` or any extension files

## Verification

1. `python -m devtools.verity_bench scrape https://en.wikipedia.org/wiki/Python_(programming_language)` — should show full diagnostic output with metadata
2. `python -m devtools.verity_bench scrape https://nature.com/articles/s41586-024-07930-y` — should detect paywall, show metadata extraction
3. `python -m devtools.verity_bench test --urls-file devtools/fixtures/example_urls.json --prompt "test" --label "smoke-test"` — batch test, results persisted
4. `python -m devtools.verity_bench failures --run smoke-test` — failure breakdown
5. `python -m devtools.verity_bench history` — shows the smoke-test run

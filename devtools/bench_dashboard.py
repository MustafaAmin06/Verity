"""
Verity Bench Dashboard — lightweight read-only web UI for browsing test results.

Usage:
    python -m devtools.bench_dashboard [--port 8099]
"""

import argparse
import json
import pathlib
import sqlite3

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
from devtools.triage_catalog import (
    ensure_schema as triage_ensure_schema,
    get_case_detail as triage_get_case_detail,
    get_domain_rollups as triage_get_domain_rollups,
    list_cases as triage_list_cases,
    update_case_review as triage_update_case_review,
)

_DB_PATH = pathlib.Path(__file__).resolve().parent / "verity_bench.db"

app = FastAPI(title="Verity Bench Dashboard", version="0.1.0")


def _get_db() -> sqlite3.Connection:
    db = sqlite3.connect(str(_DB_PATH))
    db.row_factory = sqlite3.Row
    triage_ensure_schema(db)
    return db


def _rows_to_dicts(rows: list) -> list[dict]:
    return [dict(r) for r in rows]


@app.get("/", response_class=HTMLResponse)
async def index():
    return _DASHBOARD_HTML


@app.get("/api/runs")
async def api_runs():
    db = _get_db()
    rows = db.execute("SELECT * FROM test_runs ORDER BY created_at DESC LIMIT 100").fetchall()
    return JSONResponse(_rows_to_dicts(rows))


@app.get("/api/runs/{run_id}")
async def api_run_detail(run_id: str):
    db = _get_db()
    run = db.execute(
        "SELECT * FROM test_runs WHERE run_id = ? OR label = ?", (run_id, run_id)
    ).fetchone()
    if not run:
        return JSONResponse({"error": "Not found"}, status_code=404)
    results = db.execute(
        "SELECT * FROM source_results WHERE run_id = ?", (run["run_id"],)
    ).fetchall()
    return JSONResponse({"run": dict(run), "results": _rows_to_dicts(results)})


@app.get("/api/failures")
async def api_failures():
    db = _get_db()
    rows = db.execute(
        """SELECT failure_category, COUNT(*) as count, GROUP_CONCAT(DISTINCT domain) as domains
           FROM source_results
           WHERE failure_category IS NOT NULL
           GROUP BY failure_category
           ORDER BY count DESC"""
    ).fetchall()
    total = db.execute("SELECT COUNT(*) as cnt FROM source_results").fetchone()["cnt"]
    return JSONResponse({"total_tested": total, "failures": _rows_to_dicts(rows)})


@app.get("/api/compare")
async def api_compare(a: str, b: str):
    db = _get_db()
    results_a = {
        row["url"]: dict(row)
        for row in db.execute(
            "SELECT * FROM source_results WHERE run_id = (SELECT run_id FROM test_runs WHERE run_id = ? OR label = ? LIMIT 1)", (a, a)
        )
    }
    results_b = {
        row["url"]: dict(row)
        for row in db.execute(
            "SELECT * FROM source_results WHERE run_id = (SELECT run_id FROM test_runs WHERE run_id = ? OR label = ? LIMIT 1)", (b, b)
        )
    }
    common = sorted(set(results_a) & set(results_b))
    comparisons = []
    for url in common:
        ra, rb = results_a[url], results_b[url]
        comparisons.append({
            "url": url,
            "a": {"composite_score": ra.get("composite_score"), "verdict": ra.get("verdict")},
            "b": {"composite_score": rb.get("composite_score"), "verdict": rb.get("verdict")},
            "delta": (rb.get("composite_score") or 0) - (ra.get("composite_score") or 0),
        })
    return JSONResponse({"common_urls": len(common), "comparisons": comparisons})


@app.get("/api/triage/summary")
async def api_triage_summary():
    db = _get_db()
    open_cases = db.execute(
        """SELECT COUNT(*) AS cnt FROM triage_cases
           WHERE likely_scrapable = 1 AND review_status IN ('new', 'reviewing')"""
    ).fetchone()["cnt"]
    total_cases = db.execute("SELECT COUNT(*) AS cnt FROM triage_cases").fetchone()["cnt"]
    rollups = triage_get_domain_rollups(db, limit=15)
    return JSONResponse(
        {
            "open_cases": open_cases,
            "total_cases": total_cases,
            "domains": rollups,
        }
    )


@app.get("/api/triage/cases")
async def api_triage_cases(
    status: str | None = None,
    domain: str | None = None,
    category: str | None = None,
    likely_only: bool = True,
    limit: int = 100,
):
    db = _get_db()
    try:
        rows = triage_list_cases(
            db,
            review_status=status,
            domain=domain,
            failure_category=category,
            likely_only=likely_only,
            limit=limit,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not status and likely_only:
        rows = [row for row in rows if row["review_status"] in {"new", "reviewing"}]
    return JSONResponse(rows)


@app.get("/api/triage/cases/{case_id}")
async def api_triage_case_detail(case_id: int):
    db = _get_db()
    detail = triage_get_case_detail(db, case_id)
    if not detail:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse(detail)


@app.post("/api/triage/cases/{case_id}/review")
async def api_triage_case_review(case_id: int, request: Request):
    body = await request.json()
    review_status = body.get("review_status")
    note = body.get("note")
    if not review_status:
        return JSONResponse({"error": "review_status is required"}, status_code=400)

    db = _get_db()
    try:
        ok = triage_update_case_review(
            db,
            case_id=case_id,
            review_status=review_status,
            note=note,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    if not ok:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return JSONResponse({"ok": True})


_DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verity Bench Dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
         background: #0d1117; color: #c9d1d9; padding: 24px; max-width: 1320px; margin: 0 auto; }
  h1 { color: #58a6ff; margin-bottom: 8px; font-size: 1.4em; }
  h2 { color: #8b949e; font-size: 1.1em; margin: 20px 0 10px; }
  h3 { color: #c9d1d9; font-size: 0.95em; margin: 16px 0 10px; }
  table { width: 100%; border-collapse: collapse; margin: 10px 0; }
  th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #21262d; font-size: 0.85em; vertical-align: top; }
  th { color: #8b949e; font-weight: 600; }
  tr:hover { background: #161b22; }
  .clickable { cursor: pointer; color: #58a6ff; }
  .clickable:hover { text-decoration: underline; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 0.75em; font-weight: 600; }
  .badge-green { background: #1b4332; color: #52c41a; }
  .badge-amber { background: #3d2e00; color: #faad14; }
  .badge-red { background: #3b1114; color: #f5222d; }
  .badge-gray { background: #21262d; color: #8b949e; }
  .detail-panel { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 16px; margin: 12px 0; display: none; }
  .detail-panel.active { display: block; }
  .stat { display: inline-block; margin-right: 24px; margin-bottom: 12px; }
  .stat-value { font-size: 1.5em; font-weight: 700; color: #58a6ff; }
  .stat-label { font-size: 0.75em; color: #8b949e; }
  #nav { margin-bottom: 20px; display: flex; gap: 8px; flex-wrap: wrap; }
  #nav button { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 6px 14px;
                border-radius: 6px; cursor: pointer; font-size: 0.85em; }
  #nav button:hover, #nav button.active { background: #30363d; border-color: #58a6ff; }
  .filters { display: flex; gap: 10px; flex-wrap: wrap; margin: 10px 0 14px; align-items: center; }
  .filters input, .filters select, .filters textarea {
    background: #0d1117; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px; padding: 7px 10px;
  }
  .filters button, .action-row button {
    background: #21262d; color: #c9d1d9; border: 1px solid #30363d; border-radius: 6px;
    padding: 7px 10px; cursor: pointer;
  }
  .filters button:hover, .action-row button:hover { border-color: #58a6ff; }
  .grid-two { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; }
  .meta-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 16px; margin: 12px 0; }
  .meta-label { color: #8b949e; font-size: 0.75em; text-transform: uppercase; letter-spacing: 0.04em; }
  .meta-value { color: #c9d1d9; font-size: 0.88em; margin-top: 3px; word-break: break-word; }
  .action-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }
  textarea { width: 100%; min-height: 80px; resize: vertical; }
  .domain-rollups { margin-top: 14px; }
  .muted { color: #8b949e; }
  .nowrap { white-space: nowrap; }
  @media (max-width: 960px) {
    .grid-two { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>

<h1>Verity Bench Dashboard</h1>

<div id="nav">
  <button class="active" onclick="showTab('runs', event)">Runs</button>
  <button onclick="showTab('failures', event)">Failures</button>
  <button onclick="showTab('triage', event)">Triage</button>
</div>

<div id="runs-tab">
  <div id="runs-stats"></div>
  <table id="runs-table">
    <thead><tr><th>Label</th><th>Sources</th><th>Duration</th><th>Variant</th><th>Created</th></tr></thead>
    <tbody id="runs-body"></tbody>
  </table>
  <div id="run-detail" class="detail-panel"></div>
</div>

<div id="failures-tab" style="display:none">
  <div id="failures-content"></div>
</div>

<div id="triage-tab" style="display:none">
  <div id="triage-summary"></div>

  <div class="filters">
    <select id="triage-status">
      <option value="">Open queue</option>
      <option value="new">new</option>
      <option value="reviewing">reviewing</option>
      <option value="confirmed_fixable">confirmed_fixable</option>
      <option value="not_fixable">not_fixable</option>
      <option value="duplicate">duplicate</option>
      <option value="deferred">deferred</option>
    </select>
    <input id="triage-domain" placeholder="Filter domain">
    <input id="triage-category" placeholder="Filter failure category">
    <label class="muted"><input type="checkbox" id="triage-likely" checked> likely only</label>
    <button onclick="loadTriageCases()">Apply Filters</button>
    <button onclick="openTriageJson()">Open JSON</button>
  </div>

  <div class="grid-two">
    <div>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Status</th>
            <th>Priority</th>
            <th>Seen</th>
            <th>Failure</th>
            <th>Domain</th>
            <th>URL</th>
          </tr>
        </thead>
        <tbody id="triage-cases-body"></tbody>
      </table>
    </div>
    <div>
      <div class="detail-panel active" id="triage-detail">
        <div class="muted">Select a triage case to inspect it.</div>
      </div>
    </div>
  </div>
</div>

<script>
let currentTriageCaseId = null;

function badgeClass(verdict) {
  if (verdict === 'reliable' || verdict === 'supported' || verdict === 'confirmed_fixable') return 'green';
  if (verdict === 'caution' || verdict === 'cautious_support' || verdict === 'relevant_unverified' || verdict === 'reviewing' || verdict === 'new') return 'amber';
  if (verdict === 'skeptical' || verdict === 'contradicted' || verdict === 'not_fixable' || verdict === 'duplicate') return 'red';
  if (verdict === 'unverified' || verdict === 'inaccessible') return 'gray';
  return 'gray';
}

async function loadRuns() {
  const resp = await fetch('/api/runs');
  const runs = await resp.json();
  const tbody = document.getElementById('runs-body');
  tbody.innerHTML = '';
  runs.forEach(r => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="clickable" onclick="loadRunDetail('${r.run_id}')">${r.label || r.run_id}</td>
      <td>${r.total_sources}</td>
      <td>${r.duration_ms ? r.duration_ms + 'ms' : 'N/A'}</td>
      <td>${r.prompt_variant || 'default'}</td>
      <td>${(r.created_at || '').slice(0, 19)}</td>
    `;
    tbody.appendChild(tr);
  });
}

async function loadRunDetail(runId) {
  const resp = await fetch('/api/runs/' + runId);
  const data = await resp.json();
  const panel = document.getElementById('run-detail');
  panel.classList.add('active');

  let html = '<h2>Run: ' + (data.run.label || data.run.run_id) + '</h2>';
  html += '<table><thead><tr><th>URL</th><th>Status</th><th>Words</th><th>Method</th><th>Failure</th><th>Score</th><th>Verdict</th></tr></thead><tbody>';
  data.results.forEach(r => {
    const verdictClass = badgeClass(r.verdict);
    const shortUrl = r.url.length > 60 ? r.url.slice(0, 58) + '..' : r.url;
    html += '<tr>'
      + '<td title="' + r.url + '">' + shortUrl + '</td>'
      + '<td>' + (r.http_status || 'N/A') + '</td>'
      + '<td>' + (r.word_count || 0) + '</td>'
      + '<td>' + (r.scrape_method || 'N/A') + '</td>'
      + '<td>' + (r.failure_category || '<span class="badge badge-green">OK</span>') + '</td>'
      + '<td>' + (r.composite_score != null ? r.composite_score : 'N/A') + '</td>'
      + '<td><span class="badge badge-' + verdictClass + '">' + (r.verdict || 'N/A') + '</span></td>'
      + '</tr>';
  });
  html += '</tbody></table>';
  panel.innerHTML = html;
}

async function loadFailures() {
  const resp = await fetch('/api/failures');
  const data = await resp.json();
  const el = document.getElementById('failures-content');

  let html = '<div class="stat"><div class="stat-value">' + data.total_tested + '</div><div class="stat-label">Total URLs Tested</div></div>';
  const totalFail = data.failures.reduce((s, f) => s + f.count, 0);
  html += '<div class="stat"><div class="stat-value">' + totalFail + '</div><div class="stat-label">Total Failures</div></div>';

  html += '<table><thead><tr><th>Category</th><th>Count</th><th>% of Total</th><th>Example Domains</th></tr></thead><tbody>';
  data.failures.forEach(f => {
    const pct = (f.count / data.total_tested * 100).toFixed(1);
    const domains = (f.domains || '').split(',').slice(0, 3).join(', ');
    html += '<tr><td>' + f.failure_category + '</td><td>' + f.count + '</td><td>' + pct + '%</td><td>' + domains + '</td></tr>';
  });
  html += '</tbody></table>';
  el.innerHTML = html;
}

async function loadTriageSummary() {
  const resp = await fetch('/api/triage/summary');
  const data = await resp.json();
  const el = document.getElementById('triage-summary');

  let html = '<div class="stat"><div class="stat-value">' + data.open_cases + '</div><div class="stat-label">Open Queue</div></div>';
  html += '<div class="stat"><div class="stat-value">' + data.total_cases + '</div><div class="stat-label">Total Cases</div></div>';

  if (data.domains && data.domains.length > 0) {
    html += '<div class="domain-rollups"><h3>Hot Domains</h3><table><thead><tr><th>Domain</th><th>Open Cases</th><th>Observations</th><th>Max Priority</th></tr></thead><tbody>';
    data.domains.forEach(row => {
      html += '<tr>'
        + '<td class="clickable" onclick="document.getElementById(\\'triage-domain\\').value=\\'' + row.normalized_domain + '\\'; loadTriageCases();">' + row.normalized_domain + '</td>'
        + '<td>' + row.open_cases + '</td>'
        + '<td>' + row.total_observations + '</td>'
        + '<td>' + row.max_priority + '</td>'
        + '</tr>';
    });
    html += '</tbody></table></div>';
  }

  el.innerHTML = html;
}

async function loadTriageCases() {
  const status = document.getElementById('triage-status').value;
  const domain = document.getElementById('triage-domain').value.trim();
  const category = document.getElementById('triage-category').value.trim();
  const likelyOnly = document.getElementById('triage-likely').checked;
  const params = new URLSearchParams({ limit: '100', likely_only: String(likelyOnly) });
  if (status) params.set('status', status);
  if (domain) params.set('domain', domain);
  if (category) params.set('category', category);

  const resp = await fetch('/api/triage/cases?' + params.toString());
  const rows = await resp.json();
  const tbody = document.getElementById('triage-cases-body');
  tbody.innerHTML = '';

  rows.forEach(row => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="clickable" onclick="loadTriageDetail(${row.case_id})">${row.case_id}</td>
      <td><span class="badge badge-${badgeClass(row.review_status)}">${row.review_status}</span></td>
      <td>${row.priority_score}</td>
      <td>${row.times_seen}</td>
      <td>${row.latest_failure_category || 'N/A'}</td>
      <td>${row.normalized_domain}</td>
      <td title="${row.canonical_url}">${row.canonical_url.length > 68 ? row.canonical_url.slice(0, 66) + '..' : row.canonical_url}</td>
    `;
    tbody.appendChild(tr);
  });

  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="muted">No triage cases match the current filters.</td></tr>';
  }
}

function triageParams() {
  const status = document.getElementById('triage-status').value;
  const domain = document.getElementById('triage-domain').value.trim();
  const category = document.getElementById('triage-category').value.trim();
  const likelyOnly = document.getElementById('triage-likely').checked;
  const params = new URLSearchParams({ limit: '100', likely_only: String(likelyOnly) });
  if (status) params.set('status', status);
  if (domain) params.set('domain', domain);
  if (category) params.set('category', category);
  return params;
}

function openTriageJson() {
  window.open('/api/triage/cases?' + triageParams().toString(), '_blank');
}

function openTriageCaseJson(caseId) {
  window.open('/api/triage/cases/' + caseId, '_blank');
}

async function loadTriageDetail(caseId) {
  currentTriageCaseId = caseId;
  const resp = await fetch('/api/triage/cases/' + caseId);
  const data = await resp.json();
  const panel = document.getElementById('triage-detail');
  const c = data.case;

  let html = '<h2>Triage Case #' + c.case_id + '</h2>';
  html += '<div class="meta-grid">';
  html += '<div><div class="meta-label">URL</div><div class="meta-value">' + c.canonical_url + '</div></div>';
  html += '<div><div class="meta-label">Domain</div><div class="meta-value">' + c.normalized_domain + '</div></div>';
  html += '<div><div class="meta-label">Review Status</div><div class="meta-value"><span class="badge badge-' + badgeClass(c.review_status) + '">' + c.review_status + '</span></div></div>';
  html += '<div><div class="meta-label">Priority</div><div class="meta-value">' + c.priority_score + '</div></div>';
  html += '<div><div class="meta-label">Failure</div><div class="meta-value">' + (c.latest_failure_category || 'N/A') + ' / ' + (c.likely_scrapable_reason || 'not queued') + '</div></div>';
  html += '<div><div class="meta-label">Score / Verdict</div><div class="meta-value">' + (c.latest_composite_score != null ? c.latest_composite_score : 'N/A') + ' / ' + (c.latest_verdict || 'N/A') + '</div></div>';
  html += '<div><div class="meta-label">Prompt Sample</div><div class="meta-value">' + (c.latest_prompt_snippet || 'N/A') + '</div></div>';
  html += '<div><div class="meta-label">Context Sample</div><div class="meta-value">' + (c.latest_context_snippet || 'N/A') + '</div></div>';
  html += '<div><div class="meta-label">Seen</div><div class="meta-value">' + c.times_seen + '</div></div>';
  html += '<div><div class="meta-label">Confirmed Fixable</div><div class="meta-value">' + c.times_confirmed_fixable + '</div></div>';
  html += '</div>';

  html += '<h3>Review Note</h3>';
  html += '<textarea id="triage-note">' + (c.review_note || '') + '</textarea>';
  html += '<div class="action-row">';
  html += '<button onclick="openTriageCaseJson(' + c.case_id + ')">Case JSON</button>';
  ['new', 'reviewing', 'confirmed_fixable', 'not_fixable', 'duplicate', 'deferred'].forEach(status => {
    html += '<button onclick="submitTriageReview(\\'' + status + '\\')">' + status + '</button>';
  });
  html += '</div>';

  html += '<h3>Recent Events</h3>';
  html += '<table><thead><tr><th>Observed</th><th>Source</th><th>Failure</th><th>Words</th><th>HTTP</th><th>Method</th><th>Playwright</th><th>Note</th></tr></thead><tbody>';
  data.events.forEach(event => {
    html += '<tr>'
      + '<td class="nowrap">' + (event.observed_at || '').slice(0, 19) + '</td>'
      + '<td>' + (event.source_kind || 'N/A') + '</td>'
      + '<td>' + (event.failure_category || 'N/A') + '</td>'
      + '<td>' + (event.word_count || 0) + '</td>'
      + '<td>' + (event.http_status || 'N/A') + '</td>'
      + '<td>' + (event.scrape_method || 'N/A') + '</td>'
      + '<td>' + (event.playwright_attempted ? (event.playwright_improved ? 'improved' : 'attempted') : 'no') + '</td>'
      + '<td>' + (event.scrape_note || 'ok') + '</td>'
      + '</tr>';
  });
  html += '</tbody></table>';

  if (data.actions && data.actions.length > 0) {
    html += '<h3>Review History</h3>';
    html += '<table><thead><tr><th>When</th><th>Status</th><th>Note</th></tr></thead><tbody>';
    data.actions.forEach(action => {
      html += '<tr>'
        + '<td class="nowrap">' + (action.created_at || '').slice(0, 19) + '</td>'
        + '<td>' + (action.review_status || action.action_type) + '</td>'
        + '<td>' + (action.note || '') + '</td>'
        + '</tr>';
    });
    html += '</tbody></table>';
  }

  panel.innerHTML = html;
}

async function submitTriageReview(status) {
  if (!currentTriageCaseId) return;
  const note = document.getElementById('triage-note').value;
  await fetch('/api/triage/cases/' + currentTriageCaseId + '/review', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ review_status: status, note })
  });
  await loadTriageSummary();
  await loadTriageCases();
  await loadTriageDetail(currentTriageCaseId);
}

function showTab(tab, evt) {
  document.getElementById('runs-tab').style.display = tab === 'runs' ? 'block' : 'none';
  document.getElementById('failures-tab').style.display = tab === 'failures' ? 'block' : 'none';
  document.getElementById('triage-tab').style.display = tab === 'triage' ? 'block' : 'none';
  document.querySelectorAll('#nav button').forEach(b => b.classList.remove('active'));
  if (evt) evt.target.classList.add('active');
  if (tab === 'failures') loadFailures();
  if (tab === 'triage') {
    loadTriageSummary();
    loadTriageCases();
  }
}

loadRuns();
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Verity Bench Dashboard")
    parser.add_argument("--port", type=int, default=8099, help="Port to serve on")
    args = parser.parse_args()

    if not _DB_PATH.exists():
        print(f"Database not found at {_DB_PATH}")
        print("Run some tests first: python -m devtools.verity_bench scrape <url> --save")
        return

    print(f"Dashboard: http://localhost:{args.port}")
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

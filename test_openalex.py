"""Quick smoke test for the OpenAlex API integration."""
import asyncio
import os
import sys
import pathlib

# Load .env so OPENALEX_EMAIL is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import httpx

OPENALEX_EMAIL = os.getenv("OPENALEX_EMAIL", "")
BASE = "https://api.openalex.org"
UA = f"Verity/1.0 (test) (mailto:{OPENALEX_EMAIL})" if OPENALEX_EMAIL else "Verity/1.0 (test)"

# Test data
TEST_DOI = "10.48550/arXiv.1706.03762"   # "Attention Is All You Need"
TEST_ISSN = "0028-0836"                  # Nature

passed = 0
total = 0


def check(label: str, ok: bool, detail: str = ""):
    global passed, total
    total += 1
    if ok:
        passed += 1
        print(f"   ✓ {label}{': ' + detail if detail else ''}")
    else:
        print(f"   ✗ {label}{': ' + detail if detail else ''}")


async def main():
    print(f"OpenAlex email : {OPENALEX_EMAIL or '(not set — common pool)'}")
    print(f"User-Agent     : {UA}")

    async with httpx.AsyncClient(
        timeout=10,
        headers={"User-Agent": UA, "Accept": "application/json"},
        http2=True,
    ) as client:

        # ── 1. Work by DOI (filter form avoids path-encoding issues) ──
        print(f"\n1. Work lookup (DOI: {TEST_DOI})")
        r = await client.get(f"{BASE}/works", params={
            "filter": f"doi:{TEST_DOI}",
            "per_page": "1",
            "select": "id,title,cited_by_count,type,primary_location,authorships,topics,open_access",
        })
        work_source_id = ""
        work_author_id = ""
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                w = results[0]
                check("HTTP 200 + result", True)
                check("title", bool(w.get("title")), w.get("title", "")[:60])
                check("type", bool(w.get("type")), w.get("type", ""))
                check("cited_by_count", w.get("cited_by_count", 0) > 0, str(w.get("cited_by_count")))
                topics = [t["display_name"] for t in (w.get("topics") or [])[:3] if t.get("display_name")]
                check("topics", bool(topics), str(topics))
                oa = w.get("open_access") or {}
                check("open_access flag", "is_oa" in oa, str(oa.get("is_oa")))
                loc = w.get("primary_location") or {}
                src = loc.get("source") or {}
                work_source_id = src.get("id", "")
                check("source_id embedded", bool(work_source_id), work_source_id)
                authorships = w.get("authorships") or []
                if authorships:
                    work_author_id = ((authorships[0].get("author") or {}).get("id", ""))
                check("author_id embedded", bool(work_author_id), work_author_id)
            else:
                check("got results", False, "empty results list")
        else:
            check("HTTP 200", False, f"HTTP {r.status_code}: {r.text[:120]}")

        # ── 2. Source by ISSN ──
        print(f"\n2. Source lookup (ISSN: {TEST_ISSN} — Nature)")
        r = await client.get(f"{BASE}/sources", params={
            "filter": f"issn:{TEST_ISSN}",
            "per_page": "1",
            "select": "id,display_name,type,host_organization_name,is_oa,summary_stats",
        })
        if r.status_code == 200:
            results = r.json().get("results", [])
            if results:
                s = results[0]
                stats = s.get("summary_stats") or {}
                check("HTTP 200 + result", True)
                check("display_name", bool(s.get("display_name")), s.get("display_name", ""))
                check("type", bool(s.get("type")), s.get("type", ""))
                check("publisher", bool(s.get("host_organization_name")), s.get("host_organization_name", ""))
                check("h_index", (stats.get("h_index") or 0) > 0, str(stats.get("h_index")))
                check("2yr_mean_citedness", stats.get("2yr_mean_citedness") is not None, str(stats.get("2yr_mean_citedness")))
            else:
                check("got results", False, "empty results list")
        else:
            check("HTTP 200", False, f"HTTP {r.status_code}: {r.text[:120]}")

        # ── 3. Source by OpenAlex ID ──
        if work_source_id:
            short = work_source_id.split("/")[-1]
            print(f"\n3. Source by OpenAlex ID ({short})")
            r = await client.get(f"{BASE}/sources/{short}", params={
                "select": "id,display_name,type,host_organization_name,is_oa,summary_stats",
            })
            if r.status_code == 200:
                s = r.json()
                stats = s.get("summary_stats") or {}
                check("HTTP 200", True)
                check("display_name", bool(s.get("display_name")), s.get("display_name", ""))
                check("h_index", stats.get("h_index") is not None, str(stats.get("h_index")))
            else:
                check("HTTP 200", False, f"HTTP {r.status_code}")
        else:
            print("\n3. Source by ID — skipped (no source_id returned from work)")

        # ── 4. Author lookup ──
        if work_author_id:
            short = work_author_id.split("/")[-1]
            print(f"\n4. Author lookup ({short})")
            r = await client.get(f"{BASE}/authors/{short}", params={
                "select": "id,display_name,summary_stats,last_known_institutions",
            })
            if r.status_code == 200:
                a = r.json()
                stats = a.get("summary_stats") or {}
                insts = a.get("last_known_institutions") or []
                check("HTTP 200", True)
                check("display_name", bool(a.get("display_name")), a.get("display_name", ""))
                check("h_index", stats.get("h_index") is not None, str(stats.get("h_index")))
                check("institution", bool(insts), insts[0].get("display_name", "") if insts else "N/A")
            else:
                check("HTTP 200", False, f"HTTP {r.status_code}")
        else:
            print("\n4. Author lookup — skipped (no author_id returned from work)")

        # ── 5. SQLite cache round-trip ──
        print("\n5. SQLite cache round-trip")
        try:
            sys.path.insert(0, str(pathlib.Path(__file__).parent))
            from verity_extractor import _oa_cache_set, _oa_cache_get
            _oa_cache_set("openalex_works", "__test_key__", {"hello": "world"})
            got = _oa_cache_get("openalex_works", "__test_key__")
            check("write + read", got == {"hello": "world"}, str(got))
        except Exception as e:
            check("cache import + round-trip", False, str(e))

        # ── 6. Full pipeline via enrich_with_openalex ──
        print("\n6. Full enrich_with_openalex() pipeline")
        try:
            from verity_extractor import enrich_with_openalex, ScrapedSource
            # Build a minimal ScrapedSource with the test DOI
            mock = ScrapedSource(
                url="https://arxiv.org/abs/1706.03762",
                label="Attention Is All You Need",
                context="test",
                domain="arxiv.org",
                live=True,
                http_status=200,
                title="Attention Is All You Need",
                description=None,
                body_text=None,
                date="2017",
                author="Ashish Vaswani",
                doi=TEST_DOI,
                paywalled=False,
                is_pdf=False,
                json_ld=None,
                keywords=[],
                word_count=0,
                scrape_method="test",
                scrape_note=None,
                scrape_success=True,
            )
            enrichment = await enrich_with_openalex(mock)
            check("returns dict", isinstance(enrichment, dict))
            check("oa_cited_by_count", "oa_cited_by_count" in enrichment, str(enrichment.get("oa_cited_by_count")))
            check("oa_topics", bool(enrichment.get("oa_topics")), str(enrichment.get("oa_topics", [])[:2]))
            check("oa_work_type", bool(enrichment.get("oa_work_type")), enrichment.get("oa_work_type", ""))
        except Exception as e:
            check("enrich_with_openalex", False, str(e))

    # ── Summary ──
    print("\n" + "─" * 50)
    print(f"  {passed}/{total} checks passed")
    if passed < total:
        sys.exit(1)


asyncio.run(main())

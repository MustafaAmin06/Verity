# VerifyAI

**A browser extension that audits LLM-generated citations in real time — before you act on them.**

---

## Recognition

**🏆 First Place — EWB Annual Hackathon, University of Toronto**
*Recognized for innovation in AI accountability and citation integrity.*

---

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [How It Works — The Verification Pipeline](#2-how-it-works--the-verification-pipeline)
3. [Content Extraction Stack](#3-content-extraction-stack)
4. [Challenges, Debates, and Future Directions](#4-challenges-debates-and-future-directions)
5. [Design Principles](#5-design-principles)
6. [Getting Started](#6-getting-started)

---

## 1. Product Overview

### The Problem
Large language models have become a default research tool for millions of people. Students, professionals, and everyday users rely on LLMs for confident, well-structured answers — answers that are frequently accompanied by cited sources. The fundamental issue is that LLMs hallucinate: they fabricate statistics, attribute claims to real papers that never made those assertions, and cite URLs that either do not exist or, more insidiously, link to real pages whose content contradicts the claim entirely.

Existing verification tools address this problem at the platform level (e.g., Gemini Grounding, Perplexity citations, SearchGPT), but they share a critical blind spot: **they confirm that a source exists, not that the source actually supports the specific claim being made.** A green checkmark next to a real journal URL is not the same as a verified fact.

### What VerifyAI Does
VerifyAI is a browser extension that intercepts LLM responses, extracts every cited source, and executes a multi-layer verification pipeline in the background. By the time a user finishes reading a response, each source has been independently scored and a trust verdict is displayed inline — requiring no additional action from the user.

The verification pipeline operates across two distinct layers simultaneously:
1. **Layer 1: Metadata Credibility** — applying the CRAAP Test framework (Currency, Relevance, Authority, Accuracy, Purpose) to evaluate each source's publication date, author attribution, domain tier, and institutional standing.
2. **Layer 2: Claim-Level Content Verification** — retrieving the full source body, extracting its text, and determining whether the specific sentence cited by the LLM is semantically supported by what the source actually states.

The result is a trust score, a plain-English verdict, and — where sources are found to be insufficient — curated alternative sources that are pre-verified and directly relevant to the subject matter.

### Intended Audience
- **Students** conducting research who cannot afford to cite a fabricated statistic in an academic submission.
- **Professionals** who use LLMs to prepare for technical or legal discussions and require reliable source attribution.
- **Journalists and fact-checkers** who need to validate AI-assisted research rapidly and with confidence.
- **General users** who have encountered inaccurate or hallucinated information after acting on an LLM-generated response.

### Core Differentiator
VerifyAI does not merely confirm whether a link resolves. **It reads the source.** It determines whether the source contains the information the LLM asserted it does. This distinction separates a metadata badge from genuine verification — and it is precisely what no native LLM platform currently offers at the individual claim level.

---

## 2. How It Works — The Verification Pipeline

VerifyAI employs a rigorous seven-stage pipeline to transform raw LLM output into an actionable trust verdict.

![Full System Architecture flowchart](Image/unified_pipeline_combined.svg)

### Stage 1: Signal Extraction and Semantic Claim Matching (Per Source)
For each cited URL, the pipeline verifies URL liveness and retrieves content via a fallback chain. It then parses the CRAAP metadata and extracts five independent signals in parallel:

![Signal Extraction flowchart](Image/stage1_signal_extraction_detail.svg)
1. **Domain Credibility:** Tier classification (academic journal, official body, established news outlet, etc.). A confirmed DOI overrides domain tier classification entirely.
2. **Publication Recency:** Derived from the extracted publication date. Missing dates are explicitly flagged.
3. **Author Presence:** Named individuals receive higher scores than corporate bylines; an absent author is treated as a credibility concern.
4. **Relevance to Query:** Significant terms from the original prompt are matched against the source title and body text.
5. **Claim Alignment (Highest Weight):** Determines whether the cited sentence is supported by the source text. Matches are classified as **Supported** (claim found in source) or **Unsupported** (absent or contradicted).

### Stage 2: Composite Scoring
The five signals are combined using a weighted formula:
- **Domain credibility** and **claim alignment** carry the highest weight.
- **Author presence** carries the lowest weight.
- **URL liveness functions as a hard gate** — a dead link produces a score of 0, regardless of all other signals.
- A confirmed DOI adds a credibility bonus to the overall score.

### Stage 3: Verdict Mapping
Composite scores are mapped to one of four trust levels:
- **≥ 75 — Reliable:** The source is real, credible, current, and supports the stated claim.
- **50 to 74 — Treat with Caution:** One or more meaningful signals are weak or absent.
- **< 50 — Exercise Skepticism:** Multiple signals are poor; independent verification with stronger sources is advised.
- **0 — Unverified:** The link is dead or the source content cannot be retrieved.

### Stage 4: LLM-Based Scoring and Plain-English Generation
A locally executed Ollama model (default: `qwen3.5:2b`) evaluates each source against the original claim. It scores both relevance (how well the source addresses the user's query) and alignment (how well the source content supports the specific claim asserted). Scores are guided by explicit rubrics to prevent the model from defaulting to neutral mid-range values. Each source also receives a plain-English rationale and a one-sentence implication for the user. If Ollama is unavailable, the pipeline falls back gracefully to neutral scores and a manual-check notice.

### Stages 5 and 6: Topic Detection and Further Reading
Once verdicts are established, the system identifies the overarching topic using keyword clusters derived from the prompt and AI response. The local LLM then recommends three authoritative sources for further reading — specified by title, domain, and search query rather than direct URL (to eliminate hallucination risk). The backend constructs functional, deterministic search URLs from these suggestions (Google Scholar for academic sources; site-specific search for known domains).

### Stage 7: Assembly and Delivery
All verdicts are sorted with reliable sources first and packaged into a structured object alongside summary counts. This object is transmitted to the extension front end, which renders results inline within the user's active browser session.

---

## 3. Content Extraction Stack

Content extraction employs a two-step fallback chain, designed to maximize coverage while minimizing latency:
- **Step 1 — HTTP GET:** A fast raw HTML fetch, sufficient for static pages, open-access journals, Wikipedia, government sites, and most news outlets. Parsed with Mozilla Readability to strip boilerplate and isolate the article body.
- **Step 2 — Playwright Fallback:** A full headless Chromium instance invoked for JavaScript-rendered pages where a direct GET request returns an empty DOM. Executed server-side.
- **Graceful Degradation:** Paywalled or bot-blocked pages are explicitly flagged as *'content inaccessible — metadata only'*, ensuring an honest signal rather than silently omitting the failure.

---

## 4. Challenges, Debates, and Future Directions

### Key Design Tensions
- **The Threshold Problem:** Determining an appropriate score cutoff (e.g., 75 points for "Reliable") is fundamentally a product values decision. The architecture supports flexible tuning — recency may be weighted more heavily in fast-moving domains, and semantic alignment can be configured as a hard-gate requirement rather than a weighted signal.
- **The Auditor Paradox:** Using an AI system to evaluate the output of another AI system risks compounding hallucinations. VerifyAI mitigates this by ensuring the auditing layer does not generate new claims. Claim matching relies on string and term overlap; plain-English verdicts follow standardized templates rather than free-form generation.
- **Scope Transparency:** A verification tool that explicitly surfaces what it cannot assess is more trustworthy than one that silently omits failures. Paywalled pages, dead links, and missing author information are deliberately presented as caution indicators rather than hidden from the user.

### Future Roadmap
- **Embedding-Based Semantic Similarity (v2):** Moving beyond keyword overlap to capture paraphrasing without requiring a full LLM inference call.
- **User-Adjustable Signal Weights:** Enabling power users to reprioritize recency for rapidly evolving domains or elevate authority for technical fields.
- **Cross-Source Contradiction Detection:** Flagging instances in which two sources cited in the same LLM response contradict one another.
- **Citation History and Learning:** Tracking which LLM–topic combinations produce the highest rates of citation failure over time.
- **Enterprise API Access:** Providing organizations with a programmatic interface to route LLM outputs through the verification backend at scale.

---

## 5. Design Principles

- **Local inference only** — all scoring is performed using a locally hosted Ollama model; no user data or query content leaves the machine.
- **Transparency over false confidence** — limitations are surfaced explicitly; unknown or unverifiable signals are flagged rather than silently passed.
- **Speed through architecture, not shortcuts** — HTTP GET is attempted first; Playwright is invoked only when required, ensuring responsiveness without sacrificing coverage.
- **Plain language over raw numbers** — verdicts are written to communicate intent clearly, not merely to report a score.
- **Real URLs only** — further reading links are constructed deterministically from LLM-suggested search queries and are never passed through directly from model output.

---

## 6. Getting Started

The following steps describe how to run VerifyAI locally.

### Prerequisites
- **Python 3.10 or later**
- **[Ollama](https://ollama.com/)** — required for local LLM scoring

### Backend Setup (Python)
The backend is responsible for source extraction, web scraping, and verification scoring.

1. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Install Playwright browsers:**
   ```bash
   playwright install chromium
   ```

3. **Pull the Ollama model:**
   ```bash
   ollama pull qwen3.5:2b
   ```
   Any Ollama-compatible model may be used by setting the `OLLAMA_MODEL` variable in `.env`. Larger models (e.g. `llama3.2:3b`, `phi4-mini`) will produce more accurate scoring results.

4. **Start the extractor server:**
   ```bash
   python verity_extractor.py
   ```
   *The server will start on `http://localhost:8001`. Ollama must also be running (`ollama serve`) for LLM scoring to be active.*

### Extension Installation
1. Open Chrome and navigate to `chrome://extensions/`.
2. Enable **Developer mode** using the toggle in the top-right corner.
3. Click **Load unpacked**.
4. Select the `verity-extension` folder from this repository.

---

> **VerifyAI** — Reducing AI misinformation risk through rigorous, claim-level citation verification.

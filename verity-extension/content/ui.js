window.Verity = window.Verity || {};

window.Verity.ui = {
  /**
   * Inject the "Check sources" button below a response element.
   */
  injectButton(responseEl, sources, platformConfig) {
    const container = responseEl.closest("[data-message-id]") || responseEl.parentElement;
    if (container.hasAttribute("data-verity-processed")) return;
    container.setAttribute("data-verity-processed", "true");

    const btn = document.createElement("button");
    btn.className = "verity-trigger-btn";
    btn.textContent = "Check sources with Verity";
    btn.addEventListener("click", () => {
      this._handleCheck(responseEl, sources, btn, platformConfig, container);
    });

    container.after(btn);
  },

  /**
   * Auto-check sources without requiring a button click.
   * Creates the panel directly and triggers the check flow.
   */
  autoCheck(responseEl, sources, platformConfig) {
    const container = responseEl.closest("[data-message-id]") || responseEl.parentElement;
    if (container.hasAttribute("data-verity-processed")) return;
    container.setAttribute("data-verity-processed", "true");

    // Create a hidden placeholder button so _handleCheck can remove it cleanly
    const placeholder = document.createElement("span");
    container.after(placeholder);

    this._handleCheck(responseEl, sources, placeholder, platformConfig, container);
  },

  /** Remove all child nodes (Trusted Types safe — no innerHTML) */
  _clearElement(el) {
    while (el.firstChild) el.firstChild.remove();
  },

  async _handleCheck(responseEl, sources, btn, platformConfig, container) {
    btn.textContent = "Checking...";
    btn.disabled = true;

    const panel = document.createElement("div");
    panel.className = "verity-panel";
    btn.after(panel);
    this._renderProgress(panel, sources.length);

    const progressEl = panel.querySelector(".verity-progress-text");
    window.Verity.api.onProgress((msg) => {
      if (progressEl) {
        progressEl.textContent = `Scraping ${msg.domain} [${msg.completed}/${msg.total}]`;
      }
    });

    const prompt = window.Verity.extractor.extractPrompt(platformConfig.selectors);
    const fullResponse = window.Verity.extractor.extractResponse(
      responseEl,
      VERITY_CONFIG.maxBodyTextChars
    );

    const payload = {
      sources,
      original_prompt: prompt || fullResponse.slice(0, 200),
      full_ai_response: fullResponse,
    };

    try {
      const cacheKey = window.Verity.api.computeCacheKey(payload);
      const data = await window.Verity.api.fetchWithDedup(cacheKey, payload);
      window.Verity.api.clearProgress();
      btn.remove();
      this._clearElement(panel);
      panel.classList.remove("verity-loading");
      this._renderScorecard(data, panel, responseEl);
    } catch (err) {
      window.Verity.api.clearProgress();
      btn.remove();
      this._clearElement(panel);
      panel.classList.remove("verity-loading");
      this._renderError(err, panel, () => {
        panel.remove();
        container.removeAttribute("data-verity-processed");
        this.injectButton(responseEl, sources, platformConfig);
      });
    }
  },

  _renderSkeleton(panel) {
    panel.classList.add("verity-loading");
    for (let i = 0; i < 3; i++) {
      const skel = document.createElement("div");
      skel.className = "verity-skeleton";
      panel.appendChild(skel);
    }
  },

  _renderProgress(panel, totalSources) {
    panel.classList.add("verity-loading");
    const progressText = document.createElement("div");
    progressText.className = "verity-progress-text";
    progressText.textContent = `Preparing to scrape ${totalSources} source${totalSources !== 1 ? "s" : ""}...`;
    panel.appendChild(progressText);

    const skel = document.createElement("div");
    skel.className = "verity-skeleton";
    panel.appendChild(skel);
  },

  // --- Score helpers ---

  _toFiveScale(score100) {
    if (score100 === undefined || score100 === null) return null;
    return (score100 / 20).toFixed(1);
  },

  _scoreColor(score100) {
    if (score100 === undefined || score100 === null) return '#9ca3af';
    if (score100 >= 75) return '#4ade80';
    if (score100 >= 50) return '#facc15';
    if (score100 >= 25) return '#f87171';
    return '#9ca3af';
  },

  _primaryScore(source) {
    if (!source) return null;
    if (source.overall_score !== undefined && source.overall_score !== null) {
      return source.overall_score;
    }
    if (source.composite_score !== undefined && source.composite_score !== null) {
      return source.composite_score;
    }
    return null;
  },

  _themeColor(source, fallbackScore) {
    const map = {
      green: '#4ade80',
      amber: '#facc15',
      red: '#f87171',
      gray: '#9ca3af',
    };
    if (source && typeof source.color === "string" && source.color.trim()) {
      return map[source.color] || source.color;
    }
    return this._scoreColor(fallbackScore);
  },

  _verdictLabel(source) {
    if (source?.verdict_label) return source.verdict_label;
    const map = {
      supported: 'Supported by source',
      cautious_support: 'Some support, but use caution',
      relevant_unverified: 'Relevant, but not verified',
      contradicted: 'Contradicted by source',
      inaccessible: 'Inaccessible or insufficient evidence',
      reliable: 'Reliable',
      unverified: 'Unverified',
    };
    if (source?.verdict && map[source.verdict]) return map[source.verdict];
    if (source?.live === false) return 'Inaccessible or insufficient evidence';
    return 'Unverified';
  },

  _titleCase(text) {
    return String(text || '')
      .replace(/[_-]+/g, ' ')
      .split(/\s+/)
      .filter(Boolean)
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ');
  },

  _scoreDescriptor(score) {
    if (score === undefined || score === null) return 'Not scored';
    if (score >= 80) return 'High';
    if (score >= 60) return 'Moderate';
    if (score >= 40) return 'Limited';
    return 'Weak';
  },

  _humanizeSupportClass(value) {
    const map = {
      direct_support: 'Direct support',
      qualified_support: 'Qualified support',
      topic_relevant_unverified: 'Relevant, not verified',
      mixed_or_ambiguous: 'Mixed or ambiguous',
      contradicted: 'Contradicted',
    };
    return map[value] || this._titleCase(value || 'unknown');
  },

  _humanizeEvidenceSpecificity(value) {
    const map = {
      direct: 'Direct evidence',
      paraphrased: 'Paraphrased evidence',
      weak: 'Weak evidence',
      none: 'No clear evidence',
    };
    return map[value] || this._titleCase(value || 'unknown');
  },

  _hasRubricSignals(signals) {
    if (!signals || typeof signals !== "object") return false;
    const required = [
      'source_credibility_score',
      'claim_support_score',
      'decision_confidence_score',
    ];
    return required.every((key) => Number.isFinite(signals[key]));
  },

  _humanizeTier(tier) {
    const map = {
      academic_journal: 'Academic / Research',
      official_body: 'Official / Government',
      medical_authority: 'Medical Authority',
      established_news: 'Established News',
      independent_blog: 'Independent / Blog',
      reference_tertiary: 'Reference / Tertiary',
      flagged: 'Flagged Source',
    };
    return map[tier] || 'Unknown';
  },

  _authorDisplay(source) {
    const signals = source.signals || {};
    if (source.author) {
      const hIndex = signals.oa_author_h_index;
      return hIndex ? `${source.author} · h-index ${hIndex}` : source.author;
    }
    if (source.author_label) return source.author_label;
    if (
      source.authorship_type === "institutional" ||
      ['academic_journal', 'official_body', 'medical_authority'].includes(signals.domain_tier)
    ) {
      return 'Institutional page';
    }
    return 'Unknown';
  },

  _humanizeAuthoritySource(source) {
    const map = {
      registry: 'Curated registry',
      openalex: 'OpenAlex',
      crossref: 'Crossref',
      ror: 'ROR',
      wikidata: 'Wikidata',
      learned_domain: 'Learned domain cache',
    };
    return map[source] || source || 'Unknown';
  },

  // --- Main render ---

  _renderScorecard(data, panel) {
    const sources = data.sources || data.scraped_sources || [];

    if (sources.length === 0) {
      const empty = document.createElement("p");
      empty.className = "verity-empty";
      empty.textContent = "No sources could be analyzed.";
      panel.appendChild(empty);
      return;
    }

    // Sort by the new overall score, falling back to legacy composite score.
    const sorted = [...sources].sort((a, b) =>
      (this._primaryScore(b) || 0) - (this._primaryScore(a) || 0)
    );

    // Render all sources as full-size, expandable cards
    sorted.forEach((source) => {
      panel.appendChild(this._createCard(source));
    });

  },

  // --- Full-size card ---

  _createCard(source) {
    const score100 = this._primaryScore(source);
    const color = this._themeColor(source, score100);
    const summaryText = source.reason || source.description || source.implication || "";

    const card = document.createElement("div");
    card.className = "verity-card";
    card.style.setProperty('--verity-score-color', color);

    // Click to toggle expanded
    card.addEventListener("click", () => {
      card.classList.toggle("verity-card--expanded");
    });

    // Summary row: verdict-first content + compact score chip
    const summary = document.createElement("div");
    summary.className = "verity-card-summary";

    const main = document.createElement("div");
    main.className = "verity-card-main";

    const header = document.createElement("div");
    header.className = "verity-card-header";

    const badge = document.createElement("span");
    badge.className = "verity-verdict-badge";
    badge.textContent = this._verdictLabel(source);

    const title = document.createElement("a");
    title.className = "verity-card-title";
    title.textContent = source.title || source.label || source.url || "";
    if (source.url) {
      title.href = source.url;
      title.target = "_blank";
      title.rel = "noopener noreferrer";
      title.addEventListener("click", (e) => e.stopPropagation());
    }

    const meta = document.createElement("div");
    meta.className = "verity-card-meta";
    meta.textContent = [source.domain, source.date].filter(Boolean).join(" · ");

    const reason = document.createElement("div");
    reason.className = "verity-card-summary-reason";
    reason.textContent = summaryText;

    const scoreChip = document.createElement("div");
    scoreChip.className = "verity-score-chip";
    scoreChip.textContent = score100 !== undefined && score100 !== null ? `${score100}/100` : '—';

    header.appendChild(badge);
    main.append(header, title, meta);
    if (summaryText) {
      main.appendChild(reason);
    }

    summary.append(main, scoreChip);
    card.appendChild(summary);

    // Expandable detail
    card.appendChild(this._createDetail(source));

    return card;
  },

  // --- Expanded detail ---

  _createDetail(source) {
    const detail = document.createElement("div");
    detail.className = "verity-card-detail";

    const signals = source.signals || {};
    const hasRubric = this._hasRubricSignals(signals);

    if (hasRubric) {
      this._appendDecisionSummary(detail, source);
      this._appendRubricFlags(detail, signals);
      this._appendRubricSection(detail, signals);
      this._appendVerificationDetails(detail, signals);
      this._appendMetadataSection(detail, source, signals);
    } else {
      this._appendLegacyDetail(detail, source, signals);
    }

    this._appendSourceEnrichment(detail, source);

    const contextSection = this._createContextSection(source);
    if (contextSection) {
      detail.appendChild(contextSection);
    }

    return detail;
  },

  _appendDecisionSummary(detail, source) {
    const reason = source.reason || source.description || "";
    const implication = source.implication || "";
    if (!reason && !implication) return;

    const section = this._createDetailSection("Decision summary");

    if (reason) {
      const reasonEl = document.createElement("p");
      reasonEl.className = "verity-card-reason";
      reasonEl.textContent = reason;
      section.appendChild(reasonEl);
    }

    if (implication) {
      const implicationEl = document.createElement("p");
      implicationEl.className = "verity-card-implication";
      implicationEl.textContent = implication;
      section.appendChild(implicationEl);
    }

    detail.appendChild(section);
  },

  _appendRubricFlags(detail, signals) {
    const pills = [];
    if (signals.retrieval_limited) {
      pills.push("Limited source access");
    }
    if (signals.metadata_only) {
      pills.push("Metadata only");
    }
    if (pills.length === 0) return;

    const row = document.createElement("div");
    row.className = "verity-flag-row";

    pills.forEach((label) => {
      const pill = document.createElement("span");
      pill.className = "verity-flag-pill";
      pill.textContent = label;
      row.appendChild(pill);
    });

    detail.appendChild(row);
  },

  _appendRubricSection(detail, signals) {
    const section = this._createDetailSection("Rubric breakdown");
    const rubric = document.createElement("div");
    rubric.className = "verity-rubric";

    const axes = [
      { label: "Credibility", value: signals.source_credibility_score },
      { label: "Claim support", value: signals.claim_support_score },
      { label: "Confidence", value: signals.decision_confidence_score },
    ];

    axes.forEach(({ label, value }) => {
      const row = document.createElement("div");
      row.className = "verity-rubric-row";

      const head = document.createElement("div");
      head.className = "verity-rubric-row-head";

      const labelWrap = document.createElement("div");
      labelWrap.className = "verity-rubric-label-wrap";

      const labelEl = document.createElement("div");
      labelEl.className = "verity-rubric-label";
      labelEl.textContent = label;

      const descriptor = document.createElement("div");
      descriptor.className = "verity-rubric-descriptor";
      descriptor.textContent = this._scoreDescriptor(value);

      const scoreEl = document.createElement("div");
      scoreEl.className = "verity-rubric-score";
      scoreEl.textContent = value != null ? `${value}/100` : '—';

      const track = document.createElement("div");
      track.className = "verity-rubric-track";

      const fill = document.createElement("div");
      fill.className = "verity-rubric-fill";
      fill.style.width = `${Math.max(0, Math.min(100, Number(value) || 0))}%`;

      labelWrap.append(labelEl, descriptor);
      head.append(labelWrap, scoreEl);
      track.appendChild(fill);
      row.append(head, track);
      rubric.appendChild(row);
    });

    section.appendChild(rubric);
    detail.appendChild(section);
  },

  _appendVerificationDetails(detail, signals) {
    const facts = [];
    facts.push(["Support class", this._humanizeSupportClass(signals.support_class)]);
    facts.push(["Evidence specificity", this._humanizeEvidenceSpecificity(signals.evidence_specificity)]);
    facts.push(["Confidence level", this._titleCase(signals.decision_confidence_level || 'unknown')]);

    if (signals.contradiction_strength && signals.contradiction_strength !== "none") {
      facts.push(["Contradiction strength", this._titleCase(signals.contradiction_strength)]);
    }

    if (Array.isArray(signals.matched_terms) && signals.matched_terms.length > 0) {
      facts.push(["Matched terms", signals.matched_terms.join(", ")]);
    }

    const section = this._createDetailSection("Verification details");
    const grid = document.createElement("div");
    grid.className = "verity-facts-grid";

    facts.forEach(([label, value]) => {
      if (!value) return;
      grid.appendChild(this._factCell(label, value));
    });

    section.appendChild(grid);
    detail.appendChild(section);
  },

  _appendMetadataSection(detail, source, signals) {
    const section = this._createDetailSection("Source metadata");
    const grid = document.createElement("div");
    grid.className = "verity-detail-grid";

    grid.appendChild(this._createStatusCell(source));
    grid.appendChild(this._detailCell("Source tier", this._humanizeTier(signals.domain_tier)));
    grid.appendChild(this._detailCell("Author", this._authorDisplay(source)));
    grid.appendChild(this._detailCell("Publication", source.domain || ""));

    const authorityLabel = signals.authority_label || source.authority_name || this._humanizeTier(signals.domain_tier);
    grid.appendChild(this._detailCell("Authority", authorityLabel));

    const provenance = this._humanizeAuthoritySource(source.authority_source || signals.authority_source);
    const authorityConfidence = source.authority_confidence || signals.authority_confidence;
    grid.appendChild(this._detailCell("Verified via", authorityConfidence ? `${provenance} · ${authorityConfidence}` : provenance));

    if (source.date) {
      grid.appendChild(this._detailCell("Publication date", source.date));
    }

    if (source.publisher) {
      grid.appendChild(this._detailCell("Publisher", source.publisher));
    }

    const citedBy = signals.oa_cited_by_count;
    if (citedBy != null && citedBy > 0) {
      grid.appendChild(this._detailCell("Citations", citedBy.toLocaleString()));
    }

    section.appendChild(grid);
    detail.appendChild(section);
  },

  _appendLegacyDetail(detail, source, signals) {
    const grid = document.createElement("div");
    grid.className = "verity-detail-grid";

    const scoreFive = this._toFiveScale(this._primaryScore(source));
    grid.appendChild(this._detailCell("Score", scoreFive ? `${scoreFive} / 5.0` : '—'));
    grid.appendChild(this._createStatusCell(source));
    grid.appendChild(this._detailCell("Source Tier", this._humanizeTier(signals.domain_tier)));

    const relevanceScore = signals.relevance_score;
    const relevanceText = relevanceScore != null ? `${relevanceScore}% overlap` : 'Not assessed';
    grid.appendChild(this._detailCell("Relevance", relevanceText));
    grid.appendChild(this._detailCell("Author", this._authorDisplay(source)));
    grid.appendChild(this._detailCell("Publication", source.domain || ""));

    const authorityLabel = signals.authority_label || source.authority_name || this._humanizeTier(signals.domain_tier);
    grid.appendChild(this._detailCell("Authority", authorityLabel));
    const provenance = this._humanizeAuthoritySource(source.authority_source || signals.authority_source);
    const authorityConfidence = source.authority_confidence || signals.authority_confidence;
    grid.appendChild(this._detailCell("Verified Via", authorityConfidence ? `${provenance} · ${authorityConfidence}` : provenance));

    if (source.publisher) {
      grid.appendChild(this._detailCell("Publisher", source.publisher));
    }
    const citedBy = signals.oa_cited_by_count;
    if (citedBy != null && citedBy > 0) {
      grid.appendChild(this._detailCell("Citations", citedBy.toLocaleString()));
    }

    detail.appendChild(grid);

    const reason = source.reason || source.description || "";
    if (reason) {
      const p = document.createElement("p");
      p.className = "verity-card-reason";
      p.textContent = reason;
      detail.appendChild(p);
    }
  },

  _appendSourceEnrichment(detail, source) {
    if (source.topics && source.topics.length > 0) {
      const tagRow = document.createElement("div");
      tagRow.className = "verity-topic-tags";
      for (const topic of source.topics.slice(0, 4)) {
        const tag = document.createElement("span");
        tag.className = "verity-topic-tag";
        tag.textContent = topic;
        tagRow.appendChild(tag);
      }
      detail.appendChild(tagRow);
    }

    if (source.funders && source.funders.length > 0) {
      const funderRow = document.createElement("div");
      funderRow.className = "verity-funders-row";
      funderRow.textContent = "Funded by: " + source.funders.join(", ");
      detail.appendChild(funderRow);
    }
  },

  _createDetailSection(label) {
    const section = document.createElement("div");
    section.className = "verity-detail-section";

    const heading = document.createElement("div");
    heading.className = "verity-section-label";
    heading.textContent = label;

    section.appendChild(heading);
    return section;
  },

  _createStatusCell(source) {
    const statusCell = this._detailCell("Source status", "");
    const statusVal = statusCell.querySelector('.verity-detail-value');
    const indicator = document.createElement("span");
    indicator.className = "verity-live-indicator";
    const dot = document.createElement("span");
    dot.className = "verity-live-dot " + (source.live !== false ? "verity-live-dot--live" : "verity-live-dot--dead");
    const statusText = document.createTextNode(source.live !== false ? " Live" : " Unavailable");
    indicator.append(dot, statusText);
    statusVal.appendChild(indicator);
    if (source.live !== false) {
      statusVal.classList.add("verity-detail-value--green");
    } else {
      statusVal.classList.add("verity-detail-value--red");
    }
    return statusCell;
  },

  _factCell(label, value) {
    const cell = document.createElement("div");
    cell.className = "verity-fact-cell";

    const labelEl = document.createElement("div");
    labelEl.className = "verity-fact-label";
    labelEl.textContent = label;

    const valueEl = document.createElement("div");
    valueEl.className = "verity-fact-value";
    valueEl.textContent = value;

    cell.append(labelEl, valueEl);
    return cell;
  },

  _createContextSection(source) {
    const contextText = typeof source.context === "string" ? source.context : "";
    if (!contextText.trim()) {
      return null;
    }

    const section = document.createElement("div");
    section.className = "verity-context-section";
    section.addEventListener("click", (e) => e.stopPropagation());
    section.addEventListener("mousedown", (e) => e.stopPropagation());

    const button = document.createElement("button");
    button.type = "button";
    button.className = "verity-context-toggle";
    button.textContent = "Show exact context";
    button.setAttribute("aria-expanded", "false");

    const panel = document.createElement("div");
    panel.className = "verity-context-panel";
    panel.hidden = true;

    const label = document.createElement("div");
    label.className = "verity-context-label";
    label.textContent = "Exact context picked up";

    const body = document.createElement("pre");
    body.className = "verity-context-text";
    body.textContent = contextText;

    panel.append(label, body);

    button.addEventListener("click", (e) => {
      e.stopPropagation();
      const isOpen = !panel.hidden;
      panel.hidden = isOpen;
      button.textContent = isOpen ? "Show exact context" : "Hide exact context";
      button.setAttribute("aria-expanded", String(!isOpen));
    });

    section.append(button, panel);
    return section;
  },

  _detailCell(label, value) {
    const cell = document.createElement("div");
    cell.className = "verity-detail-cell";

    const labelEl = document.createElement("div");
    labelEl.className = "verity-detail-label";
    labelEl.textContent = label;

    const valueEl = document.createElement("div");
    valueEl.className = "verity-detail-value";
    if (typeof value === "string") {
      valueEl.textContent = value;
    }

    cell.append(labelEl, valueEl);
    return cell;
  },

  // --- Compact card (below "More") ---

  _createCompactCard(source) {
    const score100 = this._primaryScore(source);
    const scoreFive = this._toFiveScale(score100);
    const color = this._themeColor(source, score100);
    const signals = source.signals || {};

    const card = document.createElement("div");
    card.className = "verity-compact-card";
    card.style.setProperty('--verity-score-color', color);

    const circle = document.createElement("div");
    circle.className = "verity-score-circle verity-score-circle--sm";
    circle.style.setProperty('--verity-score-color', color);
    circle.textContent = scoreFive || '—';

    const domain = document.createElement("span");
    domain.className = "verity-compact-domain";
    domain.textContent = source.domain || "";

    const title = document.createElement("a");
    title.className = "verity-compact-title";
    title.textContent = source.title || "";
    if (source.url) {
      title.href = source.url;
      title.target = "_blank";
      title.rel = "noopener noreferrer";
    }

    const info = document.createElement("span");
    info.className = "verity-compact-info";

    const tier = document.createElement("span");
    tier.className = "verity-tier-badge";
    tier.textContent = this._humanizeTier(signals.domain_tier);

    const liveDot = document.createElement("span");
    liveDot.className = "verity-live-dot " + (source.live !== false ? "verity-live-dot--live" : "verity-live-dot--dead");

    info.append(tier, liveDot);
    card.append(circle, domain, title, info);

    return card;
  },

  // --- "More" divider ---

  _createMoreDivider() {
    const div = document.createElement("div");
    div.className = "verity-more-divider";
    div.textContent = "More";
    return div;
  },

  // --- Error / empty ---

  _renderError(err, panel, retryFn) {
    const errorDiv = document.createElement("div");
    errorDiv.className = "verity-error";

    const isContextDead =
      err.message?.includes("context invalidated") ||
      err.message?.includes("Extension context") ||
      err.message?.includes("Extension was reloaded");

    const msg = document.createElement("p");
    msg.textContent = isContextDead
      ? "Verity lost connection — please refresh the page to reconnect."
      : `Verity couldn't check sources: ${err.message}`;
    errorDiv.appendChild(msg);

    if (isContextDead) {
      const refreshBtn = document.createElement("button");
      refreshBtn.className = "verity-trigger-btn";
      refreshBtn.textContent = "Refresh page";
      refreshBtn.addEventListener("click", () => window.location.reload());
      errorDiv.appendChild(refreshBtn);
    } else {
      const retryBtn = document.createElement("button");
      retryBtn.className = "verity-trigger-btn";
      retryBtn.textContent = "Retry";
      retryBtn.addEventListener("click", retryFn);
      errorDiv.appendChild(retryBtn);
    }

    panel.appendChild(errorDiv);
  },

  _inferVerdict(source) {
    if (!source.live && source.live !== undefined) return "unverified";
    return "reliable";
  },

};

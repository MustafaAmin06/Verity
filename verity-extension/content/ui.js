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
    this._renderSkeleton(panel);

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
      const cacheKey = window.Verity.api.computeCacheKey(sources);
      const data = await window.Verity.api.fetchWithDedup(cacheKey, payload);
      btn.remove();
      this._clearElement(panel);
      panel.classList.remove("verity-loading");
      this._renderScorecard(data, panel);
    } catch (err) {
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

  _humanizeTier(tier) {
    const map = {
      academic_journal: 'Academic / Research',
      official_body: 'Official / Government',
      established_news: 'Established News',
      independent_blog: 'Independent / Blog',
      flagged: 'Flagged Source',
    };
    return map[tier] || 'Unknown';
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

    // Sort by composite_score descending
    const sorted = [...sources].sort((a, b) =>
      (b.composite_score || 0) - (a.composite_score || 0)
    );

    // Render all sources as full-size, expandable cards
    sorted.forEach((source) => {
      panel.appendChild(this._createCard(source));
    });

    // Further reading
    if (data.further_reading && data.further_reading.length > 0) {
      panel.appendChild(this._createFurtherReading(data.further_reading));
    }
  },

  // --- Full-size card ---

  _createCard(source) {
    const score100 = source.composite_score;
    const scoreFive = this._toFiveScale(score100);
    const color = this._scoreColor(score100);

    const card = document.createElement("div");
    card.className = "verity-card";
    card.style.setProperty('--verity-score-color', color);

    // Click to toggle expanded
    card.addEventListener("click", () => {
      card.classList.toggle("verity-card--expanded");
    });

    // Summary row: score circle + content
    const summary = document.createElement("div");
    summary.className = "verity-card-summary";

    const circle = document.createElement("div");
    circle.className = "verity-score-circle";
    circle.style.setProperty('--verity-score-color', color);
    circle.textContent = scoreFive || '—';

    const content = document.createElement("div");
    content.className = "verity-card-content";

    const domain = document.createElement("div");
    domain.className = "verity-card-domain";
    domain.textContent = source.domain || "";

    const title = document.createElement("a");
    title.className = "verity-card-title";
    title.textContent = source.title || source.label || source.url || "";
    if (source.url) {
      title.href = source.url;
      title.target = "_blank";
      title.rel = "noopener noreferrer";
      title.addEventListener("click", (e) => e.stopPropagation());
    }

    const date = document.createElement("div");
    date.className = "verity-card-date";
    date.textContent = source.date || "";

    content.append(domain, title, date);
    summary.append(circle, content);
    card.appendChild(summary);

    // Expandable detail
    card.appendChild(this._createDetail(source, scoreFive, color));

    return card;
  },

  // --- Expanded detail (2-column grid) ---

  _createDetail(source, scoreFive, color) {
    const detail = document.createElement("div");
    detail.className = "verity-card-detail";

    const signals = source.signals || {};

    const grid = document.createElement("div");
    grid.className = "verity-detail-grid";

    // Row 1: Score | URL Status
    grid.appendChild(this._detailCell("Score", scoreFive ? `${scoreFive} / 5.0` : '—'));

    const statusCell = this._detailCell("URL Status", "");
    const statusVal = statusCell.querySelector('.verity-detail-value');
    const indicator = document.createElement("span");
    indicator.className = "verity-live-indicator";
    const dot = document.createElement("span");
    dot.className = "verity-live-dot " + (source.live !== false ? "verity-live-dot--live" : "verity-live-dot--dead");
    const statusText = document.createTextNode(source.live !== false ? " Live" : " Dead");
    indicator.append(dot, statusText);
    statusVal.appendChild(indicator);
    if (source.live !== false) {
      statusVal.classList.add("verity-detail-value--green");
    } else {
      statusVal.classList.add("verity-detail-value--red");
    }
    grid.appendChild(statusCell);

    // Row 2: Source Tier | Relevance
    grid.appendChild(this._detailCell("Source Tier", this._humanizeTier(signals.domain_tier)));

    const relevanceScore = signals.relevance_score;
    const relevanceText = relevanceScore != null ? `${relevanceScore}% overlap` : 'Not assessed';
    grid.appendChild(this._detailCell("Relevance", relevanceText));

    // Row 3: Author | Publication
    grid.appendChild(this._detailCell("Author", source.author || "Unknown"));
    grid.appendChild(this._detailCell("Publication", source.domain || ""));

    detail.appendChild(grid);

    // Summary paragraph
    const reason = source.reason || source.description || "";
    if (reason) {
      const p = document.createElement("p");
      p.className = "verity-card-reason";
      p.textContent = reason;
      detail.appendChild(p);
    }

    return detail;
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
    const score100 = source.composite_score;
    const scoreFive = this._toFiveScale(score100);
    const color = this._scoreColor(score100);
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

  // --- Further reading ---

  _createFurtherReading(items) {
    const section = document.createElement("div");
    section.className = "verity-further-reading";

    const header = document.createElement("div");
    header.className = "verity-fr-header";
    header.textContent = "Further reading";
    section.appendChild(header);

    items.forEach((item) => {
      const card = document.createElement("div");
      card.className = "verity-fr-card";

      const domain = document.createElement("span");
      domain.className = "verity-fr-domain";
      domain.textContent = item.domain || "";

      const title = document.createElement("span");
      title.className = "verity-fr-title";
      title.textContent = item.title || "";

      const date = document.createElement("span");
      date.className = "verity-fr-date";
      date.textContent = item.date || "";

      const link = document.createElement("a");
      link.className = "verity-fr-link";
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = "View";

      card.append(domain, title, date, link);
      section.appendChild(card);
    });

    const disclaimer = document.createElement("div");
    disclaimer.className = "verity-disclaimer";
    disclaimer.textContent =
      "These suggestions are based on topic detection, not personalised search. Always verify before citing.";
    section.appendChild(disclaimer);

    return section;
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

window.Verity = window.Verity || {};

window.Verity.ui = {
  /**
   * Inject the "Check sources" button below a response element.
   */
  injectButton(responseEl, sources, platformConfig) {
    // Guard against duplicate injection
    const container = responseEl.closest("[data-message-id]") || responseEl.parentElement;
    if (container.hasAttribute("data-verity-processed")) return;
    container.setAttribute("data-verity-processed", "true");

    const btn = document.createElement("button");
    btn.className = "verity-trigger-btn";
    btn.textContent = "Check sources with Verity";
    btn.addEventListener("click", () => {
      this._handleCheck(responseEl, sources, btn, platformConfig, container);
    });

    // Insert after the response container
    container.after(btn);
  },

  async _handleCheck(responseEl, sources, btn, platformConfig, container) {
    btn.textContent = "Checking...";
    btn.disabled = true;

    // Create panel container and show skeleton
    const panel = document.createElement("div");
    panel.className = "verity-panel";
    btn.after(panel);
    this._renderSkeleton(panel);

    // Build payload
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
      const data = await window.Verity.api.checkSources(payload);
      btn.remove();
      panel.innerHTML = "";
      panel.classList.remove("verity-loading");
      this._renderScorecard(data, panel);
    } catch (err) {
      btn.remove();
      panel.innerHTML = "";
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

  _renderScorecard(data, panel) {
    // Handle both spec format (data.sources) and extractor format (data.scraped_sources)
    const sources = data.sources || data.scraped_sources || [];

    if (sources.length === 0) {
      const empty = document.createElement("p");
      empty.className = "verity-empty";
      empty.textContent = "No sources could be analyzed.";
      panel.appendChild(empty);
      return;
    }

    sources.forEach((source) => {
      const card = this._createCard(source);
      panel.appendChild(card);
    });

    // Further reading
    if (data.further_reading && data.further_reading.length > 0) {
      panel.appendChild(this._createFurtherReading(data.further_reading));
    }
  },

  _createCard(source) {
    const verdict = source.verdict || this._inferVerdict(source);
    const verdictLabel = source.verdict_label || this._verdictLabel(verdict);
    const color = source.color || this._verdictColor(verdict);

    const card = document.createElement("div");
    card.className = `verity-card`;
    card.setAttribute("data-verdict", verdict);

    // Compact row (always visible)
    const summary = document.createElement("div");
    summary.className = "verity-card-summary";

    const dot = document.createElement("span");
    dot.className = "verity-dot";

    const domain = document.createElement("span");
    domain.className = "verity-domain";
    domain.textContent = source.domain || "";

    const date = document.createElement("span");
    date.className = "verity-date";
    date.textContent = source.date || "";

    const label = document.createElement("span");
    label.className = "verity-verdict-label";
    label.textContent = verdictLabel;

    summary.append(dot, domain, date, label);
    card.appendChild(summary);

    // Hover detail panel (hidden by default)
    const hoverDetail = document.createElement("div");
    hoverDetail.className = "verity-card-hover-detail";

    if (source.title) {
      const titleP = document.createElement("p");
      titleP.className = "verity-summary";
      titleP.textContent = source.title;
      hoverDetail.appendChild(titleP);
    }

    if (source.reason) {
      const reasonP = document.createElement("p");
      reasonP.className = "verity-reason";
      reasonP.textContent = source.reason;
      hoverDetail.appendChild(reasonP);
    }

    if (source.implication) {
      const impP = document.createElement("p");
      impP.className = "verity-implication";
      impP.textContent = source.implication;
      hoverDetail.appendChild(impP);
    }

    if (source.flags && source.flags.length > 0) {
      const flagsDiv = document.createElement("div");
      flagsDiv.className = "verity-flags";
      source.flags.forEach((flag) => {
        const span = document.createElement("span");
        span.className = "verity-flag";
        span.textContent = flag;
        flagsDiv.appendChild(span);
      });
      hoverDetail.appendChild(flagsDiv);
    }

    const expandBtn = document.createElement("button");
    expandBtn.className = "verity-expand-btn";
    expandBtn.textContent = "Full details";
    expandBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      card.classList.toggle("verity-card--expanded");
    });
    hoverDetail.appendChild(expandBtn);

    card.appendChild(hoverDetail);

    // Full detail panel (hidden by default)
    const fullDetail = this._createFullDetail(source);
    card.appendChild(fullDetail);

    // Hover behavior with 300ms delay
    let hoverTimeout = null;
    card.addEventListener("mouseenter", () => {
      hoverTimeout = setTimeout(() => {
        card.classList.add("verity-card--hover");
      }, VERITY_CONFIG.hoverDelayMs);
    });
    card.addEventListener("mouseleave", () => {
      clearTimeout(hoverTimeout);
      card.classList.remove("verity-card--hover");
    });

    return card;
  },

  _createFullDetail(source) {
    const container = document.createElement("div");
    container.className = "verity-card-full-detail";

    const signals = source.signals || {};

    const rows = [
      { label: "Domain", value: signals.domain_tier || source.domain || "", score: signals.domain_score },
      { label: "Relevance", value: "", score: signals.relevance_score },
      {
        label: "Claim alignment",
        value: signals.claim_aligned === true ? "Supported"
             : signals.claim_aligned === false ? "Unsupported"
             : "Unconfirmed",
        score: signals.alignment_score,
      },
      { label: "Publication date", value: source.date || "Not found", score: signals.recency_score },
      { label: "Author", value: source.author || "Not listed", score: signals.author_score },
      { label: "Peer reviewed", value: signals.is_peer_reviewed ? "Yes" : "No" },
      { label: "Paywalled", value: source.paywalled ? "Yes" : "No" },
    ];

    rows.forEach((row) => {
      const rowDiv = document.createElement("div");
      rowDiv.className = "verity-signal-row";

      const labelSpan = document.createElement("span");
      labelSpan.className = "verity-signal-label";
      labelSpan.textContent = row.label;

      const valueSpan = document.createElement("span");
      valueSpan.className = "verity-signal-value";
      valueSpan.textContent = row.value;

      rowDiv.append(labelSpan, valueSpan);

      if (row.score !== undefined && row.score !== null) {
        const scoreSpan = document.createElement("span");
        scoreSpan.className = "verity-signal-score";
        scoreSpan.textContent = `${row.score}/100`;
        rowDiv.appendChild(scoreSpan);
      }

      container.appendChild(rowDiv);
    });

    // View source link
    const link = document.createElement("a");
    link.className = "verity-source-link";
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = "View source";
    container.appendChild(link);

    // Methodology note
    const note = document.createElement("p");
    note.className = "verity-methodology-note";
    note.textContent =
      "Scored by Verity using domain registry, publication metadata, keyword relevance, and AI-assisted claim alignment.";
    container.appendChild(note);

    return container;
  },

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

      const dot = document.createElement("span");
      dot.className = "verity-dot dot-green";

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

      card.append(dot, domain, title, date, link);
      section.appendChild(card);
    });

    const disclaimer = document.createElement("div");
    disclaimer.className = "verity-disclaimer";
    disclaimer.textContent =
      "These suggestions are based on topic detection, not personalised search. Always verify before citing.";
    section.appendChild(disclaimer);

    return section;
  },

  _renderError(err, panel, retryFn) {
    const errorDiv = document.createElement("div");
    errorDiv.className = "verity-error";

    const msg = document.createElement("p");
    msg.textContent = `Verity couldn't check sources: ${err.message}`;
    errorDiv.appendChild(msg);

    const retryBtn = document.createElement("button");
    retryBtn.className = "verity-trigger-btn";
    retryBtn.textContent = "Retry";
    retryBtn.addEventListener("click", retryFn);
    errorDiv.appendChild(retryBtn);

    panel.appendChild(errorDiv);
  },

  // Fallback verdict inference for extractor-only responses (no scorer)
  _inferVerdict(source) {
    if (!source.live && source.live !== undefined) return "unverified";
    return "reliable";
  },

  _verdictLabel(verdict) {
    const map = {
      reliable: "Looks reliable",
      caution: "Treat with caution",
      skeptical: "Be skeptical",
      unverified: "Couldn't verify",
    };
    return map[verdict] || verdict;
  },

  _verdictColor(verdict) {
    const map = {
      reliable: "green",
      caution: "amber",
      skeptical: "red",
      unverified: "gray",
    };
    return map[verdict] || "gray";
  },
};

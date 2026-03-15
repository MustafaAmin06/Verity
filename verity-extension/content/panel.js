window.Verity = window.Verity || {};

window.Verity._responseCache = new Map();
window.Verity._pendingRequests = new Map();

window.Verity.panel = {
  _observer: null,
  _scanTimer: null,
  _processedPanels: new WeakSet(),

  // Critical CSS embedded inline — renders immediately without waiting for fetch
  _CRITICAL_CSS: `
    :host { display: block; width: 100%; height: 100%; overflow-y: auto; }
    *, *::before, *::after { box-sizing: border-box; }
    .verity-panel-root {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      color: #e0e0e0;
      line-height: 1.4;
      padding: 16px;
      background: transparent;
    }
    .verity-panel-header { margin-bottom: 16px; }
    .verity-panel-header-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 4px;
    }
    .verity-panel-title { font-size: 16px; font-weight: 700; color: #e0e0e0; }
    .verity-panel-subtitle {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 12px;
      color: #999;
    }
    .verity-panel-legend { display: flex; gap: 12px; font-size: 11px; color: #999; }
    .verity-legend-item { display: flex; align-items: center; gap: 4px; }
    .verity-legend-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; flex-shrink: 0; }
    .verity-legend-dot--reliable { background: #4ade80; }
    .verity-legend-dot--unreliable { background: #f87171; }
    .verity-panel-body { min-height: 60px; }
    .verity-panel-body.verity-loading { display: flex; flex-direction: column; gap: 8px; }
    .verity-skeleton {
      height: 64px;
      border-radius: 10px;
      background: rgba(255,255,255,0.12);
      animation: verity-pulse 1.2s ease-in-out infinite;
    }
    .verity-skeleton:nth-child(2) { animation-delay: 0.15s; }
    .verity-skeleton:nth-child(3) { animation-delay: 0.3s; }
    @keyframes verity-pulse { 0%,100%{opacity:0.5} 50%{opacity:0.9} }
    .verity-loading-label {
      color: #999;
      font-size: 12px;
      text-align: center;
      margin: 8px 0 0;
    }
  `,

  init() {
    console.log("[Verity] Panel takeover observer started");

    // Use debounced full-page scan instead of per-node checking.
    // This is more robust — React may add the panel in multiple mutations.
    this._observer = new MutationObserver(() => {
      clearTimeout(this._scanTimer);
      this._scanTimer = setTimeout(() => this._scanForPanel(), 300);
    });

    this._observer.observe(document.body, {
      childList: true,
      subtree: true,
    });

    // Expose manual trigger for debugging
    window.Verity.debugPanel = () => {
      console.log('[Verity] Manual panel scan triggered');
      this._scanForPanel();
    };
  },

  /**
   * Scan the entire page for unprocessed sources panels.
   * Safer than checking individual addedNodes since React may build
   * the panel across multiple mutation batches.
   */
  _scanForPanel() {
    // Find all external anchor groups not inside assistant messages
    const allAnchors = document.querySelectorAll('a[href^="http"]');
    const candidates = new Set();

    for (const a of allAnchors) {
      try {
        const host = new URL(a.href).hostname;
        if (host.includes('openai.com') || host.includes('chatgpt.com')) continue;
      } catch {
        continue;
      }

      // Skip anchors inside assistant/user messages
      if (a.closest('[data-message-author-role]')) continue;
      // Skip anchors inside our own shadow hosts
      if (a.closest('[data-verity-host]')) continue;

      // Walk up to find a panel-like container
      const container = this._findPanelContainer(a);
      if (container && !this._processedPanels.has(container)) {
        candidates.add(container);
      }
    }

    for (const panel of candidates) {
      // Verify: must have 2+ external anchors to be a real sources panel
      const extAnchors = this._countExternalAnchors(panel);
      if (extAnchors >= 2) {
        console.log('[Verity] Sources panel found via scan:', panel.tagName, 'with', extAnchors, 'external links');
        this._processedPanels.add(panel);
        this._handlePanelAppearance(panel);
        return; // Process one panel at a time
      }
    }
  },

  /**
   * Walk up from an anchor to find the likely panel container.
   * Looks for a reasonably sized container that's not the whole page.
   */
  _findPanelContainer(anchor) {
    let el = anchor.parentElement;
    while (el && el !== document.body) {
      // A panel container is typically a large-ish block element
      // that is NOT the main chat thread
      if (el.getAttribute('data-message-author-role')) return null;
      if (el.getAttribute('role') === 'main') return null;

      // Check if this looks like a panel (has multiple external anchors)
      const extCount = this._countExternalAnchors(el);
      if (extCount >= 2) {
        // Keep walking up if parent also qualifies and is not too big
        const parent = el.parentElement;
        if (parent && parent !== document.body
            && !parent.getAttribute('role')
            && this._countExternalAnchors(parent) === extCount) {
          el = parent;
          continue;
        }
        return el;
      }
      el = el.parentElement;
    }
    return null;
  },

  _countExternalAnchors(node) {
    const anchors = node.querySelectorAll('a[href^="http"]');
    let count = 0;
    for (const a of anchors) {
      try {
        const host = new URL(a.href).hostname;
        if (!host.includes('openai.com') && !host.includes('chatgpt.com')) count++;
      } catch { /* skip */ }
    }
    return count;
  },

  _extractFromPanel(panelNode) {
    const anchors = panelNode.querySelectorAll('a[href^="http"]');
    return [...anchors]
      .filter((a) => {
        try {
          const host = new URL(a.href).hostname;
          return !host.includes('openai.com') && !host.includes('chatgpt.com');
        } catch {
          return false;
        }
      })
      .map((a) => ({
        url: a.href,
        label: a.textContent.trim() || window.Verity.extractor._domainFromUrl(a.href),
        context: a.closest('div')?.textContent?.trim()?.slice(0, 400) || 'Source from ChatGPT panel',
      }));
  },

  _computeCacheKey(sources) {
    const joined = sources.map((s) => s.url).sort().join('|');
    let hash = 5381;
    for (let i = 0; i < joined.length; i++) {
      hash = ((hash << 5) + hash) + joined.charCodeAt(i);
      hash = hash & hash;
    }
    return 'verity_' + Math.abs(hash).toString(36);
  },

  /** Remove all child nodes from an element (innerHTML-free, Trusted Types safe) */
  _clearChildren(el) {
    while (el.firstChild) el.firstChild.remove();
  },

  // Inject the full cards.css non-blocking (called after structure is built)
  _injectFullCSS(shadowRoot) {
    // If the extension context is dead, skip — critical inline CSS still renders
    if (!window.Verity.api.isContextAlive()) {
      console.warn('[Verity] Skipping full CSS load — extension context invalidated');
      return;
    }
    try {
      const cssUrl = chrome.runtime.getURL('styles/cards.css');
      fetch(cssUrl)
        .then((r) => r.text())
        .then((cssText) => {
          const style = document.createElement('style');
          style.textContent = cssText;
          shadowRoot.appendChild(style);
        })
        .catch((e) => console.warn('[Verity] Could not load full CSS:', e));
    } catch (e) {
      console.warn('[Verity] CSS URL resolution failed:', e);
    }
  },

  _buildShadowStructure(shadowRoot, sourceCount) {
    // 1. Inject critical CSS synchronously — renders immediately, no async wait
    const criticalStyle = document.createElement('style');
    criticalStyle.textContent = this._CRITICAL_CSS;
    shadowRoot.appendChild(criticalStyle);

    // 2. Build DOM structure
    const root = document.createElement('div');
    root.className = 'verity-panel-root';

    const header = document.createElement('div');
    header.className = 'verity-panel-header';

    const headerTop = document.createElement('div');
    headerTop.className = 'verity-panel-header-top';

    const title = document.createElement('span');
    title.className = 'verity-panel-title';
    title.textContent = 'Cited Sources';

    headerTop.appendChild(title);

    const subtitle = document.createElement('div');
    subtitle.className = 'verity-panel-subtitle';

    const stats = document.createElement('span');
    stats.className = 'verity-panel-stats';
    stats.textContent = `${sourceCount} source${sourceCount !== 1 ? 's' : ''}`;

    const legend = document.createElement('span');
    legend.className = 'verity-panel-legend';

    // Build legend items via createElement (Trusted Types safe — no innerHTML)
    const reliableItem = document.createElement('span');
    reliableItem.className = 'verity-legend-item';
    const reliableDot = document.createElement('span');
    reliableDot.className = 'verity-legend-dot verity-legend-dot--reliable';
    reliableItem.append(reliableDot, ' Reliable');

    const unreliableItem = document.createElement('span');
    unreliableItem.className = 'verity-legend-item';
    const unreliableDot = document.createElement('span');
    unreliableDot.className = 'verity-legend-dot verity-legend-dot--unreliable';
    unreliableItem.append(unreliableDot, ' Unreliable');

    legend.append(reliableItem, unreliableItem);
    subtitle.append(stats, legend);
    header.append(headerTop, subtitle);

    const body = document.createElement('div');
    body.className = 'verity-panel-body';

    root.append(header, body);
    shadowRoot.appendChild(root);

    // 3. Load full CSS non-blocking (upgrades styling once fetched)
    this._injectFullCSS(shadowRoot);
  },

  async _fetchWithDedup(cacheKey, payload) {
    if (window.Verity._pendingRequests.has(cacheKey)) {
      return window.Verity._pendingRequests.get(cacheKey);
    }
    const promise = window.Verity.api.checkSources(payload);
    window.Verity._pendingRequests.set(cacheKey, promise);
    try {
      const data = await promise;
      window.Verity._responseCache.set(cacheKey, {
        timestamp: Date.now(),
        data,
      });
      return data;
    } finally {
      window.Verity._pendingRequests.delete(cacheKey);
    }
  },

  async _handlePanelAppearance(panelNode) {
    console.log('[Verity] Panel detected, taking over...', panelNode.tagName, panelNode.className);

    // Bail early if the extension context is dead — no point attempting chrome.runtime calls
    if (!window.Verity.api.isContextAlive()) {
      console.warn('[Verity] Extension context invalidated, skipping panel takeover');
      return;
    }

    // Declare shadow before try/catch so error handler can reference it
    let shadow = null;

    try {
      // Step 1: Extract sources from native panel before touching DOM
      const panelSources = this._extractFromPanel(panelNode);
      console.log('[Verity] Panel sources:', panelSources.length);

      // Step 2: Merge with response-extracted sources
      const latestSources = window.Verity._latestSources || [];
      const merged = window.Verity.extractor._deduplicate([...panelSources, ...latestSources]);
      console.log('[Verity] Merged sources:', merged.length);

      if (merged.length === 0) {
        console.log('[Verity] No sources to score, skipping takeover');
        return;
      }

      // Step 3: Check cache
      const cacheKey = this._computeCacheKey(merged);
      const cached = window.Verity._responseCache.get(cacheKey);
      const CACHE_TTL = 30 * 60 * 1000;

      // Step 4: Create a shadow DOM host div inside panelNode.
      // Using our own <div> guarantees shadow DOM support (avoids attachShadow
      // failing on arbitrary elements like <ul>, <a>, <li>, etc.).
      let host = panelNode.querySelector('[data-verity-host]');

      if (!host) {
        // Extract sources FIRST, then clear the native panel content
        this._clearChildren(panelNode);

        host = document.createElement('div');
        host.setAttribute('data-verity-host', 'true');
        // Ensure host fills the panel area
        host.style.cssText = 'display:block;width:100%;height:100%;overflow-y:auto;';
        panelNode.appendChild(host);

        shadow = host.attachShadow({ mode: 'open' });
        console.log('[Verity] Shadow DOM created on new host div');

        this._buildShadowStructure(shadow, merged.length);

        // Defend against React restoring native children — keep only our host
        const cleanupObserver = new MutationObserver(() => {
          for (const child of [...panelNode.childNodes]) {
            if (child !== host) {
              child.remove();
            }
          }
        });
        cleanupObserver.observe(panelNode, { childList: true });
      } else {
        shadow = host.shadowRoot;
        console.log('[Verity] Reusing existing shadow host');
        // Update stats on reopen
        const stats = shadow.querySelector('.verity-panel-stats');
        if (stats) stats.textContent = `${merged.length} source${merged.length !== 1 ? 's' : ''}`;
      }

      // Step 5: Cache hit — render immediately
      if (cached && Date.now() - cached.timestamp < CACHE_TTL) {
        console.log('[Verity] Cache hit, rendering instantly');
        window.Verity.ui.renderInShadow(shadow, cached.data);
        return;
      }

      // Step 6: Show skeleton and fire API call
      window.Verity.ui.renderSkeletonInShadow(shadow);

      const platformConfig = window.Verity._latestPlatformConfig;
      const responseEl = window.Verity._latestResponseEl;

      const prompt = platformConfig
        ? window.Verity.extractor.extractPrompt(platformConfig.selectors)
        : '';
      const fullResponse = responseEl
        ? window.Verity.extractor.extractResponse(responseEl, VERITY_CONFIG.maxBodyTextChars)
        : '';

      const payload = {
        sources: merged,
        original_prompt: prompt || fullResponse.slice(0, 200),
        full_ai_response: fullResponse,
      };

      const data = await this._fetchWithDedup(cacheKey, payload);
      console.log('[Verity] API response received, rendering cards');
      window.Verity.ui.renderInShadow(shadow, data);

    } catch (err) {
      console.error('[Verity] Panel takeover error:', err);
      if (shadow) {
        window.Verity.ui.renderErrorInShadow(shadow, err, () => {
          // Allow re-processing this panel
          this._processedPanels.delete(panelNode);
          this._handlePanelAppearance(panelNode);
        });
      }
    }
  },
};

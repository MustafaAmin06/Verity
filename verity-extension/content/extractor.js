window.Verity = window.Verity || {};

// --- Intercepted citation cache (filled by MAIN-world interceptor.js) ---
window.Verity._interceptedCitations = null;
window.Verity._interceptedTimestamp = 0;

document.addEventListener("verity-citations", (e) => {
  if (e.detail && Array.isArray(e.detail.citations)) {
    window.Verity._interceptedCitations = e.detail.citations;
    window.Verity._interceptedTimestamp = e.detail.timestamp || Date.now();
  }
});

window.Verity.extractor = {
  /**
   * Extract all sources (url, label, context) from a response element.
   * Uses intercepted API data as primary source, DOM extraction as fallback.
   */
  extractSources(element) {
    const intercepted = this._getInterceptedCitations(element);

    const pills = this._extractFromCitationPills(element);
    const anchors = this._extractFromAnchors(element);
    const bare = this._extractFromText(element, anchors);
    const footnotes = this._extractFromFootnotes(element);
    const citationEls = this._extractFromCitationElements(element);

    const domSources = [...pills, ...anchors, ...bare, ...footnotes, ...citationEls];
    const all = intercepted.length > 0
      ? [...intercepted, ...domSources]
      : domSources;

    return this._deduplicate(all);
  },

  _extractFromAnchors(element) {
    const sources = [];
    const links = element.querySelectorAll('a[href]');
    for (const a of links) {
      let url = a.href;
      if (!url || !url.startsWith("http")) continue;
      const label = a.innerText.trim() || this._domainFromUrl(url);
      const context = this._getContextForNode(a, element);
      sources.push({ url, label, context });
    }
    return sources;
  },

  _extractFromText(element, existingAnchors) {
    const existingUrls = new Set(existingAnchors.map((s) => s.url));
    const fullText = element.innerText || "";
    const urlRegex = /https?:\/\/[^\s<>"')\]]+/g;
    const sources = [];
    let match;
    while ((match = urlRegex.exec(fullText)) !== null) {
      let url = match[0].replace(/[.,;:!?)\]>]+$/, "");
      if (existingUrls.has(url)) continue;
      const label = this._domainFromUrl(url);
      const context = this._getContextFromPosition(fullText, match.index, url);
      sources.push({ url, label, context });
      existingUrls.add(url);
    }
    return sources;
  },

  _getContextForNode(node, responseElement) {
    // Walk up to find a containing paragraph or list item
    let container = node.closest("p, li, div");
    if (container && container !== responseElement) {
      let text = container.innerText.trim();
      if (text.length >= 30 && text.length <= 400) return text;
      if (text.length > 400) return text.slice(0, 400);
    }
    // Fallback: get text around the link position in full response
    const fullText = responseElement.innerText || "";
    const linkText = node.innerText.trim();
    const pos = fullText.indexOf(linkText);
    if (pos >= 0) {
      return this._getContextFromPosition(fullText, pos, "");
    }
    // Last resort
    return node.innerText.trim() || "Source citation";
  },

  _getContextFromPosition(fullText, position, urlToRemove) {
    const start = Math.max(0, position - 150);
    const end = Math.min(fullText.length, position + 150);
    let context = fullText.slice(start, end).trim();
    if (urlToRemove) {
      context = context.replace(urlToRemove, "").trim();
    }
    // Clean up: try to start and end at sentence boundaries
    const firstDot = context.indexOf(". ");
    if (firstDot > 0 && firstDot < 30 && start > 0) {
      context = context.slice(firstDot + 2);
    }
    if (context.length > 400) context = context.slice(0, 400);
    if (context.length < 30) context = fullText.slice(start, Math.min(fullText.length, position + 300)).trim();
    if (context.length < 30) context = "Source citation from AI response";
    return context;
  },

  _domainFromUrl(url) {
    try {
      const hostname = new URL(url).hostname.replace(/^www\./, "");
      return hostname;
    } catch {
      return url.slice(0, 40);
    }
  },

  // --- DOM fallback: ChatGPT citation pills ---

  _extractFromCitationPills(element) {
    const sources = [];
    // ChatGPT renders citation pills with data-testid="webpage-citation-pill"
    // Each pill contains a single <a> with the visible source URL.
    // For grouped citations ("+1"), only the first URL is in the DOM.
    const pills = element.querySelectorAll('[data-testid="webpage-citation-pill"] a[href]');
    for (const a of pills) {
      const url = a.href || a.getAttribute("alt") || "";
      if (!url || !url.startsWith("http")) continue;
      // Strip utm_source=chatgpt.com for cleaner URLs
      let cleanUrl = url;
      try {
        const u = new URL(url);
        u.searchParams.delete("utm_source");
        cleanUrl = u.toString();
      } catch {}
      const label = a.innerText.trim() || this._domainFromUrl(cleanUrl);
      const context = this._getContextForNode(a, element);
      sources.push({ url: cleanUrl, label, context });
    }
    return sources;
  },

  // --- Intercepted API citation helpers ---

  _getInterceptedCitations(element) {
    const data = window.Verity._interceptedCitations;
    const ts = window.Verity._interceptedTimestamp;
    // Only use if data exists and is recent (< 15 seconds old)
    if (!data || !Array.isArray(data) || data.length === 0) return [];
    if (Date.now() - ts > 15000) return [];

    const fullText = element.innerText || "";
    return data.map((c) => ({
      url: c.url,
      label: c.title || c.domain || this._domainFromUrl(c.url),
      context: this._findContextForUrl(fullText, c.url, c.domain),
    }));
  },

  _findContextForUrl(fullText, url, domain) {
    // Try to find the URL or domain in the response text for context
    const searchTerms = [url, domain].filter(Boolean);
    for (const term of searchTerms) {
      const pos = fullText.indexOf(term);
      if (pos >= 0) {
        return this._getContextFromPosition(fullText, pos, "");
      }
    }
    // Fallback: use the beginning of the response
    if (fullText.length >= 30) {
      return fullText.slice(0, 400).trim();
    }
    return "Source citation from AI response";
  },

  // --- DOM fallback: footnote lists ---

  _extractFromFootnotes(element) {
    const sources = [];
    // ChatGPT sometimes renders a numbered source list at the end
    const orderedLists = element.querySelectorAll("ol");
    for (const ol of orderedLists) {
      const items = ol.querySelectorAll("li");
      for (const li of items) {
        const links = li.querySelectorAll("a[href]");
        for (const a of links) {
          const url = a.href;
          if (!url || !url.startsWith("http")) continue;
          const label = a.innerText.trim() || this._domainFromUrl(url);
          const context = li.innerText.trim().slice(0, 400) || "Source citation";
          sources.push({ url, label, context });
        }
      }
    }
    return sources;
  },

  // --- DOM fallback: citation elements with data attributes ---

  _extractFromCitationElements(element) {
    const sources = [];

    // Check elements with data-href or data-url attributes
    const dataAttrSelectors = "[data-href], [data-url]";
    const elements = element.querySelectorAll(dataAttrSelectors);
    for (const el of elements) {
      const url = el.getAttribute("data-href") || el.getAttribute("data-url");
      if (!url || !url.startsWith("http")) continue;
      const label = el.innerText.trim() || this._domainFromUrl(url);
      const context = this._getContextForNode(el, element);
      sources.push({ url, label, context });
    }

    // Check preview card selectors from config
    if (typeof VERITY_CONFIG !== "undefined" && VERITY_CONFIG.previewCardSelectors) {
      for (const selector of VERITY_CONFIG.previewCardSelectors) {
        try {
          const cards = element.querySelectorAll(selector);
          for (const card of cards) {
            const a = card.querySelector("a[href]");
            if (!a) continue;
            const url = a.href;
            if (!url || !url.startsWith("http")) continue;
            const label = a.innerText.trim() || card.innerText.trim().slice(0, 80) || this._domainFromUrl(url);
            const context = card.innerText.trim().slice(0, 400) || "Source citation";
            sources.push({ url, label, context });
          }
        } catch {
          // Invalid selector — skip
        }
      }
    }

    return sources;
  },

  _deduplicate(sources) {
    const seen = new Set();
    return sources.filter((s) => {
      if (seen.has(s.url)) return false;
      seen.add(s.url);
      return true;
    });
  },

  /**
   * Get the user's most recent prompt.
   */
  extractPrompt(selectors) {
    const all = document.querySelectorAll(selectors.userMessage);
    const latest = all[all.length - 1];
    if (latest) return latest.innerText.trim();
    return "";
  },

  /**
   * Get the full AI response text, capped at maxChars.
   */
  extractResponse(element, maxChars) {
    return (element.innerText || "").trim().slice(0, maxChars || 8000);
  },
};

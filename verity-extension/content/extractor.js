window.Verity = window.Verity || {};

window.Verity.extractor = {
  /**
   * Extract all sources (url, label, context) from a response element.
   */
  extractSources(element) {
    const anchors = this._extractFromAnchors(element);
    const bare = this._extractFromText(element, anchors);
    const all = [...anchors, ...bare];
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

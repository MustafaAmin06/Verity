/**
 * Verity — ChatGPT API response interceptor.
 *
 * Runs in the MAIN world (page JS context) so it can monkey-patch
 * window.fetch and read the streaming conversation response.
 *
 * ChatGPT uses two endpoints:
 *   - POST /backend-api/f/conversation  → initial SSE stream
 *   - GET  /ces/v1/t                    → chunked stream continuation
 *
 * Both return SSE with `event: delta` / `data: {json}` lines.
 * Delta format: {"p":"","o":"add","v":{"message":{...}}}
 *
 * Dispatches a CustomEvent("verity-citations") on `document` with
 * the extracted citation array whenever citation data is found.
 */

(function () {
  "use strict";

  const VERITY_SHARED = globalThis.VerityShared;

  if (window.__VERITY_FETCH_PATCHED__) {
    return;
  }
  window.__VERITY_FETCH_PATCHED__ = true;

  // Accumulated citations across all chunks for the current response
  let sessionCitations = [];
  let sessionSeenUrls = new Set();
  let sessionTimer = null;
  let currentSessionId = 0;

  function getRequestUrl(resource) {
    try {
      return typeof resource === "string"
        ? resource
        : resource instanceof Request
          ? resource.url
          : "";
    } catch {
      return "";
    }
  }

  function getRequestMethod(resource, init) {
    const explicit = init && init.method ? init.method : null;
    if (explicit) return explicit.toUpperCase();
    try {
      if (resource instanceof Request && resource.method) {
        return resource.method.toUpperCase();
      }
    } catch {}
    return "GET";
  }

  function parseRequestPath(resource) {
    try {
      const url = new URL(getRequestUrl(resource), window.location.origin);
      return url.pathname;
    } catch {
      return "";
    }
  }

  function isConversationRequest(resource) {
    try {
      const path = parseRequestPath(resource);
      if (!path) return false;
      return (
        path.includes("/conversation") ||
        path.startsWith("/ces/v1/")
      );
    } catch {
      return false;
    }
  }

  function isConversationStart(resource, init) {
    return parseRequestPath(resource).includes("/conversation") && getRequestMethod(resource, init) === "POST";
  }

  function beginSession() {
    currentSessionId += 1;
    resetSession();
    document.dispatchEvent(
      new CustomEvent(VERITY_SHARED.CUSTOM_EVENTS.GENERATION_START, {
        detail: {
          sessionId: currentSessionId,
          timestamp: Date.now(),
        },
      })
    );
    return currentSessionId;
  }

  function ensureSession() {
    if (!currentSessionId) {
      return beginSession();
    }
    return currentSessionId;
  }

  /**
   * Walk a parsed message object and collect every citation entry.
   * ChatGPT nests citations in several possible locations — we check
   * all known paths so we survive minor schema shuffles.
   */
  function extractCitationsFromMessage(msg) {
    const citations = [];

    function collect(arr) {
      if (!Array.isArray(arr)) return;
      for (const c of arr) {
        if (!c || typeof c !== "object") continue;
        const url =
          c.url ||
          c.href ||
          (c.metadata && c.metadata.url) ||
          (c.extra && c.extra.cited_message_url);
        if (!url || typeof url !== "string" || !url.startsWith("http")) continue;
        citations.push({
          url,
          title: c.title || c.name || "",
          domain: c.domain || c.hostname || "",
        });
      }
    }

    // Check all known and plausible locations
    if (msg.metadata) {
      collect(msg.metadata.citations);
      collect(msg.metadata.content_references);
      collect(msg.metadata._citations);
      collect(msg.metadata.webpage_citations);
      // Walk any array-valued metadata field that contains url objects
      for (const val of Object.values(msg.metadata)) {
        if (Array.isArray(val) && val.length > 0 && val[0] && typeof val[0] === "object" && (val[0].url || val[0].href)) {
          collect(val);
        }
      }
    }
    if (msg.citations) collect(msg.citations);
    if (msg.content) {
      if (msg.content.parts) {
        for (const part of msg.content.parts) {
          if (part && typeof part === "object") {
            collect(part.citations);
            collect(part.content_references);
            // Walk any array-valued part field
            for (const val of Object.values(part)) {
              if (Array.isArray(val) && val.length > 0 && val[0] && typeof val[0] === "object" && (val[0].url || val[0].href)) {
                collect(val);
              }
            }
          }
        }
      }
    }

    return citations;
  }

  /**
   * Parse a response body for SSE data lines containing citation info.
   * Handles both single-stream and chunked responses.
   */
  async function parseResponseForCitations(body) {
    const found = [];

    try {
      const reader = body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop(); // keep incomplete last line

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const jsonStr = line.slice(6).trim();
          if (jsonStr === "[DONE]" || jsonStr === '"v1"') continue;

          try {
            const data = JSON.parse(jsonStr);

            // Delta encoding format: {p, o, v} where v contains the message
            const msg = data?.v?.message || data?.message || data;
            if (!msg || typeof msg !== "object") continue;

            // Skip non-assistant messages
            if (msg.author && msg.author.role && msg.author.role !== "assistant") continue;

            const citations = extractCitationsFromMessage(msg);
            found.push(...citations);
          } catch {
            // Malformed JSON — skip
          }
        }
      }

      // Process remaining buffer
      if (buffer.startsWith("data: ")) {
        const jsonStr = buffer.slice(6).trim();
        if (jsonStr !== "[DONE]" && jsonStr !== '"v1"') {
          try {
            const data = JSON.parse(jsonStr);
            const msg = data?.v?.message || data?.message || data;
            if (msg && typeof msg === "object") {
              found.push(...extractCitationsFromMessage(msg));
            }
          } catch {}
        }
      }

      reader.releaseLock();
    } catch {
      // Stream error — return what we have
    }

    return found;
  }

  /**
   * Add new citations to the session accumulator and dispatch an event.
   * Uses a debounce timer so we dispatch once after a burst of chunks,
   * not on every single chunk.
   */
  function addToSession(sessionId, newCitations) {
    let added = false;
    for (const c of newCitations) {
      if (!sessionSeenUrls.has(c.url)) {
        sessionSeenUrls.add(c.url);
        sessionCitations.push(c);
        added = true;
      }
    }

    if (!added) return;

    // Debounce: dispatch 500ms after the last new citation arrives
    if (sessionTimer) clearTimeout(sessionTimer);
    sessionTimer = setTimeout(() => {
      if (sessionCitations.length > 0) {
        document.dispatchEvent(
          new CustomEvent(VERITY_SHARED.CUSTOM_EVENTS.CITATIONS, {
            detail: {
              sessionId,
              citations: [...sessionCitations],
              timestamp: Date.now(),
            },
          })
        );
      }
    }, 500);
  }

  /**
   * Reset the session when a new conversation request starts.
   */
  function resetSession() {
    sessionCitations = [];
    sessionSeenUrls = new Set();
    if (sessionTimer) {
      clearTimeout(sessionTimer);
      sessionTimer = null;
    }
  }

  // --- Monkey-patch fetch ---

  const originalFetch = window.fetch;

  window.fetch = async function (...args) {
    const resource = args[0];
    const url = getRequestUrl(resource);
    const method = getRequestMethod(resource, args[1]);

    // Reset session on a new conversation POST
    let sessionId = currentSessionId;
    if (isConversationStart(resource, args[1])) {
      sessionId = beginSession();
    }

    const response = await originalFetch.apply(this, args);

    if (isConversationRequest(resource)) {
      sessionId = sessionId || ensureSession();
      try {
        const cloned = response.clone();
        // Fire-and-forget — never block the original response
        parseResponseForCitations(cloned.body)
          .then((citations) => {
            if (citations.length > 0) {
              addToSession(sessionId, citations);
            }
          })
          .catch(() => {
            // Silently ignore parse failures
          });
      } catch {
        // clone() or body access failed — ignore
      }
    }

    return response;
  };
})();

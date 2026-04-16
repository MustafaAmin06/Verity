window.Verity = window.Verity || {};

var VERITY_SHARED = globalThis.VerityShared;
const CONTEXT_DEAD_MSG = "Extension was reloaded — please refresh the page.";
const RESPONSE_CACHE_TTL_MS = 30 * 60 * 1000;
const responseCache = new Map();
const pendingRequests = new Map();
const progressListeners = new Map();

chrome.runtime.onMessage.addListener((message) => {
  if (
    message.type !== VERITY_SHARED.MESSAGE_TYPES.SCRAPE_PROGRESS ||
    !message.requestKey
  ) {
    return;
  }

  const listeners = progressListeners.get(message.requestKey);
  if (!listeners) return;

  for (const listener of listeners) {
    try {
      listener(message);
    } catch (error) {
      console.warn("Verity: progress listener failed", error);
    }
  }
});

function isContextAlive() {
  try {
    return !!chrome.runtime?.id;
  } catch {
    return false;
  }
}

function hashString(value) {
  let hash = 5381;
  for (let i = 0; i < value.length; i++) {
    hash = ((hash << 5) + hash) + value.charCodeAt(i);
    hash &= hash;
  }
  return Math.abs(hash).toString(36);
}

function subscribeToProgress(requestKey, callback) {
  if (!requestKey || typeof callback !== "function") {
    return () => {};
  }

  if (!progressListeners.has(requestKey)) {
    progressListeners.set(requestKey, new Set());
  }

  const listeners = progressListeners.get(requestKey);
  listeners.add(callback);

  return () => {
    const current = progressListeners.get(requestKey);
    if (!current) return;
    current.delete(callback);
    if (current.size === 0) {
      progressListeners.delete(requestKey);
    }
  };
}

window.Verity.api = {
  isContextAlive,

  onProgress(requestKey, callback) {
    if (typeof requestKey === "function") {
      return subscribeToProgress("legacy", requestKey);
    }
    return subscribeToProgress(requestKey, callback);
  },

  clearProgress(requestKey) {
    if (requestKey) {
      progressListeners.delete(requestKey);
      return;
    }
    progressListeners.clear();
  },

  computeCacheKey(payload) {
    const normalized = {
      original_prompt: (payload.original_prompt || "").trim(),
      full_ai_response: (payload.full_ai_response || "").trim(),
      sources: (payload.sources || [])
        .map((source) => ({
          url: source.url || "",
          label: source.label || "",
          context: source.context || "",
        }))
        .sort((left, right) => {
          const leftValue = `${left.url}\n${left.context}\n${left.label}`;
          const rightValue = `${right.url}\n${right.context}\n${right.label}`;
          return leftValue.localeCompare(rightValue);
        }),
    };
    return `verity_${hashString(JSON.stringify(normalized))}`;
  },

  async fetchWithDedup(cacheKey, payload) {
    const cached = responseCache.get(cacheKey);
    if (cached && Date.now() - cached.timestamp < RESPONSE_CACHE_TTL_MS) {
      return cached.data;
    }

    if (pendingRequests.has(cacheKey)) {
      return pendingRequests.get(cacheKey);
    }

    const promise = this.checkSources(payload, cacheKey);
    pendingRequests.set(cacheKey, promise);

    try {
      const data = await promise;
      responseCache.set(cacheKey, {
        timestamp: Date.now(),
        data,
      });
      return data;
    } finally {
      pendingRequests.delete(cacheKey);
    }
  },

  checkSources(payload, requestKey) {
    return new Promise((resolve, reject) => {
      if (!isContextAlive()) {
        reject(new Error(CONTEXT_DEAD_MSG));
        return;
      }

      try {
        chrome.runtime.sendMessage(
          {
            type: VERITY_SHARED.MESSAGE_TYPES.EXTRACT_SOURCES,
            requestKey,
            payload,
          },
          (response) => {
            if (chrome.runtime.lastError) {
              const message = chrome.runtime.lastError.message || "";
              if (
                message.includes("context invalidated") ||
                message.includes("Extension context")
              ) {
                reject(new Error(CONTEXT_DEAD_MSG));
              } else {
                reject(new Error(message));
              }
              return;
            }

            if (!response || !response.ok) {
              reject(
                new Error((response && response.error) || "Unknown error")
              );
              return;
            }

            resolve(response.data);
          }
        );
      } catch (error) {
        if (
          error.message?.includes("context invalidated") ||
          error.message?.includes("Extension context")
        ) {
          reject(new Error(CONTEXT_DEAD_MSG));
        } else {
          reject(error);
        }
      }
    });
  },
};

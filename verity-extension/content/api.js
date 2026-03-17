window.Verity = window.Verity || {};

const CONTEXT_DEAD_MSG = "Extension was reloaded — please refresh the page.";
const RESPONSE_CACHE_TTL_MS = 30 * 60 * 1000;
const responseCache = new Map();
const pendingRequests = new Map();

let _progressCallback = null;

chrome.runtime.onMessage.addListener((message) => {
  if (message.type === "SCRAPE_PROGRESS" && _progressCallback) {
    _progressCallback(message);
  }
});

/**
 * Returns true when the extension context is still alive
 * (i.e. the service worker hasn't been replaced by a newer version).
 */
function isContextAlive() {
  try {
    return !!chrome.runtime?.id;
  } catch {
    return false;
  }
}

window.Verity.api = {
  /** Expose for other modules that need to guard chrome.runtime calls */
  isContextAlive,

  onProgress(callback) {
    _progressCallback = callback;
  },

  clearProgress() {
    _progressCallback = null;
  },

  computeCacheKey(sources) {
    const joined = sources.map((source) => source.url).sort().join('|');
    let hash = 5381;
    for (let i = 0; i < joined.length; i++) {
      hash = ((hash << 5) + hash) + joined.charCodeAt(i);
      hash &= hash;
    }
    return 'verity_' + Math.abs(hash).toString(36);
  },

  async fetchWithDedup(cacheKey, payload) {
    const cached = responseCache.get(cacheKey);
    if (cached && (Date.now() - cached.timestamp) < RESPONSE_CACHE_TTL_MS) {
      return cached.data;
    }

    if (pendingRequests.has(cacheKey)) {
      return pendingRequests.get(cacheKey);
    }

    const promise = this.checkSources(payload);
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

  checkSources(payload) {
    return new Promise((resolve, reject) => {
      if (!isContextAlive()) {
        reject(new Error(CONTEXT_DEAD_MSG));
        return;
      }

      try {
        chrome.runtime.sendMessage(
          { type: "EXTRACT_SOURCES", payload },
          (response) => {
            if (chrome.runtime.lastError) {
              const msg = chrome.runtime.lastError.message || "";
              if (msg.includes("context invalidated") || msg.includes("Extension context")) {
                reject(new Error(CONTEXT_DEAD_MSG));
              } else {
                reject(new Error(msg));
              }
              return;
            }
            if (!response || !response.ok) {
              reject(new Error((response && response.error) || "Unknown error"));
              return;
            }
            resolve(response.data);
          }
        );
      } catch (err) {
        if (
          err.message?.includes("context invalidated") ||
          err.message?.includes("Extension context")
        ) {
          reject(new Error(CONTEXT_DEAD_MSG));
        } else {
          reject(err);
        }
      }
    });
  },
};

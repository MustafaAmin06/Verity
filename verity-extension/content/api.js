window.Verity = window.Verity || {};

const CONTEXT_DEAD_MSG = "Extension was reloaded — please refresh the page.";

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

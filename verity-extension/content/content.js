// Verity configuration
const VERITY_CONFIG = {
  extractorUrl: "http://localhost:8001/extract",
  hoverDelayMs: 300,
  maxBodyTextChars: 8000,
  minContextChars: 30,
  maxContextChars: 400,
  minUrlsToShowButton: 1,
};

// Platform-specific selectors — multiple fallbacks for each since ChatGPT changes their DOM frequently
const PLATFORMS = {
  chatgpt: {
    hostPatterns: ["chat.openai.com", "chatgpt.com"],
    selectors: {
      stopButton: [
        'button[data-testid="stop-button"]',
        'button[aria-label="Stop streaming"]',
        'button[aria-label="Stop generating"]',
        'button[aria-label="Stop"]',
      ],
      assistantMessage: [
        '[data-message-author-role="assistant"]',
        '[data-testid="conversation-turn"] .agent-turn',
        '.agent-turn',
      ],
      userMessage: [
        '[data-message-author-role="user"]',
        '[data-testid="conversation-turn"] .human-turn',
      ],
    },
  },
};

// Resolve the first matching selector from an array of candidates
function resolveSelector(candidates) {
  if (typeof candidates === "string") return candidates;
  for (const sel of candidates) {
    if (document.querySelector(sel) !== null) return sel;
  }
  return candidates[0]; // fall back to first even if not found yet
}

// Run diagnostics and log what we can/can't find
function runDiagnostics(config) {
  console.log("[Verity] --- Diagnostics ---");
  for (const [key, candidates] of Object.entries(config.selectors)) {
    const list = typeof candidates === "string" ? [candidates] : candidates;
    let found = false;
    for (const sel of list) {
      const el = document.querySelector(sel);
      if (el) {
        console.log(`[Verity]   ${key}: FOUND via "${sel}"`, el);
        found = true;
        break;
      }
    }
    if (!found) {
      console.warn(`[Verity]   ${key}: NOT FOUND — tried:`, list);
    }
  }
  console.log("[Verity] --- End diagnostics (run after a response generates) ---");
}

// Flatten selectors to the first matching one, updating config in place
function resolveAllSelectors(config) {
  const resolved = {};
  for (const [key, candidates] of Object.entries(config.selectors)) {
    resolved[key] = resolveSelector(candidates);
  }
  return { ...config, selectors: resolved };
}

function cleanupInjectedUi() {
  let removedLegacyPanel = false;

  for (const host of document.querySelectorAll('[data-verity-host]')) {
    const container = host.parentElement;
    if (container && container.childElementCount === 1) {
      container.remove();
    } else {
      host.remove();
    }
    removedLegacyPanel = true;
  }

  for (const node of document.querySelectorAll('.verity-trigger-btn, .verity-panel')) {
    node.remove();
  }

  for (const node of document.querySelectorAll('[data-verity-processed]')) {
    node.removeAttribute('data-verity-processed');
  }

  if (removedLegacyPanel) {
    console.warn('[Verity] Removed stale legacy panel takeover DOM so ChatGPT can rebuild its native sources panel');
  }

  return removedLegacyPanel;
}

// Detect platform and initialize
// Listen for extension reload notifications from the service worker.
// When the extension is reloaded/updated, the service worker broadcasts
// VERITY_RELOAD so we refresh the page and get fresh content scripts.
try {
  chrome.runtime.onMessage.addListener((message) => {
    if (message.type === "VERITY_RELOAD") {
      console.log("[Verity] Extension reloaded, refreshing page...");
      window.location.reload();
    }
  });
} catch {
  // Extension context already dead — nothing to listen on
}

(function () {
  console.log("[Verity] Content script loaded on", window.location.hostname);

  cleanupInjectedUi();

  const hostname = window.location.hostname;
  let matchedPlatform = null;

  for (const [name, config] of Object.entries(PLATFORMS)) {
    if (config.hostPatterns.some((pattern) => hostname.includes(pattern))) {
      matchedPlatform = config;
      console.log("[Verity] Matched platform:", name);
      break;
    }
  }

  if (!matchedPlatform) {
    console.warn("[Verity] No supported platform detected on", hostname);
    return;
  }

  // Re-resolve selectors after DOM settles in case ChatGPT loads lazily
  // Also expose a manual trigger for debugging: window.Verity.diagnose()
  window.Verity.diagnose = () => runDiagnostics(matchedPlatform);
  window.Verity.triggerCheck = () => window.Verity.observer._onGenerationComplete();

  // Give the SPA a moment to render before resolving selectors
  setTimeout(() => {
    const resolved = resolveAllSelectors(matchedPlatform);
    console.log("[Verity] Resolved selectors:", resolved.selectors);
    window.Verity.observer.init(resolved);
  }, 2000);
})();

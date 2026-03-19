// VERITY_CONFIG is declared and loaded from chrome.storage by settings.js

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

  for (const node of document.querySelectorAll('.verity-trigger-btn, .verity-panel, .verity-tooltip')) {
    node.remove();
  }

  for (const node of document.querySelectorAll('[data-verity-processed]')) {
    node.removeAttribute('data-verity-processed');
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
      window.location.reload();
    }
  });
} catch {
  // Extension context already dead — nothing to listen on
}

// Expose cleanup so settings.js can call it on live enable/disable toggle
window.Verity.cleanup = cleanupInjectedUi;

function _initVerity() {
  cleanupInjectedUi();

  const hostname = window.location.hostname;
  let matchedPlatform = null;

  for (const [, config] of Object.entries(PLATFORMS)) {
    if (config.hostPatterns.some((pattern) => hostname.includes(pattern))) {
      matchedPlatform = config;
      break;
    }
  }

  if (!matchedPlatform) {
    return;
  }

  // Give the SPA a moment to render before resolving selectors
  setTimeout(() => {
    const resolved = resolveAllSelectors(matchedPlatform);
    window.Verity.observer.init(resolved);
  }, 2000);
}

// Expose reinit so settings.js can re-enable after a live toggle
window.Verity.reinit = _initVerity;

// Wait for settings to load, then initialize if enabled
window.Verity.settingsReady.then(() => {
  if (!VERITY_CONFIG.enabled) {
    return;
  }
  _initVerity();
});

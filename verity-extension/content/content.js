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

// Keep all candidate selectors active, with currently matching ones ordered first.
function resolveSelector(candidates) {
  if (typeof candidates === "string") return candidates;
  const matching = [];
  const fallback = [];
  for (const sel of candidates) {
    try {
      if (document.querySelector(sel) !== null) {
        matching.push(sel);
      } else {
        fallback.push(sel);
      }
    } catch {
      fallback.push(sel);
    }
  }
  return [...new Set([...matching, ...fallback])].join(", ");
}

// Flatten selectors into comma-separated selector queries.
function resolveAllSelectors(config) {
  const resolved = {};
  for (const [key, candidates] of Object.entries(config.selectors)) {
    resolved[key] = resolveSelector(candidates);
  }
  return { ...config, selectors: resolved };
}

function cleanupInjectedUi() {
  if (window.Verity.observer && typeof window.Verity.observer.stop === "function") {
    window.Verity.observer.stop();
  }
  if (window.Verity.extractor && typeof window.Verity.extractor.clearInterceptedSessions === "function") {
    window.Verity.extractor.clearInterceptedSessions();
  }

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

  const startObserver = (attemptsRemaining) => {
    if (!document.body) {
      if (attemptsRemaining > 0) {
        setTimeout(() => startObserver(attemptsRemaining - 1), 100);
      }
      return;
    }
    const resolved = resolveAllSelectors(matchedPlatform);
    window.Verity.observer.init(resolved);
  };

  startObserver(20);
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

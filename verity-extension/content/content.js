// VERITY_CONFIG is declared and loaded from chrome.storage by settings.js

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
        ".agent-turn",
      ],
      userMessage: [
        '[data-message-author-role="user"]',
        '[data-testid="conversation-turn"] .human-turn',
      ],
    },
  },
};

function resolveSelector(candidates) {
  if (typeof candidates === "string") return candidates;

  const matching = [];
  const fallback = [];

  for (const selector of candidates) {
    try {
      if (document.querySelector(selector) !== null) {
        matching.push(selector);
      } else {
        fallback.push(selector);
      }
    } catch {
      fallback.push(selector);
    }
  }

  return [...new Set([...matching, ...fallback])].join(", ");
}

function resolveAllSelectors(config) {
  const resolved = {};
  for (const [key, candidates] of Object.entries(config.selectors)) {
    resolved[key] = resolveSelector(candidates);
  }
  return { ...config, selectors: resolved };
}

function cleanupInjectedUi() {
  if (window.Verity.observer?.stop) {
    window.Verity.observer.stop();
  }

  if (window.Verity.extractor?.clearInterceptedSessions) {
    window.Verity.extractor.clearInterceptedSessions();
  }

  if (window.Verity.runtime?.disposeAll) {
    window.Verity.runtime.disposeAll();
  }

  for (const node of document.querySelectorAll(
    ".verity-trigger-btn, .verity-panel, .verity-tooltip, [data-verity-host]"
  )) {
    node.remove();
  }
}

window.Verity.cleanup = cleanupInjectedUi;

function initVerity() {
  cleanupInjectedUi();

  const hostname = window.location.hostname;
  const matchedPlatform = Object.values(PLATFORMS).find((config) =>
    config.hostPatterns.some((pattern) => hostname.includes(pattern))
  );

  if (!matchedPlatform) return;

  const startObserver = (attemptsRemaining) => {
    if (!document.body) {
      if (attemptsRemaining > 0) {
        setTimeout(() => startObserver(attemptsRemaining - 1), 100);
      }
      return;
    }

    window.Verity.observer.init(resolveAllSelectors(matchedPlatform));
  };

  startObserver(20);
}

window.Verity.reinit = initVerity;

window.Verity.settingsReady.then(() => {
  if (!VERITY_CONFIG.enabled) return;
  initVerity();
});

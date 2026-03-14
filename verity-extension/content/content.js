// Verity configuration
const VERITY_CONFIG = {
  extractorUrl: "http://localhost:8001/extract",
  hoverDelayMs: 300,
  maxBodyTextChars: 8000,
  minContextChars: 30,
  maxContextChars: 400,
  minUrlsToShowButton: 1,
};

// Platform-specific selectors
const PLATFORMS = {
  chatgpt: {
    hostPatterns: ["chat.openai.com", "chatgpt.com"],
    selectors: {
      stopButton: 'button[data-testid="stop-button"]',
      assistantMessage: '[data-message-author-role="assistant"]',
      userMessage: '[data-message-author-role="user"]',
    },
  },
};

// Detect platform and initialize
(function () {
  const hostname = window.location.hostname;
  let matchedPlatform = null;

  for (const [name, config] of Object.entries(PLATFORMS)) {
    if (config.hostPatterns.some((pattern) => hostname.includes(pattern))) {
      matchedPlatform = config;
      break;
    }
  }

  if (!matchedPlatform) {
    console.log("[Verity] No supported platform detected on", hostname);
    return;
  }

  console.log("[Verity] Initialized on", hostname);
  window.Verity.observer.init(matchedPlatform);
})();

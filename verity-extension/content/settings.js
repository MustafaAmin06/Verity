// Verity settings — loaded from chrome.storage.local, kept live-updated.
// This script MUST be listed first in manifest.json content_scripts.

window.Verity = window.Verity || {};

const VERITY_AZURE_EXTRACTOR_URL =
  "https://verity-api.thankfulsmoke-1985157b.eastus.azurecontainerapps.io";
const VERITY_LOCAL_EXTRACTOR_URL = "http://localhost:8001";

var VERITY_CONFIG = {
  enabled: true,
  autoCheck: false,
  extractorUrl: VERITY_LOCAL_EXTRACTOR_URL,
  maxBodyTextChars: 8000,
  minContextChars: 30,
  maxContextChars: 400,
  minUrlsToShowButton: 1,
  previewCardSelectors: [
    '[data-testid*="link-preview"]',
    '[data-testid*="preview"]',
    '[class*="LinkPreview"]',
    '[class*="link-preview"]',
    '[class*="linkPreview"]',
  ],
  previewSearchTimeoutMs: 800,
};

// Freeze the default keys so we know what belongs in config
const _VERITY_DEFAULTS = Object.assign({}, VERITY_CONFIG);

// Resolves once chrome.storage values have been merged into VERITY_CONFIG
window.Verity.settingsReady = new Promise((resolve) => {
  try {
    chrome.storage.local.get(_VERITY_DEFAULTS, (stored) => {
      Object.assign(VERITY_CONFIG, stored);
      resolve();
    });
  } catch {
    // Extension context dead — use defaults
    resolve();
  }
});

// Live-update VERITY_CONFIG when popup (or anything) writes to storage
try {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;
    for (const [key, { newValue }] of Object.entries(changes)) {
      if (key in VERITY_CONFIG) {
        VERITY_CONFIG[key] = newValue;
      }
    }

    // Handle live enable/disable toggle
    if ("enabled" in changes) {
      if (changes.enabled.newValue === false) {
        if (typeof window.Verity.cleanup === "function") {
          window.Verity.cleanup();
        }
      } else {
        if (typeof window.Verity.reinit === "function") {
          window.Verity.reinit();
        }
      }
    }
  });
} catch {
  // Extension context dead — ignore
}

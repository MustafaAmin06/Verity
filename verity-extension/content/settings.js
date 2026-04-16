// Verity settings — loaded from chrome.storage.local, kept live-updated.
// This script MUST be listed before any content modules that read VERITY_CONFIG.

window.Verity = window.Verity || {};

var VERITY_SHARED = globalThis.VerityShared;
var VERITY_CONFIG = VERITY_SHARED.buildContentConfig();

window.Verity.settingsReady = new Promise((resolve) => {
  try {
    chrome.storage.local.get(VERITY_SHARED.DEFAULT_SETTINGS, (stored) => {
      Object.assign(VERITY_CONFIG, VERITY_SHARED.buildContentConfig(stored));
      resolve();
    });
  } catch {
    resolve();
  }
});

try {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "local") return;

    const nextSettings = {};
    let hasRelevantChange = false;

    for (const [key, { newValue }] of Object.entries(changes)) {
      if (!VERITY_SHARED.STORAGE_KEYS.includes(key) && key !== "advancedSettingsVisible") {
        continue;
      }
      nextSettings[key] = newValue;
      hasRelevantChange = true;
    }

    if (!hasRelevantChange) return;

    Object.assign(VERITY_CONFIG, VERITY_SHARED.buildContentConfig({
      ...VERITY_CONFIG,
      ...nextSettings,
    }));

    if (!Object.prototype.hasOwnProperty.call(changes, "enabled")) return;

    if (changes.enabled.newValue === false) {
      if (typeof window.Verity.cleanup === "function") {
        window.Verity.cleanup();
      }
      return;
    }

    if (typeof window.Verity.reinit === "function") {
      window.Verity.reinit();
    }
  });
} catch {
  // Extension context dead — ignore
}

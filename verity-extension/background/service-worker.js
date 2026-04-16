importScripts("../shared/contracts.js");

const VERITY_SHARED = globalThis.VerityShared;
const inFlightExtracts = new Map();

// When the extension is installed or reloaded, tell open ChatGPT tabs to
// refresh so they pick up fresh content scripts instead of running stale ones.
chrome.runtime.onInstalled.addListener(async () => {
  chrome.storage.local.get(
    {
      ...VERITY_SHARED.DEFAULT_SETTINGS,
      advancedSettingsVisible: false,
    },
    (settings) => {
      const normalized = VERITY_SHARED.normalizeSettings(settings);
      const shouldWriteNormalized =
        normalized.extractorUrl !== VERITY_SHARED.normalizeUrl(settings.extractorUrl) ||
        normalized.devMode !== Boolean(
          settings.devMode !== undefined
            ? settings.devMode
            : settings.advancedSettingsVisible
        ) ||
        settings.advancedSettingsVisible !== undefined;

      if (shouldWriteNormalized) {
        chrome.storage.local.set(normalized);
      }
    }
  );

  const tabs = await chrome.tabs.query({
    url: ["https://chat.openai.com/*", "https://chatgpt.com/*"],
  });
  for (const tab of tabs) {
    try {
      await chrome.tabs.reload(tab.id);
    } catch {
      // Tab may be gone or not reloadable — that's fine
    }
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type !== VERITY_SHARED.MESSAGE_TYPES.EXTRACT_SOURCES) return false;

  if (sender.id !== chrome.runtime.id) return false;
  if (!sender.tab?.url?.match(/^https:\/\/(chat\.openai\.com|chatgpt\.com)\//)) {
    return false;
  }

  const tabId = sender.tab.id;
  const requestKey = String(message.requestKey || "");

  if (requestKey && inFlightExtracts.has(requestKey)) {
    inFlightExtracts.get(requestKey)
      .then((data) => sendResponse({ ok: true, data }))
      .catch((error) => sendResponse({ ok: false, error: error.message }));
    return true;
  }

  const requestPromise = new Promise((resolve, reject) => {
    chrome.storage.local.get(VERITY_SHARED.DEFAULT_SETTINGS, (stored) => {
      const settings = VERITY_SHARED.normalizeSettings(stored);
      const baseUrl = settings.extractorUrl;

      if (!baseUrl.startsWith("https://") && !VERITY_SHARED.isLocalDevUrl(baseUrl)) {
        reject(new Error("Backend URL must use HTTPS"));
        return;
      }

      const headers = { "Content-Type": "application/json" };
      if (settings.apiKey) {
        headers.Authorization = `Bearer ${settings.apiKey}`;
      }

      fetch(
        VERITY_SHARED.createBackendUrl(
          baseUrl,
          VERITY_SHARED.BACKEND_ENDPOINTS.EXTRACT_STREAM
        ),
        {
          method: "POST",
          headers,
          body: JSON.stringify(message.payload),
          signal: AbortSignal.timeout(
            VERITY_SHARED.REQUEST_TIMEOUTS_MS.backendExtract
          ),
        }
      )
        .then(async (res) => {
          if (!res.ok) throw new Error(`HTTP ${res.status}`);

          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          let finalData = null;

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            const parts = buffer.split("\n\n");
            buffer = parts.pop();

            for (const part of parts) {
              if (!part.trim()) continue;
              const lines = part.split("\n");
              let eventType = null;
              let data = null;

              for (const line of lines) {
                if (line.startsWith("event: ")) eventType = line.slice(7);
                if (line.startsWith("data: ")) data = line.slice(6);
              }

              if (
                eventType === "progress" &&
                tabId &&
                requestKey
              ) {
                try {
                  chrome.tabs.sendMessage(tabId, {
                    type: VERITY_SHARED.MESSAGE_TYPES.SCRAPE_PROGRESS,
                    requestKey,
                    ...JSON.parse(data),
                  });
                } catch (error) {
                  console.warn("Verity: failed to parse progress event", error);
                }
              } else if (eventType === "result") {
                try {
                  finalData = JSON.parse(data);
                } catch (error) {
                  console.error("Verity: failed to parse result event", error);
                }
              }
            }
          }

          if (finalData) {
            resolve(finalData);
          } else {
            reject(new Error("No result received"));
          }
        })
        .catch((error) => reject(error));
    });
  });

  if (requestKey) {
    inFlightExtracts.set(
      requestKey,
      requestPromise.finally(() => {
        inFlightExtracts.delete(requestKey);
      })
    );
  }

  requestPromise
    .then((data) => sendResponse({ ok: true, data }))
    .catch((error) => sendResponse({ ok: false, error: error.message }));

  return true;
});

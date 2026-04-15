const AZURE_EXTRACTOR_URL =
  "https://verity-api.thankfulsmoke-1985157b.eastus.azurecontainerapps.io";
const LOCAL_EXTRACTOR_URL = "http://localhost:8001";
const LOCAL_LOOPBACK_URL = "http://127.0.0.1:8001";
const LEGACY_RAILWAY_URL = "https://verity-production-e8f2.up.railway.app";

// When the extension is installed or reloaded, tell open ChatGPT tabs to
// refresh so they pick up fresh content scripts instead of running stale ones.
chrome.runtime.onInstalled.addListener(async () => {
  chrome.storage.local.get(
    {
      extractorUrl: AZURE_EXTRACTOR_URL,
      devMode: false,
      advancedSettingsVisible: false,
    },
    (settings) => {
      const normalized = (settings.extractorUrl || "").replace(/\/+$/, "");
      const devMode = Boolean(
        settings.devMode !== undefined ? settings.devMode : settings.advancedSettingsVisible
      );
      const shouldResetToConsumer = normalized === LEGACY_RAILWAY_URL;

      if (shouldResetToConsumer) {
        chrome.storage.local.set({
          extractorUrl: AZURE_EXTRACTOR_URL,
          devMode,
          apiKey: "",
        });
      } else if (settings.advancedSettingsVisible !== undefined && settings.devMode === undefined) {
        chrome.storage.local.set({
          devMode,
        });
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
  if (message.type !== "EXTRACT_SOURCES") return false;

  // Only accept messages from our own extension's content scripts
  if (sender.id !== chrome.runtime.id) return false;
  if (!sender.tab?.url?.match(/^https:\/\/(chat\.openai\.com|chatgpt\.com)\//)) return false;

  const tabId = sender.tab?.id;

  // Read backend URL and API key from storage (set via the settings popup).
  chrome.storage.local.get(
    { extractorUrl: AZURE_EXTRACTOR_URL, apiKey: "" },
    (settings) => {
      const baseUrl = settings.extractorUrl.replace(/\/+$/, "");

      // Enforce HTTPS except for explicit local development URLs.
      const isLocalDevUrl =
        baseUrl === LOCAL_EXTRACTOR_URL ||
        baseUrl === LOCAL_LOOPBACK_URL ||
        baseUrl.startsWith("http://localhost") ||
        baseUrl.startsWith("http://127.0.0.1");

      if (!baseUrl.startsWith("https://") && !isLocalDevUrl) {
        sendResponse({ ok: false, error: "Backend URL must use HTTPS" });
        return;
      }

      const headers = { "Content-Type": "application/json" };
      if (settings.apiKey) {
        headers["Authorization"] = `Bearer ${settings.apiKey}`;
      }

      fetch(`${baseUrl}/extract-stream`, {
        method: "POST",
        headers,
        body: JSON.stringify(message.payload),
        signal: AbortSignal.timeout(120000),
      })
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

            // Parse SSE events from buffer (events separated by \n\n)
            const parts = buffer.split("\n\n");
            buffer = parts.pop(); // last part may be incomplete

            for (const part of parts) {
              if (!part.trim()) continue;
              const lines = part.split("\n");
              let eventType = null;
              let data = null;
              for (const line of lines) {
                if (line.startsWith("event: ")) eventType = line.slice(7);
                if (line.startsWith("data: ")) data = line.slice(6);
              }

              if (eventType === "progress" && tabId) {
                try {
                  chrome.tabs.sendMessage(tabId, {
                    type: "SCRAPE_PROGRESS",
                    ...JSON.parse(data),
                  });
                } catch (e) {
                  console.warn("Verity: failed to parse progress event", e);
                }
              } else if (eventType === "result") {
                try {
                  finalData = JSON.parse(data);
                } catch (e) {
                  console.error("Verity: failed to parse result event", e);
                }
              }
            }
          }

          if (finalData) {
            sendResponse({ ok: true, data: finalData });
          } else {
            sendResponse({ ok: false, error: "No result received" });
          }
        })
        .catch((err) => sendResponse({ ok: false, error: err.message }));
    }
  );

  return true; // keep channel open for async response
});

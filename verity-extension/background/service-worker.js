// When the extension is installed or reloaded, tell open ChatGPT tabs to
// refresh so they pick up fresh content scripts instead of running stale ones.
chrome.runtime.onInstalled.addListener(async () => {
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

  const tabId = sender.tab?.id;

  // Read backend URL from storage (set via popup dashboard)
  chrome.storage.local.get({ extractorUrl: "https://YOUR_RAILWAY_URL.up.railway.app" }, (settings) => {
    const baseUrl = settings.extractorUrl.replace(/\/+$/, "");

    fetch(`${baseUrl}/extract-stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.payload),
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
              chrome.tabs.sendMessage(tabId, {
                type: "SCRAPE_PROGRESS",
                ...JSON.parse(data),
              });
            } else if (eventType === "result") {
              finalData = JSON.parse(data);
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
  });

  return true; // keep channel open for async response
});

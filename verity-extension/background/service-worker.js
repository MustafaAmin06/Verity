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

  fetch("http://localhost:8001/extract", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(message.payload),
  })
    .then((res) => {
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res.json();
    })
    .then((data) => sendResponse({ ok: true, data }))
    .catch((err) => sendResponse({ ok: false, error: err.message }));

  return true; // keep channel open for async response
});

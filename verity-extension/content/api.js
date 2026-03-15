window.Verity = window.Verity || {};

window.Verity.api = {
  checkSources(payload, extractorUrl) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(
        { type: "EXTRACT_SOURCES", payload, extractorUrl },
        (response) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
            return;
          }
          if (!response || !response.ok) {
            reject(new Error((response && response.error) || "Unknown error"));
            return;
          }
          resolve(response.data);
        }
      );
    });
  },
};

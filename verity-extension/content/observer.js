window.Verity = window.Verity || {};

window.Verity.observer = {
  _wasGenerating: false,
  _debounceTimer: null,
  _platformConfig: null,

  init(platformConfig) {
    this._platformConfig = platformConfig;
    console.log("[Verity] Observer started, watching for generation...");

    const observer = new MutationObserver(() => {
      this._checkGenerationState();
    });

    observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
    });
  },

  _checkGenerationState() {
    const selectors = this._platformConfig.selectors;
    const stopButton = document.querySelector(selectors.stopButton);
    const isGenerating = stopButton !== null;

    if (isGenerating) {
      if (!this._wasGenerating) {
        console.log("[Verity] Generation started");
      }
      this._wasGenerating = true;
      // Cancel any pending debounce if stop button reappears
      if (this._debounceTimer) {
        clearTimeout(this._debounceTimer);
        this._debounceTimer = null;
      }
    }

    if (this._wasGenerating && !isGenerating) {
      this._wasGenerating = false;
      // 500ms debounce to handle stop button flickering
      if (this._debounceTimer) clearTimeout(this._debounceTimer);
      this._debounceTimer = setTimeout(() => {
        this._debounceTimer = null;
        console.log("[Verity] Generation complete, scanning for URLs...");
        this._onGenerationComplete();
      }, 500);
    }
  },

  _onGenerationComplete() {
    const selectors = this._platformConfig.selectors;

    // Get the latest assistant message
    const allResponses = document.querySelectorAll(selectors.assistantMessage);
    const latestResponse = allResponses[allResponses.length - 1];
    if (!latestResponse) {
      console.warn("[Verity] No assistant message found — selector may have changed:", selectors.assistantMessage);
      return;
    }

    // Check if already processed
    const container = latestResponse.closest("[data-message-id]") || latestResponse.parentElement;
    if (container && container.hasAttribute("data-verity-processed")) {
      console.log("[Verity] Already processed, skipping");
      return;
    }

    // Extract sources
    const sources = window.Verity.extractor.extractSources(latestResponse);
    console.log("[Verity] URLs found:", sources.length, sources.map(s => s.url));

    // Expose for panel takeover module
    window.Verity._latestSources = sources;
    window.Verity._latestResponseEl = latestResponse;
    window.Verity._latestPlatformConfig = this._platformConfig;

    if (sources.length >= VERITY_CONFIG.minUrlsToShowButton) {
      window.Verity.ui.injectButton(latestResponse, sources, this._platformConfig);
    } else {
      console.log("[Verity] No URLs found — button not injected");
    }
  },
};

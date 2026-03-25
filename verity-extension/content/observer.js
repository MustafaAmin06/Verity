window.Verity = window.Verity || {};

window.Verity.observer = {
  _wasGenerating: false,
  _debounceTimer: null,
  _platformConfig: null,

  init(platformConfig) {
    this._platformConfig = platformConfig;
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
      return;
    }

    // Check if already processed
    const container = latestResponse.closest("[data-message-id]") || latestResponse.parentElement;
    if (container && container.hasAttribute("data-verity-processed")) {
      return;
    }

    // Wait briefly for intercepted API citations to arrive, then extract.
    // The MAIN-world interceptor may fire slightly after the stop button
    // disappears, so we give it up to 1.5s before falling back to DOM-only.
    this._waitForInterceptedThenExtract(latestResponse);
  },

  _waitForInterceptedThenExtract(responseEl) {
    const maxWaitMs = 1500;
    const pollIntervalMs = 200;
    let elapsed = 0;

    const tryExtract = () => {
      const hasFreshData =
        window.Verity._interceptedCitations &&
        window.Verity._interceptedCitations.length > 0 &&
        Date.now() - window.Verity._interceptedTimestamp < 15000;

      if (hasFreshData || elapsed >= maxWaitMs) {
        const sources = window.Verity.extractor.extractSources(responseEl);

        // Clear the intercepted cache so it's not reused for the next message
        window.Verity._interceptedCitations = null;
        window.Verity._interceptedTimestamp = 0;

        if (sources.length >= VERITY_CONFIG.minUrlsToShowButton) {
          if (VERITY_CONFIG.autoCheck) {
            window.Verity.ui.autoCheck(responseEl, sources, this._platformConfig);
          } else {
            window.Verity.ui.injectButton(responseEl, sources, this._platformConfig);
          }
        }
        return;
      }

      elapsed += pollIntervalMs;
      setTimeout(tryExtract, pollIntervalMs);
    };

    tryExtract();
  },
};

window.Verity = window.Verity || {};

window.Verity.observer = {
  _observer: null,
  _wasGenerating: false,
  _debounceTimer: null,
  _stabilityTimer: null,
  _platformConfig: null,
  _lastResponseSignature: "",
  _responseSettledDelayMs: 1200,

  init(platformConfig) {
    this.stop();
    this._platformConfig = platformConfig;
    if (!document.body) {
      return;
    }

    this._observer = new MutationObserver(() => {
      this._checkGenerationState();
    });

    this._observer.observe(document.body, {
      childList: true,
      subtree: true,
      attributes: true,
    });

    this._checkGenerationState();
  },

  stop() {
    if (this._observer) {
      this._observer.disconnect();
      this._observer = null;
    }
    if (this._debounceTimer) {
      clearTimeout(this._debounceTimer);
      this._debounceTimer = null;
    }
    if (this._stabilityTimer) {
      clearTimeout(this._stabilityTimer);
      this._stabilityTimer = null;
    }
    this._wasGenerating = false;
    this._lastResponseSignature = "";
  },

  _checkGenerationState() {
    if (!this._platformConfig) return;
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
      if (this._stabilityTimer) {
        clearTimeout(this._stabilityTimer);
        this._stabilityTimer = null;
      }
    }

    if (this._wasGenerating && !isGenerating) {
      this._wasGenerating = false;
      this._scheduleGenerationComplete(500);
      return;
    }

    if (!isGenerating) {
      this._watchForSettledResponse();
    }
  },

  _scheduleGenerationComplete(delayMs) {
    if (this._debounceTimer) clearTimeout(this._debounceTimer);
    this._debounceTimer = setTimeout(() => {
      this._debounceTimer = null;
      this._onGenerationComplete();
    }, delayMs);
  },

  _getLatestResponse() {
    if (!this._platformConfig) return null;
    const selectors = this._platformConfig.selectors;
    const allResponses = document.querySelectorAll(selectors.assistantMessage);
    return allResponses[allResponses.length - 1] || null;
  },

  _getResponseContainer(responseEl) {
    return responseEl.closest("[data-message-id]") || responseEl.parentElement;
  },

  _watchForSettledResponse() {
    const latestResponse = this._getLatestResponse();
    if (!latestResponse) return;

    const container = this._getResponseContainer(latestResponse);
    if (!container || container.hasAttribute("data-verity-processed")) return;

    const signature = this._buildResponseSignature(latestResponse);
    if (!signature || signature.length < 80) return;
    if (signature === this._lastResponseSignature) return;

    this._lastResponseSignature = signature;
    if (this._stabilityTimer) clearTimeout(this._stabilityTimer);
    this._stabilityTimer = setTimeout(() => {
      this._stabilityTimer = null;
      const currentStopButton = document.querySelector(this._platformConfig.selectors.stopButton);
      if (!currentStopButton) {
        this._onGenerationComplete();
      }
    }, this._responseSettledDelayMs);
  },

  _buildResponseSignature(responseEl) {
    const text = (responseEl.innerText || "").trim();
    const tail = text.slice(-400);
    return `${text.length}:${tail}`;
  },

  _onGenerationComplete() {
    const latestResponse = this._getLatestResponse();
    if (!latestResponse) {
      return;
    }

    // Check if already processed
    const container = this._getResponseContainer(latestResponse);
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
      const interceptedSession = window.Verity.extractor.peekPendingInterceptedSession();

      if (interceptedSession || elapsed >= maxWaitMs) {
        const claimedSession = interceptedSession
          ? window.Verity.extractor.claimPendingInterceptedSession()
          : null;
        const sources = window.Verity.extractor.extractSources(responseEl, claimedSession);

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

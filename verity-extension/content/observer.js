window.Verity = window.Verity || {};

var VERITY_SHARED = globalThis.VerityShared;

window.Verity.observer = {
  _observer: null,
  _wasGenerating: false,
  _debounceTimer: null,
  _stabilityTimer: null,
  _platformConfig: null,
  _lastResponseSignature: "",
  _responseSettledDelayMs: VERITY_SHARED.REQUEST_TIMEOUTS_MS.observerResponseSettled,

  init(platformConfig) {
    this.stop();
    this._platformConfig = platformConfig;
    if (!document.body) return;

    this._observer = new MutationObserver((mutations) => {
      if (this._shouldIgnoreMutations(mutations)) return;
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

  _isVerityNode(node) {
    return Boolean(
      node &&
      node.nodeType === Node.ELEMENT_NODE &&
      (
        node.hasAttribute?.("data-verity-host") ||
        node.closest?.("[data-verity-host]") ||
        node.classList?.contains(VERITY_SHARED.SHADOW_ROOT_CLASS)
      )
    );
  },

  _shouldIgnoreMutations(mutations) {
    return mutations.every((mutation) => {
      const targetIsVerity = this._isVerityNode(mutation.target);
      const hasStructuralNodes =
        (mutation.addedNodes?.length || 0) > 0 ||
        (mutation.removedNodes?.length || 0) > 0;
      const addedAllVerity = Array.from(mutation.addedNodes || []).every((node) => this._isVerityNode(node));
      const removedAllVerity = Array.from(mutation.removedNodes || []).every((node) => this._isVerityNode(node));

      return targetIsVerity || (hasStructuralNodes && addedAllVerity && removedAllVerity);
    });
  },

  _checkGenerationState() {
    if (!this._platformConfig) return;

    const stopButton = document.querySelector(
      this._platformConfig.selectors.stopButton
    );
    const isGenerating = stopButton !== null;

    if (isGenerating) {
      this._wasGenerating = true;
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
    const responses = document.querySelectorAll(
      this._platformConfig.selectors.assistantMessage
    );
    return responses[responses.length - 1] || null;
  },

  _watchForSettledResponse() {
    const latestResponse = this._getLatestResponse();
    if (!latestResponse) return;

    const signature = this._buildResponseSignature(latestResponse);
    if (!signature || signature.length < 80) return;
    if (!window.Verity.runtime.canStartProcessing(latestResponse, signature)) return;
    if (signature === this._lastResponseSignature) return;

    this._lastResponseSignature = signature;
    if (this._stabilityTimer) clearTimeout(this._stabilityTimer);

    this._stabilityTimer = setTimeout(() => {
      this._stabilityTimer = null;
      const currentStopButton = document.querySelector(
        this._platformConfig.selectors.stopButton
      );
      if (!currentStopButton) {
        this._onGenerationComplete(latestResponse, signature);
      }
    }, this._responseSettledDelayMs);
  },

  _buildResponseSignature(responseEl) {
    const text = (responseEl.innerText || "").trim();
    const tail = text.slice(-400);
    return `${text.length}:${tail}`;
  },

  _onGenerationComplete(responseEl, signature) {
    const latestResponse = responseEl || this._getLatestResponse();
    if (!latestResponse) return;
    const responseSignature = signature || this._buildResponseSignature(latestResponse);
    if (!responseSignature || !window.Verity.runtime.canStartProcessing(latestResponse, responseSignature)) return;

    const session = window.Verity.runtime.setState(
      latestResponse,
      VERITY_SHARED.SESSION_STATES.WAITING_FOR_CITATIONS
    );
    window.Verity.runtime.markProcessed(session, responseSignature);

    this._waitForInterceptedThenExtract(latestResponse, session);
  },

  _waitForInterceptedThenExtract(responseEl, session) {
    const maxWaitMs = VERITY_SHARED.REQUEST_TIMEOUTS_MS.observerCitationsWait;
    const pollIntervalMs = 200;
    let elapsed = 0;

    const tryExtract = () => {
      const liveSession = window.Verity.runtime.getSessionById(session.responseId);
      if (
        !liveSession ||
        liveSession.state === VERITY_SHARED.SESSION_STATES.DISPOSED
      ) {
        return;
      }

      const interceptedSession = window.Verity.extractor.peekPendingInterceptedSession();
      if (interceptedSession || elapsed >= maxWaitMs) {
        const claimedSession = interceptedSession
          ? window.Verity.extractor.claimPendingInterceptedSession()
          : null;
        const sources = window.Verity.extractor.extractSources(
          responseEl,
          claimedSession
        );

        if (sources.length >= VERITY_CONFIG.minUrlsToShowButton) {
          window.Verity.runtime.setState(
            liveSession,
            VERITY_SHARED.SESSION_STATES.READY_TO_MOUNT
          );

          if (VERITY_CONFIG.autoCheck) {
            window.Verity.ui.autoCheck(
              responseEl,
              sources,
              this._platformConfig,
              liveSession
            );
          } else {
            window.Verity.ui.injectButton(
              responseEl,
              sources,
              this._platformConfig,
              liveSession
            );
          }
          return;
        }

        window.Verity.runtime.reset(liveSession);
        return;
      }

      elapsed += pollIntervalMs;
      setTimeout(tryExtract, pollIntervalMs);
    };

    tryExtract();
  },
};

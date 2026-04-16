window.Verity = window.Verity || {};

var VERITY_SHARED = globalThis.VerityShared;

var VERITY_UI_CONTEXT_DEAD_MSG = "Verity lost connection — please refresh the page to reconnect.";

function getBridge() {
  return window.Verity.reactUiBridge || null;
}

async function ensureRenderableSession(responseElOrSession) {
  const session =
    responseElOrSession && responseElOrSession.responseId
      ? responseElOrSession
      : window.Verity.runtime.getOrCreateSession(responseElOrSession);
  if (!session) return null;

  const mountNode = await window.Verity.runtime.ensureMount(session);
  if (!mountNode) {
    window.Verity.runtime.reset(session);
    return null;
  }

  return {
    session,
    mountNode,
  };
}

function renderBridgeState(responseElOrSession, state) {
  const bridge = getBridge();
  if (!bridge?.render) {
    console.error("Verity: React UI bridge is unavailable");
    return;
  }

  return ensureRenderableSession(responseElOrSession).then((result) => {
    if (!result) return;
    bridge.render({
      sessionId: result.session.responseId,
      mountNode: result.mountNode,
      ...state,
    });
  });
}

window.Verity.ui = {
  async injectButton(responseEl, sources, platformConfig, session) {
    const activeSession =
      session || window.Verity.runtime.getOrCreateSession(responseEl);
    if (!activeSession) return;

    window.Verity.runtime.setState(
      activeSession,
      VERITY_SHARED.SESSION_STATES.READY_TO_MOUNT
    );

    await renderBridgeState(activeSession, {
      mode: "idle",
      sourceCount: sources.length,
      onCheck: () => {
        this._handleCheck(responseEl, sources, platformConfig, activeSession);
      },
    });
  },

  async autoCheck(responseEl, sources, platformConfig, session) {
    const activeSession =
      session || window.Verity.runtime.getOrCreateSession(responseEl);
    if (!activeSession) return;
    await this._handleCheck(responseEl, sources, platformConfig, activeSession);
  },

  async _handleCheck(responseEl, sources, platformConfig, session) {
    const activeSession =
      session || window.Verity.runtime.getOrCreateSession(responseEl);
    if (!activeSession) return;

    window.Verity.runtime.setState(
      activeSession,
      VERITY_SHARED.SESSION_STATES.CHECKING
    );

    const prompt = window.Verity.extractor.extractPrompt(platformConfig.selectors);
    const fullResponse = window.Verity.extractor.extractResponse(
      responseEl,
      VERITY_CONFIG.maxBodyTextChars
    );

    const payload = {
      sources,
      original_prompt: prompt || fullResponse.slice(0, 200),
      full_ai_response: fullResponse,
    };

    const cacheKey = window.Verity.api.computeCacheKey(payload);
    window.Verity.runtime.setRequestKey(activeSession, cacheKey);

    await renderBridgeState(activeSession, {
      mode: "loading",
      sourceCount: sources.length,
      progressText:
        `Preparing to analyze ${sources.length} source${sources.length === 1 ? "" : "s"}...`,
    });

    const unsubscribeProgress = window.Verity.api.onProgress(cacheKey, (message) => {
      renderBridgeState(activeSession, {
        mode: "loading",
        sourceCount: sources.length,
        progressText: `Scraping ${message.domain} [${message.completed}/${message.total}]`,
      });
    });

    try {
      const data = await window.Verity.api.fetchWithDedup(cacheKey, payload);
      unsubscribeProgress();
      window.Verity.api.clearProgress(cacheKey);

      const liveSession = window.Verity.runtime.getSessionById(activeSession.responseId);
      if (
        !liveSession ||
        liveSession.state === VERITY_SHARED.SESSION_STATES.DISPOSED
      ) {
        return;
      }

      const normalizedSources = window.Verity.renderModel.normalizeResult(data);
      window.Verity.runtime.setState(
        liveSession,
        VERITY_SHARED.SESSION_STATES.RENDERED
      );

      await renderBridgeState(liveSession, {
        mode: "results",
        resultKey: cacheKey,
        sources: normalizedSources,
      });
    } catch (error) {
      unsubscribeProgress();
      window.Verity.api.clearProgress(cacheKey);

      const liveSession = window.Verity.runtime.getSessionById(activeSession.responseId);
      if (!liveSession) return;

      window.Verity.runtime.setState(
        liveSession,
        VERITY_SHARED.SESSION_STATES.READY_TO_MOUNT
      );

      const isContextDead =
        error.message?.includes("context invalidated") ||
        error.message?.includes("Extension context") ||
        error.message?.includes("Extension was reloaded");

      await renderBridgeState(liveSession, {
        mode: "error",
        errorMessage: isContextDead
          ? VERITY_UI_CONTEXT_DEAD_MSG
          : `Verity couldn't check sources: ${error.message}`,
        errorActionLabel: isContextDead ? "Refresh page" : "Retry",
        onRetry: () => {
          if (isContextDead) {
            window.location.reload();
            return;
          }
          this._handleCheck(responseEl, sources, platformConfig, liveSession);
        },
      });
    }
  },
};

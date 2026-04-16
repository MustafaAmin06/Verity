window.Verity = window.Verity || {};

var VERITY_SHARED = globalThis.VerityShared;
const responseSessions = new Map();
const responseIdsByContainer = new WeakMap();
const responseIdsByMessageId = new Map();
const processedResponseKeys = new Set();
let fallbackResponseCounter = 0;

function resolveContainer(responseEl) {
  return responseEl?.closest?.("[data-message-id]") || responseEl?.parentElement || null;
}

function getResponseId(container) {
  if (!container) return null;

  const messageId = container.getAttribute?.("data-message-id");
  if (messageId) {
    if (!responseIdsByMessageId.has(messageId)) {
      responseIdsByMessageId.set(messageId, `message:${messageId}`);
    }
    const responseId = responseIdsByMessageId.get(messageId);
    responseIdsByContainer.set(container, responseId);
    return responseId;
  }

  const existingId = responseIdsByContainer.get(container);
  if (existingId) return existingId;

  fallbackResponseCounter += 1;
  const responseId = `response:${fallbackResponseCounter}`;
  responseIdsByContainer.set(container, responseId);
  return responseId;
}

function buildProcessedResponseKey(session, signature) {
  const container = session?.container || null;
  const messageId = container?.getAttribute?.("data-message-id");
  const normalizedSignature = String(signature || "").trim();
  if (messageId && normalizedSignature) {
    return `message:${messageId}:${normalizedSignature}`;
  }
  if (messageId) {
    return `message:${messageId}`;
  }
  if (normalizedSignature) {
    return `signature:${normalizedSignature}`;
  }
  return session?.responseId || null;
}

function getOrCreateSession(responseEl) {
  const container = resolveContainer(responseEl);
  const responseId = getResponseId(container);
  if (!responseId) return null;

  if (!responseSessions.has(responseId)) {
    responseSessions.set(responseId, {
      responseId,
      container,
      responseEl,
      host: null,
      shadowRoot: null,
      mountNode: null,
      state: VERITY_SHARED.SESSION_STATES.IDLE,
      requestKey: null,
      processedKey: null,
    });
  }

  const session = responseSessions.get(responseId);
  session.container = container;
  session.responseEl = responseEl;

  if (session.state === VERITY_SHARED.SESSION_STATES.DISPOSED) {
    session.state = VERITY_SHARED.SESSION_STATES.IDLE;
  }

  return session;
}

async function ensureMount(session) {
  if (!session?.container || !session.container.isConnected) {
    return null;
  }

  if (session.host?.isConnected && session.mountNode?.isConnected) {
    return session.mountNode;
  }

  const host = document.createElement("div");
  host.setAttribute("data-verity-host", "");
  host.setAttribute("data-verity-response-id", session.responseId);
  host.className = "verity-mount-host";

  const mountNode = document.createElement("div");
  mountNode.className = VERITY_SHARED.SHADOW_ROOT_CLASS;

  host.appendChild(mountNode);
  session.container.after(host);

  session.host = host;
  session.shadowRoot = null;
  session.mountNode = mountNode;

  return mountNode;
}

function disposeMount(session) {
  if (!session) return;

  if (window.Verity.reactUiBridge?.dispose) {
    window.Verity.reactUiBridge.dispose(session.responseId);
  }

  if (session.host?.isConnected) {
    session.host.remove();
  }

  session.host = null;
  session.shadowRoot = null;
  session.mountNode = null;
  session.requestKey = null;
}

window.Verity.runtime = {
  getContainer: resolveContainer,

  getSessionForResponse(responseEl) {
    const session = getOrCreateSession(responseEl);
    return session ? { ...session } : null;
  },

  getOrCreateSession(responseEl) {
    return getOrCreateSession(responseEl);
  },

  getSessionById(responseId) {
    return responseSessions.get(responseId) || null;
  },

  getSessionState(responseEl) {
    const session = getOrCreateSession(responseEl);
    return session ? session.state : null;
  },

  canStartProcessing(responseEl, signature) {
    const session = getOrCreateSession(responseEl);
    if (!session) return false;

    const canEnter = [
      VERITY_SHARED.SESSION_STATES.IDLE,
      VERITY_SHARED.SESSION_STATES.DISPOSED,
    ].includes(session.state);
    if (!canEnter) return false;

    const processedKey = buildProcessedResponseKey(session, signature);
    return !processedKey || !processedResponseKeys.has(processedKey);
  },

  setState(responseElOrSession, state) {
    const session =
      responseElOrSession && responseElOrSession.responseId
        ? responseElOrSession
        : getOrCreateSession(responseElOrSession);
    if (!session) return null;
    session.state = state;
    return session;
  },

  setRequestKey(responseElOrSession, requestKey) {
    const session =
      responseElOrSession && responseElOrSession.responseId
        ? responseElOrSession
        : getOrCreateSession(responseElOrSession);
    if (!session) return null;
    session.requestKey = requestKey || null;
    return session;
  },

  markProcessed(responseElOrSession, signature) {
    const session =
      responseElOrSession && responseElOrSession.responseId
        ? responseElOrSession
        : getOrCreateSession(responseElOrSession);
    if (!session) return null;

    const processedKey = buildProcessedResponseKey(session, signature);
    if (!processedKey) return null;

    processedResponseKeys.add(processedKey);
    session.processedKey = processedKey;
    return processedKey;
  },

  async ensureMount(responseElOrSession) {
    const session =
      responseElOrSession && responseElOrSession.responseId
        ? responseElOrSession
        : getOrCreateSession(responseElOrSession);
    if (!session) return null;
    return ensureMount(session);
  },

  reset(responseElOrSession) {
    const session =
      responseElOrSession && responseElOrSession.responseId
        ? responseElOrSession
        : getOrCreateSession(responseElOrSession);
    if (!session) return;

    disposeMount(session);
    session.state = VERITY_SHARED.SESSION_STATES.IDLE;
  },

  dispose(responseElOrSession) {
    const session =
      responseElOrSession && responseElOrSession.responseId
        ? responseElOrSession
        : getOrCreateSession(responseElOrSession);
    if (!session) return;

    disposeMount(session);
    session.state = VERITY_SHARED.SESSION_STATES.DISPOSED;
  },

  disposeAll() {
    if (window.Verity.reactUiBridge?.disposeAll) {
      window.Verity.reactUiBridge.disposeAll();
    }

    for (const session of responseSessions.values()) {
      disposeMount(session);
      session.state = VERITY_SHARED.SESSION_STATES.DISPOSED;
    }

    responseSessions.clear();
    responseIdsByMessageId.clear();
    processedResponseKeys.clear();
    fallbackResponseCounter = 0;
    window.Verity.api?.clearProgress?.();
  },
};

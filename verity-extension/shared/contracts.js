(function (global) {
  "use strict";

  if (typeof global.process === "undefined") {
    global.process = { env: { NODE_ENV: "production" } };
  } else if (!global.process.env) {
    global.process.env = { NODE_ENV: "production" };
  } else if (!global.process.env.NODE_ENV) {
    global.process.env.NODE_ENV = "production";
  }

  const PRODUCTION_EXTRACTOR_URL =
    "https://verity-api.thankfulsmoke-1985157b.eastus.azurecontainerapps.io";
  const LOCAL_EXTRACTOR_URL = "http://localhost:8001";
  const LOCAL_LOOPBACK_URL = "http://127.0.0.1:8001";
  const LEGACY_RAILWAY_URL = "https://verity-production-e8f2.up.railway.app";

  const MESSAGE_TYPES = Object.freeze({
    EXTRACT_SOURCES: "EXTRACT_SOURCES",
    SCRAPE_PROGRESS: "SCRAPE_PROGRESS",
  });

  const CUSTOM_EVENTS = Object.freeze({
    GENERATION_START: "verity-generation-start",
    CITATIONS: "verity-citations",
  });

  const BACKEND_ENDPOINTS = Object.freeze({
    HEALTH: "/health",
    EXTRACT_STREAM: "/extract-stream",
  });

  const STORAGE_KEYS = Object.freeze([
    "enabled",
    "autoCheck",
    "devMode",
    "extractorUrl",
    "apiKey",
    "minUrlsToShowButton",
  ]);

  const DEFAULT_SETTINGS = Object.freeze({
    enabled: true,
    autoCheck: false,
    devMode: false,
    extractorUrl: PRODUCTION_EXTRACTOR_URL,
    apiKey: "",
    minUrlsToShowButton: 1,
  });

  const CONTENT_DEFAULTS = Object.freeze({
    maxBodyTextChars: 8000,
    minContextChars: 30,
    maxContextChars: 400,
    minUrlsToShowButton: DEFAULT_SETTINGS.minUrlsToShowButton,
    previewCardSelectors: [
      '[data-testid*="link-preview"]',
      '[data-testid*="preview"]',
      '[class*="LinkPreview"]',
      '[class*="link-preview"]',
      '[class*="linkPreview"]',
    ],
    previewSearchTimeoutMs: 800,
    stylesheetPath: "styles/cards.css",
  });

  const REQUEST_TIMEOUTS_MS = Object.freeze({
    popupHealth: 3000,
    backendExtract: 120000,
    observerCitationsWait: 1500,
    observerResponseSettled: 1200,
  });

  const SESSION_STATES = Object.freeze({
    IDLE: "idle",
    WAITING_FOR_CITATIONS: "waiting_for_citations",
    READY_TO_MOUNT: "ready_to_mount",
    CHECKING: "checking",
    RENDERED: "rendered",
    DISPOSED: "disposed",
  });

  const SHADOW_ROOT_CLASS = "verity-shadow-root";

  function normalizeUrl(value) {
    return String(value || "").trim().replace(/\/+$/, "");
  }

  function isLocalDevUrl(url) {
    const normalized = normalizeUrl(url);
    return (
      normalized === LOCAL_EXTRACTOR_URL ||
      normalized === LOCAL_LOOPBACK_URL ||
      normalized.startsWith("http://localhost") ||
      normalized.startsWith("http://127.0.0.1")
    );
  }

  function clampMinUrlsToShowButton(value) {
    return Math.min(
      10,
      Math.max(
        1,
        Number.parseInt(value, 10) || DEFAULT_SETTINGS.minUrlsToShowButton
      )
    );
  }

  function pickKnownSettings(source) {
    const picked = {};
    for (const key of STORAGE_KEYS) {
      if (Object.prototype.hasOwnProperty.call(source || {}, key)) {
        picked[key] = source[key];
      }
    }
    return picked;
  }

  function normalizeSettings(settings) {
    const next = {
      ...DEFAULT_SETTINGS,
      ...pickKnownSettings(settings),
    };

    next.devMode = Boolean(
      settings && settings.devMode !== undefined
        ? settings.devMode
        : settings && settings.advancedSettingsVisible
    );
    next.extractorUrl =
      normalizeUrl(next.extractorUrl) || PRODUCTION_EXTRACTOR_URL;
    next.apiKey = String(next.apiKey || "");
    next.minUrlsToShowButton = clampMinUrlsToShowButton(
      next.minUrlsToShowButton
    );

    if (!next.devMode && next.extractorUrl === LEGACY_RAILWAY_URL) {
      next.extractorUrl = PRODUCTION_EXTRACTOR_URL;
    }

    return next;
  }

  function buildContentConfig(settings) {
    return {
      ...CONTENT_DEFAULTS,
      ...normalizeSettings(settings),
    };
  }

  function getBackendPresentation(url) {
    const normalized = normalizeUrl(url);
    const isLocal = isLocalDevUrl(normalized);
    return {
      url: normalized,
      isLocal,
      title: isLocal ? "Local backend" : "Production API",
      summaryLabel: isLocal
        ? "Developer override active"
        : "Production backend active",
    };
  }

  function createBackendUrl(baseUrl, endpointPath) {
    const normalizedBase = normalizeUrl(baseUrl);
    return `${normalizedBase}${endpointPath}`;
  }

  function createRequestId(prefix) {
    const label = prefix || "request";
    if (global.crypto && typeof global.crypto.randomUUID === "function") {
      return `${label}:${global.crypto.randomUUID()}`;
    }
    return `${label}:${Date.now().toString(36)}:${Math.random()
      .toString(36)
      .slice(2, 8)}`;
  }

  global.VerityShared = Object.freeze({
    PRODUCTION_EXTRACTOR_URL,
    LOCAL_EXTRACTOR_URL,
    LOCAL_LOOPBACK_URL,
    LEGACY_RAILWAY_URL,
    MESSAGE_TYPES,
    CUSTOM_EVENTS,
    BACKEND_ENDPOINTS,
    STORAGE_KEYS,
    DEFAULT_SETTINGS,
    CONTENT_DEFAULTS,
    REQUEST_TIMEOUTS_MS,
    SESSION_STATES,
    SHADOW_ROOT_CLASS,
    normalizeUrl,
    normalizeSettings,
    buildContentConfig,
    isLocalDevUrl,
    clampMinUrlsToShowButton,
    getBackendPresentation,
    createBackendUrl,
    createRequestId,
  });
})(typeof globalThis !== "undefined" ? globalThis : this);

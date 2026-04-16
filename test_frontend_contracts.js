const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function loadFrontendContext() {
  const sharedCode = fs.readFileSync(
    "/home/mustafaamin/Verity/verity-extension/shared/contracts.js",
    "utf8"
  );
  const renderModelCode = fs.readFileSync(
    "/home/mustafaamin/Verity/verity-extension/content/render-model.js",
    "utf8"
  );

  const context = {
    console,
    window: {},
    crypto: {
      randomUUID() {
        return "test-uuid";
      },
    },
  };
  context.globalThis = context;

  vm.createContext(context);
  vm.runInContext(sharedCode, context);
  vm.runInContext(renderModelCode, context);

  return {
    shared: context.VerityShared,
    renderModel: context.window.Verity.renderModel,
  };
}

test("content scripts evaluate sequentially in one context without top-level collisions", () => {
  const files = [
    "/home/mustafaamin/Verity/verity-extension/shared/contracts.js",
    "/home/mustafaamin/Verity/verity-extension/content/settings.js",
    "/home/mustafaamin/Verity/verity-extension/content/runtime.js",
    "/home/mustafaamin/Verity/verity-extension/content/api.js",
    "/home/mustafaamin/Verity/verity-extension/content/extractor.js",
    "/home/mustafaamin/Verity/verity-extension/content/render-model.js",
    "/home/mustafaamin/Verity/verity-extension/content/ui.js",
    "/home/mustafaamin/Verity/verity-extension/content/observer.js",
  ];

  const noop = () => {};
  const context = {
    URL,
    console,
    window: {},
    setTimeout,
    clearTimeout,
    fetch: async () => ({
      ok: true,
      text: async () => "",
    }),
    chrome: {
      runtime: {
        id: "test-extension",
        getURL(path) {
          return `chrome-extension://test/${path}`;
        },
        onMessage: {
          addListener: noop,
        },
        sendMessage: noop,
      },
      storage: {
        local: {
          get(defaults, callback) {
            callback(defaults);
          },
          set(_value, callback) {
            if (callback) callback();
          },
        },
        onChanged: {
          addListener: noop,
        },
      },
    },
    document: {
      body: {},
      addEventListener: noop,
      querySelector() {
        return null;
      },
      querySelectorAll() {
        return [];
      },
    },
  };
  context.globalThis = context;
  context.window = context.window || {};
  context.window.location = { hostname: "chatgpt.com" };
  context.window.Verity = {};
  context.VERITY_CONFIG = {
    enabled: false,
    autoCheck: false,
    extractorUrl: "https://example.com",
    maxBodyTextChars: 8000,
    minContextChars: 30,
    maxContextChars: 400,
    minUrlsToShowButton: 1,
    previewCardSelectors: [],
    previewSearchTimeoutMs: 800,
    stylesheetPath: "styles/cards.css",
  };

  vm.createContext(context);

  for (const file of files) {
    const code = fs.readFileSync(file, "utf8");
    assert.doesNotThrow(() => vm.runInContext(code, context), file);
  }
});

test("normalizeSettings migrates legacy backend values and canonicalizes dev mode", () => {
  const { shared } = loadFrontendContext();

  const normalized = shared.normalizeSettings({
    extractorUrl: shared.LEGACY_RAILWAY_URL,
    advancedSettingsVisible: true,
    minUrlsToShowButton: 42,
  });

  assert.equal(normalized.enabled, true);
  assert.equal(normalized.autoCheck, false);
  assert.equal(normalized.devMode, true);
  assert.equal(normalized.extractorUrl, shared.LEGACY_RAILWAY_URL);
  assert.equal(normalized.apiKey, "");
  assert.equal(normalized.minUrlsToShowButton, 10);

  const consumerNormalized = shared.normalizeSettings({
    extractorUrl: shared.LEGACY_RAILWAY_URL,
    devMode: false,
  });

  assert.equal(
    consumerNormalized.extractorUrl,
    shared.PRODUCTION_EXTRACTOR_URL
  );
});

test("buildContentConfig merges content defaults with storage-backed settings", () => {
  const { shared } = loadFrontendContext();

  const config = shared.buildContentConfig({
    enabled: false,
    autoCheck: true,
    extractorUrl: "http://localhost:8001/",
  });

  assert.equal(config.enabled, false);
  assert.equal(config.autoCheck, true);
  assert.equal(config.extractorUrl, "http://localhost:8001");
  assert.equal(config.maxBodyTextChars, 8000);
  assert.ok(Array.isArray(config.previewCardSelectors));
  assert.equal(config.stylesheetPath, "styles/cards.css");
});

test("render model produces explicit card models for rubric and legacy sources", () => {
  const { renderModel } = loadFrontendContext();

  const sources = renderModel.normalizeResult({
    sources: [
      {
        url: "https://example.com/legacy",
        title: "Legacy Source",
        domain: "example.com",
        composite_score: 45,
        description: "Legacy description",
        signals: {
          domain_tier: "independent_blog",
        },
      },
      {
        url: "https://example.com/rubric",
        title: "Rubric Source",
        domain: "example.com",
        overall_score: 88,
        reason: "Directly supports the claim.",
        implication: "Safe to rely on for this statement.",
        signals: {
          domain_tier: "academic_journal",
          source_credibility_score: 92,
          claim_support_score: 86,
          decision_confidence_score: 84,
          support_class: "direct_support",
          evidence_specificity: "direct",
          decision_confidence_level: "high",
        },
      },
    ],
  });

  assert.equal(sources.length, 2);
  assert.equal(sources[0].title, "Rubric Source");
  assert.equal(sources[0].scoreDisplay, "88");
  assert.equal(sources[0].scoreSuffix, "/100");
  assert.equal(sources[0].verdictTone, "positive");
  assert.equal(sources[0].sourceMeta, "example.com");
  assert.equal(Array.isArray(sources[0].rubricAxes), true);
  assert.equal(sources[0].rubricAxes.length, 3);
  assert.equal(
    sources[0].verificationFacts.some((fact) => fact.label === "Support class"),
    true
  );
  assert.equal(
    sources[0].metadataFacts.some((fact) => fact.label === "Source tier"),
    true
  );
  assert.equal(sources[1].verdictTone, "caution");
  assert.equal(Array.isArray(sources[1].verificationFacts), true);
  assert.equal(sources[1].verificationFacts.length > 0, true);
});

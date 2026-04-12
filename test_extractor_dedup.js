const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function loadExtractor() {
  const code = fs.readFileSync(
    "/home/mustafaamin/Verity/verity-extension/content/extractor.js",
    "utf8"
  );

  const context = {
    URL,
    console,
    setTimeout,
    clearTimeout,
    window: {},
    document: {
      addEventListener() {},
    },
    VERITY_CONFIG: {
      minContextChars: 30,
      maxContextChars: 400,
      previewCardSelectors: [],
    },
  };

  vm.createContext(context);
  vm.runInContext(code, context);
  return context.window.Verity.extractor;
}

test("canonicalizeUrl strips tracking parameters and normalizes host/path", () => {
  const extractor = loadExtractor();

  assert.equal(
    extractor._canonicalizeUrl("https://www.example.com/article/?utm_source=chatgpt.com#section"),
    "https://example.com/article"
  );
});

test("canonicalizeUrl preserves meaningful query parameters", () => {
  const extractor = loadExtractor();

  assert.equal(
    extractor._canonicalizeUrl("https://www.example.com/article/?id=123&lang=en"),
    "https://example.com/article?id=123&lang=en"
  );
});

test("deduplicate collapses canonical-equal URLs and keeps the better label/context", () => {
  const extractor = loadExtractor();

  const deduped = extractor._deduplicate([
    extractor._buildSource(
      "https://www.example.com/article/?utm_source=chatgpt.com",
      "example.com",
      "Source citation from AI response",
      { contextQuality: 0 }
    ),
    extractor._buildSource(
      "https://example.com/article",
      "Example Article",
      "This source explains the key result in the model answer.",
      { contextQuality: 1 }
    ),
  ]);

  assert.equal(deduped.length, 1);
  assert.equal(
    JSON.stringify(deduped[0]),
    JSON.stringify({
      url: "https://example.com/article",
      label: "Example Article",
      context: "This source explains the key result in the model answer.",
    })
  );
});

test("deduplicate collapses reordered non-tracking query params but keeps real variants", () => {
  const extractor = loadExtractor();

  const deduped = extractor._deduplicate([
    extractor._buildSource(
      "https://example.com/article?lang=en&id=123",
      "Example",
      "First"
    ),
    extractor._buildSource(
      "https://www.example.com/article?id=123&lang=en",
      "Example",
      "Second"
    ),
    extractor._buildSource(
      "https://example.com/article?id=124&lang=en",
      "Example",
      "Third"
    ),
  ]);

  assert.equal(deduped.length, 2);
  assert.equal(deduped[0].url, "https://example.com/article?id=123&lang=en");
  assert.equal(deduped[1].url, "https://example.com/article?id=124&lang=en");
});

test("extractFromText does not re-add a canonical duplicate already found from anchors", () => {
  const extractor = loadExtractor();

  const existingAnchors = [
    extractor._buildSource(
      "https://example.com/article",
      "Example Article",
      "Anchor context"
    ),
  ];

  const element = {
    innerText: "Supporting source: https://www.example.com/article/?utm_source=chatgpt.com",
  };

  const sources = extractor._extractFromText(element, existingAnchors);
  assert.equal(sources.length, 0);
});

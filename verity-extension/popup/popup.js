const VERITY_SHARED = globalThis.VerityShared;
const manifest = chrome.runtime.getManifest();

const els = Object.fromEntries(
  VERITY_SHARED.STORAGE_KEYS.map((id) => [id, document.getElementById(id)])
);

const advancedPanel = document.getElementById("advancedPanel");
const backendTitle = document.getElementById("backendTitle");
const backendUrlLabel = document.getElementById("backendUrlLabel");
const projectLink = document.getElementById("projectLink");
const resetDefaults = document.getElementById("resetDefaults");
const savedIndicator = document.getElementById("savedIndicator");
const serverStatus = document.getElementById("serverStatus");
const serverSummary = document.getElementById("serverSummary");
const useProductionPreset = document.getElementById("useProductionPreset");
const useLocalPreset = document.getElementById("useLocalPreset");
const versionLabel = document.getElementById("versionLabel");

let saveTimer = null;
let savedTimer = null;

if (versionLabel) {
  versionLabel.textContent = `v${manifest.version}`;
}

if (projectLink && manifest.homepage_url) {
  projectLink.href = manifest.homepage_url;
}

function populateForm(settings) {
  for (const key of VERITY_SHARED.STORAGE_KEYS) {
    const el = els[key];
    if (!el) continue;
    if (el.type === "checkbox") {
      el.checked = Boolean(settings[key]);
    } else {
      el.value = settings[key];
    }
  }

  setAdvancedVisibility(Boolean(settings.devMode));
  updateBackendCopy(settings.extractorUrl);
}

function writeSettings(partial) {
  chrome.storage.local.get(VERITY_SHARED.DEFAULT_SETTINGS, (stored) => {
    const next = VERITY_SHARED.normalizeSettings({
      ...stored,
      ...partial,
    });

    chrome.storage.local.set(next, () => {
      populateForm(next);
      flashSaved();
      scheduleHealthCheck(next.extractorUrl);
    });
  });
}

function updateBackendCopy(url) {
  const presentation = VERITY_SHARED.getBackendPresentation(url);
  backendTitle.textContent = presentation.title;
  backendUrlLabel.textContent = presentation.summaryLabel;
}

function setAdvancedVisibility(visible) {
  if (!advancedPanel) return;
  advancedPanel.hidden = !visible;
}

function flashSaved() {
  savedIndicator.classList.add("verity-saved--visible");
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => {
    savedIndicator.classList.remove("verity-saved--visible");
  }, 1200);
}

function collectFormValues() {
  return {
    enabled: els.enabled.checked,
    autoCheck: els.autoCheck.checked,
    extractorUrl: VERITY_SHARED.normalizeUrl(els.extractorUrl.value),
    apiKey: String(els.apiKey.value || ""),
    minUrlsToShowButton: VERITY_SHARED.clampMinUrlsToShowButton(
      els.minUrlsToShowButton.value
    ),
    devMode: els.devMode.checked,
  };
}

function scheduleHealthCheck(url) {
  if (document.activeElement === els.extractorUrl) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => checkServer(url), 500);
    return;
  }
  checkServer(url);
}

function save() {
  writeSettings(collectFormValues());
}

async function checkServer(url) {
  const normalized = VERITY_SHARED.normalizeUrl(url);
  updateBackendCopy(normalized);
  serverStatus.className = "verity-status-dot verity-status-checking";
  serverStatus.title = "Checking";
  serverSummary.textContent = `Checking ${normalized || "backend"}…`;

  try {
    const response = await fetch(
      VERITY_SHARED.createBackendUrl(
        normalized,
        VERITY_SHARED.BACKEND_ENDPOINTS.HEALTH
      ),
      {
        method: "GET",
        signal: AbortSignal.timeout(
          VERITY_SHARED.REQUEST_TIMEOUTS_MS.popupHealth
        ),
      }
    );

    if (!response.ok) {
      serverStatus.className = "verity-status-dot verity-status-dead";
      serverStatus.title = `HTTP ${response.status}`;
      serverSummary.textContent =
        `Backend reachable, but /health returned HTTP ${response.status}.`;
      return;
    }

    const payload = await response.json().catch(() => ({}));
    const llmLabel = payload.llm_enabled
      ? payload.llm_model || "LLM ready"
      : "Extraction only";

    serverStatus.className = "verity-status-dot verity-status-live";
    serverStatus.title = "Connected";
    serverSummary.textContent = `Connected. ${llmLabel}.`;
  } catch {
    serverStatus.className = "verity-status-dot verity-status-dead";
    serverStatus.title = "Not reachable";
    serverSummary.textContent = "Backend is not reachable from the extension.";
  }
}

for (const key of VERITY_SHARED.STORAGE_KEYS) {
  const el = els[key];
  if (!el) continue;
  el.addEventListener("change", save);
}

resetDefaults?.addEventListener("click", () => {
  writeSettings(VERITY_SHARED.DEFAULT_SETTINGS);
});

useProductionPreset?.addEventListener("click", () => {
  writeSettings({
    extractorUrl: VERITY_SHARED.PRODUCTION_EXTRACTOR_URL,
    apiKey: "",
  });
});

useLocalPreset?.addEventListener("click", () => {
  writeSettings({
    extractorUrl: VERITY_SHARED.LOCAL_EXTRACTOR_URL,
  });
});

chrome.storage.local.get(
  {
    ...VERITY_SHARED.DEFAULT_SETTINGS,
    advancedSettingsVisible: false,
  },
  (stored) => {
    const normalized = VERITY_SHARED.normalizeSettings(stored);
    populateForm(normalized);

    const needsRewrite =
      stored.advancedSettingsVisible !== undefined ||
      Boolean(
        stored.devMode !== undefined
          ? stored.devMode
          : stored.advancedSettingsVisible
      ) !== normalized.devMode ||
      VERITY_SHARED.normalizeUrl(stored.extractorUrl) !== normalized.extractorUrl ||
      String(stored.apiKey || "") !== normalized.apiKey ||
      Boolean(stored.enabled) !== normalized.enabled ||
      Boolean(stored.autoCheck) !== normalized.autoCheck ||
      VERITY_SHARED.clampMinUrlsToShowButton(
        stored.minUrlsToShowButton
      ) !== normalized.minUrlsToShowButton;

    if (needsRewrite) {
      chrome.storage.local.set(normalized);
    }

    checkServer(normalized.extractorUrl);
  }
);

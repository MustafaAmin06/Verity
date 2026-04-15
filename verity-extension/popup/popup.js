const AZURE_EXTRACTOR_URL =
  "https://verity-api.thankfulsmoke-1985157b.eastus.azurecontainerapps.io";
const LOCAL_EXTRACTOR_URL = "http://localhost:8001";
const LEGACY_RAILWAY_URL = "https://verity-production-e8f2.up.railway.app";
const ADVANCED_CLICK_TARGET = 5;
const ADVANCED_CLICK_WINDOW_MS = 1500;

const DEFAULTS = {
  enabled: true,
  autoCheck: false,
  extractorUrl: LOCAL_EXTRACTOR_URL,
  apiKey: "",
  minUrlsToShowButton: 1,
  advancedSettingsVisible: true,
};

const FIELD_KEYS = ["enabled", "autoCheck", "extractorUrl", "apiKey", "minUrlsToShowButton"];
const manifest = chrome.runtime.getManifest();

const els = Object.fromEntries(
  FIELD_KEYS.map((id) => [id, document.getElementById(id)])
);
const advancedPanel = document.getElementById("advancedPanel");
const backendTitle = document.getElementById("backendTitle");
const backendUrlLabel = document.getElementById("backendUrlLabel");
const projectLink = document.getElementById("projectLink");
const resetDefaults = document.getElementById("resetDefaults");
const savedIndicator = document.getElementById("savedIndicator");
const serverStatus = document.getElementById("serverStatus");
const serverSummary = document.getElementById("serverSummary");
const useAzurePreset = document.getElementById("useAzurePreset");
const useLocalPreset = document.getElementById("useLocalPreset");
const versionLabel = document.getElementById("versionLabel");

let saveTimer = null;
let savedTimer = null;
let advancedClickCount = 0;
let advancedClickWindow = null;
let currentSettings = { ...DEFAULTS };

if (versionLabel) {
  versionLabel.textContent = `v${manifest.version}`;
}

if (projectLink && manifest.homepage_url) {
  projectLink.href = manifest.homepage_url;
}

function normalizeUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

function normalizeSettings(settings) {
  const next = { ...DEFAULTS, ...settings };
  next.extractorUrl = normalizeUrl(next.extractorUrl) || LOCAL_EXTRACTOR_URL;
  next.apiKey = String(next.apiKey || "");
  next.minUrlsToShowButton = Math.min(
    10,
    Math.max(1, Number.parseInt(next.minUrlsToShowButton, 10) || DEFAULTS.minUrlsToShowButton)
  );

  if (!next.advancedSettingsVisible && next.extractorUrl === LEGACY_RAILWAY_URL) {
    next.extractorUrl = LOCAL_EXTRACTOR_URL;
  }

  return next;
}

function populateForm(settings) {
  currentSettings = { ...settings };
  for (const key of FIELD_KEYS) {
    const el = els[key];
    if (!el) continue;
    if (el.type === "checkbox") {
      el.checked = Boolean(settings[key]);
    } else {
      el.value = settings[key];
    }
  }
  setAdvancedVisibility(Boolean(settings.advancedSettingsVisible));
  updateBackendCopy(settings.extractorUrl);
}

function persistSettings(partial) {
  chrome.storage.local.get(DEFAULTS, (stored) => {
    const next = normalizeSettings({ ...stored, ...partial });
    chrome.storage.local.set(next, () => {
      populateForm(next);
      flashSaved();
      checkServer(next.extractorUrl);
    });
  });
}

function updateBackendCopy(url) {
  const normalized = normalizeUrl(url);
  const isLocal = normalized.startsWith("http://localhost") || normalized.startsWith("http://127.0.0.1");

  backendTitle.textContent = isLocal ? "Local backend" : "Azure API";
  backendUrlLabel.textContent = isLocal
    ? "Developer override active"
    : "Production backend active";
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
    extractorUrl: normalizeUrl(els.extractorUrl.value),
    apiKey: String(els.apiKey.value || ""),
    minUrlsToShowButton: Math.min(
      10,
      Math.max(1, Number.parseInt(els.minUrlsToShowButton.value, 10) || DEFAULTS.minUrlsToShowButton)
    ),
    advancedSettingsVisible: currentSettings.advancedSettingsVisible,
  };
}

function save() {
  const next = normalizeSettings(collectFormValues());
  chrome.storage.local.set(next, () => {
    populateForm(next);
    flashSaved();
    if (document.activeElement === els.extractorUrl) {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => checkServer(next.extractorUrl), 500);
    } else {
      checkServer(next.extractorUrl);
    }
  });
}

async function checkServer(url) {
  const normalized = normalizeUrl(url);
  updateBackendCopy(normalized);
  serverStatus.className = "verity-status-dot verity-status-checking";
  serverStatus.title = "Checking";
  serverSummary.textContent = `Checking ${normalized || "backend"}…`;

  try {
    const res = await fetch(`${normalized}/health`, {
      method: "GET",
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) {
      serverStatus.className = "verity-status-dot verity-status-dead";
      serverStatus.title = `HTTP ${res.status}`;
      serverSummary.textContent = `Backend reachable, but /health returned HTTP ${res.status}.`;
      return;
    }

    const payload = await res.json().catch(() => ({}));
    const llmLabel = payload.llm_enabled ? payload.llm_model || "LLM ready" : "Extraction only";
    serverStatus.className = "verity-status-dot verity-status-live";
    serverStatus.title = "Connected";
    serverSummary.textContent = `Connected. ${llmLabel}.`;
  } catch {
    serverStatus.className = "verity-status-dot verity-status-dead";
    serverStatus.title = "Not reachable";
    serverSummary.textContent = "Backend is not reachable from the extension.";
  }
}

function toggleAdvancedPanel() {
  const nextVisible = !currentSettings.advancedSettingsVisible;
  persistSettings({ advancedSettingsVisible: nextVisible });
}

function handleVersionClick() {
  advancedClickCount += 1;
  clearTimeout(advancedClickWindow);
  advancedClickWindow = setTimeout(() => {
    advancedClickCount = 0;
  }, ADVANCED_CLICK_WINDOW_MS);

  if (advancedClickCount >= ADVANCED_CLICK_TARGET) {
    advancedClickCount = 0;
    toggleAdvancedPanel();
  }
}

for (const key of FIELD_KEYS) {
  const el = els[key];
  if (!el) continue;
  el.addEventListener("change", save);
}

versionLabel?.addEventListener("click", handleVersionClick);

resetDefaults?.addEventListener("click", () => {
  persistSettings({
    enabled: DEFAULTS.enabled,
    autoCheck: DEFAULTS.autoCheck,
    extractorUrl: LOCAL_EXTRACTOR_URL,
    apiKey: "",
    minUrlsToShowButton: DEFAULTS.minUrlsToShowButton,
    advancedSettingsVisible: true,
  });
});

useAzurePreset?.addEventListener("click", () => {
  persistSettings({ extractorUrl: AZURE_EXTRACTOR_URL, apiKey: "" });
});

useLocalPreset?.addEventListener("click", () => {
  persistSettings({ extractorUrl: LOCAL_EXTRACTOR_URL });
});

chrome.storage.local.get(DEFAULTS, (stored) => {
  const normalized = normalizeSettings(stored);
  populateForm(normalized);

  const changed = JSON.stringify(normalized) !== JSON.stringify({ ...DEFAULTS, ...stored });
  if (changed) {
    chrome.storage.local.set(normalized);
  }

  checkServer(normalized.extractorUrl);
});

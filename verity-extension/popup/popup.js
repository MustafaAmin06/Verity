const DEFAULTS = {
  enabled: true,
  autoCheck: false,
  extractorUrl: "https://YOUR_RAILWAY_URL.up.railway.app",
  hoverDelayMs: 300,
  minUrlsToShowButton: 1,
};

const FIELDS = Object.keys(DEFAULTS);

// ---- DOM refs ----
const els = {};
FIELDS.forEach((id) => (els[id] = document.getElementById(id)));
const savedIndicator = document.getElementById("savedIndicator");
const serverStatus = document.getElementById("serverStatus");
const hoverValue = document.getElementById("hoverDelayMs-value");

// ---- Load settings into form ----
chrome.storage.local.get(DEFAULTS, (settings) => {
  populateForm(settings);
  checkServer(settings.extractorUrl);
});

function populateForm(settings) {
  for (const key of FIELDS) {
    const el = els[key];
    if (!el) continue;
    if (el.type === "checkbox") {
      el.checked = settings[key];
    } else {
      el.value = settings[key];
    }
  }
  updateHoverLabel();
}

function updateRangeFill(el) {
  const pct = ((el.value - el.min) / (el.max - el.min)) * 100;
  el.style.setProperty('--range-pct', pct + '%');
}

function updateHoverLabel() {
  hoverValue.textContent = els.hoverDelayMs.value + "ms";
  updateRangeFill(els.hoverDelayMs);
}

// ---- Auto-save on change ----
for (const key of FIELDS) {
  const el = els[key];
  if (!el) continue;
  el.addEventListener("change", save);
  if (el.type === "range") {
    el.addEventListener("input", () => {
      updateHoverLabel();
      save();
    });
  }
}

let saveTimeout = null;

function save() {
  const settings = {};
  for (const key of FIELDS) {
    const el = els[key];
    if (!el) continue;
    if (el.type === "checkbox") {
      settings[key] = el.checked;
    } else if (el.type === "number" || el.type === "range") {
      settings[key] = parseInt(el.value, 10);
    } else {
      settings[key] = el.value.replace(/\/+$/, "");
    }
  }

  chrome.storage.local.set(settings, () => {
    flashSaved();
  });

  // Re-check server when URL changes
  if (document.activeElement === els.extractorUrl) {
    clearTimeout(saveTimeout);
    saveTimeout = setTimeout(() => checkServer(settings.extractorUrl), 600);
  }
}

// ---- Saved indicator ----
let savedTimer = null;

function flashSaved() {
  savedIndicator.classList.add("verity-saved--visible");
  clearTimeout(savedTimer);
  savedTimer = setTimeout(() => {
    savedIndicator.classList.remove("verity-saved--visible");
  }, 1200);
}

// ---- Server health check ----
async function checkServer(url) {
  serverStatus.className = "verity-status-dot verity-status-checking";
  serverStatus.title = "Checking...";
  try {
    const res = await fetch(`${url.replace(/\/+$/, "")}/health`, {
      method: "GET",
      signal: AbortSignal.timeout(3000),
    });
    if (res.ok) {
      serverStatus.className = "verity-status-dot verity-status-live";
      serverStatus.title = "Connected";
    } else {
      serverStatus.className = "verity-status-dot verity-status-dead";
      serverStatus.title = `HTTP ${res.status}`;
    }
  } catch {
    serverStatus.className = "verity-status-dot verity-status-dead";
    serverStatus.title = "Not reachable";
  }
}

// ---- Reset to defaults ----
document.getElementById("resetDefaults").addEventListener("click", () => {
  chrome.storage.local.set(DEFAULTS, () => {
    populateForm(DEFAULTS);
    flashSaved();
    checkServer(DEFAULTS.extractorUrl);
  });
});

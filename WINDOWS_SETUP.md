# Verity — Windows Setup Guide

Verity has two components: a **Python backend** that scrapes and scores sources, and a **Chrome extension** that injects into ChatGPT. Both need to be running at the same time.

---

## Prerequisites

### 1. Python 3.12+

Download from [python.org/downloads](https://www.python.org/downloads/). During installation:
- Check **"Add Python to PATH"**
- Check **"Install pip"**

Verify:
```
python --version
pip --version
```

### 2. Ollama

Download and install from [ollama.com/download](https://ollama.com/download) (Windows installer).

After installing, open a terminal and pull the model:
```
ollama pull qwen3:1.7b
```

> The backend defaults to `qwen3.5:2b` but any small Qwen/Ollama model works. `qwen3:1.7b` is fast and runs on CPU.
>
> To use a different model, set `OLLAMA_MODEL=your-model-name` in the `.env` file (see Configuration below).

Verify Ollama is running:
```
curl http://localhost:11434/api/tags
```
You should see JSON listing available models.

### 3. Google Chrome or Chromium

Required to load the extension. [Download Chrome](https://www.google.com/chrome/).

---

## Backend Setup

### Clone / download the repo

```
git clone https://github.com/yourrepo/verity.git
cd verity
```

Or download the zip and extract it.

### Create a virtual environment

```
python -m venv venv
venv\Scripts\activate
```

Your prompt should now show `(venv)`.

### Install dependencies

```
pip install beautifulsoup4 fastapi httpx pydantic playwright python-dotenv uvicorn lxml
```

### Install Playwright browser

Playwright is used as a fallback scraper for JavaScript-heavy pages:
```
playwright install chromium
```

> If you don't want Playwright (slower installs, optional), you can disable it by setting `ENABLE_PLAYWRIGHT_FALLBACK=false` in `.env`.

### Configuration (optional)

Create a `.env` file in the project root if you want to override defaults:

```
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:1.7b
REQUEST_TIMEOUT_SECONDS=5
MAX_BODY_TEXT_CHARS=2000
PLAYWRIGHT_TIMEOUT_SECONDS=10
ENABLE_PLAYWRIGHT_FALLBACK=true
EXTRACTOR_PORT=8001
```

All of these have sensible defaults — the `.env` file is optional.

### Start the backend

```
python verity_extractor.py
```

You should see:
```
INFO:     Uvicorn running on http://0.0.0.0:8001
```

Leave this terminal open. The backend must be running for the extension to work.

---

## Chrome Extension Setup

1. Open Chrome and go to `chrome://extensions/`
2. Enable **Developer mode** (toggle in the top-right corner)
3. Click **"Load unpacked"**
4. Navigate to the `verity-extension/` folder inside the project and select it
5. The Verity extension will appear in your extensions list

> No build step is needed — the extension is plain JavaScript loaded directly from the folder.

---

## Using Verity

1. Make sure **Ollama is running** (`ollama serve` or the Ollama desktop app)
2. Make sure the **backend is running** (`python verity_extractor.py`)
3. Go to [chatgpt.com](https://chatgpt.com) and ask a question
4. After ChatGPT responds, a **"Check sources with Verity"** button will appear below the response
5. Click it — Verity will scrape and score each cited source and show verdict cards inline

---

## Services & Ports

| Service | Port | Notes |
|---------|------|-------|
| Verity backend | `8001` | FastAPI/Uvicorn — started by `python verity_extractor.py` |
| Ollama | `11434` | Local LLM — started by Ollama app or `ollama serve` |

Both must be reachable on `localhost`. If you use a firewall or VPN, make sure these ports are open locally.

---

## Troubleshooting

**Extension shows nothing / button doesn't appear**
- Make sure the backend is running on port 8001
- Open `chrome://extensions/`, find Verity, click **"Service worker"** to open DevTools and check for errors

**"Ollama unavailable" or all scores are 0**
- Run `ollama serve` in a terminal, or open the Ollama desktop app
- Check that the model is downloaded: `ollama list`
- If the model name doesn't match, set `OLLAMA_MODEL=<your-model>` in `.env` and restart the backend

**Garbled or empty content from scraped sources**
- This is normal for paywalled sites (NYT, Bloomberg, etc.)
- For sites that require JavaScript, make sure Playwright is installed (`playwright install chromium`)

**`playwright` install fails**
- Run the terminal as Administrator
- Or disable Playwright entirely: set `ENABLE_PLAYWRIGHT_FALLBACK=false` in `.env`

**`pip install` fails with "access denied"**
- Make sure you activated the virtual environment: `venv\Scripts\activate`
- Or run the terminal as Administrator

---

## Full Startup Checklist

```
[ ] Python 3.12+ installed and on PATH
[ ] Ollama installed and running (ollama serve)
[ ] Model downloaded: ollama pull qwen3:1.7b
[ ] Virtual environment created and activated (venv\Scripts\activate)
[ ] pip install beautifulsoup4 fastapi httpx pydantic playwright python-dotenv uvicorn lxml
[ ] playwright install chromium
[ ] python verity_extractor.py  ← keep this terminal open
[ ] Chrome extension loaded from verity-extension/ folder
[ ] Go to chatgpt.com and test
```

from __future__ import annotations

import asyncio

from .models import BrowserRenderResult, PipelineConfig

try:
    from playwright.async_api import async_playwright

    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    async_playwright = None


_COOKIE_CONSENT_BUTTON_TEXTS = [
    "Accept all",
    "Accept All",
    "Accept cookies",
    "Accept Cookies",
    "Accept all cookies",
    "Accept All Cookies",
    "I accept",
    "I agree",
    "Agree",
    "Allow all",
    "Allow All",
    "Allow all cookies",
    "Allow cookies",
    "Got it",
    "OK",
    "Okay",
    "Continue",
    "Consent",
    "Close",
]

_COOKIE_SELECTOR = ", ".join(
    f"button:has-text('{text}'), a:has-text('{text}'), [role='button']:has-text('{text}')"
    for text in _COOKIE_CONSENT_BUTTON_TEXTS
)

_CONSENT_REMOVAL_JS = """
() => {
    const SELECTORS = [
        '[aria-modal="true"]',
        '[role="dialog"]',
        '[role="alertdialog"]',
        '[class*="cookie" i]', '[id*="cookie" i]',
        '[class*="consent" i]', '[id*="consent" i]',
        '[class*="gdpr" i]', '[id*="gdpr" i]',
        '[class*="onetrust" i]', '[id*="onetrust" i]',
        '[class*="optanon" i]', '[id*="optanon" i]',
        '[class*="ot-sdk" i]', '[id*="ot-sdk" i]',
        '[class*="cookiebot" i]', '[id*="cookiebot" i]',
        '[class*="truste" i]', '[id*="truste" i]',
        '[class*="evidon" i]', '[id*="evidon" i]',
        '[class*="quantcast" i]', '[id*="quantcast" i]',
        '[class*="sp_veil" i]', '[id*="sp_message" i]',
        '[class*="privacy-banner" i]', '[class*="privacy-modal" i]',
        '[class*="privacy-center" i]',
        '[class*="cc-banner" i]', '[class*="cc-window" i]',
    ];
    const mainContent = document.querySelector(
        'article, main, [role="main"], #content, .article-body, .post-content'
    );
    let removed = 0;
    for (const sel of SELECTORS) {
        try {
            document.querySelectorAll(sel).forEach(el => {
                if (mainContent && (el === mainContent || el.contains(mainContent))) return;
                const tag = el.tagName.toLowerCase();
                if (tag === 'body' || tag === 'html' || tag === 'head') return;
                if (el.innerText && el.innerText.length > 2000) return;
                el.remove();
                removed++;
            });
        } catch (e) {}
    }
    return removed;
}
"""


class PlaywrightBrowserPool:
    def __init__(self, config: PipelineConfig, logger):
        self.config = config
        self.logger = logger
        self._semaphore = asyncio.Semaphore(config.browser_concurrency)
        self._lock = asyncio.Lock()
        self._playwright = None
        self._browser = None
        self._launch_failed = False
        self._launch_error = None

    @property
    def available(self) -> bool:
        return PLAYWRIGHT_AVAILABLE and not self._launch_failed

    async def _ensure_browser(self):
        if not PLAYWRIGHT_AVAILABLE:
            return None
        if self._launch_failed:
            return None
        if self._browser is not None:
            return self._browser
        async with self._lock:
            if self._browser is not None:
                return self._browser
            if self._launch_failed:
                return None
            try:
                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
            except Exception as exc:
                self._launch_failed = True
                self._launch_error = str(exc)
                self.logger.warning("  PLAYWRIGHT   browser launch failed: %s", exc)
                if self._playwright is not None:
                    try:
                        await self._playwright.stop()
                    except Exception:
                        pass
                    self._playwright = None
                return None
        return self._browser

    async def _dismiss_cookie_popup(self, page) -> bool:
        try:
            button = page.locator(_COOKIE_SELECTOR).first
            if await button.is_visible(timeout=300):
                await button.click(timeout=1000)
                await page.wait_for_timeout(300)
                self.logger.info("  COOKIE_POPUP dismissed")
                return True
        except Exception:
            return False
        return False

    async def _remove_consent_elements(self, page) -> int:
        try:
            removed = await page.evaluate(_CONSENT_REMOVAL_JS)
            if removed:
                self.logger.info("  CONSENT_DOM  removed %d consent element(s) via JS", removed)
            return removed or 0
        except Exception:
            return 0

    async def render(self, url: str) -> BrowserRenderResult:
        if not PLAYWRIGHT_AVAILABLE:
            return BrowserRenderResult(kind="unavailable")

        browser = await self._ensure_browser()
        if browser is None:
            return BrowserRenderResult(kind="unavailable", error=self._launch_error)
        async with self._semaphore:
            context = None
            page = None
            try:
                context = await browser.new_context(user_agent=self.config.browser_user_agent)
                page = await context.new_page()
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=self.config.browser_timeout_seconds * 1000,
                )
                try:
                    await page.wait_for_load_state("networkidle", timeout=2000)
                except Exception:
                    pass

                await self._dismiss_cookie_popup(page)
                try:
                    await page.wait_for_selector(
                        "article, main, [role='main'], #content, .article-body",
                        timeout=self.config.browser_wait_after_load_ms,
                    )
                except Exception:
                    pass

                await self._remove_consent_elements(page)
                html = await page.content()
                http_status = response.status if response else None
                final_url = page.url
                if http_status == 403:
                    return BrowserRenderResult(kind="blocked_403", http_status=http_status, final_url=final_url)
                if http_status and http_status >= 400:
                    return BrowserRenderResult(kind="url_dead", http_status=http_status, final_url=final_url)
                return BrowserRenderResult(
                    kind="ok",
                    html=html,
                    http_status=http_status,
                    final_url=final_url,
                )
            except Exception as exc:
                self.logger.warning("  PLAYWRIGHT   %-40s  %s: %s", url, type(exc).__name__, exc)
                return BrowserRenderResult(kind="scrape_failed", error=str(exc))
            finally:
                if page is not None:
                    try:
                        await page.close()
                    except Exception:
                        pass
                if context is not None:
                    try:
                        await context.close()
                    except Exception:
                        pass

    async def close(self):
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

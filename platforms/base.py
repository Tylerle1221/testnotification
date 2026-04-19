"""
Base platform scraper class.
All platform-specific scrapers inherit from this.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

logger = logging.getLogger(__name__)


class BasePlatformScraper(ABC):
    """Abstract base class for all betting platform scrapers."""

    PLATFORM_NAME = "base"

    def __init__(self, config: dict, headless: bool = True):
        self.config = config
        self.headless = headless
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.url = config.get("url", "")
        # Optional proxy: {"server": "http://host:port", "username": "...", "password": "..."}
        self.proxy: Optional[dict] = config.get("proxy")
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._playwright = None
        self.is_logged_in = False

    async def start(self) -> bool:
        """Start the browser and context."""
        try:
            self._playwright = await async_playwright().start()
            launch_kwargs = dict(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ]
            )
            if self.proxy:
                launch_kwargs["proxy"] = self.proxy
            self.browser = await self._playwright.chromium.launch(**launch_kwargs)
            self.context = await self.browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-GB",
                timezone_id="Europe/London",
            )
            self.page = await self.context.new_page()
            logger.info(f"[{self.PLATFORM_NAME}] Browser started")
            return True
        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Failed to start browser: {e}")
            return False

    async def stop(self):
        """Shutdown the browser cleanly."""
        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self._playwright:
                await self._playwright.stop()
            logger.info(f"[{self.PLATFORM_NAME}] Browser stopped")
        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Error stopping browser: {e}")

    @abstractmethod
    async def login(self) -> bool:
        """Log into the platform. Must be implemented per platform."""
        pass

    async def login_with_retry(self, max_attempts: int = 2) -> bool:
        """Calls login() up to max_attempts times. Returns True on first success."""
        for attempt in range(1, max_attempts + 1):
            try:
                if await self.login():
                    return True
                if attempt < max_attempts:
                    logger.warning(f"[{self.PLATFORM_NAME}] Login attempt {attempt} failed, retrying in 5s...")
                    # Reload the page before retrying
                    await asyncio.sleep(5)
                    if self.page:
                        try:
                            await self.page.reload(wait_until="domcontentloaded", timeout=20000)
                            await asyncio.sleep(3)
                        except Exception:
                            pass
            except Exception as e:
                logger.error(f"[{self.PLATFORM_NAME}] Login attempt {attempt} error: {e}")
                if attempt < max_attempts:
                    await asyncio.sleep(5)
        logger.error(f"[{self.PLATFORM_NAME}] All {max_attempts} login attempts failed")
        return False

    @abstractmethod
    async def search_bets(self, bet: dict) -> list[dict]:
        """
        Search for bets matching the given bet details.
        Returns a list of found bets, each as a dict with keys:
          - event: str
          - sport: str
          - market: str
          - selection: str
          - odds: float
          - url: str
        """
        pass

    async def safe_goto(self, url: str, timeout: int = 30000) -> bool:
        """Navigate to URL with error handling."""
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            return True
        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Navigation failed to {url}: {e}")
            return False

    async def safe_click(self, selector: str, timeout: int = 10000) -> bool:
        """Click element with error handling."""
        try:
            await self.page.click(selector, timeout=timeout)
            return True
        except Exception as e:
            logger.warning(f"[{self.PLATFORM_NAME}] Click failed on '{selector}': {e}")
            return False

    async def safe_fill(self, selector: str, value: str, timeout: int = 10000) -> bool:
        """Fill input with error handling."""
        try:
            await self.page.fill(selector, value, timeout=timeout)
            return True
        except Exception as e:
            logger.warning(f"[{self.PLATFORM_NAME}] Fill failed on '{selector}': {e}")
            return False

    async def wait_for_selector(self, selector: str, timeout: int = 10000) -> bool:
        """Wait for a selector to appear."""
        try:
            await self.page.wait_for_selector(selector, timeout=timeout)
            return True
        except Exception:
            return False

    async def screenshot(self, path: str):
        """Capture screenshot for debugging."""
        try:
            await self.page.screenshot(path=path, full_page=True)
        except Exception as e:
            logger.warning(f"[{self.PLATFORM_NAME}] Screenshot failed: {e}")

    async def is_session_alive(self) -> bool:
        """
        Quick check: is the current browser session still logged in?
        Returns False if the page shows a login form or the browser is dead.
        """
        if not self.is_logged_in or not self.page:
            return False
        try:
            url = self.page.url
            # If we're already on a login page, session is dead
            if any(kw in url.lower() for kw in ["login", "default.aspx", "expired", "signout"]):
                return False
            # Quick JS eval to confirm page is alive
            await self.page.evaluate("() => document.title", timeout=3000)
            return True
        except Exception:
            return False

    def normalize_odds(self, raw: str) -> Optional[float]:
        """Convert odds string (decimal, fractional, american) to decimal float.

        Handles:
          American: +150, -110, +700  → decimal equivalent
          Decimal:  1.85, 2.10        → returned as-is
          Fractional: 5/2             → decimal equivalent
        """
        import re as _re
        if not raw:
            return None
        raw = raw.strip().replace(",", ".")

        # American odds must be detected FIRST (before float() swallows them).
        # Pattern: optional +/-, exactly 3-4 digits, nothing else.
        if _re.fullmatch(r'[+-]?\d{3,4}', raw):
            try:
                american = int(raw)
                if american == 0:
                    return None
                if american > 0:
                    return round(american / 100 + 1.0, 3)
                else:
                    return round(100 / abs(american) + 1.0, 3)
            except Exception:
                pass

        # Decimal odds (e.g. 1.85, 2.10)
        try:
            val = float(raw)
            if 1.01 < val < 100:
                return round(val, 3)
        except ValueError:
            pass

        # Fractional e.g. "5/2"
        if "/" in raw:
            try:
                num, den = raw.split("/")
                return round(float(num) / float(den) + 1.0, 3)
            except Exception:
                pass

        return None

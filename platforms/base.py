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

    def normalize_odds(self, raw: str) -> Optional[float]:
        """Convert odds string (decimal, fractional, american) to decimal float."""
        if not raw:
            return None
        raw = raw.strip().replace(",", ".")
        try:
            # Decimal odds
            return float(raw)
        except ValueError:
            pass
        # Fractional e.g. "5/2"
        if "/" in raw:
            try:
                num, den = raw.split("/")
                return round(float(num) / float(den) + 1.0, 3)
            except Exception:
                pass
        # American e.g. "+150" or "-110"
        try:
            val = int(raw.replace("+", ""))
            if val > 0:
                return round(val / 100 + 1.0, 3)
            else:
                return round(100 / abs(val) + 1.0, 3)
        except Exception:
            pass
        return None

"""
Betway platform scraper.
"""

import asyncio
import logging
from .base import BasePlatformScraper

logger = logging.getLogger(__name__)


class BetwayScraper(BasePlatformScraper):
    PLATFORM_NAME = "Betway"

    async def login(self) -> bool:
        if not self.username or not self.password:
            logger.warning(f"[{self.PLATFORM_NAME}] No credentials configured")
            return False

        logger.info(f"[{self.PLATFORM_NAME}] Attempting login...")
        try:
            await self.safe_goto("https://betway.com/en/sports")
            await asyncio.sleep(3)
            await self._dismiss_overlays()

            # Click login button to open modal
            await self.safe_click('[data-testid="login-button"], button:has-text("Log In"), .btn-login', timeout=10000)
            await asyncio.sleep(1)

            await self.safe_fill('input[name="username"], input[id*="username"]', self.username, timeout=8000)
            await self.safe_fill('input[name="password"], input[type="password"]', self.password)
            await asyncio.sleep(0.3)

            await self.safe_click('button[type="submit"], button:has-text("Log In")')
            await asyncio.sleep(4)

            self.is_logged_in = await self._verify_login()
            if self.is_logged_in:
                logger.info(f"[{self.PLATFORM_NAME}] Login successful")
            else:
                logger.error(f"[{self.PLATFORM_NAME}] Login failed")
            return self.is_logged_in

        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Login error: {e}")
            return False

    async def _dismiss_overlays(self):
        for sel in [
            'button:has-text("Accept All")',
            'button:has-text("Accept Cookies")',
            'button:has-text("Close")',
            '[aria-label="Close"]',
        ]:
            try:
                await self.page.click(sel, timeout=2000)
                await asyncio.sleep(0.5)
            except Exception:
                pass

    async def _verify_login(self) -> bool:
        for sel in [
            '[data-testid="user-balance"]',
            '.user-info',
            '[class*="AccountBalance"]',
            'a[href*="logout"]',
        ]:
            if await self.wait_for_selector(sel, timeout=3000):
                return True
        return False

    async def search_bets(self, bet: dict) -> list[dict]:
        if not self.is_logged_in:
            logger.warning(f"[{self.PLATFORM_NAME}] Not logged in, skipping search")
            return []

        results = []
        sport = bet.get("sport", "").lower()
        event = bet.get("event", "")

        try:
            sport_url = self._get_sport_url(sport)
            await self.safe_goto(sport_url)
            await asyncio.sleep(3)
            await self._dismiss_overlays()

            results = await self._scrape_events(bet)

        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Search error: {e}")

        return results

    def _get_sport_url(self, sport: str) -> str:
        sport_map = {
            "football": "https://betway.com/en/sports/grp/soccer",
            "soccer": "https://betway.com/en/sports/grp/soccer",
            "basketball": "https://betway.com/en/sports/grp/basketball",
            "tennis": "https://betway.com/en/sports/grp/tennis",
            "american football": "https://betway.com/en/sports/grp/american-football",
            "baseball": "https://betway.com/en/sports/grp/baseball",
            "ice hockey": "https://betway.com/en/sports/grp/ice-hockey",
            "cricket": "https://betway.com/en/sports/grp/cricket",
        }
        return sport_map.get(sport, "https://betway.com/en/sports")

    async def _scrape_events(self, bet: dict) -> list[dict]:
        results = []
        event_lower = bet.get("event", "").lower()

        try:
            event_rows = await self.page.query_selector_all(
                '[class*="event-row"], [class*="EventCard"], [class*="fixture"]'
            )
            for row in event_rows[:30]:
                try:
                    text = await row.inner_text()
                    if not any(w in text.lower() for w in event_lower.split() if len(w) > 2):
                        continue

                    odds_els = await row.query_selector_all('[class*="odds"], [class*="price"], [class*="Price"]')
                    for el in odds_els:
                        raw = await el.inner_text()
                        odds_val = self.normalize_odds(raw)
                        if odds_val:
                            results.append({
                                "event": text.split("\n")[0].strip(),
                                "sport": bet.get("sport", ""),
                                "market": bet.get("market", ""),
                                "selection": "",
                                "odds": odds_val,
                                "url": self.page.url,
                            })
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"[{self.PLATFORM_NAME}] Scrape error: {e}")

        return results

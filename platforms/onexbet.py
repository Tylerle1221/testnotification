"""
1xBet platform scraper.
"""

import asyncio
import logging
from .base import BasePlatformScraper

logger = logging.getLogger(__name__)


class OneXBetScraper(BasePlatformScraper):
    PLATFORM_NAME = "1xBet"

    async def login(self) -> bool:
        if not self.username or not self.password:
            logger.warning(f"[{self.PLATFORM_NAME}] No credentials configured")
            return False

        logger.info(f"[{self.PLATFORM_NAME}] Attempting login...")
        try:
            await self.safe_goto("https://1xbet.com/en/")
            await asyncio.sleep(3)
            await self._dismiss_overlays()

            await self.safe_click('.auth-button, button:has-text("Log in"), [class*="login"]', timeout=10000)
            await asyncio.sleep(1)

            await self.safe_fill('input[name="l_username"], input[placeholder*="Login"]', self.username, timeout=8000)
            await self.safe_fill('input[name="l_password"], input[type="password"]', self.password)
            await asyncio.sleep(0.3)

            await self.safe_click('button[type="submit"], .auth-button--submit')
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
            'button:has-text("Accept")',
            '.cookie-policy__btn',
            '[class*="close-btn"]',
        ]:
            try:
                await self.page.click(sel, timeout=2000)
                await asyncio.sleep(0.5)
            except Exception:
                pass

    async def _verify_login(self) -> bool:
        for sel in [
            '.user-info__balance',
            '[class*="UserBalance"]',
            '.profile-avatar',
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
            "football": "https://1xbet.com/en/sport/football",
            "soccer": "https://1xbet.com/en/sport/football",
            "basketball": "https://1xbet.com/en/sport/basketball",
            "tennis": "https://1xbet.com/en/sport/tennis",
            "american football": "https://1xbet.com/en/sport/american_football",
            "baseball": "https://1xbet.com/en/sport/baseball",
            "ice hockey": "https://1xbet.com/en/sport/ice_hockey",
            "cricket": "https://1xbet.com/en/sport/cricket",
        }
        return sport_map.get(sport, "https://1xbet.com/en/")

    async def _scrape_events(self, bet: dict) -> list[dict]:
        results = []
        event_lower = bet.get("event", "").lower()

        try:
            event_rows = await self.page.query_selector_all(
                '.c-events__item, [class*="event-item"], [class*="EventRow"]'
            )
            for row in event_rows[:30]:
                try:
                    text = await row.inner_text()
                    if not any(w in text.lower() for w in event_lower.split() if len(w) > 2):
                        continue

                    odds_els = await row.query_selector_all('.c-bets__bet, [class*="coef"], [class*="odd"]')
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

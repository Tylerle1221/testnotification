"""
V2Sports platform scraper - used by Smash66 and Leftcoast797.
These sites share the same DGS/pay-per-head platform at /v2/#/sports.
Login form POSTs to /player-api/identity/CustomerLoginRedir
"""

import asyncio
import logging
from .base import BasePlatformScraper

logger = logging.getLogger(__name__)


class V2SportsScraper(BasePlatformScraper):
    """Base scraper for any site running the /v2/#/sports DGS platform."""
    PLATFORM_NAME = "V2Sports"

    def _base_origin(self):
        """Return scheme+host e.g. https://smash66.com"""
        from urllib.parse import urlparse
        p = urlparse(self.url)
        return f"{p.scheme}://{p.netloc}"

    async def login(self) -> bool:
        if not self.username or not self.password:
            logger.warning(f"[{self.PLATFORM_NAME}] No credentials configured")
            return False

        logger.info(f"[{self.PLATFORM_NAME}] Attempting login to {self._base_origin()}...")
        try:
            login_url = self._base_origin() + "/"
            await self.safe_goto(login_url)
            await asyncio.sleep(3)
            await self._dismiss_overlays()

            # Both Smash66 and Leftcoast797 use customerid / password fields
            user_filled = await self.safe_fill('#customerid', self.username, timeout=12000)
            if not user_filled:
                # Fallback selector
                user_filled = await self.safe_fill('input[name="customerid"]', self.username, timeout=5000)
            if not user_filled:
                logger.error(f"[{self.PLATFORM_NAME}] Username field not found")
                return False

            await self.safe_fill('#password', self.password)
            await asyncio.sleep(0.5)

            # Smash66 uses a button; Leftcoast uses input[type=submit]
            clicked = await self.safe_click('input#submit, button[name="button"], .login__submit, .login_btn', timeout=6000)
            if not clicked:
                await self.page.keyboard.press("Enter")

            await asyncio.sleep(5)
            self.is_logged_in = await self._verify_login()
            if self.is_logged_in:
                logger.info(f"[{self.PLATFORM_NAME}] Login successful")
            else:
                logger.error(f"[{self.PLATFORM_NAME}] Login failed - check credentials")
            return self.is_logged_in

        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Login error: {e}")
            return False

    async def _dismiss_overlays(self):
        for sel in ['button:has-text("Accept")', '.cookie-btn', '[class*="close"]']:
            try:
                await self.page.click(sel, timeout=1500)
                await asyncio.sleep(0.3)
            except Exception:
                pass

    async def _verify_login(self) -> bool:
        """After login the URL changes to /v2/#/sports or balance is shown."""
        cur = self.page.url
        if "sports" in cur or "v2" in cur:
            return True
        # Check for common post-login elements
        for sel in [
            '[class*="balance"]', '[class*="Balance"]',
            '[class*="account"]', '[class*="logout"]',
            '.user-info', '#balance'
        ]:
            if await self.wait_for_selector(sel, timeout=2000):
                return True
        # If we are past the login page (login form gone), assume success
        login_still_present = await self.wait_for_selector('#customerid', timeout=1500)
        return not login_still_present

    async def search_bets(self, bet: dict) -> list[dict]:
        if not self.is_logged_in:
            logger.warning(f"[{self.PLATFORM_NAME}] Not logged in")
            return []

        results = []
        sport = bet.get("sport", "").lower()

        try:
            # Navigate to the sports betting page
            sports_url = self._base_origin() + "/v2/#/sports"
            await self.safe_goto(sports_url)
            await asyncio.sleep(4)
            await self._dismiss_overlays()

            # Navigate to specific sport if possible
            sport_results = await self._navigate_to_sport(sport)
            if sport_results:
                results.extend(sport_results)
            else:
                results.extend(await self._scrape_all_games(bet))

        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Search error: {e}")

        return results

    async def _navigate_to_sport(self, sport: str) -> list[dict]:
        """Try clicking a sport tab matching the requested sport."""
        sport_aliases = {
            "football": ["football", "nfl", "ncaaf", "college football"],
            "soccer": ["soccer", "football"],
            "basketball": ["basketball", "nba", "ncaab"],
            "baseball": ["baseball", "mlb"],
            "hockey": ["hockey", "nhl"],
            "tennis": ["tennis"],
        }
        aliases = sport_aliases.get(sport, [sport])

        try:
            sport_links = await self.page.query_selector_all(
                'a[class*="sport"], button[class*="sport"], [class*="sport-item"], [class*="nav-item"], li a'
            )
            for link in sport_links:
                text = (await link.inner_text()).strip().lower()
                if any(a in text for a in aliases):
                    await link.click()
                    await asyncio.sleep(3)
                    return await self._scrape_lines_from_page({})
        except Exception:
            pass
        return []

    async def _scrape_all_games(self, bet: dict) -> list[dict]:
        """Generic fallback: grab all visible odds from the page."""
        return await self._scrape_lines_from_page(bet)

    async def _scrape_lines_from_page(self, bet: dict) -> list[dict]:
        results = []
        event_filter = bet.get("event", "").lower()

        try:
            await asyncio.sleep(2)
            # Grab all text content in table rows or game rows
            rows = await self.page.query_selector_all(
                'tr[class*="game"], tr[class*="event"], [class*="game-row"], [class*="event-row"], '
                '[class*="GameRow"], table tbody tr, [class*="matchup"]'
            )
            if not rows:
                # Try a broader selector
                rows = await self.page.query_selector_all('tbody tr')

            for row in rows[:50]:
                try:
                    text = await row.inner_text()
                    text_stripped = text.strip()
                    if not text_stripped or len(text_stripped) < 5:
                        continue

                    # If event filter set, skip non-matching rows
                    if event_filter:
                        words = [w for w in event_filter.split() if len(w) > 2]
                        if words and not any(w in text_stripped.lower() for w in words):
                            continue

                    # Extract odds: look for decimal/american odds patterns
                    import re
                    odds_matches = re.findall(r'([+-]\d{3,4}|\d+\.\d+|\b\d{3,4}\b)', text_stripped)
                    for odd_str in odds_matches[:4]:
                        val = self.normalize_odds(odd_str)
                        if val and 1.05 < val < 50:
                            results.append({
                                "event": text_stripped.split("\n")[0][:80],
                                "sport": bet.get("sport", ""),
                                "market": bet.get("market", ""),
                                "selection": "",
                                "odds": val,
                                "url": self.page.url,
                            })
                            break
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"[{self.PLATFORM_NAME}] Scrape page error: {e}")

        return results


class Smash66Scraper(V2SportsScraper):
    PLATFORM_NAME = "Smash66"


class Leftcoast797Scraper(V2SportsScraper):
    PLATFORM_NAME = "Leftcoast797"
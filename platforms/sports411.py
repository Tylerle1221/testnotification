"""
Sports411 platform scraper.
Angular SPA at /en/sports/
Login is typically via a modal triggered by a header button.
"""
import asyncio
import logging
import re
from .base import BasePlatformScraper

logger = logging.getLogger(__name__)


class Sports411Scraper(BasePlatformScraper):
    PLATFORM_NAME = "Sports411"

    async def login(self) -> bool:
        if not self.username or not self.password:
            logger.warning(f"[{self.PLATFORM_NAME}] No credentials configured")
            return False

        logger.info(f"[{self.PLATFORM_NAME}] Logging in to Sports411...")
        try:
            await self.safe_goto("https://be.sports411.ag/en/sports/")
            # Angular takes time to boot
            await asyncio.sleep(6)
            await self._dismiss_overlays()

            # Try to find and click the login button in the header
            login_btn_sels = [
                'button:has-text("Login")',
                'button:has-text("Log In")',
                'button:has-text("Sign In")',
                '[class*="login-btn"]',
                '[class*="LoginBtn"]',
                'a:has-text("Login")',
                '[data-action="login"]',
                '[class*="header"] button',
            ]
            clicked_login = False
            for sel in login_btn_sels:
                if await self.safe_click(sel, timeout=3000):
                    clicked_login = True
                    break

            if not clicked_login:
                logger.warning(f"[{self.PLATFORM_NAME}] Could not find login button, trying direct field search")

            await asyncio.sleep(2)

            # Fill username
            user_sels = [
                'input[formcontrolname="username"]',
                'input[name="username"]',
                'input[placeholder*="sername"]',
                'input[placeholder*="Login"]',
                'input[type="text"]',
            ]
            user_filled = False
            for sel in user_sels:
                if await self.safe_fill(sel, self.username, timeout=5000):
                    user_filled = True
                    break
            if not user_filled:
                logger.error(f"[{self.PLATFORM_NAME}] Could not fill username")
                return False

            # Fill password
            pass_sels = [
                'input[formcontrolname="password"]',
                'input[type="password"]',
                'input[placeholder*="assword"]',
            ]
            for sel in pass_sels:
                if await self.safe_fill(sel, self.password, timeout=5000):
                    break

            await asyncio.sleep(0.5)

            # Submit
            submit_sels = [
                'button[type="submit"]',
                'button:has-text("Login")',
                'button:has-text("Sign In")',
                '[class*="submit"]',
            ]
            for sel in submit_sels:
                if await self.safe_click(sel, timeout=5000):
                    break
            else:
                await self.page.keyboard.press("Enter")

            await asyncio.sleep(6)

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
            'button:has-text("Accept")', 'button:has-text("OK")',
            '.cookie-accept', '[class*="close"]', '[aria-label="Close"]',
        ]:
            try:
                await self.page.click(sel, timeout=1500)
                await asyncio.sleep(0.3)
            except Exception:
                pass

    async def _verify_login(self) -> bool:
        for sel in [
            '[class*="balance"]', '[class*="Balance"]',
            '[class*="user-name"]', '[class*="UserName"]',
            '[class*="account-menu"]', 'app-header [class*="logged"]',
            'button:has-text("Logout")', 'button:has-text("Log Out")',
        ]:
            if await self.wait_for_selector(sel, timeout=3000):
                return True
        # Check if login form is gone
        login_gone = not await self.wait_for_selector(
            'input[formcontrolname="password"], input[type="password"]', timeout=1500
        )
        return login_gone

    async def search_bets(self, bet: dict) -> list[dict]:
        if not self.is_logged_in:
            logger.warning(f"[{self.PLATFORM_NAME}] Not logged in")
            return []

        results = []
        sport = bet.get("sport", "").lower()

        try:
            await self.safe_goto("https://be.sports411.ag/en/sports/")
            await asyncio.sleep(5)
            await self._dismiss_overlays()

            # Try clicking into the matching sport
            await self._select_sport(sport)
            await asyncio.sleep(3)
            results = await self._scrape_events(bet)

        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Search error: {e}")

        return results

    async def _select_sport(self, sport: str):
        sport_aliases = {
            "football": ["football", "nfl", "ncaa"],
            "soccer": ["soccer"],
            "basketball": ["basketball", "nba"],
            "baseball": ["baseball", "mlb"],
            "hockey": ["hockey", "nhl"],
        }
        aliases = sport_aliases.get(sport, [sport])
        try:
            links = await self.page.query_selector_all(
                '[class*="sport-item"], [class*="SportItem"], app-sports-menu a, '
                '[class*="left-menu"] a, [class*="sidebar"] a'
            )
            for link in links:
                txt = (await link.inner_text()).strip().lower()
                if any(a in txt for a in aliases):
                    await link.click()
                    return
        except Exception:
            pass

    async def _scrape_events(self, bet: dict) -> list[dict]:
        results = []
        event_filter = bet.get("event", "").lower()
        try:
            rows = await self.page.query_selector_all(
                '[class*="event-row"], [class*="EventRow"], app-event, '
                '[class*="game-row"], tr[class*="game"], [class*="bet-row"]'
            )
            if not rows:
                rows = await self.page.query_selector_all('tbody tr, [class*="match"]')

            for row in rows[:60]:
                try:
                    text = (await row.inner_text()).strip()
                    if not text or len(text) < 5:
                        continue
                    if event_filter:
                        words = [w for w in event_filter.split() if len(w) > 2]
                        if words and not any(w in text.lower() for w in words):
                            continue
                    odds_matches = re.findall(r'([+-]\d{3,4}|\d+\.\d+)', text)
                    for odd_str in odds_matches[:4]:
                        val = self.normalize_odds(odd_str)
                        if val and 1.05 < val < 50:
                            results.append({
                                "event": text.split("\n")[0][:80],
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
            logger.debug(f"[{self.PLATFORM_NAME}] Scrape error: {e}")
        return results
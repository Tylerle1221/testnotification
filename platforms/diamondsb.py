"""
DiamondSB platform scraper.
Vue.js login at /pla/#/msg
Form: .signin-form  |  input[type=text] for username, #password-field for password
"""
import asyncio
import logging
import re
from .base import BasePlatformScraper

logger = logging.getLogger(__name__)


class DiamondSBScraper(BasePlatformScraper):
    PLATFORM_NAME = "DiamondSB"

    def _origin(self):
        from urllib.parse import urlparse
        p = urlparse(self.url)
        return f"{p.scheme}://{p.netloc}"

    async def login(self) -> bool:
        if not self.username or not self.password:
            logger.warning(f"[{self.PLATFORM_NAME}] No credentials configured")
            return False

        logger.info(f"[{self.PLATFORM_NAME}] Logging in...")
        try:
            await self.safe_goto(self._origin() + "/pla/#/msg")
            await asyncio.sleep(6)  # Vue.js needs time to initialise on Render
            await self._dismiss_overlays()

            # Vue form: first text input = username, #password-field = password
            user_sel = '.signin-form input[type="text"], .signin-form input:not([type="password"])'
            pass_sel = '#password-field, .signin-form input[type="password"]'

            if not await self.safe_fill(user_sel, self.username, timeout=15000):
                logger.error(f"[{self.PLATFORM_NAME}] Username field not found")
                return False
            await self.safe_fill(pass_sel, self.password)
            await asyncio.sleep(0.4)

            await self.safe_click('.signin-form button[type="submit"], .signin-form .btn-primary', timeout=8000)
            await asyncio.sleep(7)  # wait for Vue router to update

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
        for sel in ['button:has-text("Accept")', '.close', '[aria-label="Close"]']:
            try:
                await self.page.click(sel, timeout=1500)
                await asyncio.sleep(0.3)
            except Exception:
                pass

    async def _verify_login(self) -> bool:
        # 1. Title changes to 'DiamondSB Players' after successful login
        try:
            title = await self.page.title()
            if "Players" in title or "player" in title.lower():
                return True
        except Exception:
            pass

        # 2. URL moves away from the login route
        cur = self.page.url
        if "/msg" not in cur:
            return True

        # 3. Look for post-login navigation elements
        for sel in ['[class*="balance"]', '[class*="account"]', '.user-balance',
                    '.nav-user', '[class*="nav"]', '[class*="header"]']:
            if await self.wait_for_selector(sel, timeout=2000):
                return True

        # 4. Login form is gone (most reliable fallback)
        login_gone = not await self.wait_for_selector('.signin-form', timeout=2000)
        return login_gone

    async def search_bets(self, bet: dict) -> list[dict]:
        if not self.is_logged_in:
            logger.warning(f"[{self.PLATFORM_NAME}] Not logged in")
            return []

        results = []
        sport = bet.get("sport", "").lower()
        try:
            # Navigate to sports section after login
            sports_url = self._origin() + "/pla/#/sports"
            await self.safe_goto(sports_url)
            await asyncio.sleep(4)
            await self._dismiss_overlays()
            results = await self._scrape_events(bet)
        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Search error: {e}")
        return results

    async def _scrape_events(self, bet: dict) -> list[dict]:
        results = []
        event_filter = bet.get("event", "").lower()
        try:
            rows = await self.page.query_selector_all(
                'tr, [class*="event"], [class*="game"], [class*="match"], [class*="fixture"]'
            )
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
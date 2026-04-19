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
            # Navigate to the login page. The site may redirect to /#/?expired=true
            # when a prior session expired — that's fine, the .signin-form is still present.
            await self.safe_goto(self._origin() + "/pla/#/msg")
            try:
                await self.page.wait_for_selector('.signin-form input[type="text"]', timeout=10000)
            except Exception:
                await asyncio.sleep(6)
            await self._dismiss_overlays()

            cur_url = self.page.url
            logger.debug(f"[{self.PLATFORM_NAME}] Pre-login URL: {cur_url}")

            # If the site redirected us to the base domain login, we're still on the right page
            # Both /pla/#/msg and /#/?expired=true have .signin-form
            user_sel = '.signin-form input[type="text"], .signin-form input:not([type="password"])'
            pass_sel = '#password-field, .signin-form input[type="password"]'

            if not await self.safe_fill(user_sel, self.username, timeout=15000):
                logger.error(f"[{self.PLATFORM_NAME}] Username field not found (URL: {self.page.url})")
                return False
            await self.safe_fill(pass_sel, self.password)
            await asyncio.sleep(0.4)

            await self.safe_click('.signin-form button[type="submit"], .signin-form .btn-primary', timeout=8000)
            await asyncio.sleep(7)

            self.is_logged_in = await self._verify_login()
            if self.is_logged_in:
                logger.info(f"[{self.PLATFORM_NAME}] Login successful")
            else:
                logger.error(f"[{self.PLATFORM_NAME}] Login failed (URL: {self.page.url}, title: {await self.page.title()})")
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
        event = bet.get("event", "").lower()
        try:
            await self.safe_goto(self._origin() + "/pla/#/bet")
            await asyncio.sleep(4)
            await self._dismiss_overlays()

            # Determine primary sport category
            if "acb" in event or "spain" in event or any(t in event for t in ["barcelona", "madrid", "lleida"]):
                primary_sport = "Basketball"
                subcategory = "Spain ACB"
            elif any(t in event for t in ["nba", "celtics", "lakers", "warriors", "bulls"]):
                primary_sport = "Basketball"
                subcategory = "NBA"
            elif any(t in event for t in ["bbl", "germany"]):
                primary_sport = "Basketball"
                subcategory = "Germany BBL"
            elif sport == "basketball":
                primary_sport = "Basketball"
                subcategory = None
            elif sport in ("football", "nfl", "ncaa"):
                primary_sport = "Football"
                subcategory = None
            elif sport in ("baseball", "mlb"):
                primary_sport = "Baseball"
                subcategory = None
            elif sport in ("hockey", "nhl"):
                primary_sport = "Hockey"
                subcategory = None
            elif sport == "soccer":
                primary_sport = "Soccer"
                subcategory = None
            else:
                primary_sport = sport.title()
                subcategory = None

            async def click_text(text: str) -> bool:
                """Click any visible element containing this text."""
                try:
                    el = await self.page.query_selector(f'text="{text}"')
                    if el and await el.is_visible():
                        await el.click()
                        return True
                except Exception:
                    pass
                # Partial match fallback
                for sel in ["a", "button", "span[class*='sport']", "div[class*='sport']", "li"]:
                    try:
                        els = await self.page.query_selector_all(sel)
                        for e in els:
                            try:
                                if await e.is_visible():
                                    txt = (await e.inner_text()).strip()
                                    if text.lower() in txt.lower():
                                        await e.click()
                                        return True
                            except Exception:
                                pass
                    except Exception:
                        pass
                return False

            # Click primary sport category
            if await click_text(primary_sport):
                logger.info(f"[{self.PLATFORM_NAME}] Clicked: {primary_sport!r}")
                await asyncio.sleep(4)

                # Click subcategory if needed
                if subcategory:
                    if await click_text(subcategory):
                        logger.info(f"[{self.PLATFORM_NAME}] Clicked subcategory: {subcategory!r}")
                        await asyncio.sleep(4)

            results = await self._scrape_events(bet)

        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Search error: {e}")
        return results

    async def _scrape_events(self, bet: dict) -> list[dict]:
        results = []
        event_filter = bet.get("event", "").lower()
        try:
            # First try structured rows
            rows = await self.page.query_selector_all(
                'tr, [class*="event"], [class*="game"], [class*="match"], [class*="fixture"], [class*="bet-row"]'
            )

            # Fallback: scrape from full page text
            if not rows:
                page_text = await self.page.inner_text("body")
                lines = page_text.split("\n")
                for line in lines[:300]:
                    if event_filter:
                        words = [w for w in event_filter.split() if len(w) > 2]
                        if words and not any(w in line.lower() for w in words):
                            continue
                    for odd_str in re.findall(r'([+-]\d{3,4}|\b[12]\.\d{2,3}\b)', line)[:4]:
                        val = self.normalize_odds(odd_str)
                        if val and 1.05 < val < 50:
                            results.append({
                                "event": line.strip()[:80],
                                "sport": bet.get("sport", ""),
                                "market": bet.get("market", ""),
                                "selection": "",
                                "odds": val,
                                "url": self.page.url,
                            })
                            break
                return results

            for row in rows[:60]:
                try:
                    text = (await row.inner_text()).strip()
                    if not text or len(text) < 5:
                        continue
                    if event_filter:
                        words = [w for w in event_filter.split() if len(w) > 2]
                        if words and not any(w in text.lower() for w in words):
                            continue
                    for odd_str in re.findall(r'([+-]\d{3,4}|\d+\.\d+)', text)[:4]:
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
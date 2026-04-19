"""
Sports411 platform scraper.
Angular SPA at /en/sports/
Login button: button.login-enter
Credential fields: input[name=account] / input[type=password]
Uses Bright Data ISP proxy to bypass geo/bot restrictions.
"""
import asyncio
import logging
import re
from .base import BasePlatformScraper

logger = logging.getLogger(__name__)

# Bright Data proxy credentials for Sports411
_S411_PROXY = {
    "server": "http://brd.superproxy.io:33335",
    "username": "brd-customer-hl_5c133191-zone-sports411",
    "password": "uuszpgqk1e3m",
}


class Sports411Scraper(BasePlatformScraper):
    PLATFORM_NAME = "Sports411"

    def __init__(self, config: dict, headless: bool = True):
        # Inject proxy into config so base.start() picks it up
        if "proxy" not in config:
            config = {**config, "proxy": _S411_PROXY}
        super().__init__(config, headless)

    async def login(self) -> bool:
        if not self.username or not self.password:
            logger.warning(f"[{self.PLATFORM_NAME}] No credentials configured")
            return False

        logger.info(f"[{self.PLATFORM_NAME}] Logging in to Sports411 (via proxy)...")
        try:
            await self.safe_goto("https://be.sports411.ag/en/sports/")
            await asyncio.sleep(12)  # Angular + proxy needs more time to fully render

            # Dismiss outdated-browser warning first (must go before login click)
            await self._dismiss_overlays()
            await asyncio.sleep(0.5)

            # Click the Login button (identified from live snapshot)
            clicked = await self.safe_click("button.login-enter", timeout=8000)
            if not clicked:
                # Fallback text-based selectors
                for sel in ['button:has-text("Log In")', 'button:has-text("Login")']:
                    if await self.safe_click(sel, timeout=3000):
                        clicked = True
                        break
            if not clicked:
                logger.error(f"[{self.PLATFORM_NAME}] Login button not found")
                return False

            await asyncio.sleep(3)

            # Fill username  (field name=account, discovered via live test)
            user_filled = False
            for sel in [
                'input[name="account"]',
                'input[formcontrolname="username"]',
                'input[type="text"]',
            ]:
                if await self.safe_fill(sel, self.username, timeout=5000):
                    user_filled = True
                    break
            if not user_filled:
                logger.error(f"[{self.PLATFORM_NAME}] Could not fill username")
                return False

            # Fill password
            for sel in ['input[name="password"]', 'input[type="password"]']:
                if await self.safe_fill(sel, self.password, timeout=5000):
                    break

            await asyncio.sleep(0.4)

            # Submit
            submitted = False
            for sel in ['button[type="submit"]', 'button:has-text("Log In")', 'button:has-text("Sign In")']:
                if await self.safe_click(sel, timeout=5000):
                    submitted = True
                    break
            if not submitted:
                await self.page.keyboard.press("Enter")

            await asyncio.sleep(7)

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
        for sel in [
            "button.outdated-button",
            'button:has-text("Continue Anyway")',
            'button:has-text("Accept")',
            '[aria-label="Close"]',
        ]:
            try:
                await self.page.click(sel, timeout=1500)
                await asyncio.sleep(0.3)
            except Exception:
                pass

    async def _verify_login(self) -> bool:
        # Angular SPA — URL and title don't change on login.
        # Check: user balance/info appears OR login form disappears.
        for sel in [
            '[class*="balance"]', '[class*="Balance"]',
            '[class*="user-name"]', '[class*="account-menu"]',
            '.user-balance', '.header-user',
            'button:has-text("Logout")', 'button:has-text("Log Out")',
        ]:
            if await self.wait_for_selector(sel, timeout=3000):
                return True
        # Most reliable: the login account field is gone after a successful login
        login_field_gone = not await self.wait_for_selector(
            'input[name="account"], button.login-enter', timeout=2000
        )
        return login_field_gone

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
            await self._select_sport(sport)
            await asyncio.sleep(3)
            results = await self._scrape_events(bet)
        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Search error: {e}")

        return results

    async def _select_sport(self, sport: str):
        aliases = {
            "football": ["football", "nfl", "ncaa"],
            "soccer": ["soccer"],
            "basketball": ["basketball", "nba"],
            "baseball": ["baseball", "mlb"],
            "hockey": ["hockey", "nhl"],
        }.get(sport, [sport])
        try:
            links = await self.page.query_selector_all(
                '[class*="sport-item"], app-sports-menu a, '
                '[class*="left-menu"] a, [class*="sidebar"] a, nav a'
            )
            for link in links:
                if any(a in (await link.inner_text()).strip().lower() for a in aliases):
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
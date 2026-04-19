"""
Bet365 platform scraper.
"""

import asyncio
import logging
from typing import Optional
from .base import BasePlatformScraper

logger = logging.getLogger(__name__)


class Bet365Scraper(BasePlatformScraper):
    PLATFORM_NAME = "Bet365"

    async def login(self) -> bool:
        if not self.username or not self.password:
            logger.warning(f"[{self.PLATFORM_NAME}] No credentials configured")
            return False

        logger.info(f"[{self.PLATFORM_NAME}] Attempting login...")
        try:
            await self.safe_goto("https://www.bet365.com/#/LGN/")
            await asyncio.sleep(3)

            # Dismiss any popups / cookie banners
            await self._dismiss_overlays()

            # Fill login form
            username_sel = 'input[placeholder*="Username"], input[name*="username"], .hm-Login_InputField-username input'
            password_sel = 'input[placeholder*="Password"], input[type="password"], .hm-Login_InputField-password input'

            if not await self.safe_fill(username_sel, self.username, timeout=15000):
                logger.error(f"[{self.PLATFORM_NAME}] Could not find username field")
                return False

            if not await self.safe_fill(password_sel, self.password):
                logger.error(f"[{self.PLATFORM_NAME}] Could not find password field")
                return False

            await asyncio.sleep(0.5)
            login_btn = 'button[class*="Login"], .hm-Login_Btn, button:has-text("Log in")'
            await self.safe_click(login_btn)
            await asyncio.sleep(4)

            # Verify login success
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
        """Try to close cookie banners or accept buttons."""
        for sel in [
            'button:has-text("Accept")',
            'button:has-text("Accept All")',
            'button:has-text("Close")',
            '.cookie-accept',
        ]:
            try:
                await self.page.click(sel, timeout=2000)
                await asyncio.sleep(0.5)
            except Exception:
                pass

    async def _verify_login(self) -> bool:
        """Check if we are logged in by looking for account indicators."""
        for sel in [
            '.hm-MainHeaderRHSLoggedIn',
            '.hm-Account_Container',
            '[class*="UserBalance"]',
            'a[href*="logout"]',
        ]:
            if await self.wait_for_selector(sel, timeout=3000):
                return True
        return False

    async def search_bets(self, bet: dict) -> list[dict]:
        """Search Bet365 for bets matching the given details."""
        if not self.is_logged_in:
            logger.warning(f"[{self.PLATFORM_NAME}] Not logged in, skipping search")
            return []

        results = []
        sport = bet.get("sport", "").lower()
        event = bet.get("event", "")

        try:
            # Navigate to sports section
            sport_url = self._get_sport_url(sport)
            await self.safe_goto(sport_url)
            await asyncio.sleep(3)
            await self._dismiss_overlays()

            # Search using the site search if available
            search_results = await self._search_event(event)
            if not search_results:
                # Fall back to browsing the sport page
                search_results = await self._browse_sport_page(bet)

            results.extend(search_results)

        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Search error: {e}")

        return results

    def _get_sport_url(self, sport: str) -> str:
        sport_map = {
            "football": "https://www.bet365.com/#/AS/B1/",
            "soccer": "https://www.bet365.com/#/AS/B1/",
            "basketball": "https://www.bet365.com/#/AS/B4/",
            "tennis": "https://www.bet365.com/#/AS/B13/",
            "american football": "https://www.bet365.com/#/AS/B12/",
            "baseball": "https://www.bet365.com/#/AS/B16/",
            "ice hockey": "https://www.bet365.com/#/AS/B17/",
            "cricket": "https://www.bet365.com/#/AS/B19/",
            "golf": "https://www.bet365.com/#/AS/B18/",
        }
        return sport_map.get(sport, f"https://www.bet365.com/#/AS/B1/")

    async def _search_event(self, event: str) -> list[dict]:
        """Use Bet365 search to find the event."""
        results = []
        try:
            search_sel = 'input[placeholder*="Search"], .hm-SearchBarPhone_InputField'
            search_icon = '.hm-SearchBarPhone, [class*="SearchButton"]'

            # Try opening search
            await self.safe_click(search_icon, timeout=3000)
            await asyncio.sleep(1)

            if not await self.safe_fill(search_sel, event, timeout=5000):
                return results

            await asyncio.sleep(2)

            # Collect search result links
            links = await self.page.query_selector_all('[class*="SearchResult"] a, [class*="sm-SuggestedMatch"] a')
            for link in links[:5]:
                try:
                    text = await link.inner_text()
                    href = await link.get_attribute("href")
                    results.append({
                        "event": text.strip(),
                        "sport": "",
                        "market": "",
                        "selection": "",
                        "odds": None,
                        "url": f"https://www.bet365.com/{href}" if href else self.url,
                    })
                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"[{self.PLATFORM_NAME}] Search attempt failed: {e}")

        return results

    async def _browse_sport_page(self, bet: dict) -> list[dict]:
        """Scrape event/odds from the current sport page."""
        results = []
        event_lower = bet.get("event", "").lower()
        selection_lower = bet.get("selection", "").lower()
        market_lower = bet.get("market", "").lower()

        try:
            # Look for event rows / coupon rows
            event_rows = await self.page.query_selector_all(
                '[class*="rc-EventCouple"], [class*="sl-CouponParticipantWithPreferences"], '
                '[class*="Fixture"], [class*="EventRow"]'
            )

            for row in event_rows[:30]:
                try:
                    text = await row.inner_text()
                    text_lower = text.lower()

                    if not any(w in text_lower for w in event_lower.split() if len(w) > 2):
                        continue

                    # Extract odds buttons
                    odd_buttons = await row.query_selector_all('[class*="Odds"], [class*="Price"]')
                    for btn in odd_buttons:
                        btn_text = await btn.inner_text()
                        odds_val = self.normalize_odds(btn_text)
                        if odds_val:
                            results.append({
                                "event": text.split("\n")[0].strip(),
                                "sport": bet.get("sport", ""),
                                "market": market_lower,
                                "selection": "",
                                "odds": odds_val,
                                "url": self.page.url,
                            })

                except Exception:
                    pass

        except Exception as e:
            logger.debug(f"[{self.PLATFORM_NAME}] Browse error: {e}")

        return results

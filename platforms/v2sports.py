"""
V2Sports platform scraper - DGS pay-per-head platform.
Used by Smash66 and Leftcoast797.

Sports page: /v2/#/sports  (shows event list with evId links)
Game odds page: /v2/#/schedule?evId=XXXXXX  (shows full spread/ML/total board)
"""
import asyncio
import logging
import re
from .base import BasePlatformScraper

logger = logging.getLogger(__name__)


class V2SportsScraper(BasePlatformScraper):
    """Base scraper for any site running the /v2/#/sports DGS platform."""
    PLATFORM_NAME = "V2Sports"

    def _base_origin(self):
        from urllib.parse import urlparse
        p = urlparse(self.url)
        return f"{p.scheme}://{p.netloc}"

    async def login(self) -> bool:
        if not self.username or not self.password:
            logger.warning(f"[{self.PLATFORM_NAME}] No credentials configured")
            return False

        logger.info(f"[{self.PLATFORM_NAME}] Attempting login to {self._base_origin()}...")
        try:
            await self.safe_goto(self._base_origin() + "/")
            await asyncio.sleep(3)
            await self._dismiss_overlays()

            if not await self.safe_fill('#customerid', self.username, timeout=12000):
                if not await self.safe_fill('input[name="customerid"]', self.username, timeout=5000):
                    logger.error(f"[{self.PLATFORM_NAME}] Username field not found")
                    return False

            await self.safe_fill('#password', self.password)
            await asyncio.sleep(0.5)

            clicked = await self.safe_click('input#submit, button[name="button"], .login__submit, .login_btn', timeout=6000)
            if not clicked:
                await self.page.keyboard.press("Enter")

            await asyncio.sleep(5)
            self.is_logged_in = await self._verify_login()
            if self.is_logged_in:
                logger.info(f"[{self.PLATFORM_NAME}] Login successful")
            else:
                logger.error(f"[{self.PLATFORM_NAME}] Login failed (URL: {self.page.url})")
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
        cur = self.page.url
        if "sports" in cur or "v2" in cur:
            return True
        for sel in ['[class*="balance"]', '[class*="Balance"]', '[class*="account"]', '#balance']:
            if await self.wait_for_selector(sel, timeout=2000):
                return True
        return not await self.wait_for_selector('#customerid', timeout=1500)

    async def search_bets(self, bet: dict) -> list[dict]:
        if not self.is_logged_in:
            logger.warning(f"[{self.PLATFORM_NAME}] Not logged in")
            return []

        results = []
        try:
            # Load main sports page
            sports_url = self._base_origin() + "/v2/#/sports"
            await self.safe_goto(sports_url)
            await asyncio.sleep(8)  # DGS loads async
            await self._dismiss_overlays()

            # Find all game event links on the page
            event_links = await self.page.query_selector_all("a[href*='schedule'], a[href*='evId']")
            logger.info(f"[{self.PLATFORM_NAME}] Found {len(event_links)} event links on main page")

            event_filter = bet.get("event", "").lower()
            matched_links = []

            for link in event_links[:30]:
                try:
                    text = (await link.inner_text()).strip()
                    href = await link.get_attribute("href") or ""
                    if not text or not href:
                        continue

                    # If we have a specific event to match, filter
                    if event_filter:
                        words = [w for w in event_filter.split() if len(w) > 2]
                        if words and any(w in text.lower() for w in words):
                            matched_links.append((text, href))
                    else:
                        matched_links.append((text, href))
                except Exception:
                    pass

            # If no match found by name, take first few events (broad search)
            if not matched_links:
                for link in event_links[:5]:
                    try:
                        text = (await link.inner_text()).strip()
                        href = await link.get_attribute("href") or ""
                        if text and href:
                            matched_links.append((text, href))
                    except Exception:
                        pass

            # Click into each matched event to get full odds
            for event_text, event_href in matched_links[:3]:
                try:
                    event_url = self._base_origin() + "/v2/" + event_href.lstrip("/")
                    if "#/" in event_href:
                        event_url = self._base_origin() + "/v2/" + event_href
                    elif event_href.startswith("#"):
                        event_url = self._base_origin() + "/v2/" + event_href

                    await self.safe_goto(event_url)
                    await asyncio.sleep(5)

                    # Scrape odds from the event page
                    page_text = await self.page.inner_text("body")
                    american_odds = re.findall(r'([+-]\d{3,4})', page_text)
                    decimal_odds = re.findall(r'(\b[12]\.\d{2,3}\b)', page_text)

                    for odd_str in (american_odds + decimal_odds)[:10]:
                        val = self.normalize_odds(odd_str)
                        if val and 1.05 < val < 30:
                            results.append({
                                "event": event_text.replace("\n", " ").strip()[:80],
                                "sport": bet.get("sport", ""),
                                "market": bet.get("market", ""),
                                "selection": "",
                                "odds": val,
                                "url": self.page.url,
                            })
                            break

                    logger.info(f"[{self.PLATFORM_NAME}] Event {event_text.strip()[:40]!r}: {len(american_odds)} American + {len(decimal_odds)} decimal odds")

                except Exception as e:
                    logger.debug(f"[{self.PLATFORM_NAME}] Error on event {event_text!r}: {e}")

        except Exception as e:
            logger.error(f"[{self.PLATFORM_NAME}] Search error: {e}")

        return results


class Smash66Scraper(V2SportsScraper):
    PLATFORM_NAME = "Smash66"


class Leftcoast797Scraper(V2SportsScraper):
    PLATFORM_NAME = "Leftcoast797"
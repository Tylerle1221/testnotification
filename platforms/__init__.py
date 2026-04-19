from .base import BasePlatformScraper
from .v2sports import Smash66Scraper, Leftcoast797Scraper
from .diamondsb import DiamondSBScraper
from .sports411 import Sports411Scraper

PLATFORM_MAP = {
    "smash66": Smash66Scraper,
    "diamondsb": DiamondSBScraper,
    "sports411": Sports411Scraper,
    "leftcoast797": Leftcoast797Scraper,
}

__all__ = [
    "BasePlatformScraper",
    "Smash66Scraper",
    "Leftcoast797Scraper",
    "DiamondSBScraper",
    "Sports411Scraper",
    "PLATFORM_MAP",
]
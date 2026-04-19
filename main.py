"""
Bet Finder Agent - Main orchestrator.
Reads open bets from ibetcoin.win, then searches all configured platforms
for matching bets, applying slippage rules for Over/Under lines.
Sends Telegram alerts when matches are found.
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.logging import RichHandler

from telegram_notifier import TelegramNotifier
from bet_matcher import BetMatcher, is_hedge
from ibetcoin_reader import IbetcoinReader
from platforms import PLATFORM_MAP

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
)
logger = logging.getLogger("bet_agent")
for noisy in ("httpx", "telegram", "playwright"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

console = Console()
CONFIG_PATH = Path(__file__).parent / "config.json"


# ─── Config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg: dict = {"telegram": {}, "platforms": {}, "agent": {}, "ibetcoin": {}}

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)

    # Env-var overrides
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg.setdefault("telegram", {})["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg.setdefault("telegram", {})["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]
    if os.environ.get("IBETCOIN_USERNAME"):
        cfg.setdefault("ibetcoin", {})["username"] = os.environ["IBETCOIN_USERNAME"]
    if os.environ.get("IBETCOIN_PASSWORD"):
        cfg.setdefault("ibetcoin", {})["password"] = os.environ["IBETCOIN_PASSWORD"]

    PLATFORM_ENVS = {
        "smash66":     ("SMASH66_USERNAME",     "SMASH66_PASSWORD",     "https://smash66.com/v2/#/sports"),
        "diamondsb":   ("DIAMONDSB_USERNAME",   "DIAMONDSB_PASSWORD",   "https://diamondsb.com/pla/#/msg"),
        "sports411":   ("SPORTS411_USERNAME",   "SPORTS411_PASSWORD",   "https://be.sports411.ag/en/sports/"),
        "leftcoast797":("LEFTCOAST797_USERNAME","LEFTCOAST797_PASSWORD","https://leftcoast797.com/v2/#/sports"),
    }
    for name, (u_var, p_var, default_url) in PLATFORM_ENVS.items():
        u, p = os.environ.get(u_var), os.environ.get(p_var)
        if u or p:
            ex = cfg.get("platforms", {}).get(name, {})
            cfg.setdefault("platforms", {})[name] = {
                "enabled": True,
                "url": ex.get("url", default_url),
                "username": u or ex.get("username", ""),
                "password": p or ex.get("password", ""),
            }

    a = cfg.setdefault("agent", {})
    if os.environ.get("CHECK_INTERVAL"):
        a["check_interval_seconds"] = int(os.environ["CHECK_INTERVAL"])
    a.setdefault("check_interval_seconds", 300)
    a.setdefault("headless", True)
    a.setdefault("odds_tolerance", 0.05)
    a.setdefault("similarity_threshold", 75)
    a.setdefault("line_slippage", 1.0)
    a.setdefault("juice_slippage", 20)
    a.setdefault("notify_on_similar", True)
    a.setdefault("notify_on_exact", True)

    return cfg


def get_enabled_platforms(config: dict) -> list[str]:
    return [
        n for n, c in config.get("platforms", {}).items()
        if c.get("enabled", False) and n in PLATFORM_MAP
    ]


# ─── Display helpers ──────────────────────────────────────────────────────────

def display_banner():
    console.print(Panel.fit(
        "[bold cyan]Bet Finder Agent[/bold cyan]\n"
        "[dim]Reads bets from ibetcoin.win → searches platforms → Telegram alerts[/dim]",
        border_style="cyan"
    ))


def display_bet(bet: dict, idx: int):
    side = bet.get("bet_side", "")
    line = f" {side.upper()} {bet.get('line')}" if side and bet.get("line") else ""
    odds = f"  odds={bet.get('odds_american') or bet.get('odds')}"
    console.print(
        f"  [cyan]#{idx}[/cyan] [{bet.get('sport','')}] "
        f"[bold]{bet.get('event','?')[:60]}[/bold]"
        f"{line}{odds}  "
        f"[dim]ticket={bet.get('ticket_id')}[/dim]"
    )


# ─── Core search ─────────────────────────────────────────────────────────────

async def search_bet_on_platforms(
    bet: dict,
    platforms: list[str],
    config: dict,
    notifier: TelegramNotifier,
    matcher: BetMatcher,
    all_open_bets: list[dict],
) -> int:
    agent_cfg = config.get("agent", {})
    headless = agent_cfg.get("headless", True)
    notify_exact = agent_cfg.get("notify_on_exact", True)
    notify_similar = agent_cfg.get("notify_on_similar", True)
    total = 0

    for platform_name in platforms:
        PlatformClass = PLATFORM_MAP[platform_name]
        platform_cfg = config["platforms"][platform_name]
        scraper = PlatformClass(platform_cfg, headless=headless)

        console.print(f"  [cyan]→ {scraper.PLATFORM_NAME}[/cyan]", end="")

        try:
            if not await scraper.start():
                console.print(" [red]browser failed[/red]")
                continue
            if not await scraper.login():
                console.print(" [red]login failed[/red]")
                await notifier.notify_error(scraper.PLATFORM_NAME, "Login failed")
                continue

            candidates = await scraper.search_bets(bet)
            matches = matcher.filter_results(bet, candidates)
            console.print(f" [dim]{len(candidates)} candidates → [/dim][yellow]{len(matches)} matches[/yellow]")

            for match in matches:
                total += 1
                score = match["similarity_score"]

                # Hedge check
                if is_hedge(all_open_bets, match, same_account=True):
                    console.print(f"  [dim]↳ HEDGE detected, skipping[/dim]")
                    continue

                label = "EXACT" if match["is_exact"] else f"SIMILAR ({score:.0f}%)"
                event_str = match.get("event", "?")[:60]
                console.print(f"  [bold green]{label}[/bold green]: {event_str} | odds={match.get('odds')}")

                if match["is_exact"] and notify_exact:
                    await notifier.notify_exact_match(scraper.PLATFORM_NAME, bet, match)
                elif match["is_similar"] and not match["is_exact"] and notify_similar:
                    await notifier.notify_similar_match(scraper.PLATFORM_NAME, bet, match, score)

        except Exception as e:
            logger.error(f"Error on {platform_name}: {e}")
            await notifier.notify_error(platform_name, str(e))
        finally:
            await scraper.stop()

    return total


# ─── Main loop ────────────────────────────────────────────────────────────────

async def main():
    display_banner()
    config = load_config()
    agent_cfg = config.get("agent", {})

    # Validate Telegram
    tg = config.get("telegram", {})
    if not tg.get("bot_token"):
        console.print("[red]TELEGRAM_BOT_TOKEN not configured.[/red]")
        sys.exit(1)
    if not tg.get("chat_id"):
        console.print("[red]TELEGRAM_CHAT_ID not set. Run setup_telegram.py first.[/red]")
        sys.exit(1)

    # Validate ibetcoin
    ibet = config.get("ibetcoin", {})
    if not ibet.get("username"):
        console.print("[red]ibetcoin username not configured. Set IBETCOIN_USERNAME env var or config.json.[/red]")
        sys.exit(1)

    notifier = TelegramNotifier(tg["bot_token"], tg["chat_id"])
    matcher = BetMatcher(
        similarity_threshold=agent_cfg.get("similarity_threshold", 75),
        odds_tolerance=agent_cfg.get("odds_tolerance", 0.05),
        line_slippage=agent_cfg.get("line_slippage", 1.0),
        juice_slippage=agent_cfg.get("juice_slippage", 20),
    )
    reader = IbetcoinReader(
        username=ibet["username"],
        password=ibet["password"],
        headless=agent_cfg.get("headless", True),
    )

    enabled_platforms = get_enabled_platforms(config)
    if not enabled_platforms:
        console.print("[red]No platforms enabled.[/red]")
        sys.exit(1)

    # Test Telegram
    console.print("[dim]Testing Telegram...[/dim]")
    await notifier.test_connection()

    interval = agent_cfg.get("check_interval_seconds", 300)
    console.print(
        f"\n[bold green]Agent running[/bold green] — polling ibetcoin every {interval}s\n"
        f"Platforms: {', '.join(enabled_platforms)}\n"
        f"Slippage: line±{agent_cfg['line_slippage']}pt, juice±{agent_cfg['juice_slippage']}\n"
    )

    try:
        cycle = 0
        while True:
            cycle += 1
            console.rule(f"[dim]Cycle #{cycle} — {time.strftime('%H:%M:%S')}[/dim]")

            # 1. Fetch open bets from ibetcoin
            console.print("[dim]Fetching open bets from ibetcoin.win...[/dim]")
            all_bets = await reader.fetch_open_bets()
            new_bets = await reader.fetch_new_bets()  # only NEW tickets

            console.print(f"[dim]Total open: {len(all_bets)} | New this cycle: {len(new_bets)}[/dim]")

            if not new_bets:
                console.print("[dim]No new bets to process[/dim]")
            else:
                await notifier.notify_new_bets_found(new_bets)

                for i, bet in enumerate(new_bets, 1):
                    display_bet(bet, i)
                    console.print(f"  Searching {len(enabled_platforms)} platforms...")
                    found = await search_bet_on_platforms(
                        bet, enabled_platforms, config, notifier, matcher, all_bets
                    )
                    if found == 0:
                        console.print("  [dim]No matches found on any platform[/dim]")

            console.print(f"[dim]Next check in {interval}s...[/dim]")
            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping...[/yellow]")
        await notifier.notify_agent_stopped("Manual stop")


if __name__ == "__main__":
    asyncio.run(main())
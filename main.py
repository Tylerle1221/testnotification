"""
Bet Finder Agent - Main orchestrator.
Supports two modes:
  - Interactive (local): prompts user for bet details via Rich CLI
  - Env-var (Render/CI): reads all config from environment variables
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
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich import box
from rich.logging import RichHandler

from telegram_notifier import TelegramNotifier
from bet_matcher import BetMatcher
from platforms import PLATFORM_MAP

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
)
logger = logging.getLogger("bet_agent")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("playwright").setLevel(logging.WARNING)

console = Console()
CONFIG_PATH = Path(__file__).parent / "config.json"

SPORT_CHOICES = [
    "Football / Soccer", "Basketball", "Tennis", "American Football",
    "Baseball", "Ice Hockey", "Cricket", "Golf", "Other",
]
MARKET_EXAMPLES = [
    "Match Winner (1X2)", "Both Teams to Score", "Over/Under Goals",
    "Asian Handicap", "First Goalscorer", "Correct Score",
    "Double Chance", "Draw No Bet", "Moneyline", "Spread",
    "Player Props", "Other",
]

# ─── True when all required bet env vars are present ─────────────────────────
def _is_env_mode() -> bool:
    return bool(os.environ.get("BET_EVENT"))

# ─── Config helpers ───────────────────────────────────────────────────────────
def load_config() -> dict:
    """
    Build config from environment variables if present, otherwise fall back
    to config.json.  Env vars always override the file for credentials.
    """
    cfg: dict = {
        "telegram": {"bot_token": "", "chat_id": ""},
        "platforms": {},
        "agent": {},
    }

    # Try loading base file first
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)

    # Env-var overrides (take precedence over file)
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg.setdefault("telegram", {})["bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg.setdefault("telegram", {})["chat_id"] = os.environ["TELEGRAM_CHAT_ID"]

    PLATFORM_ENV_MAP = {
        "smash66":     ("SMASH66_USERNAME",     "SMASH66_PASSWORD",     "https://smash66.com/v2/#/sports"),
        "diamondsb":   ("DIAMONDSB_USERNAME",   "DIAMONDSB_PASSWORD",   "https://diamondsb.com/pla/#/msg"),
        "sports411":   ("SPORTS411_USERNAME",   "SPORTS411_PASSWORD",   "https://be.sports411.ag/en/sports/"),
        "leftcoast797":("LEFTCOAST797_USERNAME","LEFTCOAST797_PASSWORD","https://leftcoast797.com/v2/#/sports"),
    }
    for platform, (u_var, p_var, default_url) in PLATFORM_ENV_MAP.items():
        u = os.environ.get(u_var)
        p = os.environ.get(p_var)
        if u or p:
            existing = cfg.get("platforms", {}).get(platform, {})
            cfg.setdefault("platforms", {})[platform] = {
                "enabled": True,
                "url": existing.get("url", default_url),
                "username": u or existing.get("username", ""),
                "password": p or existing.get("password", ""),
            }

    # Agent settings from env
    a = cfg.setdefault("agent", {})
    if os.environ.get("CHECK_INTERVAL"):
        a["check_interval_seconds"] = int(os.environ["CHECK_INTERVAL"])
    if os.environ.get("SIMILARITY_THRESHOLD"):
        a["similarity_threshold"] = int(os.environ["SIMILARITY_THRESHOLD"])
    a.setdefault("check_interval_seconds", 300)
    a.setdefault("headless", True)
    a.setdefault("odds_tolerance", 0.05)
    a.setdefault("similarity_threshold", 75)
    a.setdefault("notify_on_exact", True)
    a.setdefault("notify_on_similar", True)

    return cfg


def get_enabled_platforms(config: dict) -> list[str]:
    return [
        name for name, cfg in config["platforms"].items()
        if cfg.get("enabled", False) and name in PLATFORM_MAP
    ]


# ─── Bet detail loaders ───────────────────────────────────────────────────────
def load_bet_from_env() -> dict:
    """Read bet details from environment variables (Render/CI mode)."""
    sport = os.environ.get("BET_SPORT", "football").lower()
    event = os.environ.get("BET_EVENT", "")
    market = os.environ.get("BET_MARKET", "Moneyline")
    selection = os.environ.get("BET_SELECTION", "")
    odds_raw = os.environ.get("BET_ODDS", "")
    odds = None
    if odds_raw:
        try:
            odds = float(odds_raw)
        except ValueError:
            pass
    return {"sport": sport, "event": event, "market": market,
            "selection": selection, "odds": odds}


def collect_bet_interactive() -> dict:
    """Prompt the user interactively (local mode)."""
    console.print("\n[bold yellow]── Enter Bet Details ──────────────────────────[/bold yellow]")

    sport_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    for i, s in enumerate(SPORT_CHOICES, 1):
        sport_table.add_row(f"[cyan]{i}[/cyan]", s)
    console.print(sport_table)
    sport_idx = Prompt.ask("Select sport number", default="1")
    try:
        sport = SPORT_CHOICES[int(sport_idx) - 1].split(" / ")[0].lower()
    except (ValueError, IndexError):
        sport = sport_idx

    event = Prompt.ask("\n[bold]Event / Match[/bold] (e.g. 'Arsenal vs Chelsea')")

    console.print("\n[dim]Common market types:[/dim]")
    mkt_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
    for i, m in enumerate(MARKET_EXAMPLES, 1):
        mkt_table.add_row(f"[cyan]{i}[/cyan]", m)
    console.print(mkt_table)
    market_input = Prompt.ask("Market type (number or type your own)", default="1")
    try:
        market = MARKET_EXAMPLES[int(market_input) - 1]
    except (ValueError, IndexError):
        market = market_input

    selection = Prompt.ask(
        "\n[bold]Selection[/bold] (e.g. 'Arsenal', 'Over 2.5', 'Yes')", default=""
    )
    odds_raw = Prompt.ask("\n[bold]Target odds[/bold] (decimal, e.g. '2.10')", default="")
    odds = None
    if odds_raw.strip():
        try:
            odds = float(odds_raw.strip())
        except ValueError:
            console.print("[yellow]Could not parse odds — will match without odds filter[/yellow]")

    return {"sport": sport, "event": event, "market": market,
            "selection": selection, "odds": odds}


# ─── Display helpers ──────────────────────────────────────────────────────────
def display_banner():
    console.print(Panel.fit(
        "[bold cyan]Bet Finder Agent[/bold cyan]\n"
        "[dim]Monitors betting platforms & alerts you via Telegram[/dim]",
        border_style="cyan",
    ))


def display_bet_summary(bet: dict, platforms: list[str]):
    table = Table(title="Bet to Find", box=box.ROUNDED, border_style="green")
    table.add_column("Field", style="bold cyan", width=15)
    table.add_column("Value", style="white")
    table.add_row("Sport", bet.get("sport", "").title())
    table.add_row("Event", bet.get("event", ""))
    table.add_row("Market", bet.get("market", ""))
    table.add_row("Selection", bet.get("selection", "") or "[dim]any[/dim]")
    table.add_row("Target Odds", str(bet.get("odds", "")) or "[dim]any[/dim]")
    table.add_row("Platforms", ", ".join(platforms))
    console.print(table)


# ─── Core search loop ─────────────────────────────────────────────────────────
async def run_search_cycle(
    bet: dict,
    platforms: list[str],
    config: dict,
    notifier: TelegramNotifier,
    matcher: BetMatcher,
) -> int:
    agent_cfg = config.get("agent", {})
    headless = agent_cfg.get("headless", True)
    notify_exact = agent_cfg.get("notify_on_exact", True)
    notify_similar = agent_cfg.get("notify_on_similar", True)
    total_matches = 0

    for platform_name in platforms:
        PlatformClass = PLATFORM_MAP[platform_name]
        platform_cfg = config["platforms"][platform_name]
        scraper = PlatformClass(platform_cfg, headless=headless)

        console.print(f"\n[cyan]Checking [bold]{scraper.PLATFORM_NAME}[/bold]...[/cyan]")

        try:
            if not await scraper.start():
                console.print("  [red]Could not start browser[/red]")
                continue

            if not await scraper.login():
                console.print("  [red]Login failed[/red]")
                await notifier.notify_error(scraper.PLATFORM_NAME, "Login failed")
                continue

            console.print("  [green]Logged in[/green]")
            candidates = await scraper.search_bets(bet)
            console.print(f"  [dim]{len(candidates)} raw candidates[/dim]")

            matches = matcher.filter_results(bet, candidates)
            console.print(f"  [yellow]Matched: {len(matches)}[/yellow]")

            for match in matches:
                total_matches += 1
                score = match["similarity_score"]
                label = "EXACT" if match["is_exact"] else f"SIMILAR ({score:.0f}%)"
                console.print(f"  [bold green]{label}[/bold green]: {match.get('event')} | {match.get('odds')}")

                if match["is_exact"] and notify_exact:
                    await notifier.notify_exact_match(scraper.PLATFORM_NAME, bet, match)
                elif match["is_similar"] and not match["is_exact"] and notify_similar:
                    await notifier.notify_similar_match(scraper.PLATFORM_NAME, bet, match, score)

        except Exception as e:
            logger.error(f"Error on {platform_name}: {e}")
            await notifier.notify_error(platform_name, str(e))
        finally:
            await scraper.stop()

    return total_matches


# ─── Main ─────────────────────────────────────────────────────────────────────
async def main():
    display_banner()
    env_mode = _is_env_mode()
    config = load_config()
    agent_cfg = config.get("agent", {})

    # Validate Telegram
    tg_cfg = config.get("telegram", {})
    if not tg_cfg.get("bot_token") or tg_cfg["bot_token"] == "YOUR_TELEGRAM_BOT_TOKEN":
        console.print("[red]TELEGRAM_BOT_TOKEN not configured. Set it as an env var or in config.json.[/red]")
        sys.exit(1)

    if not tg_cfg.get("chat_id"):
        console.print("[red]TELEGRAM_CHAT_ID not configured. Run setup_telegram.py first.[/red]")
        sys.exit(1)

    notifier = TelegramNotifier(tg_cfg["bot_token"], tg_cfg["chat_id"])
    matcher = BetMatcher(
        similarity_threshold=agent_cfg.get("similarity_threshold", 75),
        odds_tolerance=agent_cfg.get("odds_tolerance", 0.05),
    )

    enabled_platforms = get_enabled_platforms(config)
    if not enabled_platforms:
        console.print("[red]No platforms enabled. Set credentials via env vars or config.json.[/red]")
        sys.exit(1)

    # Test Telegram
    console.print("[dim]Testing Telegram connection...[/dim]")
    ok = await notifier.test_connection()
    console.print("[green]Telegram connected[/green]" if ok else "[red]Telegram connection failed[/red]")
    if not ok and not env_mode:
        if not Confirm.ask("Continue anyway?", default=False):
            sys.exit(1)

    # Collect bet details
    if env_mode:
        bet = load_bet_from_env()
        console.print("[dim]Env-var mode: loaded bet from environment variables[/dim]")
        if not bet["event"]:
            console.print("[red]BET_EVENT env var is required.[/red]")
            sys.exit(1)
    else:
        bet = collect_bet_interactive()
        if not Confirm.ask("\n[bold]Start monitoring?[/bold]", default=True):
            console.print("[yellow]Cancelled.[/yellow]")
            sys.exit(0)

    display_bet_summary(bet, enabled_platforms)
    await notifier.notify_agent_started(bet, enabled_platforms)

    interval = agent_cfg.get("check_interval_seconds", 300)
    console.print(f"\n[bold green]Agent running[/bold green] — checking every {interval}s.\n")

    try:
        cycle = 0
        while True:
            cycle += 1
            console.rule(f"[dim]Cycle #{cycle} — {time.strftime('%H:%M:%S')}[/dim]")

            total = await run_search_cycle(bet, enabled_platforms, config, notifier, matcher)

            if total > 0:
                console.print(f"[bold green]{total} match(es) found this cycle[/bold green]")
            else:
                console.print("[dim]No matches this cycle[/dim]")

            console.print(f"[dim]Next check in {interval}s...[/dim]")
            await asyncio.sleep(interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]Stopping...[/yellow]")
        await notifier.notify_agent_stopped("Manual stop")


if __name__ == "__main__":
    asyncio.run(main())
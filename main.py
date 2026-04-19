"""
Bet Finder Agent - Main orchestrator.
Reads open bets from ibetcoin.win, searches platforms, sends Telegram alerts.
Supports /status and /help commands via Telegram.

Speed architecture:
  - One persistent browser session per platform (login once at startup)
  - All platforms searched in PARALLEL for each new bet (~15s vs ~3min)
  - Sessions auto-refresh on expiry
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from rich.console import Console
from rich.panel import Panel
from rich.logging import RichHandler

from telegram_notifier import TelegramNotifier, TelegramCommandServer, AgentState
from bet_matcher import BetMatcher, is_hedge
from ibetcoin_reader import IbetcoinReader
from platform_pool import PlatformPool
from platforms import PLATFORM_MAP

# ─── Logging ─────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
)
logger = logging.getLogger("bet_agent")
for noisy in ("httpx", "telegram", "playwright", "apscheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

console = Console()
CONFIG_PATH = Path(__file__).parent / "config.json"


# ─── Health server ────────────────────────────────────────────────────────────

def _start_health_server():
    """Start a minimal HTTP server for Render health checks — always runs."""
    port = int(os.environ.get("PORT", 10000))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")
        def log_message(self, *args):
            pass

    Thread(target=HTTPServer(("0.0.0.0", port), Handler).serve_forever, daemon=True).start()
    logger.info(f"Health server on port {port}")


# ─── Config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    cfg: dict = {"telegram": {}, "platforms": {}, "agent": {}, "ibetcoin": {}}

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)

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
    a.setdefault("check_interval_seconds", 60)   # default 60s — fast enough for live bets
    a.setdefault("headless", True)
    a.setdefault("odds_tolerance", 0.05)
    a.setdefault("similarity_threshold", 75)
    a.setdefault("line_slippage", 1.0)
    a.setdefault("juice_slippage", 20)
    a.setdefault("notify_on_exact", True)
    a.setdefault("notify_on_similar", True)
    return cfg


def get_enabled_platforms(config: dict) -> list[str]:
    return [
        n for n, c in config.get("platforms", {}).items()
        if c.get("enabled", False) and n in PLATFORM_MAP
    ]


# ─── Core search — parallel across all platforms ──────────────────────────────

async def process_bet(
    bet: dict,
    pool: PlatformPool,
    notifier: TelegramNotifier,
    matcher: BetMatcher,
    all_open_bets: list[dict],
    state: AgentState,
    cfg: dict,
) -> int:
    agent_cfg = cfg.get("agent", {})
    notify_exact = agent_cfg.get("notify_on_exact", True)
    notify_similar = agent_cfg.get("notify_on_similar", True)
    total_matches = 0

    side_str = ""
    if bet.get("bet_side") and bet.get("line"):
        side_str = f" {bet['bet_side'].upper()} {bet['line']}"
    odds_str = str(bet.get("odds_american") or bet.get("odds") or "")
    console.print(
        f"  [cyan]Searching:[/cyan] [{bet.get('sport','')}] [bold]{bet.get('event','?')[:55]}[/bold]"
        f"{side_str}  odds={odds_str}  ticket={bet.get('ticket_id')}"
    )

    t0 = time.time()
    # Search ALL platforms in parallel — the big speed win
    results_by_platform = await pool.search_all_parallel(bet)
    elapsed = time.time() - t0
    console.print(f"  [dim]Parallel search complete in {elapsed:.1f}s[/dim]")

    for platform_name, candidates in results_by_platform.items():
        if not candidates:
            continue

        matches = matcher.filter_results(bet, candidates)
        console.print(f"  [{platform_name}] {len(candidates)} candidates → [yellow]{len(matches)} matches[/yellow]")

        for match in matches:
            total_matches += 1
            state.total_matches_found += 1
            score = match["similarity_score"]

            if is_hedge(all_open_bets, match, same_account=True):
                console.print(f"    [dim]↳ hedge detected, skipping[/dim]")
                continue

            label = "EXACT" if match["is_exact"] else f"SIMILAR ({score:.0f}%)"
            console.print(
                f"    [bold green]{label}[/bold green] on [bold]{platform_name}[/bold]: "
                f"{match.get('event','?')[:50]} | odds={match.get('odds')}"
            )

            if match["is_exact"] and notify_exact:
                await notifier.notify_exact_match(platform_name, bet, match)
            elif match["is_similar"] and not match["is_exact"] and notify_similar:
                await notifier.notify_similar_match(platform_name, bet, match, score)

    return total_matches


# ─── Main loop ────────────────────────────────────────────────────────────────

async def polling_loop(
    config: dict,
    pool: PlatformPool,
    notifier: TelegramNotifier,
    state: AgentState,
    reader: IbetcoinReader,
):
    agent_cfg = config.get("agent", {})
    ibet = config.get("ibetcoin", {})

    matcher = BetMatcher(
        similarity_threshold=agent_cfg.get("similarity_threshold", 75),
        odds_tolerance=agent_cfg.get("odds_tolerance", 0.05),
        line_slippage=agent_cfg.get("line_slippage", 1.0),
        juice_slippage=agent_cfg.get("juice_slippage", 20),
    )

    interval = agent_cfg.get("check_interval_seconds", 300)
    active = pool.platform_names()

    console.print(
        f"\n[bold green]Agent running[/bold green]\n"
        f"  Platforms ready: {', '.join(active)}\n"
        f"  ibetcoin poll interval: {interval}s\n"
        f"  Slippage: line±{agent_cfg['line_slippage']}pt, juice±{agent_cfg['juice_slippage']}\n"
        f"  Search mode: [bold cyan]PARALLEL[/bold cyan] (all platforms at once)\n"
    )
    state.platforms_enabled = active
    await notifier.notify_agent_started(active)

    while True:
        state.total_cycles += 1
        from datetime import datetime, timezone
        state.last_cycle_at = datetime.now(timezone.utc)
        console.rule(f"[dim]Cycle #{state.total_cycles} — {time.strftime('%H:%M:%S')}[/dim]")

        console.print("[dim]Fetching open bets from ibetcoin.win...[/dim]")
        all_bets = await reader.fetch_open_bets()
        new_bets = await reader.fetch_new_bets()
        state.total_bets_scraped += len(new_bets)

        console.print(f"[dim]Total open: {len(all_bets)} | New this cycle: {len(new_bets)}[/dim]")

        if not new_bets:
            console.print("[dim]No new bets to process[/dim]")
        else:
            await notifier.notify_new_bets_found(new_bets)

            for bet in new_bets:
                found = await process_bet(
                    bet, pool, notifier, matcher, all_bets, state, config
                )
                if found == 0:
                    console.print("  [dim]No matches found on any platform[/dim]")

        console.print(f"[dim]Next check in {interval}s...[/dim]")
        await asyncio.sleep(interval)


# ─── Entry point ─────────────────────────────────────────────────────────────

async def main():
    _start_health_server()
    console.print(Panel.fit(
        "[bold cyan]Bet Finder Agent[/bold cyan]\n"
        "[dim]ibetcoin.win → parallel platform search → Telegram alerts[/dim]",
        border_style="cyan"
    ))

    config = load_config()
    tg = config.get("telegram", {})

    if not tg.get("bot_token"):
        console.print("[red]TELEGRAM_BOT_TOKEN not set.[/red]"); sys.exit(1)
    if not tg.get("chat_id"):
        console.print("[red]TELEGRAM_CHAT_ID not set. Run setup_telegram.py first.[/red]"); sys.exit(1)
    if not config.get("ibetcoin", {}).get("username"):
        console.print("[red]IBETCOIN_USERNAME not set.[/red]"); sys.exit(1)

    enabled_platforms = get_enabled_platforms(config)
    if not enabled_platforms:
        console.print("[red]No platforms enabled.[/red]"); sys.exit(1)

    state = AgentState()
    notifier = TelegramNotifier(tg["bot_token"], tg["chat_id"])
    cmd_server = TelegramCommandServer(tg["bot_token"], tg["chat_id"], state)

    # Test Telegram
    console.print("[dim]Testing Telegram...[/dim]")
    await notifier.test_connection()

    # Start command listener
    await cmd_server.start()

    # Initialize platform pool — login ONCE, keep browsers alive
    pool = PlatformPool(config)
    console.print(f"[dim]Starting browsers and logging in to {len(enabled_platforms)} platforms...[/dim]")
    ok = await pool.initialize(enabled_platforms)
    if ok == 0:
        console.print("[red]All platform logins failed.[/red]"); sys.exit(1)

    # ── IMPORTANT: mark ALL currently open bets as already seen ──────────────
    # This ensures we only process BRAND NEW bets from this point forward,
    # not the bets that were already sitting on ibetcoin.win when we started.
    ibet = config.get("ibetcoin", {})
    agent_cfg = config.get("agent", {})
    reader = IbetcoinReader(
        username=ibet["username"],
        password=ibet["password"],
        headless=agent_cfg.get("headless", True),
    )
    console.print("[dim]Checking current open bets (marking as already seen)...[/dim]")
    existing = await reader.fetch_new_bets()  # adds all current IDs to _known_tickets
    if existing:
        console.print(
            f"[dim]Skipped {len(existing)} pre-existing open bet(s) — "
            f"only NEW bets will trigger alerts.[/dim]"
        )
    else:
        console.print("[dim]No pre-existing open bets. Ready for new bets.[/dim]")

    try:
        await polling_loop(config, pool, notifier, state, reader)
    except (KeyboardInterrupt, asyncio.CancelledError):
        console.print("\n[yellow]Stopping...[/yellow]")
        await notifier.notify_agent_stopped("Manual stop")
    finally:
        await cmd_server.stop()
        await pool.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
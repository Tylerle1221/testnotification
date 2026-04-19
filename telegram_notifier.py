"""
Telegram notification + command server for the Bet Finder Agent.
Handles outbound alerts AND inbound /status and /help commands.
"""

import asyncio
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from telegram import Bot, Update
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger(__name__)


# ─── Shared agent state ───────────────────────────────────────────────────────

@dataclass
class AgentState:
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_cycle_at: Optional[datetime] = None
    total_cycles: int = 0
    total_bets_scraped: int = 0
    total_matches_found: int = 0
    last_error: str = ""
    platforms_enabled: list = field(default_factory=list)
    ibetcoin_url: str = "https://reports.ibetcoin.win/Report/OpenBets.aspx"
    platform_urls: dict = field(default_factory=dict)

    @property
    def uptime_str(self) -> str:
        delta = datetime.now(timezone.utc) - self.started_at
        h, rem = divmod(int(delta.total_seconds()), 3600)
        m, s = divmod(rem, 60)
        return f"{h}h {m}m {s}s"

    @property
    def last_cycle_str(self) -> str:
        if not self.last_cycle_at:
            return "never"
        delta = datetime.now(timezone.utc) - self.last_cycle_at
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        return f"{secs // 60}m ago"


# ─── Quick HTTP ping (no browser) ────────────────────────────────────────────

def _ping(url: str, timeout: int = 6) -> tuple[bool, int]:
    """Returns (reachable, http_status_code)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True, resp.status
    except urllib.error.HTTPError as e:
        return True, e.code   # site responded (even 4xx = reachable)
    except Exception:
        return False, 0


# ─── TelegramNotifier ─────────────────────────────────────────────────────────

class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.bot = Bot(token=bot_token)

    async def send_message(self, text: str) -> bool:
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode=ParseMode.HTML
            )
            return True
        except TelegramError as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    async def notify_exact_match(self, platform: str, bet: dict, found_bet: dict) -> bool:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = (
            f"🎯 <b>EXACT BET FOUND!</b>\n\n"
            f"⏰ {ts}  |  🏦 <b>{platform}</b>\n\n"
            f"📋 <b>Source bet:</b>\n"
            f"  [{bet.get('sport','')}] {bet.get('event','?')[:60]}\n"
            f"  Market: {bet.get('market','?')}  |  Sel: {bet.get('selection','?')}\n"
            f"  Odds: {bet.get('odds_american') or bet.get('odds','?')}\n\n"
            f"✅ <b>Match on {platform}:</b>\n"
            f"  {found_bet.get('event','?')[:60]}\n"
            f"  Odds: {found_bet.get('odds','?')}\n"
            f"  🔗 {found_bet.get('url','')}"
        )
        return await self.send_message(msg)

    async def notify_similar_match(self, platform: str, bet: dict, found_bet: dict, similarity: float) -> bool:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        msg = (
            f"🔍 <b>SIMILAR BET FOUND ({similarity:.0f}%)</b>\n\n"
            f"⏰ {ts}  |  🏦 <b>{platform}</b>\n\n"
            f"📋 <b>Source bet:</b>\n"
            f"  [{bet.get('sport','')}] {bet.get('event','?')[:60]}\n"
            f"  Market: {bet.get('market','?')}  |  Sel: {bet.get('selection','?')}\n"
            f"  Odds: {bet.get('odds_american') or bet.get('odds','?')}\n\n"
            f"🔄 <b>Similar on {platform}:</b>\n"
            f"  {found_bet.get('event','?')[:60]}\n"
            f"  Odds: {found_bet.get('odds','?')}\n"
            f"  🔗 {found_bet.get('url','')}"
        )
        return await self.send_message(msg)

    async def notify_agent_started(self, platforms: list[str]) -> bool:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        p_list = "  " + "\n  ".join(f"• {p}" for p in platforms)
        msg = (
            f"🤖 <b>Bet Finder Agent Started</b>\n\n"
            f"⏰ {ts}\n\n"
            f"📡 Reading bets from ibetcoin.win\n"
            f"🏦 Monitoring platforms:\n{p_list}\n\n"
            f"Send /status to check live connection health."
        )
        return await self.send_message(msg)

    async def notify_agent_stopped(self, reason: str = "Manual stop") -> bool:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return await self.send_message(
            f"🛑 <b>Bet Finder Agent Stopped</b>\n\n⏰ {ts}\n📝 {reason}"
        )

    async def notify_error(self, platform: str, error: str) -> bool:
        return await self.send_message(
            f"⚠️ <b>Error on {platform}</b>\n❌ {error[:200]}"
        )

    async def notify_new_bets_found(self, bets: list[dict]) -> bool:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [f"📥 <b>{len(bets)} new open bet(s) from ibetcoin.win</b>\n⏰ {ts}\n"]
        for b in bets[:5]:
            side = b.get("bet_side", "")
            line_str = f" {side.upper()} {b.get('line')}" if side and b.get("line") else ""
            odds = b.get("odds_american") or b.get("odds") or ""
            lines.append(
                f"  🎫 #{b.get('ticket_id')} [{b.get('sport','')}] "
                f"{b.get('event','?')[:50]}{line_str} @ {odds}"
            )
        if len(bets) > 5:
            lines.append(f"  ...and {len(bets)-5} more")
        lines.append("\nSearching platforms now...")
        return await self.send_message("\n".join(lines))

    async def test_connection(self) -> bool:
        return await self.send_message(
            "✅ <b>Bet Finder Agent - Connected</b>\n\n"
            "Bot is online and ready.\nSend /status to check all connections."
        )


# ─── Telegram Command Server ──────────────────────────────────────────────────

class TelegramCommandServer:
    """
    Runs a background Telegram polling loop that handles /status and /help.
    Share an AgentState instance to serve live data.
    """

    def __init__(self, bot_token: str, allowed_chat_id: str, state: AgentState):
        self.bot_token = bot_token
        self.allowed_chat_id = str(allowed_chat_id)
        self.state = state
        self._app: Optional[Application] = None

    def _is_allowed(self, update: Update) -> bool:
        return str(update.effective_chat.id) == self.allowed_chat_id

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        await update.message.reply_text("🔍 Checking connections, please wait...")

        lines = [
            f"<b>Bet Finder Agent — Status</b>",
            f"",
            f"⏱ <b>Uptime:</b> {self.state.uptime_str}",
            f"🔄 <b>Cycles run:</b> {self.state.total_cycles}",
            f"📥 <b>Bets scraped:</b> {self.state.total_bets_scraped}",
            f"🎯 <b>Matches found:</b> {self.state.total_matches_found}",
            f"🕐 <b>Last cycle:</b> {self.state.last_cycle_str}",
            f"",
            f"<b>Connection Health:</b>",
        ]

        # Telegram itself
        lines.append(f"  ✅ Telegram — connected (you sent this command)")

        # ibetcoin.win
        ok, code = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _ping(self.state.ibetcoin_url)
        )
        icon = "✅" if ok else "❌"
        lines.append(f"  {icon} ibetcoin.win — {'reachable' if ok else 'UNREACHABLE'} (HTTP {code})")

        # Each platform
        PLATFORM_DISPLAY = {
            "smash66":     ("Smash66",     "https://smash66.com/"),
            "diamondsb":   ("DiamondSB",   "https://diamondsb.com/"),
            "sports411":   ("Sports411",   "https://be.sports411.ag/"),
            "leftcoast797":("Leftcoast797","https://leftcoast797.com/"),
        }
        for key, (name, url) in PLATFORM_DISPLAY.items():
            if key in self.state.platforms_enabled:
                ok2, code2 = await asyncio.get_event_loop().run_in_executor(
                    None, lambda u=url: _ping(u)
                )
                icon2 = "✅" if ok2 else "❌"
                lines.append(f"  {icon2} {name} — {'reachable' if ok2 else 'UNREACHABLE'} (HTTP {code2})")

        if self.state.last_error:
            lines += ["", f"⚠️ <b>Last error:</b> {self.state.last_error[:150]}"]

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode=ParseMode.HTML
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update):
            return
        await update.message.reply_text(
            "<b>Bet Finder Agent — Commands</b>\n\n"
            "/status — Live health check of all connections\n"
            "/help   — Show this help message\n\n"
            "The agent polls ibetcoin.win every 5 minutes for new open bets, "
            "then searches Smash66, DiamondSB, Sports411, and Leftcoast797 "
            "for matching lines. You get notified here when a match is found.",
            parse_mode=ParseMode.HTML
        )

    async def start(self):
        """Build the Application and start polling for commands in background."""
        self._app = Application.builder().token(self.bot_token).build()
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("help", self._cmd_help))

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram command server started (/status, /help)")

    async def stop(self):
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
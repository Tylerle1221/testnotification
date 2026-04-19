"""
Telegram notification module for the Bet Finder Agent.
Sends alerts when matching or similar bets are found on platforms.
"""

import asyncio
import logging
from datetime import datetime
from typing import Optional
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


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
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def notify_exact_match(self, platform: str, bet: dict, found_bet: dict) -> bool:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = (
            f"🎯 <b>EXACT BET FOUND!</b>\n\n"
            f"⏰ <b>Time:</b> {timestamp}\n"
            f"🏦 <b>Platform:</b> {platform}\n\n"
            f"📋 <b>Your Bet:</b>\n"
            f"  🏟 Event: {bet.get('event', 'N/A')}\n"
            f"  ⚽ Sport: {bet.get('sport', 'N/A')}\n"
            f"  📊 Market: {bet.get('market', 'N/A')}\n"
            f"  ✅ Selection: {bet.get('selection', 'N/A')}\n"
            f"  💰 Target Odds: {bet.get('odds', 'N/A')}\n\n"
            f"✅ <b>Found Bet:</b>\n"
            f"  🏟 Event: {found_bet.get('event', 'N/A')}\n"
            f"  📊 Market: {found_bet.get('market', 'N/A')}\n"
            f"  ✅ Selection: {found_bet.get('selection', 'N/A')}\n"
            f"  💰 Odds: {found_bet.get('odds', 'N/A')}\n"
            f"  🔗 URL: {found_bet.get('url', 'N/A')}"
        )
        return await self.send_message(message)

    async def notify_similar_match(self, platform: str, bet: dict, found_bet: dict, similarity: float) -> bool:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = (
            f"🔍 <b>SIMILAR BET FOUND!</b> ({similarity:.0f}% match)\n\n"
            f"⏰ <b>Time:</b> {timestamp}\n"
            f"🏦 <b>Platform:</b> {platform}\n\n"
            f"📋 <b>Your Bet:</b>\n"
            f"  🏟 Event: {bet.get('event', 'N/A')}\n"
            f"  ⚽ Sport: {bet.get('sport', 'N/A')}\n"
            f"  📊 Market: {bet.get('market', 'N/A')}\n"
            f"  ✅ Selection: {bet.get('selection', 'N/A')}\n"
            f"  💰 Target Odds: {bet.get('odds', 'N/A')}\n\n"
            f"🔄 <b>Similar Bet:</b>\n"
            f"  🏟 Event: {found_bet.get('event', 'N/A')}\n"
            f"  📊 Market: {found_bet.get('market', 'N/A')}\n"
            f"  ✅ Selection: {found_bet.get('selection', 'N/A')}\n"
            f"  💰 Odds: {found_bet.get('odds', 'N/A')}\n"
            f"  🔗 URL: {found_bet.get('url', 'N/A')}"
        )
        return await self.send_message(message)

    async def notify_agent_started(self, bet: dict, platforms: list[str]) -> bool:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        platform_list = "\n".join(f"  • {p}" for p in platforms)
        message = (
            f"🤖 <b>Bet Finder Agent Started</b>\n\n"
            f"⏰ <b>Time:</b> {timestamp}\n\n"
            f"🔎 <b>Searching for:</b>\n"
            f"  🏟 Event: {bet.get('event', 'N/A')}\n"
            f"  ⚽ Sport: {bet.get('sport', 'N/A')}\n"
            f"  📊 Market: {bet.get('market', 'N/A')}\n"
            f"  ✅ Selection: {bet.get('selection', 'N/A')}\n"
            f"  💰 Target Odds: {bet.get('odds', 'N/A')}\n\n"
            f"🏦 <b>Monitoring platforms:</b>\n{platform_list}"
        )
        return await self.send_message(message)

    async def notify_agent_stopped(self, reason: str = "Manual stop") -> bool:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = (
            f"🛑 <b>Bet Finder Agent Stopped</b>\n\n"
            f"⏰ <b>Time:</b> {timestamp}\n"
            f"📝 <b>Reason:</b> {reason}"
        )
        return await self.send_message(message)

    async def notify_error(self, platform: str, error: str) -> bool:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        message = (
            f"⚠️ <b>Platform Error</b>\n\n"
            f"⏰ <b>Time:</b> {timestamp}\n"
            f"🏦 <b>Platform:</b> {platform}\n"
            f"❌ <b>Error:</b> {error}"
        )
        return await self.send_message(message)

    async def test_connection(self) -> bool:
        message = (
            "✅ <b>Bet Finder Agent - Connection Test</b>\n\n"
            "Your Telegram bot is working correctly!\n"
            "You will receive notifications here when bets are found."
        )
        return await self.send_message(message)

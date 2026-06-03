"""Pariston Discord bot — Milestone 1.

Whole-channel (or mention) chatbot powered by a local Ollama model. Run with:

    python bot.py

Requires a populated .env (see .env.example) and a running Ollama server.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import aiohttp
import discord

from config import config
from conversation.history import ChannelHistory
from llm.ollama_client import OllamaClient
from persona import pariston

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("pariston.bot")

DISCORD_MAX_LEN = 2000


def _chunk(text: str, limit: int = DISCORD_MAX_LEN) -> list[str]:
    """Split a reply into Discord-sized pieces, preferring line breaks."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


class ParistonBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # privileged — enable in the Developer Portal
        super().__init__(intents=intents)

        self.history = ChannelHistory()
        self.ollama = OllamaClient()
        # One in-flight Ollama call per channel: serialize to protect host RAM.
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        log.info(
            "trigger_mode=%s model=%s allowed_channels=%s",
            config.trigger_mode,
            config.ollama_model,
            config.allowed_channel_ids or "ALL",
        )

    def _should_respond(self, message: discord.Message) -> bool:
        if message.author.bot:
            return False
        if self.user and message.author.id == self.user.id:
            return False
        if not message.content.strip():
            return False

        is_dm = message.guild is None
        if config.trigger_mode == "mention":
            mentioned = self.user in message.mentions if self.user else False
            return is_dm or mentioned

        # channel mode
        if is_dm:
            return True
        if config.allowed_channel_ids and message.channel.id not in config.allowed_channel_ids:
            return False
        return True

    @staticmethod
    def _clean_content(message: discord.Message) -> str:
        """Use display-name-resolved content, minus a leading bot mention."""
        text = message.clean_content.strip()
        return text

    async def on_message(self, message: discord.Message) -> None:
        if not self._should_respond(message):
            return

        channel_id = message.channel.id
        user_text = self._clean_content(message)
        self.history.add_user(channel_id, user_text)

        messages = pariston.build_messages(self.history.get(channel_id), user_text)

        try:
            async with self._locks[channel_id]:
                async with message.channel.typing():
                    reply = await self.ollama.chat(messages)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Ollama call failed: %s", exc)
            reply = pariston.FALLBACK_REPLY
        except Exception:  # noqa: BLE001 — never crash the message loop
            log.exception("Unexpected error generating reply")
            reply = pariston.FALLBACK_REPLY

        reply = reply.strip() or pariston.FALLBACK_REPLY
        self.history.add_assistant(channel_id, reply)

        for chunk in _chunk(reply):
            await message.channel.send(chunk)


def main() -> None:
    config.validate()
    ParistonBot().run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

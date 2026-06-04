"""Pariston Discord bot — Milestone 2.

Whole-channel (or mention) chatbot powered by a local Ollama model, with speaker
identity, persistent per-channel memory (facts + running summary), and optional
proactivity (revives quiet channels with an in-character callback). Run with:

    python bot.py

Requires a populated .env (see .env.example) and a running Ollama server.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict

import aiohttp
import discord

from config import config
from conversation.history import ChannelHistory
from llm.ollama_client import OllamaClient
from memory.store import MemoryStore
from persona import pariston

LOG_FILE = "bot.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),  # your terminal
        logging.FileHandler(LOG_FILE, encoding="utf-8"),  # persistent file for diagnostics
    ],
)
log = logging.getLogger("pariston.bot")

DISCORD_MAX_LEN = 2000
# How many recent turns to feed the summarizer.
SUMMARY_WINDOW = 24
# Never re-engage a channel that's been silent longer than this (don't necro-post).
PROACTIVE_MAX_IDLE_SECONDS = 6 * 3600


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


# The persona addresses whoever it's replying to with the literal token "@user"
# (also tolerate "@you") — we swap it for a real Discord mention so it pings them
# and we never guess a name. Case-insensitive.
_USER_TOKEN_RE = re.compile(r"@(?:user|you)\b", re.IGNORECASE)


def _apply_mentions(text: str, mention: str | None) -> str:
    """Replace the @user/@you addressing token with a real mention (or 'you')."""
    return _USER_TOKEN_RE.sub(mention or "you", text)


# Pariston never uses emoji — strip any the model emits as a safety net. Covers the
# common pictograph/symbol/dingbat blocks and variation selectors; leaves the em-dash
# and ordinary punctuation alone.
_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff"  # emoticons, pictographs, transport, supplemental symbols
    "\U00002600-\U000027bf"  # misc symbols + dingbats (☺ ✨ ✔ etc.)
    "\U00002b00-\U00002bff"  # misc symbols and arrows (stars etc.)
    "\U0001f1e6-\U0001f1ff"  # regional indicators (flags)
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\U00002300-\U000023ff]+",  # technical (⌛ ⏰ etc.)
    flags=re.UNICODE,
)


def _strip_emoji(text: str) -> str:
    cleaned = _EMOJI_RE.sub("", text)
    # Collapse the spaces an emoji removal can leave behind.
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _parse_memory(raw: str) -> tuple[str, dict[str, list[str]]] | None:
    """Pull {"summary": str, "facts": {name: [..]}} out of the model's reply.

    Tolerant of code fences and surrounding prose; returns None if unparseable."""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    summary_field = data.get("summary", "")
    facts_field = data.get("facts", {})
    # Salvage a common small-model slip: the per-person dict placed under "summary"
    # with "facts" left empty.
    if isinstance(summary_field, dict) and not facts_field:
        facts_field, summary_field = summary_field, ""
    summary = summary_field.strip() if isinstance(summary_field, str) else ""
    facts: dict[str, list[str]] = {}
    if isinstance(facts_field, dict):
        for person, items in facts_field.items():
            if isinstance(items, list):
                facts[str(person)] = [str(i).strip() for i in items if str(i).strip()]
            elif isinstance(items, str) and items.strip():
                facts[str(person)] = [items.strip()]
    return summary, facts


class ParistonBot(discord.Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True  # privileged — enable in the Developer Portal
        super().__init__(intents=intents)

        self.store = MemoryStore()
        self.history = ChannelHistory(self.store)
        self.ollama = OllamaClient()
        # One in-flight Ollama call per channel: serialize to protect host RAM.
        self._locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def setup_hook(self) -> None:
        if config.proactive_enabled:
            self.loop.create_task(self._proactive_loop())

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        log.info(
            "trigger_mode=%s model=%s allowed_channels=%s proactive=%s",
            config.trigger_mode,
            config.ollama_model,
            config.allowed_channel_ids or "ALL",
            config.proactive_enabled,
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
        """Display-name-resolved content (mentions rendered as names)."""
        return message.clean_content.strip()

    async def on_message(self, message: discord.Message) -> None:
        if not self._should_respond(message):
            return

        channel_id = message.channel.id
        user_name = message.author.display_name
        user_text = self._clean_content(message)
        self.history.add_user(channel_id, user_name, user_text)
        log.info("[#%s] %s: %s", channel_id, user_name, user_text)

        memory_block = self.store.memory_block(channel_id)
        messages = pariston.build_messages(
            self.history.get(channel_id), user_name, user_text, memory_block
        )

        started = time.monotonic()
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

        elapsed = time.monotonic() - started
        reply = reply.strip() or pariston.FALLBACK_REPLY
        # Store the model-facing form (with the @user token); send the rendered form
        # (with a real mention) so it pings the right person and never guesses a name.
        self.history.add_assistant(channel_id, reply)
        log.info("[#%s] Pariston (%.1fs): %s", channel_id, elapsed, reply)

        display = _strip_emoji(_apply_mentions(reply, message.author.mention))
        chunks = _chunk(display)
        # Reply to the user's message with mention_author=True so they're reliably
        # pinged; send any overflow chunks as plain follow-ups.
        await message.reply(chunks[0], mention_author=True)
        for chunk in chunks[1:]:
            await message.channel.send(chunk)

        # Refresh distilled memory in the background (every N turns) — never blocks
        # the user's reply.
        if self.store.should_summarize(channel_id):
            self.loop.create_task(self._summarize(channel_id))

    async def _summarize(self, channel_id: int) -> None:
        """Distill recent conversation into durable facts + a running summary."""
        async with self._locks[channel_id]:
            mem = self.store.get(channel_id)
            history = self.store.recent_turns(channel_id, SUMMARY_WINDOW)
            msgs = pariston.build_summary_messages(history, mem.summary, mem.facts)
            try:
                raw = await self.ollama.chat(msgs, temperature=0.2, max_tokens=400)
            except Exception:  # noqa: BLE001
                log.exception("[#%s] memory summarization failed", channel_id)
                return
        parsed = _parse_memory(raw)
        if parsed is None:
            log.warning("[#%s] memory JSON unparseable: %r", channel_id, raw[:120])
            self.store.save_summary(channel_id, "", {})  # reset counter, back off
            return
        summary, facts = parsed
        self.store.save_summary(channel_id, summary, facts)
        log.info("[#%s] memory updated (people: %s)", channel_id, list(facts))

    # ── proactivity ───────────────────────────────────────────────────────────
    def _proactive_due(self, channel_id: int) -> bool:
        if not config.proactive_enabled:
            return False
        if config.allowed_channel_ids and channel_id not in config.allowed_channel_ids:
            return False
        mem = self.store.get(channel_id)
        if mem.last_activity <= 0 or not mem.human_since_proactive:
            return False
        now = time.time()
        idle = now - mem.last_activity
        if idle < config.idle_minutes * 60 or idle > PROACTIVE_MAX_IDLE_SECONDS:
            return False
        if now - mem.last_proactive < config.proactive_cooldown_minutes * 60:
            return False
        if self.store.proactive_count_last_hour(channel_id) >= config.proactive_max_per_hour:
            return False
        return True

    async def _proactive_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                for channel_id in self.store.channels():
                    if self._proactive_due(channel_id):
                        await self._proactive_speak(channel_id)
            except Exception:  # noqa: BLE001
                log.exception("proactive loop error")
            await asyncio.sleep(config.proactive_check_seconds)

    async def _proactive_speak(self, channel_id: int) -> None:
        channel = self.get_channel(channel_id)
        if channel is None:
            return
        async with self._locks[channel_id]:
            if not self._proactive_due(channel_id):  # re-check under lock
                return
            history = self.history.get(channel_id)
            memory_block = self.store.memory_block(channel_id)
            msgs = pariston.build_proactive_messages(history, memory_block)
            try:
                async with channel.typing():
                    line = await self.ollama.chat(msgs)
            except Exception:  # noqa: BLE001
                log.exception("[#%s] proactive generation failed", channel_id)
                return
            line = line.strip()
            if not line:
                return
            self.history.add_assistant(channel_id, line)
            self.store.mark_proactive(channel_id)
        log.info("[#%s] Pariston (proactive): %s", channel_id, line)
        # No single addressee for an unprompted line — render @user as plain "you".
        display = _strip_emoji(_apply_mentions(line, None))
        for chunk in _chunk(display):
            await channel.send(chunk)


def main() -> None:
    config.validate()
    ParistonBot().run(config.discord_token, log_handler=None)


if __name__ == "__main__":
    main()

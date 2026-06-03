"""Async client for a local Ollama server's /api/chat endpoint.

Keeps the persona/transport concerns out of bot.py. Disables reasoning-model
"thinking" (qwen3 emits <think>...</think>) and strips any that leaks through.
"""
from __future__ import annotations

import logging
import re

import aiohttp

from config import config

log = logging.getLogger(__name__)

# qwen3 and other reasoning models can emit a <think>...</think> block. We ask
# Ollama to skip it (think=false), and strip it as a belt-and-suspenders fallback.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    cleaned = _THINK_RE.sub("", text)
    # If a stray unmatched <think> remains (truncated), drop everything before it.
    if "</think>" in cleaned:
        cleaned = cleaned.split("</think>", 1)[1]
    return cleaned.strip()


class OllamaClient:
    """Thin async wrapper around POST {OLLAMA_URL}/api/chat."""

    def __init__(self) -> None:
        self._url = f"{config.ollama_url}/api/chat"

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """Send a chat completion request. Returns the assistant text.

        Raises aiohttp/asyncio errors on transport failure — the caller decides
        how to present a fallback so it can stay in character.
        """
        payload = {
            "model": config.ollama_model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": config.temperature,
                "top_p": config.top_p,
                "repeat_penalty": config.repeat_penalty,
                "num_ctx": config.num_ctx,
            },
        }
        timeout = aiohttp.ClientTimeout(total=config.ollama_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self._url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()

        content = (data.get("message") or {}).get("content", "")
        return _strip_thinking(content)

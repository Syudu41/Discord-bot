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

# qwen3 and other reasoning models can emit a <think>...</think> block. The
# `/no_think` directive (added to the system prompt) is what actually suppresses
# reasoning for qwen3 — the API `think` flag is ignored by some model/Ollama
# builds. We still strip any <think> block as a belt-and-suspenders fallback.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_thinking(text: str) -> str:
    cleaned = _THINK_RE.sub("", text)
    # If a stray unmatched <think> remains (truncated), drop everything before it.
    if "</think>" in cleaned:
        cleaned = cleaned.split("</think>", 1)[1]
    return cleaned.strip()


def _apply_no_think(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Append the /no_think directive to the system message (qwen3 honors this)."""
    out = [dict(m) for m in messages]
    for m in out:
        if m.get("role") == "system":
            m["content"] = m["content"].rstrip() + "\n\n/no_think"
            return out
    # No system message present — prepend one carrying just the directive.
    out.insert(0, {"role": "system", "content": "/no_think"})
    return out


class OllamaClient:
    """Thin async wrapper around POST {OLLAMA_URL}/api/chat."""

    def __init__(self) -> None:
        self._url = f"{config.ollama_url}/api/chat"

    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Send a chat completion request. Returns the assistant text.

        `temperature`/`max_tokens` override the configured defaults — used by
        background tasks (e.g. low-temperature memory summarization).

        Raises aiohttp/asyncio errors on transport failure — the caller decides
        how to present a fallback so it can stay in character.
        """
        if config.disable_thinking:
            messages = _apply_no_think(messages)

        payload = {
            "model": config.ollama_model,
            "messages": messages,
            "stream": False,
            "keep_alive": config.ollama_keep_alive,
            "options": {
                "temperature": config.temperature if temperature is None else temperature,
                "top_p": config.top_p,
                "repeat_penalty": config.repeat_penalty,
                "num_ctx": config.num_ctx,
                "num_predict": config.max_tokens if max_tokens is None else max_tokens,
            },
        }
        timeout = aiohttp.ClientTimeout(total=config.ollama_timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self._url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()

        content = (data.get("message") or {}).get("content", "")
        return _strip_thinking(content)

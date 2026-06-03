"""Configuration loaded from environment / .env.

Single source of truth for runtime settings. Import `config` and read attributes;
call `config.validate()` once at startup to fail fast on misconfiguration.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"{name} must be a number, got {raw!r}")


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"{name} must be an integer, got {raw!r}")


def _get_channel_ids(name: str) -> set[int]:
    raw = os.getenv(name, "") or ""
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            raise ValueError(f"{name} contains a non-numeric channel id: {part!r}")
    return ids


@dataclass
class Config:
    # Discord
    discord_token: str = os.getenv("DISCORD_TOKEN", "")
    allowed_channel_ids: set[int] = field(default_factory=lambda: _get_channel_ids("ALLOWED_CHANNEL_IDS"))
    trigger_mode: str = (os.getenv("TRIGGER_MODE", "channel") or "channel").strip().lower()

    # Ollama
    ollama_url: str = (os.getenv("OLLAMA_URL", "http://localhost:11434") or "").rstrip("/")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen3:4b")
    ollama_timeout: int = field(default_factory=lambda: _get_int("OLLAMA_TIMEOUT", 120))

    # Generation
    temperature: float = field(default_factory=lambda: _get_float("TEMPERATURE", 0.85))
    top_p: float = field(default_factory=lambda: _get_float("TOP_P", 0.9))
    repeat_penalty: float = field(default_factory=lambda: _get_float("REPEAT_PENALTY", 1.1))
    num_ctx: int = field(default_factory=lambda: _get_int("NUM_CTX", 4096))

    # Conversation
    history_turns: int = field(default_factory=lambda: _get_int("HISTORY_TURNS", 8))

    def validate(self) -> None:
        """Raise a clear error if anything required is missing/invalid."""
        if not self.discord_token:
            raise RuntimeError(
                "DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in."
            )
        if self.trigger_mode not in ("channel", "mention"):
            raise RuntimeError(
                f"TRIGGER_MODE must be 'channel' or 'mention', got {self.trigger_mode!r}"
            )
        if self.trigger_mode == "channel" and not self.allowed_channel_ids:
            # Allowed, but warn loudly — whole-channel with no filter = replies everywhere.
            print(
                "[config] WARNING: TRIGGER_MODE=channel with no ALLOWED_CHANNEL_IDS — "
                "the bot will respond in EVERY channel it can see."
            )
        if self.history_turns < 1:
            raise RuntimeError("HISTORY_TURNS must be >= 1")


config = Config()

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


def _get_keep_alive(name: str, default: str) -> "int | str":
    """Ollama accepts keep_alive as a duration string ('30m') or an integer
    number of seconds (-1 = forever, 0 = unload). It rejects the *string* '-1',
    so return an int when the value is a plain integer, else the string."""
    raw = (os.getenv(name, default) or default).strip()
    try:
        return int(raw)
    except ValueError:
        return raw


def _get_disable_thinking(model: str) -> bool:
    """Whether to append /no_think. Honor an explicit DISABLE_THINKING override;
    otherwise auto-enable only for reasoning models (qwen3) that actually think."""
    raw = os.getenv("DISABLE_THINKING")
    if raw is not None and raw.strip() != "":
        return raw.strip().lower() not in ("0", "false", "no")
    return "qwen3" in model.lower()


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
    allowed_channel_ids: set[int] = field(default_factory=lambda: _get_channel_ids("ALLOWED_CHANNELS"))
    trigger_mode: str = (os.getenv("TRIGGER_MODE", "channel") or "channel").strip().lower()

    # Ollama
    ollama_url: str = (os.getenv("OLLAMA_URL", "http://localhost:11434") or "").rstrip("/")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")
    ollama_timeout: int = field(default_factory=lambda: _get_int("OLLAMA_TIMEOUT", 240))
    # Keep the model resident between requests to avoid cold-load latency.
    # int (seconds; -1 = forever) or duration string ("30m"). NOT the string "-1".
    ollama_keep_alive: "int | str" = field(default_factory=lambda: _get_keep_alive("OLLAMA_KEEP_ALIVE", "30m"))
    # Append /no_think only for reasoning models (qwen3). Auto-detected from the
    # model name unless DISABLE_THINKING is set explicitly. (qwen2.5 doesn't think.)
    disable_thinking: bool = field(
        default_factory=lambda: _get_disable_thinking(os.getenv("OLLAMA_MODEL", "qwen2.5:3b"))
    )

    # Generation
    temperature: float = field(default_factory=lambda: _get_float("TEMPERATURE", 0.85))
    top_p: float = field(default_factory=lambda: _get_float("TOP_P", 0.9))
    repeat_penalty: float = field(default_factory=lambda: _get_float("REPEAT_PENALTY", 1.1))
    num_ctx: int = field(default_factory=lambda: _get_int("NUM_CTX", 4096))
    # Cap reply length. qwen3 generates hidden "thinking" tokens (siphoned into a
    # separate field by /no_think) that ALSO count against this budget — so it must
    # leave room for thinking + the visible answer, or content comes back empty.
    max_tokens: int = field(default_factory=lambda: _get_int("MAX_TOKENS", 512))

    # Conversation
    history_turns: int = field(default_factory=lambda: _get_int("HISTORY_TURNS", 8))

    # Persistent memory (per-channel facts + running summary on disk)
    memory_dir: str = os.getenv("MEMORY_DIR", "memory/data")
    # Refresh the distilled facts/summary after this many new turns (an extra LLM call).
    memory_summary_every: int = field(default_factory=lambda: _get_int("MEMORY_SUMMARY_EVERY", 6))

    # Proactivity (the bot may speak unprompted to keep conversation alive)
    proactive_enabled: bool = (os.getenv("PROACTIVE_ENABLED", "true") or "true").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    # Re-engage if a channel had a real exchange then went quiet this long.
    idle_minutes: float = field(default_factory=lambda: _get_float("IDLE_MINUTES", 8.0))
    # Minimum gap between two proactive messages in the same channel.
    proactive_cooldown_minutes: float = field(
        default_factory=lambda: _get_float("PROACTIVE_COOLDOWN_MINUTES", 30.0)
    )
    # Hard ceiling on proactive messages per channel per rolling hour.
    proactive_max_per_hour: int = field(default_factory=lambda: _get_int("PROACTIVE_MAX_PER_HOUR", 2))
    # How often the background loop checks for idle channels (seconds).
    proactive_check_seconds: int = field(default_factory=lambda: _get_int("PROACTIVE_CHECK_SECONDS", 60))

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

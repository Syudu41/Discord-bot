"""Per-channel conversation transcript — a thin, name-aware view over MemoryStore.

The durable storage lives in `memory.store.MemoryStore`; this class is just the
transcript-facing API the bot uses each turn. Speaker names are tracked so the
model knows who said what (whole-channel chats have many speakers) and can address
people by name.
"""
from __future__ import annotations

from config import config
from memory.store import ASSISTANT_NAME, MemoryStore


class ChannelHistory:
    """Recent {role, name, content} turns per channel, backed by the store."""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def add_user(self, channel_id: int, name: str, content: str) -> None:
        self._store.add_turn(channel_id, "user", content, name=name)

    def add_assistant(self, channel_id: int, content: str) -> None:
        self._store.add_turn(channel_id, "assistant", content, name=ASSISTANT_NAME)

    def get(self, channel_id: int) -> list[dict[str, str]]:
        """Recent turns (oldest first) for prompt assembly."""
        return self._store.recent_turns(channel_id, config.history_turns * 2)

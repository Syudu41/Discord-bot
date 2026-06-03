"""Per-channel short-term conversation memory (in-RAM, ephemeral).

A bounded buffer keeps recent turns so the bot has context without ballooning
the prompt — important on a memory-constrained host and a small model. Lost on
restart by design; persistence is a later milestone.
"""
from __future__ import annotations

from collections import defaultdict, deque

from config import config


class ChannelHistory:
    """Rolling {role, content} buffer keyed by channel id.

    Holds up to `history_turns` user+assistant pairs per channel.
    """

    def __init__(self) -> None:
        maxlen = config.history_turns * 2
        self._buffers: dict[int, deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=maxlen)
        )

    def add_user(self, channel_id: int, content: str) -> None:
        self._buffers[channel_id].append({"role": "user", "content": content})

    def add_assistant(self, channel_id: int, content: str) -> None:
        self._buffers[channel_id].append({"role": "assistant", "content": content})

    def get(self, channel_id: int) -> list[dict[str, str]]:
        """Recent turns for a channel, oldest first."""
        return list(self._buffers[channel_id])

    def clear(self, channel_id: int) -> None:
        self._buffers.pop(channel_id, None)

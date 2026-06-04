"""Persistent per-channel memory.

Each channel gets one JSON file under `MEMORY_DIR` holding:
  - transcript: rolling recent turns (with speaker names) — survives restarts so
    short-term context isn't wiped when the bot reboots.
  - facts: durable per-person notes ({"Alice": ["likes chess", ...]}).
  - summary: a compact running summary of the conversation.
  - counters/timestamps: drive summarization cadence and proactivity.

The store owns all disk I/O. It is the single source of truth; higher layers
(`ChannelHistory`, the proactive task) read/write through it. Facts and summary
are produced by an LLM call orchestrated in bot.py and handed back here to save —
the store itself never calls the model.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from config import config

log = logging.getLogger(__name__)

# Keep the on-disk transcript bounded so files (and the prompt) stay small.
_TRANSCRIPT_MAX = 40
# Caps to protect the context window when injecting memory into the prompt.
_MAX_FACTS_PER_PERSON = 6
_SUMMARY_MAX_CHARS = 600

ASSISTANT_NAME = "Pariston"


class ChannelMemory:
    """In-memory view of one channel's persisted record."""

    def __init__(self, channel_id: int, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        self.channel_id = channel_id
        self.transcript: list[dict[str, str]] = data.get("transcript", [])
        self.facts: dict[str, list[str]] = data.get("facts", {})
        self.summary: str = data.get("summary", "")
        self.turns_since_summary: int = data.get("turns_since_summary", 0)
        self.last_activity: float = data.get("last_activity", 0.0)
        self.last_proactive: float = data.get("last_proactive", 0.0)
        # Epoch timestamps of recent proactive messages (for the per-hour ceiling).
        self.proactive_times: list[float] = data.get("proactive_times", [])
        # True once a human has spoken since the last proactive message — prevents
        # the bot talking to itself repeatedly.
        self.human_since_proactive: bool = data.get("human_since_proactive", True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "transcript": self.transcript,
            "facts": self.facts,
            "summary": self.summary,
            "turns_since_summary": self.turns_since_summary,
            "last_activity": self.last_activity,
            "last_proactive": self.last_proactive,
            "proactive_times": self.proactive_times,
            "human_since_proactive": self.human_since_proactive,
        }


class MemoryStore:
    """Lazy-loading, write-through store of `ChannelMemory` records."""

    def __init__(self, directory: str | None = None) -> None:
        self._dir = directory or config.memory_dir
        os.makedirs(self._dir, exist_ok=True)
        self._cache: dict[int, ChannelMemory] = {}

    # ── disk ────────────────────────────────────────────────────────────────
    def _path(self, channel_id: int) -> str:
        return os.path.join(self._dir, f"{channel_id}.json")

    def _load(self, channel_id: int) -> ChannelMemory:
        if channel_id in self._cache:
            return self._cache[channel_id]
        data: dict[str, Any] | None = None
        path = self._path(channel_id)
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError) as exc:
                log.warning("Could not read memory %s: %s — starting fresh", path, exc)
        mem = ChannelMemory(channel_id, data)
        self._cache[channel_id] = mem
        return mem

    def _save(self, mem: ChannelMemory) -> None:
        path = self._path(mem.channel_id)
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(mem.to_dict(), fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)  # atomic
        except OSError as exc:
            log.warning("Could not write memory %s: %s", path, exc)

    # ── transcript ──────────────────────────────────────────────────────────
    def add_turn(self, channel_id: int, role: str, content: str, name: str) -> None:
        mem = self._load(channel_id)
        mem.transcript.append({"role": role, "name": name, "content": content})
        if len(mem.transcript) > _TRANSCRIPT_MAX:
            mem.transcript = mem.transcript[-_TRANSCRIPT_MAX:]
        if role == "user":
            mem.turns_since_summary += 1
            mem.last_activity = time.time()
            mem.human_since_proactive = True
        self._save(mem)

    def recent_turns(self, channel_id: int, limit: int) -> list[dict[str, str]]:
        """Most recent turns (oldest first), each {role, name, content}."""
        mem = self._load(channel_id)
        return mem.transcript[-limit:] if limit > 0 else list(mem.transcript)

    # ── facts / summary ───────────────────────────────────────────────────────
    def memory_block(self, channel_id: int) -> str:
        """A compact 'what you remember' block for the prompt, or '' if empty."""
        mem = self._load(channel_id)
        if not mem.summary and not mem.facts:
            return ""
        lines = ["WHAT YOU REMEMBER (your ongoing history here — weave it in naturally, never list it):"]
        if mem.summary:
            lines.append(f"- So far: {mem.summary[:_SUMMARY_MAX_CHARS]}")
        for person, items in mem.facts.items():
            if items:
                lines.append(f"- {person}: " + "; ".join(items[:_MAX_FACTS_PER_PERSON]))
        return "\n".join(lines)

    def should_summarize(self, channel_id: int) -> bool:
        mem = self._load(channel_id)
        return mem.turns_since_summary >= config.memory_summary_every and bool(mem.transcript)

    def save_summary(
        self, channel_id: int, summary: str, facts: dict[str, list[str]]
    ) -> None:
        mem = self._load(channel_id)
        if summary:
            mem.summary = summary.strip()[:_SUMMARY_MAX_CHARS]
        if facts:
            # Merge: keep newest facts, bounded per person.
            for person, items in facts.items():
                if not isinstance(items, list):
                    continue
                cleaned = [str(i).strip() for i in items if str(i).strip()]
                mem.facts[person] = cleaned[-_MAX_FACTS_PER_PERSON:]
        mem.turns_since_summary = 0
        self._save(mem)

    # ── proactivity bookkeeping ───────────────────────────────────────────────
    def channels(self) -> list[int]:
        """Channels we have any record for (including those only on disk)."""
        ids = set(self._cache)
        if os.path.isdir(self._dir):
            for fn in os.listdir(self._dir):
                if fn.endswith(".json"):
                    try:
                        ids.add(int(fn[:-5]))
                    except ValueError:
                        continue
        return list(ids)

    def get(self, channel_id: int) -> ChannelMemory:
        return self._load(channel_id)

    def mark_proactive(self, channel_id: int) -> None:
        mem = self._load(channel_id)
        now = time.time()
        mem.last_proactive = now
        mem.proactive_times = [t for t in mem.proactive_times if now - t < 3600] + [now]
        mem.human_since_proactive = False
        self._save(mem)

    def proactive_count_last_hour(self, channel_id: int) -> int:
        mem = self._load(channel_id)
        now = time.time()
        return sum(1 for t in mem.proactive_times if now - t < 3600)

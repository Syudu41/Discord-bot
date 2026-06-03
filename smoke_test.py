"""Standalone Ollama smoke test — no Discord required.

Verifies the model loads, `think:false` works, and the persona produces an
in-character reply. Run after `pip install -r requirements.txt` with Ollama up:

    python smoke_test.py
    python smoke_test.py "You're a snake."
"""
from __future__ import annotations

import asyncio
import sys

from config import config
from llm.ollama_client import OllamaClient
from persona import pariston


async def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Are you manipulating us?"
    print(f"[smoke] model={config.ollama_model} url={config.ollama_url}")
    print(f"[smoke] user: {prompt}")

    client = OllamaClient()
    messages = pariston.build_messages([], prompt)
    reply = await client.chat(messages)

    print(f"[smoke] pariston: {reply}")
    if not reply.strip():
        print("[smoke] WARNING: empty reply")
        sys.exit(1)
    if "<think>" in reply.lower():
        print("[smoke] WARNING: <think> leaked into the reply")
        sys.exit(1)
    print("[smoke] OK")


if __name__ == "__main__":
    asyncio.run(main())

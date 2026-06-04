# Pariston — a local Discord chatbot with a persona

A Discord chatbot that talks as **Pariston** (Hunter x Hunter–inspired): charming, polite, playful,
and quietly dangerous. It runs entirely on a **local open-source model via Ollama** — no OpenAI key,
no per-token cost, fully private.

It doesn't just answer — it **holds a conversation**: it engages with what you say, remembers people and
topics across restarts, addresses people by name, and can revive a quiet channel on its own.

> Persona design and the longer roadmap (RAG → dataset builder → fine-tuning) live in
> [`trial-persona.md`](trial-persona.md), the source of truth for character direction.

## Features

- **Engaged, in-character replies** — reacts to your actual words, asks real follow-ups, calls back to
  earlier moments. Charming with a real edge, never a generic villain.
- **Persistent memory** — per-channel transcript, per-person facts, and a running summary saved to disk;
  survives restarts. A background pass distills durable facts every few turns (never slows your reply).
- **Speaker identity** — tracks who said what in a shared channel and uses names.
- **Proactivity** — optionally breaks the silence in a quiet channel with a callback to something earlier,
  behind strict anti-spam guards.
- **Local & free** — any Ollama chat model; defaults tuned for a ~4 GB GPU.

## Layout

```
bot.py                  # entry point: on_message, memory hook, proactive loop
config.py               # env loading / validation
persona/pariston.py     # SYSTEM_PROMPT, few-shot, prompt builders — edit to tune character
llm/ollama_client.py    # async wrapper over Ollama /api/chat (qwen3 <think> handling)
conversation/history.py # name-aware transcript view over the store
memory/store.py         # persistent per-channel memory (transcript + facts + summary)
memory/data/            # saved memory, one JSON per channel (gitignored)
smoke_test.py           # test Ollama + persona without Discord
Modelfile               # optional: bake the persona into an Ollama model
```

## Setup

### 1. Python + dependencies
Needs Python 3.10+ (3.12 recommended). On Ubuntu, to install a newer Python:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update && sudo apt install python3.12 python3.12-venv -y
```

Then create the venv and install:

```bash
cd /home/lab335a/Sudarshan/Discord-bot
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Ollama
Make sure Ollama is running and the model is pulled:

```bash
ollama serve              # if not already running
ollama pull qwen2.5:3b    # default — fast (~4-5s) and stable on a 4 GB GPU
```

### 3. Config
```bash
cp .env.example .env
# edit .env: set DISCORD_TOKEN and ALLOWED_CHANNELS at minimum
```

### 4. Discord Developer Portal
- Create an application → **Bot** → copy the token into `DISCORD_TOKEN`.
- Enable **Message Content Intent** (Bot → Privileged Gateway Intents). Required for whole-channel reading.
- Invite the bot: OAuth2 → URL Generator → scope `bot` → permissions *View Channels* + *Send Messages* →
  open the URL and add it to your server.
- Enable Developer Mode (Settings → Advanced), right-click your channel → **Copy Channel ID**, put it in
  `ALLOWED_CHANNELS` (comma-separated for several).

## Run

```bash
python smoke_test.py "are you manipulating us?"   # quick check, no Discord needed
python bot.py                                     # start the bot
```

Post in an allowed channel (or @mention the bot in `mention` mode) and Pariston replies. Memory is written
to `memory/data/`; delete a channel's JSON there to wipe its memory.

## Configuration (.env)

| Key | Default | Notes |
|---|---|---|
| `DISCORD_TOKEN` | — | **Required.** |
| `ALLOWED_CHANNELS` | (empty) | Comma-separated channel IDs. Empty = every channel it can see. |
| `TRIGGER_MODE` | `channel` | `channel` (whole-channel) or `mention`. |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama server. |
| `OLLAMA_MODEL` | `qwen2.5:3b` | Any Ollama chat model. See **Choosing a model**. |
| `OLLAMA_KEEP_ALIVE` | `30m` | How long the model stays resident. `-1` = forever. |
| `DISABLE_THINKING` | *(auto)* | Auto-on for `qwen3` only; set `true`/`false` to force. |
| `TEMPERATURE` / `TOP_P` / `REPEAT_PENALTY` | `0.85` / `0.9` / `1.1` | Sampling. |
| `NUM_CTX` / `MAX_TOKENS` | `4096` / `512` | Context window / max generated tokens. |
| `HISTORY_TURNS` | `8` | User+bot pairs kept as live context. |
| `OLLAMA_TIMEOUT` | `120` | Seconds per request. |
| **Memory** | | |
| `MEMORY_DIR` | `memory/data` | Where per-channel memory is stored. |
| `MEMORY_SUMMARY_EVERY` | `6` | Refresh facts/summary after this many user turns. |
| **Proactivity** | | |
| `PROACTIVE_ENABLED` | `true` | Let Pariston speak unprompted to revive a quiet channel. |
| `IDLE_MINUTES` | `8` | Re-engage only after a real exchange goes quiet this long. |
| `PROACTIVE_COOLDOWN_MINUTES` | `30` | Minimum gap between proactive messages per channel. |
| `PROACTIVE_MAX_PER_HOUR` | `2` | Hard ceiling per channel per rolling hour. |
| `PROACTIVE_CHECK_SECONDS` | `60` | How often the idle-check loop runs. |

To turn proactivity off entirely, set `PROACTIVE_ENABLED=false`.

## Choosing a model

| Model | Speed (warm) | Notes |
|---|---|---|
| **`qwen2.5:3b`** | ~4–5s | **Default.** Fast, stable, fits a 4 GB GPU, good persona. |
| `qwen3:4b` | ~40–100s | Works, but a *reasoning* model — it thinks before every reply, so it's slow. Auto-handled via `/no_think`. |
| `llama3.2:3b` | — | ❌ Crashes the runner (`exit status 2`) on a GTX 960M. Avoid. |

Bigger models (e.g. `qwen2.5:7b`) are better but need more RAM/VRAM than a 4 GB card comfortably allows.

## Tuning the persona
Edit [`persona/pariston.py`](persona/pariston.py) — `SYSTEM_PROMPT` and `FEW_SHOT` are the character; the
`build_*_messages` helpers shape the reply, summary, and proactive prompts. Restart the bot to apply; no
rebuild needed. That file is the single source of truth for character.

## How memory works
- Every turn is appended to `memory/data/<channel_id>.json` (bounded rolling transcript) — so short-term
  context survives a restart.
- Every `MEMORY_SUMMARY_EVERY` user turns, a low-temperature background call distills **durable facts**
  (per person) and a **running summary**. These are injected into the prompt as a "WHAT YOU REMEMBER" block,
  so replies and proactive lines can call back to earlier conversations.

## Troubleshooting
- **Replies are very slow** → you're on a reasoning model (`qwen3`). Switch `OLLAMA_MODEL=qwen2.5:3b`.
- **400 Bad Request from Ollama** → `OLLAMA_KEEP_ALIVE` must be a duration (`30m`) or an integer (`-1`),
  not the string `"-1"` — the config handles this, but custom values should follow the rule.
- **Empty replies on qwen3** → its hidden "thinking" ate the token budget; raise `MAX_TOKENS`.
- **Runner crash (`exit status 2`)** → out of memory or an unsupported model for your GPU; use a smaller
  model and make sure only one model is resident (`ollama ps`).
- **Bot ignores messages** → check **Message Content Intent** is enabled and the channel ID is in
  `ALLOWED_CHANNELS`.
- **Every message answered twice** → two bot instances are running. Check with `pgrep -af bot.py` and kill
  the extras; run the bot in only one place.
- **Bot uses a wrong name or an emoji** → likely leftover from old persisted memory; delete that channel's
  file in `memory/data/` to reset it.

## Status & roadmap

### Done
- ✅ Local Ollama chatbot, whole-channel or mention mode (Milestone 1)
- ✅ "Engage & build" persona — reacts, follows up, calls back (not a pure deflector)
- ✅ Fast, stable model on a 4 GB GPU (`qwen2.5:3b`)
- ✅ Speaker identity + persistent per-channel memory: transcript, facts, running summary (Milestone 2)
- ✅ Proactivity — revives quiet channels with a callback, behind anti-spam guards
- ✅ Real @mention pings; no invented names; no emoji

### Pending / to-do
- [ ] **Persona refinement (ongoing)** — keep tuning `persona/pariston.py` from more HxH sources: voice,
      brevity, behavior rules, and few-shot examples. Treat this as a continuous task, not a one-off.
- [ ] **Manual memory commands** (Milestone 4) — `/pariston remember | correct | forget | memories`.
- [ ] **Persistent logging** (Milestone 3) — structured conversation/reply log (e.g. SQLite) for review.
- [ ] **RAG long-term memory** (Milestone 5) — embeddings + vector store for recall beyond the summary.
- [ ] **Dataset builder + fine-tuning** (Milestones 6–7) — turn good exchanges/corrections into JSONL,
      then LoRA/QLoRA a local model.
- [ ] **Multi-user mentions** — currently only the person being replied to can be pinged; pinging a third
      party by name isn't supported yet.
- [ ] **Proactive polish** — the unprompted line renders `@user` as plain "you"; could target a specific
      recent participant instead.

See [`trial-persona.md`](trial-persona.md) for the full milestone descriptions and long-term direction.

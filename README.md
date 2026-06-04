# Pariston — local Discord chatbot

A Discord chatbot with the persona of **Pariston** (Hunter x Hunter–inspired): charming, polite,
playful, and quietly dangerous. Powered entirely by a **local open-source model via Ollama** — no
OpenAI key, no per-token cost, fully private.

This is **Milestone 1**: a working whole-channel chatbot. See `trial-persona.md` for the persona
design doc and the longer roadmap (memory → logging → manual learning → RAG → fine-tuning).

## Layout

```
bot.py                  # entry point + on_message handler
config.py               # env loading / validation
persona/pariston.py     # SYSTEM_PROMPT + few-shot examples (edit this to tune character)
llm/ollama_client.py    # async wrapper over Ollama /api/chat (strips qwen3 <think>)
conversation/history.py # per-channel rolling short-term memory
smoke_test.py           # test Ollama + persona without Discord
Modelfile               # optional baked-in persona model
```

## Setup

### 1. Python + dependencies
Needs Python 3.10+ (3.12 recommended). On Ubuntu, to install a newer Python:

```bash
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install python3.12 python3.12-venv -y
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
ollama serve            # if not already running
ollama pull qwen3:4b    # default model (fits ~4 GB GPU)
```

### 3. Config
Copy and fill the env file:

```bash
cp .env.example .env
# edit .env: set DISCORD_TOKEN, ALLOWED_CHANNELS, etc.
```

### 4. Discord Developer Portal
- Create an application → **Bot** → copy the token into `DISCORD_TOKEN`.
- Enable **Message Content Intent** (Bot → Privileged Gateway Intents). Required for whole-channel reading.
- Invite the bot: OAuth2 → URL Generator → scope `bot` → permissions *Read Messages/View Channels* +
  *Send Messages* → open the URL and add it to your server.
- Enable Developer Mode in Discord (Settings → Advanced), right-click your channel → **Copy Channel ID**,
  put it in `ALLOWED_CHANNELS` (comma-separated for several).

## Run

```bash
# 1) quick check without Discord
python smoke_test.py

# 2) start the bot
python bot.py
```

Post in an allowed channel (or @mention the bot in `mention` mode) and Pariston replies.

## Configuration (.env)

| Key | Default | Notes |
|---|---|---|
| `DISCORD_TOKEN` | — | Required. |
| `ALLOWED_CHANNELS` | (empty) | Comma-separated. Empty = every channel it can see. |
| `TRIGGER_MODE` | `channel` | `channel` (whole-channel) or `mention`. |
| `OLLAMA_URL` | `http://localhost:11434` | |
| `OLLAMA_MODEL` | `qwen3:4b` | Swap freely (e.g. `qwen2.5:7b` if you have the RAM). |
| `TEMPERATURE` / `TOP_P` / `REPEAT_PENALTY` | `0.85` / `0.9` / `1.1` | Sampling. |
| `NUM_CTX` | `4096` | Context window tokens. |
| `HISTORY_TURNS` | `8` | User+bot pairs kept per channel. |
| `OLLAMA_TIMEOUT` | `120` | Seconds per request. |

## Tuning the persona
Edit `persona/pariston.py` (`SYSTEM_PROMPT` and `FEW_SHOT`), then restart the bot — no rebuild needed.
That file is the single source of truth for character.

# Omnirouter — Claude Code Gateway

> **Multi-provider gateway for the Claude Code native model picker.** One
> Anthropic-compatible endpoint in front of many providers — free and paid —
> with streaming, tool-use, and reasoning handled uniformly.

Omnirouter lets Claude Code's `/model` picker and subagent `model:` frontmatter
spawn models from many providers as if they were native Anthropic models: same
Messages API, same SSE streaming, same tool-calling flow. Point
`ANTHROPIC_BASE_URL` at the gateway and every backend answers through one
consistent interface.

## Table of Contents

- [Why](#why)
- [Providers](#providers)
- [Model menu](#model-menu)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [How It Works](#how-it-works)
- [Streaming keepalive](#streaming-keepalive)
- [Reasoning handling](#reasoning-handling)
- [Fallback chains](#fallback-chains)
- [API](#api)
- [Security: keys never leave your machine](#security-keys-never-leave-your-machine)
- [Development](#development)
- [Limitations](#limitations)
- [Ownership & Maintenance](#ownership--maintenance)
- [License](#license)

## Why

Claude Code natively routes through Anthropic. Omnirouter replaces that one hop
with a local gateway that translates the Anthropic protocol into whatever each
provider speaks — so **free-tier and multi-provider capacity become first-class**
and you keep one interface regardless of which model answers.

## Providers

| Provider | Upstream protocol | Model ID prefix | Notes |
|---|---|---|---|
| **Anthropic** | Anthropic (native) | `claude-*` (native ids) | Direct passthrough (e.g. a Max subscription) |
| **OpenRouter** | Anthropic skin | `claude-openrouter-` | Byte-for-byte passthrough; free + paid slugs |
| **Groq** | OpenAI | `claude-groq-` | Very low latency, free tier |
| **Google Gemini** | Gemini API | `claude-gemini-` | Huge context, 15 RPM (local cap) |
| **Mistral** | OpenAI | `claude-mistral-` | Broad free lineup |
| **Cerebras** | OpenAI | `claude-cerebras-` | Fastest tokens/sec; needs key |
| **NVIDIA NIM** | OpenAI | `claude-nim-` | Self-host / cloud; needs config |
| **OpenCode Zen** | OpenAI-compatible | `claude-opencode-` | opencode's own hosted free models; **tool-capable**, no local process |

> Free-tier providers and any OpenRouter `:free`/`free-*` slug are auto-tagged
> **`FREE 🆓`** in discovery. `GET /v1/models?free=1` returns only the free ones.

## Model menu

The full, current menu lives in [MODELS.md](MODELS.md) and is generated from
`gateway_config.yaml`. Highlights (all free unless noted):

- **Groq** — `llama3`, `llama-8b`, `gpt-oss-120b`, `gpt-oss-20b`, `qwen3`, `compound`, `compound-mini`
- **Gemini** — `flash`, `pro`, `flash-lite`, `3-flash`, `3.1-pro`, `3.5-flash`, `3.6-flash`, `gemma-31b`, `gemma-26b`
- **Mistral** — `large`, `medium`, `small`, `codestral`, `devstral`, `devstral-medium`, `magistral`, `ministral-14b/8b/3b`, `code`
- **OpenCode Zen** — `big-pickle`, `deepseek-v4-flash`, `hy3`, `mimo`, `laguna-s`, `ling-tiny`, `nemotron-lightning`, `nemotron-ultra`
- **OpenRouter free** — a dozen `free-*` nemotron / gemma / gpt-oss / north-code / laguna / ling slugs
- **OpenRouter paid** (💲, explicit opt-in) — `deepseek`, `qwen-coder`, `haiku-4.5`, `sonnet-5`, `opus-4.8`, `opus-5`
- **Cerebras** — `gpt-oss`, `glm` (need `CEREBRAS_API_KEY`)

`GET /v1/models` is the live source of truth.

## Quick Start

### 1. Install

```bash
cd omnirouter
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

Requires Python ≥ 3.11.

### 2. Configure

```bash
cp .env.example .env
# Edit .env — only fill in the providers you'll actually use
```

Keys are **conditional**: the gateway boots with none set; a provider simply
reports unhealthy (or errors on use) until its key exists.

- `OPENROUTER_API_KEY` — https://openrouter.ai/keys
- `GROQ_API_KEY` — https://console.groq.com/keys
- `GEMINI_API_KEY` — https://aistudio.google.com/apikey
- `MISTRAL_API_KEY` — https://console.mistral.ai/api-keys
- `CEREBRAS_API_KEY` — https://cloud.cerebras.ai
- `OPENCODE_API_KEY` — https://opencode.ai (defaults to the shared `public` key)
- `NIM_BASE_URL` + `NIM_API_KEY` — NVIDIA NIM (self-hosted or cloud)

### 3. Start the gateway

```bash
uvicorn gateway.main:app --env-file .env --host 127.0.0.1 --port 8787
# or, idempotently (used by the SessionStart hook):
./scripts/ensure-gateway.sh
```

There is **no** separate opencode process to run — OpenCode Zen is called
directly over HTTPS.

### 4. Point Claude Code at it

```bash
# .claude/settings.local.json (env block) or your shell profile
export ANTHROPIC_BASE_URL="http://127.0.0.1:8787"
```

### 5. Verify

```bash
curl -s http://127.0.0.1:8787/v1/models | jq '.data | length'
claude
> /model     # native models + the gateway's "From gateway" entries
```

## Configuration

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `GATEWAY_PORT` / `GATEWAY_HOST` | `8787` / `127.0.0.1` | Listen address |
| `GATEWAY_LOG_LEVEL` | `INFO` | Log verbosity |
| `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` | — / `https://openrouter.ai/api` | OpenRouter |
| `GROQ_API_KEY` | — | Groq |
| `GEMINI_API_KEY` | — | Gemini |
| `MISTRAL_API_KEY` | — | Mistral |
| `CEREBRAS_API_KEY` | — | Cerebras |
| `OPENCODE_API_KEY` | `public` | OpenCode Zen (personal key lifts free limits) |
| `NIM_BASE_URL` / `NIM_API_KEY` | — | NVIDIA NIM |
| `DISCOVERY_CACHE_TTL` | `300` | Discovery cache seconds |
| `REQUEST_TIMEOUT` / `CONNECT_TIMEOUT` | `600` / `10` | Upstream timeouts (s) |
| `KEEPALIVE_INTERVAL` | `15` | Streaming heartbeat interval (s); `0` disables |
| `GEMINI_RPM` / `MISTRAL_RPM` / `CEREBRAS_RPM` | `15` / `500` / `5` | Local rate caps |
| `MAX_FALLBACK_ATTEMPTS` | `3` | Max hops per request |

### Model maps

Live in `gateway_config.yaml`. Keys are the suffix after the provider prefix;
values are the upstream model id. Example:

```yaml
opencode_model_map:          # claude-opencode-<key>  ->  OpenCode Zen model id
  big-pickle: "big-pickle"
  deepseek-v4-flash: "deepseek-v4-flash-free"
opencode_base_url: "https://opencode.ai/zen/v1"
```

## How It Works

### Model discovery

1. Claude Code calls `GET /v1/models`.
2. The gateway builds the payload from the static model maps and merges the
   **free** models the live OpenCode Zen `/models` reports (cached for
   `DISCOVERY_CACHE_TTL`). Paid Zen models are never surfaced as free.
3. **Critical invariant**: every discoverable id must contain `claude` or
   `anthropic` — Claude Code silently drops anything else.
4. Each model is exposed as `claude-{provider}-{key}`.

### Request flow

```
POST /v1/messages (model = claude-groq-llama3)
        │  match prefix "claude-groq-"
        ▼
GroqBackend: Anthropic → OpenAI request (tools forwarded)
        ▼
POST https://api.groq.com/openai/v1/chat/completions   (stream)
        ▼
OpenAI SSE → Anthropic SSE (tool_calls → tool_use, <think> stripped) → client
```

OpenRouter is a byte-for-byte Anthropic passthrough (`/v1/messages` on
OpenRouter). OpenCode Zen is a plain OpenAI-compatible call to
`opencode_base_url`.

## Streaming keepalive

Large reasoning models can stay silent for tens of seconds before the first
token. During that silence the streaming endpoint emits an Anthropic `ping`
event every `KEEPALIVE_INTERVAL` seconds so the client/orchestrator knows the
request is alive and keeps waiting. It **never cancels** the in-flight request —
the same pending event is awaited across pings. Set `KEEPALIVE_INTERVAL=0` to
disable.

## Reasoning handling

Some models inline literal `<think>…</think>` spans in their content (e.g. Qwen3
via Groq). The OpenAI translator strips those spans from the visible text, with
a state machine that handles tags split across streaming chunks and never drops
ordinary text like `a < b`. Reasoning delivered in a separate provider field is
ignored rather than leaked.

## Fallback chains

Configured in `gateway_config.yaml`. Entries are backend provider names tried
**after** the implicit primary. Fallback fires only before any content has been
streamed, on a retryable error. Today every chain is empty — each provider runs
standalone (OpenCode Zen is its own provider, not a fallback for others):

```yaml
fallback_chains:
  groq: []
  gemini: []
  mistral: []
  nim: []
  cerebras: []
  openrouter: []
  opencode: []
```

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Service info + endpoint index |
| `/v1/models` | GET | Discovery payload (`?free=1` for free-only) |
| `/v1/messages` | POST | Anthropic Messages API (routing + fallback + SSE) |
| `/health` | GET | Per-backend health; `200` healthy / `503` otherwise |

## Security: keys never leave your machine

- Real keys live only in `.env`, which is **gitignored** and never tracked.
- `.env.example` carries placeholders only.
- No key appears in any tracked file, in any commit, in the whole history.
- The gateway runs on `127.0.0.1`; keys are sent only to each provider's own
  API over TLS, never to a third party.

## Development

```bash
python -m pytest tests/ -q      # unit + integration (77 tests)
ruff check gateway/ tests/
mypy gateway
```

## Limitations

- **Free-tier rate limits** are real and shared: OpenRouter `free-*`, OpenCode
  Zen, and Gemini pro/quota tiers return `429`/quota errors intermittently. They
  are surfaced cleanly (not silent empties). A personal key lifts most of them.
- **Cerebras / NIM** are inert until their keys/URL are set.
- **Tiny `max_tokens`**: reasoning models spend a hidden thinking budget; with a
  very small `max_tokens` they can stop before any visible text. Give them room
  (the gateway imposes no token limit — it forwards the client's value verbatim).
- **Single local process**: no HA; this is a local dev tool.

## Ownership & Maintenance

See [OWNERSHIP.md](OWNERSHIP.md) for the agentic ownership model, decision
rights, invariants, and quality gate. See [MODELS.md](MODELS.md) for the full
engine menu and [CLAUDE.md](CLAUDE.md) for the subagent engine policy the
orchestrator follows.

## License

MIT

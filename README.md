# Omnirouter — Claude Code Gateway

> **Multi-provider gateway for the Claude Code native model picker.** Zero friction. Zero overhead. Seven independent providers behind one Anthropic-compatible endpoint.

Omnirouter lets Claude Code's `/model` picker and subagent `model:` frontmatter spawn models from **7 independent providers** as if they were native — same Anthropic Messages API, same SSE streaming, same tool-calling flow, with automatic fallback chains.

## Table of Contents

- [Why](#why)
- [Providers](#providers)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Fallback Chains](#fallback-chains)
- [How It Works](#how-it-works)
- [API](#api)
- [Development](#development)
- [Limitations](#limitations)
- [Ownership & Maintenance](#ownership--maintenance)
- [License](#license)

## Why

Claude Code natively routes through Anthropic. Omnirouter replaces that one hop with a local gateway that translates the Anthropic protocol into whatever your provider speaks — so **free-tier and multi-provider capacity become first-class**, and you keep one consistent interface (`ANTHROPIC_BASE_URL`) regardless of which model answers.

## Providers

| Provider | Protocol | Translation | Rate limit | Model ID prefix |
|---|---|---|---|---|
| **OpenRouter** | Anthropic | Zero (pass-through) | Provider-defined | `claude-` (native) |
| **Groq** | OpenAI | Thin (Anthropic↔OpenAI) | Provider free tier | `claude-groq-` |
| **Google Gemini** | Gemini API | Thin (Anthropic↔Gemini) | 15 RPM (local) | `claude-gemini-` |
| **NVIDIA NIM** | OpenAI | Thin (Anthropic↔OpenAI) | Self-host / cloud | `claude-nim-` |
| **Mistral** | OpenAI | Thin (Anthropic↔OpenAI) | 500 RPM (local) | `claude-mistral-` |
| **Cerebras** | OpenAI | Thin (Anthropic↔OpenAI) | 5 RPM (local token bucket) | `claude-cerebras-` |
| **opencode-bridge** | OpenAI | Thin (Anthropic↔OpenAI) | Uses opencode `auth.json` | `claude-opencode-` |

> Local RPM values are **enforced client-side** token buckets so a gateway can safely drive aggressive free tiers (Gemini, Mistral, Cerebras) without tripping upstream limits.

### Default model maps (`gateway_config.yaml`)

| Group | Models |
|---|---|
| OpenRouter | `opus-5`, `sonnet-5`, `haiku-5`, `deepseek`, `qwen-coder`, `glm-5` |
| Groq | `llama3`, `mixtral`, `gemma` |
| Gemini | `flash`, `pro`, `flash-8b` |
| NIM | `llama3`, `nemotron`, `mixtral` |
| Mistral | `large`, `small`, `codestral` |
| Cerebras | `gpt-oss` (→ `gpt-oss-120b`), `glm` (→ `zai-glm-4.7`) |
| opencode-bridge | `groq`, `gemini`, `mistral` groups |

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

Keys are **conditional**: the gateway boots with none set; providers simply report unhealthy until their key exists.

- `OPENROUTER_API_KEY` — https://openrouter.ai/keys
- `GROQ_API_KEY` — https://console.groq.com/keys
- `GEMINI_API_KEY` — https://aistudio.google.com/apikey
- `MISTRAL_API_KEY` — https://console.mistral.ai/api-keys
- `CEREBRAS_API_KEY` — https://cloud.cerebras.ai (free tier ~5 RPM, 1M tokens/day)
- `NIM_BASE_URL` + `NIM_API_KEY` — NVIDIA NIM (self-hosted or cloud)

### 3. Start opencode + bridges (only for `claude-opencode-*` models)

```bash
# Terminal 1: opencode server (uses auth.json keys)
opencode serve

# Terminal 2: one opencode-bridge instance per provider
docker run -d -p 5001:5000 \
  -e OPENCODE_URL=http://host.docker.internal:4096 \
  -e OPENCODE_PROVIDER_ID=groq \
  crazyboy24/opencode-bridge
# repeat for gemini (:5002) and mistral (:5003)
```

### 4. Start the gateway

```bash
uvicorn gateway.main:app --host 127.0.0.1 --port 8787
# or: python -m gateway.main
```

### 5. Point Claude Code at it

```bash
# in your shell profile or .claude/settings.local.json
export ANTHROPIC_BASE_URL="http://127.0.0.1:8787"
export ANTHROPIC_AUTH_TOKEN="sk-or-<YOUR_OPENROUTER_KEY>"
export ANTHROPIC_API_KEY=""
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
```

### 6. Verify

```bash
claude
> /status          # Anthropic base URL: http://127.0.0.1:8787
> /model           # native models + "From gateway" entries (29 today)
```

## Configuration

### Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GATEWAY_PORT` | No | `8787` | Listen port |
| `GATEWAY_HOST` | No | `127.0.0.1` | Listen host |
| `GATEWAY_LOG_LEVEL` | No | `INFO` | Log verbosity |
| `OPENROUTER_API_KEY` | Conditional | — | OpenRouter auth |
| `OPENROUTER_BASE_URL` | No | `https://openrouter.ai/api` | Override base |
| `GROQ_API_KEY` | Conditional | — | Groq auth |
| `GEMINI_API_KEY` | Conditional | — | Gemini auth |
| `NIM_BASE_URL` | Conditional | — | NIM endpoint |
| `NIM_API_KEY` | Conditional | — | NIM auth |
| `MISTRAL_API_KEY` | Conditional | — | Mistral auth |
| `CEREBRAS_API_KEY` | Conditional | — | Cerebras auth |
| `DISCOVERY_CACHE_TTL` | No | `300` | Discovery cache seconds |
| `DISCOVERY_CACHE_PATH` | No | `~/.claude/cache/gateway-models.json` | Discovery cache file |
| `REQUEST_TIMEOUT` | No | `600` | Upstream read timeout (s) |
| `CONNECT_TIMEOUT` | No | `10` | Upstream connect timeout (s) |
| `KEEPALIVE_INTERVAL` | No | `30` | Keepalive interval (s) |
| `GEMINI_RPM` | No | `15` | Local Gemini rate cap |
| `MISTRAL_RPM` | No | `500` | Local Mistral rate cap |
| `CEREBRAS_RPM` | No | `5` | Local Cerebras rate cap |
| `MAX_FALLBACK_ATTEMPTS` | No | `3` | Max hops per request |

### Model maps & fallback chains

Both live in `gateway_config.yaml`. Keys are the suffix after the provider prefix:

```yaml
cerebras_model_map:
  gpt-oss: "gpt-oss-120b"
  glm: "zai-glm-4.7"
```

## Fallback Chains

Configured in `gateway_config.yaml`. Entries are registered backend provider
names; the primary (owner of the model prefix) is implicit, so each list holds
only the fallbacks tried **after** it, in order:

```yaml
fallback_chains:
  groq:     ["opencode"]   # groq direct, then groq via the opencode-bridge
  gemini:   ["opencode"]
  mistral:  ["opencode"]
  nim:      []
  cerebras: []             # no compatible fallback — runs standalone
  openrouter: []
  opencode:   []
```

A model's **key** is carried across providers (`claude-groq-llama3` →
`claude-opencode-groq-llama3`), so a fallback only works when the target exposes
the same key. Fallback is attempted **only before any content has been streamed**:
on a retryable failure (`timeout`, `connection_error`, `api_error`, `rate_limit`,
`overloaded`) the router advances to the next backend; once bytes have reached the
client, a later failure is surfaced as-is rather than retried on top of a
half-sent stream. `MAX_FALLBACK_ATTEMPTS` caps the walk.

## How It Works

### Model discovery

1. Claude Code starts → `GET /v1/models?limit=1000`
2. Gateway merges static model maps + live opencode-bridge models (cached for `DISCOVERY_CACHE_TTL`)
3. **Critical**: only IDs containing `claude` or `anthropic` survive — Claude Code silently filters everything else
4. Every provider model is exposed as `claude-{provider}-{key}`

### Request flow

```
POST /v1/messages (model=claude-cerebras-gpt-oss)
         │
         ▼
   Router matches prefix "claude-cerebras-"
         │
         ▼
   CerebrasBackend: Anthropic → OpenAI translation (token bucket: 5 RPM)
         │
         ▼
   POST https://api.cerebras.ai/v1/chat/completions
         │
         ▼
   SSE stream → OpenAI→Anthropic translation → client
```

### opencode-bridge routing

```
model=claude-opencode-groq-llama3
         │
         ▼
   parse: provider=groq, key=llama3
         ▼
   opencode_bridge_endpoints["groq"] → http://localhost:5001
         ▼
   POST /v1/chat/completions → opencode-bridge → opencode serve (auth.json keys)
```

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Service info + endpoint index |
| `/v1/models` | GET | Discovery payload for Claude Code |
| `/v1/messages` | POST | Anthropic Messages API (routing + fallback + SSE) |
| `/health` | GET | Per-backend health; `200` healthy / `503` otherwise |

## Development

```bash
# Tests (note: addopts includes -x — full suite stops at first failure)
python -m pytest tests/ -q

# Lint & type check
ruff check gateway/ tests/
mypy gateway

# Boot smoke test
python - <<'EOF'
from fastapi.testclient import TestClient
from gateway.main import app
with TestClient(app) as c:
    print(c.get("/v1/models").json()["data"][0])
EOF
```

Known baseline issues: 2 pre-existing test failures (`test_tool_result_conversion`, `test_openai_to_anthropic`) and non-compliant ruff/mypy baselines. See [OWNERSHIP.md §7](OWNERSHIP.md#7-known-issues--debt-tracked-not-hidden).

## Limitations

- **Non-Anthropic tool-use reliability**: known caveat for Groq/Gemini/NIM/Mistral/Cerebras. Prefer OpenRouter (native Anthropic) for critical tool-use sessions.
- **Thinking blocks**: dropped on non-Anthropic providers.
- **Single gateway process**: no HA (local dev tool).
- **opencode-bridge**: one provider per instance (run multiple containers).

## Ownership & Maintenance

See [OWNERSHIP.md](OWNERSHIP.md) for the ownership map, decision rights, invariant constraints, and quality gate. See [HANDOFF.md](HANDOFF.md) for the current-state handoff to the next contributor.

## License

MIT

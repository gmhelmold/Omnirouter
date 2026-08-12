# Claude Code Gateway

> **Multi-provider gateway for Claude Code native model picker** — Zero friction, zero overhead, zero unnecessary complexity.

Enables Claude Code's `/model` picker and subagent `model:` frontmatter to spawn models from **6 independent providers** exactly like native fable/opus/sonnet/haiku.

## Providers

| Provider | Type | Translation | Quota/Keys |
|----------|------|-------------|------------|
| **OpenRouter** | Pass-through | Zero (Anthropic Skin) | Independent |
| **Groq** | OpenAI-compat | Thin (Anthropic↔OpenAI) | Independent |
| **Google AI Studio (Gemini)** | Gemini API | Thin (Anthropic↔Gemini) | Free tier (15 RPM) |
| **NVIDIA NIM** | OpenAI-compat | Thin (Anthropic↔OpenAI) | Self-host/Cloud |
| **Mistral** | OpenAI-compat | Thin (Anthropic↔OpenAI) | Free tier (500 RPM) |
| **opencode-bridge** | OpenAI-compat | Thin (Anthropic↔OpenAI) | Uses opencode's auth.json |

## Architecture

```
Claude Code ──(Anthropic Messages)──► Gateway ──► OpenRouter (pass-through)
                                          ├──► Groq (Anthropic↔OpenAI)
                                          ├──► Gemini (Anthropic↔Gemini)
                                          ├──► NIM (Anthropic↔OpenAI)
                                          ├──► Mistral (Anthropic↔OpenAI)
                                          └──► opencode-bridge (per-provider instances)
```

## Quick Start

### 1. Install
```bash
cd claude-gateway
pip install -e ".[dev]"
```

### 2. Configure
```bash
cp .env.example .env
# Edit .env with your API keys
```

Required keys (only for providers you use):
- `OPENROUTER_API_KEY` — from https://openrouter.ai/keys
- `GROQ_API_KEY` — from https://console.groq.com/keys
- `GEMINI_API_KEY` — from https://aistudio.google.com/apikey
- `MISTRAL_API_KEY` — from https://console.mistral.ai/api-keys
- `NIM_BASE_URL` + `NIM_API_KEY` — for NVIDIA NIM

### 3. Start opencode + bridges (for opencode providers)
```bash
# Terminal 1: opencode server (uses auth.json keys)
opencode serve

# Terminal 2: opencode-bridge instances (one per provider)
docker run -d -p 5001:5000 \
  -e OPENCODE_URL=http://host.docker.internal:4096 \
  -e OPENCODE_PROVIDER_ID=groq \
  crazyboy24/opencode-bridge

docker run -d -p 5002:5000 \
  -e OPENCODE_URL=http://host.docker.internal:4096 \
  -e OPENCODE_PROVIDER_ID=gemini \
  crazyboy24/opencode-bridge

docker run -d -p 5003:5000 \
  -e OPENCODE_URL=http://host.docker.internal:4096 \
  -e OPENCODE_PROVIDER_ID=mistral \
  crazyboy24/opencode-bridge
```

### 4. Start gateway
```bash
uvicorn gateway.main:app --host 127.0.0.1 --port 8787
```

### 5. Configure Claude Code
```bash
# In your shell profile or .claude/settings.local.json
export ANTHROPIC_BASE_URL="http://127.0.0.1:8787"
export ANTHROPIC_AUTH_TOKEN="sk-or-<YOUR_OPENROUTER_KEY>"
export ANTHROPIC_API_KEY=""
export CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1
```

### 6. Verify
```bash
claude
> /status
Auth token: ANTHROPIC_AUTH_TOKEN
Anthropic base URL: http://127.0.0.1:8787

> /model
# Shows: fable, opus, sonnet, haiku + "From gateway" entries
```

## Usage

### Model Picker
```
/model
# Select any "From gateway" entry
```

### Subagents
```markdown
# .claude/agents/my-agent.md
---
name: my-agent
description: Uses Groq for fast coding
model: claude-groq-llama3
tools: Read, Write, Edit, Bash
---
You are a coding assistant using Groq's Llama 3.
```

## Fallback Chains

Configured in `gateway_config.yaml`:
```yaml
fallback_chains:
  groq: ["groq", "openrouter-groq", "opencode-groq"]
  gemini: ["gemini", "opencode-gemini", "openrouter-gemini"]
  mistral: ["mistral", "opencode-mistral", "openrouter-mistral"]
```

When primary fails (timeout, 5xx, rate limit), gateway automatically tries next in chain.

## Configuration

### Environment Variables
| Variable | Required | Default |
|----------|----------|---------|
| `GATEWAY_PORT` | No | 8787 |
| `OPENROUTER_API_KEY` | Conditional | — |
| `GROQ_API_KEY` | Conditional | — |
| `GEMINI_API_KEY` | Conditional | — |
| `MISTRAL_API_KEY` | Conditional | — |
| `NIM_BASE_URL` | Conditional | — |
| `NIM_API_KEY` | Conditional | — |
| `OPENCODE_BRIDGE_ENDPOINTS` | No | See config |

### Model Maps
Edit `gateway_config.yaml` to add/change models:
```yaml
groq_model_map:
  llama3: "llama-3.3-70b-versatile"
  custom-model: "provider/custom-model"
```

## Development

### Run Tests
```bash
pytest -v
```

### Lint & Type Check
```bash
ruff check .
mypy gateway
```

### Code Structure
```
gateway/
├── main.py                 # FastAPI app
├── config.py               # Pydantic Settings + YAML
├── router.py               # Routing + fallback
├── discovery.py            # Model discovery + caching
├── health.py               # Health checks
├── backends/
│   ├── base.py             # Backend protocol
│   ├── openrouter.py       # Pass-through
│   ├── groq.py             # OpenAI translation
│   ├── gemini.py           # Gemini translation
│   ├── nim.py              # OpenAI translation
│   ├── mistral.py          # OpenAI translation
│   └── opencode_bridge.py  # Multi-instance routing
├── translators/
│   ├── tool_schema.py      # Tool schema conversion
│   ├── openai.py           # Anthropic↔OpenAI + SSE
│   └── gemini.py           # Anthropic↔Gemini + SSE
```

## How It Works

### Model Discovery
1. Claude Code starts → queries `GET /v1/models?limit=1000`
2. Gateway returns static config + live opencode-bridge models
3. **Critical**: Only IDs containing `claude` or `anthropic` are shown
4. Gateway uses `claude-*` prefix for all custom models

### Request Flow
```
POST /v1/messages (model=claude-groq-llama3)
         │
         ▼
   Router matches prefix
         │
         ▼
   Groq Backend: Anthropic → OpenAI translation
         │
         ▼
   POST https://api.groq.com/openai/v1/chat/completions
         │
         ▼
   SSE stream → OpenAI→Anthropic translation → Client
```

### opencode-bridge Routing
```
model=claude-opencode-groq-llama3
         │
         ▼
   Parse: provider=groq, model_key=llama3
         │
         ▼
   Lookup endpoint: opencode_bridge_endpoints["groq"] → http://localhost:5001
         │
         ▼
   POST http://localhost:5001/v1/chat/completions
         │
         ▼
   opencode-bridge → opencode serve (uses auth.json keys)
```

## Limitations

- **Non-Anthropic tool-use reliability**: Known caveat for Groq/Gemini/NIM/Mistral. Use OpenRouter (Anthropic models) for critical tool-use.
- **Thinking blocks**: Dropped on non-Anthropic providers.
- **Single gateway process**: No HA (local dev tool).
- **opencode-bridge**: One provider per instance (run multiple containers).

## License

MIT
# Omnirouter — engine menu (pick any model for any agent)

Roles and engines are **decoupled**. There are only two generic subagents —
[`worker`](.claude/agents/worker.md) (full tools) and [`reader`](.claude/agents/reader.md)
(read-only) — and you choose which **engine** (provider/model) powers each spawn.

## How to pick the engine at spawn time

Claude Code resolves a subagent's model in this priority order:

1. `CLAUDE_CODE_SUBAGENT_MODEL` env var (pins *all* subagents to one engine)
2. the `model` argument passed when spawning the agent (per-spawn choice)
3. the agent's `model:` frontmatter (ours is `inherit`, so it defers to the above)
4. the main session's model

So to run `worker` on Groq for one task and on Opus for the next, just pass a different
`model` id each spawn — no new agent files. To force everything onto one free engine for
a while, set `CLAUDE_CODE_SUBAGENT_MODEL=claude-groq-llama3` in `.claude/settings*.json`.

The `model` id is any gateway id from the menu below (`GET /v1/models` for the live list;
add `?free=1` to see only free ones).

## Engine menu

Cost: 🆓 free · 💲 cheap · 💲💲 premium. Context/RPM are static (live quota not exposed).

Slugs verified live against each provider's `/models` on 2026-08-12.

### Groq — all free-tier

| Engine id | Model | Context | Notes / best for |
|---|---|---|---|
| `claude-groq-llama3` | Llama 3.3 70B | ~128K | very low latency; quick edits, summaries, bulk text |
| `claude-groq-llama-8b` | Llama 3.1 8B Instant | ~128K | fastest/cheapest Llama; trivial mechanical work |
| `claude-groq-gpt-oss-120b` | gpt-oss 120B | ~128K | strong open reasoning on Groq speed |
| `claude-groq-gpt-oss-20b` | gpt-oss 20B | ~128K | lighter gpt-oss |
| `claude-groq-qwen3` | Qwen3.6 27B | large | general + code |
| `claude-groq-compound` | Groq Compound | large | agentic/tool-augmented system |
| `claude-groq-compound-mini` | Groq Compound Mini | large | lighter compound |

`mixtral` and `gemma` were **retired by Groq** and removed from the menu.

### Gemini — all free-tier (15 RPM)

| Engine id | Model | Context | Notes / best for |
|---|---|---|---|
| `claude-gemini-flash` | Gemini Flash (latest) | 1M | huge context; long-doc reading/summarizing |
| `claude-gemini-pro` | Gemini Pro (latest) | 1M+ | strongest free reasoning + massive context |
| `claude-gemini-flash-lite` | Gemini Flash-Lite (latest) | 1M | cheapest/fastest Gemini; light tasks |
| `claude-gemini-3-flash` | Gemini 3 Flash (preview) | 1M | newer flash |
| `claude-gemini-3.1-pro` | Gemini 3.1 Pro (preview) | 1M+ | newer pro reasoning |
| `claude-gemini-3.5-flash` / `-3.6-flash` | Gemini 3.5 / 3.6 Flash | 1M | latest flash lines |
| `claude-gemini-gemma-31b` / `-gemma-26b` | Gemma 4 31B / 26B | large | open Gemma via Google |

Gemini 1.5 (`flash` / `pro` / `flash-8b`) was **retired**; `flash`/`pro` now alias the rolling `-latest`.
The pinned `gemini-2.5-*` ids were also dropped ("no longer available to new users" on the free
tier). Reasoning-tier ids (`pro`, `3.1-pro`) work but can hit free-tier quota (429). Note: Gemini
2.5/3.x spend a thinking budget — with a very small `max_tokens` they can return empty; give them room.

### Mistral — all free-tier

| Engine id | Model | Context | Notes / best for |
|---|---|---|---|
| `claude-mistral-large` | Mistral Large (latest) | 128K | solid general reasoning |
| `claude-mistral-medium` | Mistral Medium (latest) | 128K | mid tier |
| `claude-mistral-small` | Mistral Small (latest) | 32K | fast, light |
| `claude-mistral-codestral` | Codestral (latest) | 256K | code-specialized |
| `claude-mistral-devstral` / `-devstral-medium` | Devstral (latest) | large | agentic coding |
| `claude-mistral-magistral` | Magistral Small | large | reasoning |
| `claude-mistral-ministral-14b` / `-8b` / `-3b` | Ministral | large | small/edge tiers |
| `claude-mistral-code` | Mistral Code (latest) | large | coding assistant |

### Cerebras — free, need `CEREBRAS_API_KEY`

| Engine id | Model | Context | Notes |
|---|---|---|---|
| `claude-cerebras-gpt-oss` | gpt-oss 120B | ~128K | fastest tokens/sec |
| `claude-cerebras-glm` | GLM-4.7 | ~128K | fast |

Slugs unverified while the key is unset (`.env` has `CEREBRAS_API_KEY=` empty).

### OpenRouter

| Engine id | Model | Cost | Context | Notes |
|---|---|---|---|---|
| `claude-openrouter-free-nemotron-ultra` | Nemotron Ultra 550B | 🆓 | ~256K | shared pool (429s) |
| `claude-openrouter-free-nemotron-super` | Nemotron Super 120B | 🆓 | ~256K | cheap bulk analysis |
| `claude-openrouter-free-nemotron-nano` | Nemotron Nano 30B | 🆓 | large | free general |
| `claude-openrouter-free-nemotron-nano-omni` | Nemotron Nano Omni 30B (reasoning) | 🆓 | large | free reasoning |
| `claude-openrouter-free-nemotron-lightning` | Nemotron 3.5 Lightning | 🆓 | large | fast free |
| `claude-openrouter-free-nemotron-12b-vl` | Nemotron Nano 12B VL | 🆓 | large | vision-language |
| `claude-openrouter-free-nemotron-9b` | Nemotron Nano 9B | 🆓 | large | tiny free |
| `claude-openrouter-free-gemma` | Gemma 4 31B | 🆓 | large | free general |
| `claude-openrouter-free-gemma-26b` | Gemma 4 26B | 🆓 | large | free general |
| `claude-openrouter-free-gpt-oss` | gpt-oss 20B | 🆓 | large | free general |
| `claude-openrouter-free-north-code` | North Mini Code | 🆓 | large | free code model |
| `claude-openrouter-free-ling-tiny` | Ling 3.0 Tiny | 🆓 | large | free tiny |
| `claude-openrouter-free-laguna-s` / `-laguna-xs` | Poolside Laguna 2.1 | 🆓 | large | free code |
| `claude-openrouter-deepseek` | DeepSeek V3.1 | 💲 | ~160K | reliable tool-use / agentic loops |
| `claude-openrouter-qwen-coder` | Qwen3 Coder | 💲 | large | code-focused, cheap |
| `claude-openrouter-haiku-4.5` | Claude Haiku 4.5 | 💲 | 200K | fast cheap Anthropic |
| `claude-openrouter-sonnet-5` | Claude Sonnet 5 | 💲💲 | 200K | strong general |
| `claude-openrouter-opus-4.8` | Claude Opus 4.8 | 💲💲 | 200K | top reasoning alt |
| `claude-openrouter-opus-5` | Claude Opus 5 | 💲💲 | 200K | hardest reasoning, must-be-right work |

### NIM (NVIDIA-hosted; needs `NIM_BASE_URL` + `NIM_API_KEY`, both unset)

`claude-nim-llama3` (Llama 3.1 70B) · `claude-nim-nemotron` (Nemotron Ultra) · `claude-nim-mixtral` (Mixtral 8x7B).

### opencode — OpenCode Zen hosted models (all free)

opencode's own hosted models, served OpenAI-compatibly at
`https://opencode.ai/zen/v1` (`opencode_base_url`). The gateway calls the
endpoint **directly** — not through `opencode serve`'s agent loop — so these
behave like any other model: streaming and **tool-use work** (the OpenAI
translator forwards Claude Code's tool schemas and maps `tool_calls` back to
`tool_use`). No local process required. Ids are flat `claude-opencode-<key>`;
discovery auto-merges any extra **free** (`-free`) models Zen reports — Zen's paid
models (gpt-5.x, claude-*, grok, glm-*) are not surfaced and the public key can't use them.

| Engine id | Zen modelID |
|---|---|
| `claude-opencode-big-pickle` | big-pickle |
| `claude-opencode-deepseek-v4-flash` | deepseek-v4-flash-free |
| `claude-opencode-hy3` | hy3-free |
| `claude-opencode-mimo` | mimo-v2.5-free |
| `claude-opencode-laguna-s` | laguna-s-2.1-free |
| `claude-opencode-ling-tiny` | ling-3.0-tiny-free |
| `claude-opencode-nemotron-lightning` | nemotron-3.5-lightning-free |
| `claude-opencode-nemotron-ultra` | nemotron-3-ultra-free |

Free models use a shared **public** key with a shared rate limit — expect
occasional `FreeUsageLimitError` (surfaced cleanly). Set `OPENCODE_API_KEY` in
`.env` for a personal key with higher limits.

## Routing heuristics

- **Zero cost first**: `claude-groq-llama3` (speed) / `claude-gemini-flash` (big context, 1M) /
  `claude-mistral-large` for routine work. `claude-groq-llama-8b` for trivial mechanical work.
- **Tool-use / agentic loops**: OpenRouter pass-through is most reliable
  (`claude-openrouter-deepseek` cheap, `claude-openrouter-opus-5` premium).
- **Big inputs**: `claude-gemini-pro` or `claude-gemini-flash` (1M).
- **Hard problems only**: `claude-openrouter-opus-5`.
- **Add a model**: edit any `*_model_map` in `gateway_config.yaml`; `:free` slugs, `free-*`
  keys, and any provider in `free_tier_providers` are auto-tagged FREE.

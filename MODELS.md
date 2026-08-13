# Omnirouter — engine menu

The gateway exposes many provider models under one Anthropic-compatible endpoint. This is
the menu; `GET /v1/models` returns the live list, `?free=1` only the free ones.

## How a subagent actually runs on a free engine

**Read this before trying to "pick an engine per spawn" — the obvious way does not work
under an OAuth (Claude Pro/Max) login.** Claude Code, when logged in via the subscription,
sends **all** inference — the main loop *and* every subagent — to Anthropic's managed
endpoint and **ignores `ANTHROPIC_BASE_URL`**. So a subagent's own model can never be a
gateway id: the Task-tool `model` argument (only accepts `sonnet/opus/haiku/fable`) and the
agent `model:` frontmatter both hit Anthropic, which rejects gateway ids. The gateway is
used only for the `/model` discovery list. (Confirmed by measurement and by
anthropics/claude-code#48011 / #38698, both "not planned".)

So there are two real paths:

- **Keep the subscription (default): shell out, don't route.** Run a free engine as a
  subprocess that calls the gateway directly:
  ```
  scripts/gw "summarize what gateway/router.py does"  # reader (read/grep only), $0, auto 429 fallback
  scripts/gw -w "count the .py files under gateway/"   # worker mode: needs shell or edits files
  ```
  Launch `scripts/gw` (or `scripts/gw_agent.py`) as a **background task** — the free engine
  does the work at $0 subscription quota. Full policy in [CLAUDE.md](CLAUDE.md).
- **API-key mode (no OAuth):** `ANTHROPIC_API_KEY=x ANTHROPIC_BASE_URL=<gateway> claude` —
  then subagent inference honors the gateway and per-engine agent-defs
  (`scripts/gen-agent-engines.py`) work natively. This trades the whole session off the
  subscription.

The `model` ids below are what `scripts/gw`/`gw_agent.py` accept for `--model`.

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
| `claude-gemini-flash-lite` | Gemini Flash-Lite (latest) | 1M | cheapest/fastest Gemini; light tasks |
| `claude-gemini-3-flash` | Gemini 3 Flash (preview) | 1M | newer flash |
| `claude-gemini-3.5-flash` / `-3.6-flash` | Gemini 3.5 / 3.6 Flash | 1M | latest flash lines |
| `claude-gemini-gemma-31b` / `-gemma-26b` | Gemma 4 31B / 26B | large | open Gemma via Google |

Gemini 1.5 (`flash` / `pro` / `flash-8b`) was **retired**; the pinned `gemini-2.5-*` ids were also
dropped ("no longer available to new users"). The **pro tier is not free**: `gemini-pro-latest`
and `gemini-3.1-pro` return a free-tier quota of `limit: 0`, so `claude-gemini-pro` / `-3.1-pro`
are intentionally **not** in the menu — add them only with a paid Google key. The flash lines are
genuinely free (15 RPM shared, so a busy minute can still return a clean `429`).

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
- **Big inputs**: `claude-gemini-flash` or `claude-gemini-flash-lite` (1M).
- **Hard problems only**: `claude-openrouter-opus-5`.
- **Add a model**: edit any `*_model_map` in `gateway_config.yaml`; `:free` slugs, `free-*`
  keys, and any provider in `free_tier_providers` are auto-tagged FREE.

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

| Engine id | Model (provider) | Cost | Context | Notes / best for |
|---|---|---|---|---|
| `claude-groq-llama3` | Llama 3.3 70B (Groq) | 🆓 | ~128K | very low latency; quick edits, summaries, bulk text |
| `claude-groq-mixtral` | Mixtral 8x7B (Groq) | 🆓 | 32K | fast MoE; short tasks |
| `claude-groq-gemma` | Gemma2 9B (Groq) | 🆓 | 8K | tiny/fast; trivial mechanical work |
| `claude-gemini-flash` | Gemini 1.5 Flash (Google) | 🆓 | 1M | huge context; long-doc reading/summarizing |
| `claude-gemini-pro` | Gemini 1.5 Pro (Google) | 🆓 | 2M | strongest free reasoning + massive context |
| `claude-gemini-flash-8b` | Gemini 1.5 Flash-8B | 🆓 | 1M | cheapest/fastest Gemini; light tasks |
| `claude-mistral-large` | Mistral Large (Mistral) | 🆓 | 128K | solid general reasoning on free tier |
| `claude-mistral-small` | Mistral Small | 🆓 | 32K | fast, light |
| `claude-mistral-codestral` | Codestral (Mistral) | 🆓 | 256K | code-specialized |
| `claude-cerebras-gpt-oss` | gpt-oss-120b (Cerebras) | 🆓* | ~128K | fastest tokens/sec; needs `CEREBRAS_API_KEY` |
| `claude-cerebras-glm` | GLM-4.7 (Cerebras) | 🆓* | ~128K | fast; needs `CEREBRAS_API_KEY` |
| `claude-openrouter-free-nemotron-super` | Nemotron Super 120B | 🆓 | ~256K | cheap bulk analysis; shared pool (429s) |
| `claude-openrouter-free-nemotron-ultra` | Nemotron Ultra 550B | 🆓 | ~256K | bigger free model; shared pool |
| `claude-openrouter-free-gemma` | Gemma 4 31B | 🆓 | large | free general |
| `claude-openrouter-free-gpt-oss` | gpt-oss-20b | 🆓 | large | free general |
| `claude-openrouter-free-north-code` | North Mini Code | 🆓 | large | free code model |
| `claude-openrouter-deepseek` | DeepSeek V3.1 | 💲 | ~160K | reliable tool-use / agentic loops on a budget |
| `claude-openrouter-qwen-coder` | Qwen3 Coder | 💲 | large | code-focused, cheap |
| `claude-openrouter-sonnet-5` | Claude Sonnet 5 | 💲💲 | 200K | strong general (Anthropic pass-through) |
| `claude-openrouter-opus-5` | Claude Opus 5 | 💲💲 | 200K | hardest reasoning, must-be-right work |
| `claude-openrouter-opus-4.8` | Claude Opus 4.8 | 💲💲 | 200K | top reasoning alt |
| `claude-openrouter-haiku-4.5` | Claude Haiku 4.5 | 💲 | 200K | fast cheap Anthropic |
| `claude-nim-llama3` | Llama 3.1 70B (NVIDIA NIM) | — | 128K | NIM-hosted |
| `claude-nim-nemotron` | Nemotron Ultra (NIM) | — | large | NIM-hosted |
| `claude-nim-mixtral` | Mixtral 8x7B (NIM) | — | 32K | NIM-hosted |
| `claude-opencode-*` | groq/gemini/mistral via opencode bridge | 🆓 | varies | experimental bridge (external dep) |

\* Cerebras engines need `CEREBRAS_API_KEY` in `.env`.

## Routing heuristics

- **Zero cost first**: `claude-groq-llama3` (speed) / `claude-gemini-flash` (big context) /
  `claude-mistral-large` for routine work.
- **Tool-use / agentic loops**: OpenRouter pass-through is most reliable
  (`claude-openrouter-deepseek` cheap, `claude-openrouter-opus-5` premium).
- **Big inputs**: `claude-gemini-pro` (2M) or `claude-gemini-flash` (1M).
- **Hard problems only**: `claude-openrouter-opus-5`.
- **Add a model**: edit any `*_model_map` in `gateway_config.yaml`; `:free` slugs, `free-*`
  keys, and any provider in `free_tier_providers` are auto-tagged FREE.

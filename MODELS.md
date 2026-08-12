# Omnirouter — model routing cheat-sheet

Quick map for choosing which subagent (and therefore provider/model) to spawn.
The orchestrator routes by the `description` field of each `.claude/agents/*.md`;
this table is the same information in one glance. Context/RPM are **static**;
live remaining-quota is not exposed (would require per-provider account calls).

| Subagent | Model (provider) | Cost | Context | Rate limit | Best for |
|---|---|---|---|---|---|
| `fast-groq` | Llama 3.3 70B (Groq) | 🆓 free | ~128K | ~30 RPM | quick edits, summaries, bulk/mechanical text |
| `gemini-flash` | Gemini 1.5 Flash (Google) | 🆓 free | 1M | 15 RPM | long-context reading/summarizing over big inputs |
| `free-openrouter` | Nemotron Super 120B (OpenRouter) | 🆓 free* | ~256K | shared pool (429s) | cheap bulk analysis when latency is OK |
| `cerebras-fast` | gpt-oss-120b (Cerebras) | 🆓 free** | ~128K | 5 RPM | fastest tokens/sec, snappy short completions |
| `tools-openrouter` | DeepSeek V3.1 (OpenRouter) | 💲 cheap | ~160K | pass-through | reliable tool-use / agentic loops on a budget |
| `premium-openrouter` | Claude Opus 5 (OpenRouter) | 💲💲 premium | 200K | pass-through | hardest reasoning, architecture, must-be-right work |

\* free shared pool — can return HTTP 429 when busy (gateway surfaces it cleanly).
\*\* requires `CEREBRAS_API_KEY` in `.env`.

## Notes for routing
- **Zero cost first**: prefer `fast-groq` / `gemini-flash` / `cerebras-fast` for
  routine work; escalate to `tools-openrouter` only when tool reliability matters;
  use `premium-openrouter` only for genuinely hard problems.
- **Tool-use**: most reliable on the OpenRouter pass-through agents
  (`tools-openrouter`, `premium-openrouter`); Groq/Gemini tool-calling is best-effort.
- **Big inputs**: `gemini-flash` (1M) or `free-openrouter` (~256K).
- **See only free models**: `GET http://127.0.0.1:8787/v1/models?free=1`.
- **Add a model**: edit `openrouter_model_map` (or any `*_model_map`) in
  `gateway_config.yaml`; any `:free` slug or `free-*` key is auto-tagged FREE, and
  every model from a provider listed in `free_tier_providers` is tagged too.

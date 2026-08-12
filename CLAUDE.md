# Omnirouter — subagent engine policy (for the orchestrator)

When you spawn a subagent, YOU (the orchestrator) choose which engine powers it by
passing a gateway model id as the `model` argument. There are two generic subagents:
`worker` (full tools) and `reader` (read-only). Full engine menu: [MODELS.md](MODELS.md).

## Hard rule: FREE engines only, unless the user explicitly authorizes a paid one

Always spawn subagents on a **free** engine. **Never** use a paid engine unless the user
has explicitly authorized it for that task in this conversation — no "it's a hard problem"
exception, no silent escalation. If a task seems to need a paid engine, first do your best
on a free one; if that is genuinely insufficient, STOP and ask the user for permission to
use a specific paid engine, naming it and why. Free engines are labeled `FREE 🆓` in the
model list; `GET /v1/models?free=1` returns only the free ones.

### Free engine ids, by what they're good at

| Need | Engine id (free) | Notes |
|---|---|---|
| Fast quick edits / summaries / bulk text | `claude-groq-llama3` | ~128K ctx, very low latency |
| Trivial mechanical work, fastest | `claude-groq-llama-8b` | Llama 3.1 8B Instant |
| Very large input (long docs, whole files) | `claude-gemini-flash` / `claude-gemini-flash-lite` (1M) | Google free tier, 15 RPM |
| Cheap bulk analysis, some latency OK | `claude-openrouter-free-nemotron-super` | ~256K, shared pool (429s) |
| General reasoning, free | `claude-mistral-large` | 128K |
| Code-specialized, free | `claude-mistral-codestral` | 256K |
| Fastest tokens/sec (needs CEREBRAS_API_KEY) | `claude-cerebras-gpt-oss` | ~128K, 5 RPM |

Other free ids — Groq: `claude-groq-gpt-oss-120b`, `-gpt-oss-20b`, `-qwen3`, `-compound`,
`-compound-mini`. Gemini: `claude-gemini-flash-lite`, `-3-flash`,
`-3.5-flash`, `-3.6-flash`, `-gemma-31b`, `-gemma-26b` (pro tier is not free — omitted).
Mistral: `claude-mistral-small`,
`-medium`, `-devstral`, `-magistral`, `-ministral-8b`, `-code`. OpenRouter free:
`claude-openrouter-free-nemotron-ultra`, `-nemotron-nano`, `-nemotron-lightning`, `-nemotron-9b`,
`-gemma`, `-gemma-26b`, `-gpt-oss`, `-north-code`, `-ling-tiny`, `-laguna-s`.
The `claude-opencode-*` ids are opencode's own hosted "OpenCode Zen" models (e.g.
`big-pickle`, `deepseek-v4-flash`, `nemotron-ultra`) — free, OpenAI-compatible, and
tool-capable (shared public key, so occasional rate limits).
(`mixtral`/`gemma` on Groq and Gemini 1.5/2.5 were retired — no longer in the menu.)

### Paid engines — only after explicit user authorization

Do not pick these on your own. Ask first, name the engine and the reason, and wait for a
clear yes. Reference for when to propose one:

- Reliable tool-use / agentic multi-step loops → `claude-openrouter-deepseek` (cheap).
- Hardest reasoning, must-be-right refactors/architecture → `claude-openrouter-opus-5`.

## Choosing the engine for a spawn

The Task/Agent spawn tool's `model` argument only accepts the four built-in aliases
(`sonnet`/`opus`/`haiku`/`fable`), and those route to Anthropic — **not** the gateway.
So you cannot pass a gateway id straight to a spawn. Real ways to put a subagent on a
specific free gateway engine, in order of use:

1. **Per-engine subagent (per-spawn choice, shows in the native agents widget).** Spawn
   a pinned variant by `subagent_type`: `reader-gemini-flash`, `worker-groq-llama3`,
   `worker-mistral-codestral`, etc. Each is a native agent-def whose frontmatter pins
   `model:` to a gateway id, so it runs on that engine through the gateway. Generate/refresh
   the set with `python scripts/gen-agent-engines.py` (`--all` for every free id). New
   agent-defs only register on a session restart.
2. **Session default (all subagents).** Set `CLAUDE_CODE_SUBAGENT_MODEL=claude-groq-llama3`
   in `.claude/settings.local.json`; plain `worker`/`reader` then run on that engine.
3. Free engines can return HTTP 429 (rate limit) — the gateway surfaces it as a clean 429;
   retry shortly or switch to another free engine.

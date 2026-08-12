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
| Very large input (long docs, whole files) | `claude-gemini-pro` (2M) / `claude-gemini-flash` (1M) | Google free tier, 15 RPM |
| Cheap bulk analysis, some latency OK | `claude-openrouter-free-nemotron-super` | ~256K, shared pool (429s) |
| General reasoning, free | `claude-mistral-large` | 128K |
| Code-specialized, free | `claude-mistral-codestral` | 256K |
| Fastest tokens/sec (needs CEREBRAS_API_KEY) | `claude-cerebras-gpt-oss` | ~128K, 5 RPM |

Other free ids: `claude-groq-mixtral`, `claude-groq-gemma`, `claude-gemini-flash-8b`,
`claude-mistral-small`, `claude-openrouter-free-nemotron-ultra`,
`claude-openrouter-free-gemma`, `claude-openrouter-free-gpt-oss`,
`claude-openrouter-free-north-code`. The `claude-opencode-*` ids are an experimental bridge.

### Paid engines — only after explicit user authorization

Do not pick these on your own. Ask first, name the engine and the reason, and wait for a
clear yes. Reference for when to propose one:

- Reliable tool-use / agentic multi-step loops → `claude-openrouter-deepseek` (cheap).
- Hardest reasoning, must-be-right refactors/architecture → `claude-openrouter-opus-5`.

## Selection is per spawn

- Per-spawn: pass `model: <id>` when spawning `worker`/`reader`.
- To pin ALL subagents to one free engine for a while, the user can set
  `CLAUDE_CODE_SUBAGENT_MODEL=claude-groq-llama3` in `.claude/settings.local.json`.
- Free engines can return HTTP 429 (rate limit); the gateway surfaces it cleanly — retry
  shortly or fall back to another free engine.

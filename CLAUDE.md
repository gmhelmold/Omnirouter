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

## Running work on a free gateway engine

**Hard fact (measured):** under OAuth login — this repo's default — Claude Code sends
ALL inference (main loop *and* subagents) to Anthropic's managed endpoint and **ignores
`ANTHROPIC_BASE_URL`**. So a subagent's own model can never be a gateway id: the Task
tool `model` arg (4 aliases) and agent-def frontmatter `model:` both hit managed
Anthropic, which rejects gateway ids. `CLAUDE_CODE_SUBAGENT_MODEL` is likewise bypassed.
This is by design (anthropics/claude-code#48011, closed "not planned"); the gateway is
used only for the `/model` discovery list.

To actually run a free gateway engine, **don't route inference — shell out** (the
codex-plugin pattern). Use the engine loop:

```
scripts/gw "summarize what gateway/router.py does"  # reader: read_file/grep only, no shell
scripts/gw -w "count the .py files under gateway/"  # worker: needs shell (bash) or writes
scripts/gw -m claude-groq-llama3 "..."              # prefer an engine (still falls back)
# raw engine loop (what the wrapper calls):
python scripts/gw_agent.py --model <gateway-id> --mode <reader|worker> --task "..." [--cwd DIR]
```

`gw_agent.py` is a self-contained tool-use loop (reader: read_file/list_dir/grep, read-only
with no shell; worker adds bash + write/edit) that calls the gateway directly — it self-authenticates with the providers'
keys — and walks a free-engine chain internally (`DEFAULT_FALLBACKS`), so a 429 on one
engine just falls through to the next. `scripts/gw` wraps it with sensible defaults.

**Default way to run it — background task, $0 subscription quota:** launch `scripts/gw ...`
with Bash `run_in_background`. The free engine does the work; no Claude tokens are spent.
Give each a distinct `description` and fan out several for brute-force parallelism — this
is the path to prefer for anything at scale.

There is NO free way to get a native agent-widget card: a native subagent's inference is
always managed Claude (OAuth), and MCP tools don't appear in that widget either — hard app
limits. A `worker` spawned with `model: haiku` that shells out to `gw_agent.py` *does* get
a card, but it costs ~5–6k Haiku tokens of subscription quota **per spawn** (measured, and
it grows with the result size and is re-read by the orchestrator). So use the Haiku-wrapper
card only for the occasional spawn where the card matters — never for fan-out.

**Only in API-key auth mode** (`ANTHROPIC_API_KEY=x ANTHROPIC_BASE_URL=<gateway> claude`,
no OAuth) does inference honor the gateway. There, per-engine agent-defs work natively:
generate them with `python scripts/gen-agent-engines.py` and spawn `worker-groq-llama3`
etc. — native card + free engine + $0 Claude. That trades the whole session off the
Claude subscription, so it is opt-in, not the default.

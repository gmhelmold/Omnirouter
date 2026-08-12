# Omnirouter — HANDOFF for the Next Contributor

> Read this first. It is a state dump — where the repo is, what just changed,
> how to prove it works, and what debt you inherit. Current as of **2026-08-12**.

## 1. Repo state

- **Repo**: `/Users/gustavoschneiter/Documents/Omnirouter` (standalone git, branch `master`, working tree clean).
- **Product**: `claude-gateway` v0.1.0 — a local, Anthropic-compatible gateway so Claude Code's `/model` picker can use **8 providers** (Anthropic, OpenRouter, Groq, Gemini, Mistral, Cerebras, NIM, OpenCode Zen).
- **Environment**: `.venv` (Python 3.14), `pip install -e ".[dev]"` already run.
- **Tests**: **79 passing**, no known failures.
- **Keys**: `OPENROUTER_API_KEY` and `OPENCODE_API_KEY` are set in the (gitignored) `.env`; Groq/Gemini/Mistral keys were already present. Cerebras and NIM are **unset** — those providers stay inert until configured.

## 2. What the last session shipped

1. **OpenCode Zen, direct (tool-capable).** Replaced the never-working per-provider opencode-bridge (session API, text-only) with a direct OpenAI-compatible call to `opencode_base_url` (`https://opencode.ai/zen/v1`). `claude-opencode-*` now supports tool-use and streaming; no local process. Flat ids `claude-opencode-<key>`.
2. **OpenRouter non-streaming fix.** `collect_message` now folds OpenRouter's `raw` Anthropic SSE lines — previously every `claude-openrouter-*` returned empty in `stream:false`.
3. **Free-only discovery.** The live OpenCode Zen merge is restricted to `-free` ids so paid Zen models (gpt-5.x, claude-*, grok, glm-*) are never tagged `FREE 🆓`.
4. **Reasoning strip.** The OpenAI translator removes inline `<think>…</think>` (state machine safe across chunk splits; never drops `a < b`).
5. **Streaming keepalive.** The stream emits an Anthropic `ping` every `KEEPALIVE_INTERVAL` (default 15s) of upstream silence, without cancelling the request. Wires up the previously-unused `keepalive_interval`.
6. **Menu refresh.** Retired dead ids (Groq `mixtral`/`gemma`, Gemini 1.5 & 2.5, OpenRouter `free-lfm`); added current Groq/Gemini/Mistral/OpenRouter-free models, verified live.
7. **Docs.** Rewrote `README.md`, `OWNERSHIP.md` (code map, invariants §5.7–5.10, decision log §11), `CLAUDE.md`, `.env.example`, and added `docs/omnirouter.html` (a self-contained, Linear-styled onboarding + feature manual with an embedded Inter font, ⌘K palette, provider modals, and a dark/light toggle).

## 2b. This session (subagent engines on free models + gateway fixes)

**Gateway/menu fixes (committed):**
- **Error status mapping.** Non-streaming errors were hardcoded to HTTP 502; now
  mapped by type (`rate_limit`→429, auth→401, …) via `_http_status_for_error` +
  tests. Free-tier 429s now read as 429, not a fake gateway crash.
- **respx** added to dev deps — `test_opencode_backend.py` imported it undeclared,
  so the suite failed to collect (75 + error). Now **79 passing**.
- **Gemini menu honesty.** Removed `claude-gemini-pro` / `-3.1-pro`: Google gives
  them a free-tier quota of `limit: 0` (never work free, were falsely tagged FREE).
  Dropped the `pro` default from `GeminiModelMap` + YAML. Discovery = **58 models**.
- **Model logging.** `POST /v1/messages` logs `incoming request model=…` for routing
  visibility. Discovery caches to `~/.claude/cache/gateway-models.json` (TTL 300s) —
  config changes need a restart **and** a cache bust to show.

**The subagent-engine reality (the big finding — don't reopen it):**
- Under **OAuth login** (Claude Pro/Max subscription — the default here), Claude Code
  sends ALL inference (main loop AND subagents) to managed Anthropic and **ignores
  `ANTHROPIC_BASE_URL`**. Measured, and confirmed by anthropics/claude-code#48011 /
  #38698 / #52572 (all "not planned"). So a subagent can NEVER run on a gateway id:
  the Task-tool `model` arg (4 aliases) and agent-def frontmatter `model:` both hit
  Anthropic, which rejects gateway ids. Third-Party Inference mode routes to a gateway
  but **replaces the subscription** (API-key only) — you cannot have both.
- **Therefore, the compliant way to run free engines while keeping the subscription is
  to shell out**, not to route inference. `scripts/gw_agent.py` is a self-contained
  tool-use loop that calls the gateway directly (provider keys), with internal 429
  fallback. `scripts/gw "task"` is the one-line wrapper; launch it as a **background
  Bash task** ($0 subscription quota). A native agent-widget *card* is NOT free — only
  a `worker` + `model: haiku` shell-out gets one, at ~5–6k Haiku tokens/spawn (measured);
  use that only for the rare spawn where the card matters, never for fan-out.
- Full policy + usage in `CLAUDE.md` ("Running work on a free gateway engine"). The
  `scripts/gen-agent-engines.py` per-engine agent-defs only route in API-key mode.

## 3. Prove it works

```bash
cd /Users/gustavoschneiter/Documents/Omnirouter
source .venv/bin/activate

python -m pytest tests/ -q        # expect: 79 passed
scripts/gw "count the .py files under gateway/"   # free-engine spawn ($0), runs on the gateway

# boot + discovery + a live call
./scripts/ensure-gateway.sh
curl -s http://127.0.0.1:8787/v1/models | python3 -c 'import sys,json;print(len(json.load(sys.stdin)["data"]),"models")'
curl -s http://127.0.0.1:8787/v1/messages -H 'Content-Type: application/json' \
  -d '{"model":"claude-mistral-large","max_tokens":512,"stream":false,"messages":[{"role":"user","content":"say PONG"}]}'
```

Free-tier engines can return `429`/quota intermittently (shared pools) — that is expected and surfaced as a clean error, not a gateway fault.

## 4. Known failures

None. The two historically-failing tests (`test_tool_result_conversion`, `test_openai_to_anthropic`) pass in the current suite (`OWNERSHIP.md §7`, K1/K2 resolved).

## 5. Debt you inherit

- **ruff/mypy baseline is dirty** — policy is regression-gated: new errors in touched files are rejected; wholesale cleanup is not required. (`OWNERSHIP.md §7`, K3.)
- `gateway/config.py` has unused imports — an easy win (K4).
- **No CI** yet (K5) and no `pre-commit` config despite `pre-commit` in dev deps.
- **Cerebras / NIM** are unconfigured (K7) — add keys to `.env` to exercise them.

## 6. Gotchas (load-bearing — see OWNERSHIP.md §5)

- **`collect_message` must fold `raw` events** — the OpenRouter non-streaming path depends on it (§5.7).
- **Keepalive must never cancel** the upstream — await the same pending event across pings, never `asyncio.wait_for` the generator step (§5.8).
- **Discovery is free-only** for the Zen live merge (`-free` filter) — do not surface paid models as free (§5.9).
- **`<think>` isolation** — the strip must survive a tag split across chunks and not eat plain `<`/`>` text (§5.10).
- **OpenCode Zen is a direct provider**, not a bridge and not a fallback — there is no `opencode serve` to run; all `fallback_chains` are currently empty.
- **Discovery ids must contain `claude`/`anthropic`** — Claude Code drops everything else; `build_discovery_payload` validates this.
- **`orjson` for all JSON responses** — a plain dict to `Response(content=...)` raises at runtime.
- **Secrets** — never commit `.env`; keep `.env.example` placeholders-only. No real key may appear in any tracked file.

## 7. Where the docs live

- `README.md` — user-facing usage.
- `MODELS.md` — the full engine menu (kept in step with `gateway_config.yaml`).
- `OWNERSHIP.md` — ownership map, decision rights, invariants, quality gate, known-issue ledger, decision log.
- `CLAUDE.md` — subagent engine policy (free-only) for the orchestrator.
- `docs/omnirouter.html` — human onboarding + feature manual (self-contained).
- `HANDOFF.md` — this file; refresh it whenever a session ends with state to preserve.

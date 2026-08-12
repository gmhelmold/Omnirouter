# Omnirouter — HANDOFF for the Next Contributor

> Read this first. It is a state dump — where the repo is, what just changed,
> how to prove it works, and what debt you inherit. Current as of **2026-08-12**.

## 1. Repo state

- **Repo**: `/Users/gustavoschneiter/Documents/Omnirouter` (standalone git, branch `master`, working tree clean).
- **Product**: `claude-gateway` v0.1.0 — a local, Anthropic-compatible gateway so Claude Code's `/model` picker can use **8 providers** (Anthropic, OpenRouter, Groq, Gemini, Mistral, Cerebras, NIM, OpenCode Zen).
- **Environment**: `.venv` (Python 3.14), `pip install -e ".[dev]"` already run.
- **Tests**: **77 passing**, no known failures.
- **Keys**: `OPENROUTER_API_KEY` and `OPENCODE_API_KEY` are set in the (gitignored) `.env`; Groq/Gemini/Mistral keys were already present. Cerebras and NIM are **unset** — those providers stay inert until configured.

## 2. What the last session shipped

1. **OpenCode Zen, direct (tool-capable).** Replaced the never-working per-provider opencode-bridge (session API, text-only) with a direct OpenAI-compatible call to `opencode_base_url` (`https://opencode.ai/zen/v1`). `claude-opencode-*` now supports tool-use and streaming; no local process. Flat ids `claude-opencode-<key>`.
2. **OpenRouter non-streaming fix.** `collect_message` now folds OpenRouter's `raw` Anthropic SSE lines — previously every `claude-openrouter-*` returned empty in `stream:false`.
3. **Free-only discovery.** The live OpenCode Zen merge is restricted to `-free` ids so paid Zen models (gpt-5.x, claude-*, grok, glm-*) are never tagged `FREE 🆓`.
4. **Reasoning strip.** The OpenAI translator removes inline `<think>…</think>` (state machine safe across chunk splits; never drops `a < b`).
5. **Streaming keepalive.** The stream emits an Anthropic `ping` every `KEEPALIVE_INTERVAL` (default 15s) of upstream silence, without cancelling the request. Wires up the previously-unused `keepalive_interval`.
6. **Menu refresh.** Retired dead ids (Groq `mixtral`/`gemma`, Gemini 1.5 & 2.5, OpenRouter `free-lfm`); added current Groq/Gemini/Mistral/OpenRouter-free models, verified live.
7. **Docs.** Rewrote `README.md`, `OWNERSHIP.md` (code map, invariants §5.7–5.10, decision log §11), `CLAUDE.md`, `.env.example`, and added `docs/omnirouter.html` (a self-contained, Linear-styled onboarding + feature manual with an embedded Inter font, ⌘K palette, provider modals, and a dark/light toggle).

## 3. Prove it works

```bash
cd /Users/gustavoschneiter/Documents/Omnirouter
source .venv/bin/activate

python -m pytest tests/ -q        # expect: 77 passed

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

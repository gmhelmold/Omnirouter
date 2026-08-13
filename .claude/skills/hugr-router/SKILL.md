---
name: hugr-router
description: Offload grunt work to FREE gateway engines instead of spending Claude Max quota. Use when the user says "use the router / free models", "don't burn my quota", or when a task is mechanical and voluminous — bulk file reads, repo-wide greps, counts, repetitive edits, first-pass audits, or wide parallel fan-out. Dispatches scripts/gw as background tasks that run at $0 subscription quota; Claude stays the orchestrator and verifies the output.
---

# hugr-router — route grunt work to free gateway engines

Omnirouter serves free provider models (groq / gemini / mistral / nemotron / …) behind a
local Anthropic-compatible gateway. A Claude subscription (OAuth) will **not** route a
subagent's inference to the gateway, so we **shell out**: `scripts/gw` runs a real tool-use
loop on a chosen free engine as a subprocess. The heavy reasoning runs at **$0** of Max
quota; Claude stays the orchestrator — it plans, dispatches, and verifies.

## When to use
- Voluminous / mechanical work: bulk reads, repo-wide greps, counts, repetitive edits,
  first-pass audits, summarizing many files.
- Brute-force parallelism: fan out one engine per slice.
- Whenever the user asks to "use the router / free models" or wants to save quota.

## When NOT to use
- Tiny tasks you can finish inline faster than dispatching.
- Correctness-critical or judgment-heavy calls (architecture, security conclusions) —
  free engines are fallible; keep those on Claude and only use gw for the legwork.

## Dispatch — always as a background Bash task
Launch with the Bash tool and `run_in_background: true`. It runs at $0 quota and appears in
the running-tasks widget; the trace + final answer stream to the task's output file, which
you read when the task completes.

```
scripts/gw "summarize what gateway/router.py does"    # reader: read_file / list_dir / grep, NO shell
scripts/gw -w "count the .py files under gateway/"    # worker: adds bash + write_file / edit_file
scripts/gw -m claude-groq-llama3 "..."                # prefer an engine (falls back on 429)
```

Fan out several calls with distinct Bash `description`s for parallel brute force.

## reader vs worker
- **reader** (default) — read-only: `read_file` / `list_dir` / `grep`. No shell, no writes.
  Safe for analysis and audits directly on the repo.
- **worker** (`-w`) — adds unrestricted `bash` + `write_file` / `edit_file`. Use only when
  the task genuinely needs a shell (counting, running commands) or must edit files. File
  tools are confined to the working dir; bash is not — don't point worker at untrusted tasks.

## Engines & fallback
`gw` walks a reliable free chain on 429/5xx, so a rate-limited engine falls through on its
own. Pick a specific engine with `-m <id>`:
- `claude-gemini-flash` — 1M context, long-doc / whole-repo reads
- `claude-mistral-codestral` — code
- `claude-openrouter-free-nemotron-super` — cheap bulk analysis
- `claude-groq-llama3` — fast general
Full menu: `MODELS.md` or `GET http://127.0.0.1:8787/v1/models?free=1`.

## Verify (important)
Free engines are cheap but fallible — they hallucinate line numbers, miscount, and ramble.
**Confirm every concrete claim** (a finding, a number, a `file:line`) against the repo
before acting on it. Treat gw output as a fast first pass, not ground truth. (A real example:
a free-engine audit surfaced a genuine bug that a manual pass missed — but only after its
claim was verified directly, since the same run also produced pages of noise.)

## Requirements
The gateway must be listening on `127.0.0.1:8787`; the SessionStart hook runs
`scripts/ensure-gateway.sh`. If a `gw` call fails with a connection error, start it with
that script and retry.

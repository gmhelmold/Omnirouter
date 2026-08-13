#!/usr/bin/env python3
# ruff: noqa: T201, E501  (CLI script: print() is the user-facing output; a few lines run long.)
"""Generate per-engine subagent definitions for Claude Code.

IMPORTANT — API-KEY MODE ONLY. These agent-defs only route to the gateway when
Claude Code authenticates via ANTHROPIC_API_KEY (no OAuth). Under OAuth login
(the default for the desktop app / subscription), Claude Code ignores
ANTHROPIC_BASE_URL and sends subagent inference to managed Anthropic, which
rejects gateway ids — so spawning these fails. Under OAuth, run free engines via
`scripts/gw_agent.py` (shell-out) instead; see CLAUDE.md.

Each generated `.claude/agents/<mode>-<key>.md` is a NATIVE Claude Code subagent
whose frontmatter pins `model:` to a gateway model id. In API-key mode, spawning
it (by `subagent_type`) renders in the native agents widget AND routes its
inference through the gateway (ANTHROPIC_BASE_URL) on the chosen free engine.

This exists because the Task/Agent spawn tool's `model` argument only accepts the
4 built-in aliases (sonnet/opus/haiku/fable) and cannot take a gateway id. Pinning
the engine in the agent-def is the supported way to pick a free engine per spawn
while keeping the native widget.

Usage:
    python scripts/gen-agent-engines.py            # curated set (default)
    python scripts/gen-agent-engines.py --all      # every FREE gateway id (live)
    python scripts/gen-agent-engines.py --clean     # remove generated files

Generated files carry an `x-omnirouter-generated: true` marker in frontmatter so
--clean and regeneration never touch hand-written agents (reader.md / worker.md).
After running, RESTART the Claude Code session so the new agents register.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.request

REPO = pathlib.Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO / ".claude" / "agents"
GATEWAY = "http://127.0.0.1:8787"
MARKER = "x-omnirouter-generated"

# Curated engines: (gateway_id, short_label, note, modes)
# modes: which generic roles to emit — "reader" (read-only) and/or "worker" (full).
CURATED: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("claude-groq-llama3", "Groq Llama 3.3 70B", "fast general work, ~128K ctx, very low latency", ("reader", "worker")),
    ("claude-groq-llama-8b", "Groq Llama 3.1 8B", "fastest/cheapest, trivial mechanical work", ("worker",)),
    ("claude-gemini-flash", "Gemini Flash", "1M context — long docs / whole-repo reads", ("reader",)),
    ("claude-gemini-flash-lite", "Gemini Flash-Lite", "cheapest/fastest Gemini, light tasks, 1M ctx", ("reader", "worker")),
    ("claude-openrouter-free-nemotron-super", "Nemotron Super (free)", "cheap bulk analysis, ~256K ctx (shared pool, 429s)", ("reader", "worker")),
    ("claude-mistral-large", "Mistral Large", "general reasoning, 128K ctx", ("reader", "worker")),
    ("claude-mistral-codestral", "Mistral Codestral", "code-specialized, 256K ctx", ("worker",)),
]

ROLE_BODY = {
    "reader": (
        "Read, search, and analyze; do NOT edit or write files (you have no write tools). "
        "Return concrete, cited results (file:line where relevant), not vague prose."
    ),
    "worker": (
        "Do exactly what the spawn prompt asks with the tools you have (read, search, run, "
        "edit, write). Keep output focused; verify tool results before acting on them."
    ),
}
ROLE_TOOLS = {
    "reader": "Read, Grep, Glob, Bash",
    "worker": "Read, Grep, Glob, Bash, Edit, Write",
}


def render(mode: str, model_id: str, label: str, note: str) -> str:
    key = model_id.removeprefix("claude-")
    name = f"{mode}-{key}"
    role = "read-only researcher" if mode == "reader" else "general-purpose worker"
    desc = (
        f"{mode.capitalize()} subagent pinned to the free {label} engine "
        f"({note}). Routes through the Omnirouter gateway. "
        f"Use when you want this engine for the spawn."
    )
    return f"""---
name: {name}
description: "{desc}"
model: {model_id}
tools: {ROLE_TOOLS[mode]}
{MARKER}: true
---
You are a {role} running through the Omnirouter gateway on the {label} engine
(`{model_id}`). {ROLE_BODY[mode]}
Free-tier engines can be rate-limited (HTTP 429) — report it rather than looping.
"""


def fetch_free_ids() -> list[tuple[str, str]]:
    url = f"{GATEWAY}/v1/models?free=1"
    with urllib.request.urlopen(url, timeout=10) as r:
        data = json.loads(r.read())
    out = []
    for m in data.get("data", []):
        mid = m["id"]
        out.append((mid, m.get("display_name", mid)))
    return out


def clean() -> int:
    n = 0
    for p in AGENTS_DIR.glob("*.md"):
        text = p.read_text(encoding="utf-8")
        if f"{MARKER}: true" in text:
            p.unlink()
            n += 1
    return n


def _agent_path(mode: str, model_id: str) -> pathlib.Path | None:
    """Resolve the agent-def path for an engine, guarded against path traversal
    from a crafted model id (ids can be merged from a remote /models list, so a
    ``../`` in one must never let the write escape AGENTS_DIR). Returns None (and
    warns) for an unsafe id."""
    key = model_id.removeprefix("claude-")
    p = (AGENTS_DIR / f"{mode}-{key}.md").resolve()
    # Reject separators / parent refs in the key, and require the result to land
    # directly in AGENTS_DIR (belt and suspenders against traversal).
    if "/" in key or "\\" in key or ".." in key or p.parent != AGENTS_DIR.resolve():
        print(f"skip: unsafe model id {model_id!r}", file=sys.stderr)
        return None
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="emit reader+worker for every FREE gateway id (live)")
    ap.add_argument("--clean", action="store_true", help="remove generated agent files and exit")
    args = ap.parse_args()

    AGENTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.clean:
        print(f"removed {clean()} generated agent files")
        return 0

    # Regenerate cleanly: drop previously generated files first.
    clean()

    written = 0
    if args.all:
        try:
            ids = fetch_free_ids()
        except Exception as e:
            print(f"error: could not reach gateway at {GATEWAY} ({e}); is it running?", file=sys.stderr)
            return 1
        for model_id, label in ids:
            for mode in ("reader", "worker"):
                fp = _agent_path(mode, model_id)
                if fp is None:
                    continue
                fp.write_text(render(mode, model_id, label, "free gateway engine"), encoding="utf-8")
                written += 1
    else:
        for model_id, label, note, modes in CURATED:
            for mode in modes:
                fp = _agent_path(mode, model_id)
                if fp is None:
                    continue
                fp.write_text(render(mode, model_id, label, note), encoding="utf-8")
                written += 1

    print(f"wrote {written} agent-def files to {AGENTS_DIR}")
    print("RESTART the Claude Code session so the new subagents register.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

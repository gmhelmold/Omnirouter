#!/usr/bin/env python3
"""gw_agent — a self-contained agent loop that runs on a GATEWAY model.

It talks to the local Omnirouter gateway (Anthropic Messages API) with a chosen
free model id and runs a real tool-use loop (read / list / grep / bash, plus
write / edit in worker mode) until the model stops. Because it calls the gateway
directly (127.0.0.1:8787, which self-authenticates with the providers' keys), it
works regardless of how Claude Code itself is authenticated (OAuth or api-key) —
the codex-plugin trick: shell out instead of routing Claude's own inference.

Run it as a BACKGROUND Bash task so it shows up in the running-tasks widget and
costs no Claude tokens:

    python scripts/gw_agent.py --model claude-groq-llama3 --mode worker \
        --task "..." --out /tmp/gw-result.txt

Exit code 0 on a clean finish, 1 on error. Final answer goes to stdout and --out.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import urllib.error
import urllib.request

GATEWAY = "http://127.0.0.1:8787/v1/messages"

READER_TOOLS = ["read_file", "list_dir", "grep", "bash"]
WORKER_TOOLS = READER_TOOLS + ["write_file", "edit_file"]

TOOL_SCHEMAS = {
    "read_file": {
        "name": "read_file",
        "description": "Read a UTF-8 text file and return its contents.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    "list_dir": {
        "name": "list_dir",
        "description": "List entries of a directory (non-recursive).",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    "grep": {
        "name": "grep",
        "description": "Search files for a regex. Returns matching 'path:line: text' lines.",
        "input_schema": {"type": "object", "properties": {
            "pattern": {"type": "string"}, "path": {"type": "string", "description": "file or dir (default .)"}},
            "required": ["pattern"]},
    },
    "bash": {
        "name": "bash",
        "description": "Run a shell command in the working directory. Returns stdout+stderr (truncated).",
        "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]},
    },
    "write_file": {
        "name": "write_file",
        "description": "Overwrite (or create) a file with the given text.",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
    },
    "edit_file": {
        "name": "edit_file",
        "description": "Replace the first exact occurrence of old with new in a file.",
        "input_schema": {"type": "object", "properties": {
            "path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
            "required": ["path", "old", "new"]},
    },
}

MAX_OUT = 20000  # cap tool output fed back to the model


def _clip(s: str) -> str:
    return s if len(s) <= MAX_OUT else s[:MAX_OUT] + f"\n...[truncated {len(s) - MAX_OUT} chars]"


def run_tool(name: str, args: dict, cwd: pathlib.Path) -> str:
    try:
        if name == "read_file":
            return _clip((cwd / args["path"]).read_text(encoding="utf-8", errors="replace"))
        if name == "list_dir":
            p = cwd / args.get("path", ".")
            return "\n".join(sorted(e.name + ("/" if e.is_dir() else "") for e in p.iterdir()))
        if name == "grep":
            target = args.get("path", ".")
            out = subprocess.run(["grep", "-rniE", args["pattern"], target], cwd=cwd,
                                 capture_output=True, text=True, timeout=30)
            return _clip(out.stdout or "(no matches)")
        if name == "bash":
            out = subprocess.run(args["cmd"], cwd=cwd, shell=True, capture_output=True, text=True, timeout=120)
            return _clip((out.stdout or "") + (out.stderr or "") or "(no output)")
        if name == "write_file":
            (cwd / args["path"]).write_text(args["content"], encoding="utf-8")
            return f"wrote {args['path']} ({len(args['content'])} chars)"
        if name == "edit_file":
            fp = cwd / args["path"]
            text = fp.read_text(encoding="utf-8")
            if args["old"] not in text:
                return "ERROR: old string not found"
            fp.write_text(text.replace(args["old"], args["new"], 1), encoding="utf-8")
            return f"edited {args['path']}"
        return f"ERROR: unknown tool {name}"
    except Exception as e:  # tools must never crash the loop
        return f"ERROR: {type(e).__name__}: {e}"


# Reliable free fallbacks tried (in order) when the primary engine 429s / errors.
DEFAULT_FALLBACKS = [
    "claude-mistral-large",
    "claude-gemini-flash",
    "claude-openrouter-free-nemotron-super",
    "claude-groq-llama3",
]


def _post(model: str, body: dict) -> dict:
    body = {**body, "model": model}
    req = urllib.request.Request(GATEWAY, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.loads(r.read())


def call_gateway(engines: list[str], state: dict, body: dict, log) -> dict:
    """Call the gateway, walking `engines` on 429/5xx/transport errors.

    `state['i']` tracks the current engine so a mid-task fallback sticks for the
    rest of the run. Raises RuntimeError only if every engine fails.
    """
    last = ""
    while state["i"] < len(engines):
        model = engines[state["i"]]
        try:
            return _post(model, body)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (429, 500, 502, 503, 504):
                log(f"  ! {model} → {last}, falling back")
                state["i"] += 1
                continue
            raise
        except Exception as e:  # transport
            last = f"{type(e).__name__}"
            log(f"  ! {model} → {last}, falling back")
            state["i"] += 1
    raise RuntimeError(f"all engines exhausted (last: {last})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="gateway model id, e.g. claude-groq-llama3")
    ap.add_argument("--task", required=True, help="the instruction for the agent")
    ap.add_argument("--mode", choices=["reader", "worker"], default="reader")
    ap.add_argument("--fallback", default="", help="comma-separated fallback engine ids on 429/error (default: a reliable free set)")
    ap.add_argument("--cwd", default=".", help="working directory for tools")
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--out", default=None, help="write the final answer to this file too")
    args = ap.parse_args()

    cwd = pathlib.Path(args.cwd).resolve()
    tool_names = WORKER_TOOLS if args.mode == "worker" else READER_TOOLS
    tools = [TOOL_SCHEMAS[t] for t in tool_names]
    messages = [{"role": "user", "content": args.task}]

    # engine chain: primary + fallbacks (de-duped, primary first)
    fallbacks = args.fallback.split(",") if args.fallback else DEFAULT_FALLBACKS
    engines, seen = [], set()
    for m in [args.model, *fallbacks]:
        m = m.strip()
        if m and m not in seen:
            seen.add(m); engines.append(m)
    state = {"i": 0}

    def log(msg: str) -> None:
        print(msg, file=sys.stderr, flush=True)

    task_line = args.task if len(args.task) <= 88 else args.task[:88] + "…"
    log(f"┌─ gw_agent · engine={engines[0]} · mode={args.mode}")
    log(f"│  task: {task_line}")

    final_text = ""
    for step in range(1, args.max_steps + 1):
        try:
            resp = call_gateway(engines, state, {
                "max_tokens": args.max_tokens, "stream": False,
                "tools": tools, "messages": messages,
            }, log)
        except RuntimeError as e:
            log(f"└─ ✗ {e}")
            return 1

        blocks = resp.get("content", [])
        text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if text:
            final_text = text
        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        now = engines[state["i"]]

        if tool_uses:
            log(f"│  step {step:<2} [{now}] → " + ", ".join(f"{t['name']}({_arg_hint(t)})" for t in tool_uses))
        else:
            log(f"│  step {step:<2} [{now}] ✓ done")
            break

        messages.append({"role": "assistant", "content": blocks})
        results = []
        for tu in tool_uses:
            out = run_tool(tu["name"], tu.get("input", {}), cwd)
            results.append({"type": "tool_result", "tool_use_id": tu["id"], "content": out})
        messages.append({"role": "user", "content": results})
    else:
        final_text = final_text or "(hit max steps without a final answer)"

    log(f"└─ ✓ result via {engines[state['i']]}:")
    print(final_text)
    if args.out:
        pathlib.Path(args.out).write_text(final_text + "\n", encoding="utf-8")
    return 0


def _arg_hint(tool_use: dict) -> str:
    """Short one-value hint of a tool call for the progress line."""
    inp = tool_use.get("input", {})
    for k in ("cmd", "path", "pattern"):
        if k in inp:
            v = str(inp[k])
            return v if len(v) <= 40 else v[:40] + "…"
    return ""


if __name__ == "__main__":
    raise SystemExit(main())

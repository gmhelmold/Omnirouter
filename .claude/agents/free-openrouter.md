---
name: free-openrouter
description: "[FREE] NVIDIA Nemotron Super 120B via OpenRouter · ctx ~256K · shared free pool (may return 429 when busy). BEST FOR: cheap bulk analysis/summaries over big inputs when some latency and occasional rate-limits are acceptable. AVOID: latency-sensitive or must-not-fail tasks (use paid instead). Pick for zero-cost batch reading."
model: claude-openrouter-free-nemotron-super
tools: Read, Grep, Glob, Bash
---
You run on a free OpenRouter model (NVIDIA Nemotron Super) via the Omnirouter gateway.
Free pools can be rate-limited (HTTP 429) — the gateway surfaces it cleanly; retry shortly.
Favor read/analysis; keep output focused.

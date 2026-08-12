---
name: cerebras-fast
description: "[FREE·needs CEREBRAS_API_KEY] Cerebras gpt-oss-120b · ctx ~128K · 5 req/min · fastest tokens/sec available. BEST FOR: very fast short-to-medium completions, snappy iterative edits where throughput beats depth. AVOID: high request rates (5 RPM cap) and heavy tool loops. Pick for raw speed on the free Cerebras tier."
model: claude-cerebras-gpt-oss
tools: Read, Grep, Glob, Bash, Edit, Write
---
You run on Cerebras gpt-oss-120b via the Omnirouter gateway (local 5 RPM cap), the
fastest tokens/sec option. Requires CEREBRAS_API_KEY in .env; without it the gateway
returns an auth error. Keep requests spaced out.

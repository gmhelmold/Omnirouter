# Omnirouter — Ownership & Decision Rights

> Single source of truth for **who owns what** in this repository.
> If you change code, you change ownership. Read this before branching, before merging, and before delegating.

## 1. Identity

| | |
|---|---|
| **Product** | Omnirouter (aka `claude-gateway`) |
| **Purpose** | Multi-provider gateway for the Claude Code native model picker (`/model`, subagent `model:` frontmatter) |
| **Model** | Single-owner, agent-assisted. One human decision-maker; LLM agents execute, never decide. |
| **Relation to HuGR** | Standalone fork of `skill-001-fastapi-production/gateway/`. The skill is the upstream; Omnirouter is the shipped plugin. |

## 2. Ownership Model

The repo is small and mission-shaped, so we use **one accountable owner per area**, not a committee.

### 2.1 Owners

| Area | Owner | Backup | Scope / boundaries |
|---|---|---|---|
| **Product & roadmap** | Product owner | Tech lead | What providers/models ship, free-tier strategy, priorities |
| **Architecture** | Tech lead | Product owner | Cross-cutting: request flow, fallback semantics, discovery contract |
| **Routing & fallback** | Router owner | Architect | `gateway/router.py`, chain semantics, model→backend mapping |
| **Backends** | Backend owner | Router owner | `gateway/backends/*` — one backend = one external provider contract |
| **Translation layer** | Translator owner | Backend owner | `gateway/translators/*` — Anthropic↔{OpenAI,Gemini} fidelity |
| **Config** | Config owner | Architect | `gateway/config.py`, `gateway_config.yaml`, `.env*` |
| **API surface** | API owner | Router owner | `gateway/main.py` endpoints, health, discovery |
| **Quality gate** | QA owner | Tech lead | Tests, lint, mypy, CI; only owner that can veto a merge |

> One owner per area, but **anyone may open a PR**. Ownership is about final say, not about who may touch the code.

### 2.2 Defaults when no owner is named

If a file or concern is not listed above, the **tech lead is the default owner** for anything under `gateway/`, and the **product owner** for anything user-facing. Ambiguity resolves upward, never by whoever edited last.

## 3. Decision Rights

| Decision | Who decides | Notes |
|---|---|---|
| Add/remove a provider backend | Product owner + backend owner | Must ship with model map, discovery entry, health check, and tests |
| Change fallback-chain semantics | Architect + router owner | API contract change → bump minor version |
| Change model ID naming (`claude-*` prefix) | Architect | **Breaks** Claude Code discovery if wrong — see §5 |
| Change env-var schema | Config owner | Must update `.env.example` in the same commit |
| Change the discovery contract (`/v1/models`) | API owner + architect | Claude Code filters by `claude`/`anthropic` in the ID |
| Merge a PR with failing tests | QA owner only | Default: forbidden |
| Change Python version / deps | Architect | Must clear the quality gate |
| Anything not listed | Product owner | |

## 4. Code Ownership Map

```
gateway/
├── main.py                  # API owner   — app wiring, endpoints, lifespan
├── config.py                # Config owner — settings schema (env + YAML)
├── router.py                # Router owner — ModelRouter, fallback execution
├── discovery.py             # API owner   — /v1/models payload, cache, live bridge fetch
├── health.py                # API owner   — aggregate health model
├── backends/
│   ├── base.py              # Backend owner — BackendBase contract, SSEEvent
│   ├── openrouter.py        # Backend owner — raw Anthropic pass-through
│   ├── groq.py              # Backend owner — OpenAI-compat, rate-limited
│   ├── gemini.py            # Backend owner — Gemini API
│   ├── nim.py               # Backend owner — OpenAI-compat (NVIDIA)
│   ├── mistral.py           # Backend owner — OpenAI-compat, rate-limited
│   ├── cerebras.py          # Backend owner — OpenAI-compat, 5 RPM token bucket
│   └── opencode_bridge.py   # Backend owner — per-provider bridge instances
├── translators/
│   ├── tool_schema.py       # Translator owner — Anthropic↔OpenAI schema
│   ├── openai.py            # Translator owner — request build, SSE stream
│   └── gemini.py            # Translator owner — request build, SSE stream
tests/                       # QA owner   — must stay green per §6
gateway_config.yaml          # Config owner — model maps + fallback chains
.env.example                 # Config owner — every env var documented here
```

## 5. Invariant Constraints (do not break)

These are load-bearing. A change that violates one **requires the architect's sign-off and a test**:

1. **Model discovery**: every discoverable ID must contain `claude` or `anthropic` — Claude Code silently filters everything else out. Enforced by `build_discovery_payload` validation; keep it enforced.
2. **Model prefix ↔ backend**: `claude-{provider}-{key}` must resolve to exactly one backend via `model_prefix`. Two backends sharing a prefix is a routing bug.
3. **Fallback mapping**: `_map_model_for_backend` carries the model *key* across providers; it must never invent a key that a provider's model map doesn't define.
4. **Serialization**: SSE `data:` lines and JSON responses are orjson-encoded. A plain `dict` passed to `Response(content=...)` is a latent `TypeError`. (Fixed 2026-08 in `main.py`.)
5. **`/health` state**: backends live on `app.state.backends`. Access via the `app` closure — `globals().get("app")` is dead code (fixed 2026-08).
6. **API keys**: never committed. `.env` is gitignored; `.env.example` carries placeholders only.

## 6. Quality Gate

| Check | Command | Status required |
|---|---|---|
| Unit tests | `python -m pytest tests/ -q` | Green, except §7 known-issue exclusions |
| Import/boot smoke | boot the app, hit `/v1/models` + `/health` | 200/200 or documented 503 when unconfigured |
| Ruff | `ruff check gateway/ tests/` | Baseline is **not** clean (§7 debt) — must not regress |
| Mypy strict | `mypy gateway` | Baseline is **not** clean (§7 debt) — must not regress |

Rules:
- A PR that adds a **new** lint/type error is rejected, even though the baseline is dirty.
- Test-count regression (removing coverage) is rejected.
- Only the QA owner may green-light a merge against a known-issue exclusion.

## 7. Known Issues & Debt (tracked, not hidden)

| # | Area | Issue | Status |
|---|---|---|---|
| K1 | tests | `test_tool_result_conversion` fails (expects 4 msgs, gets 3) | Pre-existing, unfixed |
| K2 | tests | `test_openai_to_anthropic` reverse mapping (`object` vs `any`) | Pre-existing, unfixed |
| K3 | lint/mypy | Baseline non-compliant (mypy: 120 errors / 14 files) | Debt, regression-gated |
| K4 | config | `gateway/config.py` unused imports (`os`, `Any`, `field_validator`, `model_validator`) | Debt |
| K5 | CI | No CI pipeline yet — `ci.sh` lives upstream in skill-001 | Planned |

New findings go here with a number. Assign the highest free `K#`.

## 8. Change Management

1. **Branch** — one concern per branch, short-lived.
2. **PR** — small (see `techlead-repo-maintenance`: ~400 changed lines / 1-2 review hours budget).
3. **Review** — always an independent reviewer (cold-context agent for LLM-assisted PRs); the area owner approves.
4. **Quality gate** — §6 checks before merge, no exceptions.
5. **Commit style** — conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`).
6. **Tag** — semver; `0.x` while API surface moves.

## 9. Bus Factor & Escalation

- **Bus factor:** 1 (product owner). Mitigation: this file + `HANDOFF.md` + upstream skill-001 as the canonical mirror of intent.
- **Escalation path:** area owner → tech lead → product owner. Never silently "fix" across an owner boundary; open a discussion or PR instead.
- **Decision log:** record architecture decisions inline here or in a `docs/decisions/` series (ADR style) when they become non-obvious.

## 10. Relations & Upstream Sync

| Repository | Role | Sync policy |
|---|---|---|
| `skill-001-fastapi-production/gateway/` | Upstream | Pull fixes/features **from** upstream; push back only deliberate improvements (bug fixes, not provider experiments) |
| Omnirouter | Shipped plugin | Owns its own git history; upstream drift is reconciled per-feature, not wholesale |

> Last sync: **2026-08-12** — ported Cerebras backend + 3 `main.py` serialization/state fixes from skill-001.

# Omnirouter — HANDOFF for the Next Contributor

> Read this first. It tells you exactly where the repo is, what just changed, how to prove it still works, and what debt you inherit. It is a state dump, not a wishlist — every item below is current as of **2026-08-12**.

## 1. Repo state

- **Repo**: `/Users/gustavoschneiter/Documents/Omnirouter` (standalone git; 2 commits so far: initial + cleanup — *your commit is next*)
- **Working tree**: has **uncommitted** changes from this session (Cerebras port + `main.py` fixes + docs). **Commit them** — see §6.
- **Product**: `claude-gateway` v0.1.0 — local gateway so Claude Code's `/model` can use 7 providers.
- **Environment**: `.venv` exists (Python 3.14), `pip install -e ".[dev]"` already run.
- **Relationship**: fork of `skill-001-fastapi-production/gateway/`. Upstream is the design mirror; Omnirouter is the shipped plugin. Details in `OWNERSHIP.md §10`.

## 2. What this session shipped

1. **Ported Cerebras** from skill-001 → `gateway/backends/cerebras.py` (OpenAI-compatible, local token bucket `CEREBRAS_RPM=5`), plus:
   - `config.py`: `CerebrasModelMap` (`gpt-oss`→`gpt-oss-120b`, `glm`→`zai-glm-4.7`), `cerebras_api_key`, `cerebras_rpm`, `cerebras_model_map` property, fallback chain entry.
   - `main.py`: registered backend (lifespan + health fallback lists), top-level `orjson` import.
   - `discovery.py`: `claude-cerebras-*` entries.
   - `gateway_config.yaml`, `.env.example`: model map + key + RPM.
   - `tests/test_config.py`, `tests/test_discovery.py`: Cerebras coverage (5 new test cases + fixes).
2. **Ported 3 bug fixes from skill-001 into `main.py`**:
   - `/v1/models` — `Response(content=payload_dict)` → `orjson.dumps(payload)` (was a latent `TypeError`).
   - `/health` — `globals().get("app")` dead-code → `app.state.backends` via closure.
   - `/health` — `Response(content=health_response.to_dict())` → `orjson.dumps(...)`.
3. **Docs**: rewrote `README.md` (state-of-the-art, 7 providers), added `OWNERSHIP.md` (ownership map, invariants, quality gate) and this file.

> The skill-001 `base.py` import fix (`field`/`Any`) was **already present** in Omnirouter — no port needed there.

## 3. Prove it works (5 minutes)

```bash
cd /Users/gustavoschneiter/Documents/Omnirouter
source .venv/bin/activate

# 1) Unit tests — expect: 37 passed, 2 deselected
python -m pytest tests/ -q \
  --deselect tests/test_openai_translator.py::TestAnthropicToOpenAI::test_tool_result_conversion \
  --deselect tests/test_tool_schema.py::TestReverseMapping::test_openai_to_anthropic

# 2) Boot smoke — expect: /v1/models 200 with 29 models (2 cerebras), /health 503
python - <<'EOF'
from fastapi.testclient import TestClient
from gateway.main import app
with TestClient(app) as c:
    print(c.get("/v1/models").status_code, len(c.get("/v1/models").json()["data"]))
    print(c.get("/health").status_code)
EOF
```

**Do not** run the full `pytest` suite without `--deselect`: `addopts = "-x"` stops at the first failure and the two known failures come early.

## 4. Known failures (pre-existing, NOT introduced this session)

| Test | Symptom | Root cause (suspected) |
|---|---|---|
| `test_openai_translator.py::...::test_tool_result_conversion` | asserts 4 messages, gets 3 | Translator drops/merges the `user` turn after a `tool_result` |
| `test_tool_schema.py::...::test_openai_to_anthropic` | `object` vs `any` mismatch | Reverse schema mapping type mismatch |

Both are confirmed present on the clean tree (`git stash` check). They live in untouched files. **Fixing them is a good first contribution** — do not delete them.

## 5. Debt you inherit

- **ruff/mypy baseline is dirty** (mypy: ~120 errors / 14 files). Policy = **regression-gated**: new errors in touched files are rejected, but the baseline is not your problem to clean wholesale. `OWNERSHIP.md §7` tracks it.
- `gateway/config.py` has unused imports (`os`, `Any`, `field_validator`, `model_validator`) — an easy win.
- **No CI**. Upstream skill-001 has `ci.sh`; porting it here is on the roadmap.
- **No pre-commit config** despite `pre-commit` in dev deps.
- 2 failing tests (§4) and a `1.1s` sleep-based cache-expiry test (`test_cache_expiry`) that can flake on slow machines.

## 6. First actions for you

```bash
cd /Users/gustavoschneiter/Documents/Omnirouter
git status                     # 7 modified + cerebras.py untracked + 3 new doc files
git diff --stat               # review the port surface
git add -A && git commit -m "feat: port Cerebras backend + fix main.py serialization/health state"
```

Follow `OWNERSHIP.md §8` change management going forward (small branches, quality gate, conventional commits). Never commit `.env` — it is gitignored.

## 7. Gotchas (learned the hard way)

- **`orjson` for all JSON responses** — plain dicts passed to `Response(content=...)` raise at runtime.
- **`/health` reads `app.state.backends`** — the old `globals().get("app")` idiom is gone; don't reintroduce it.
- **Discovery IDs must contain `claude`/`anthropic`** — Claude Code silently drops everything else. `build_discovery_payload` validates this; keep the validation.
- **Fallback model keys carry across providers** — `claude-cerebras-gpt-oss` falls back to `claude-groq-gpt-oss`; the fallback provider must define that key in its model map or the hop is skipped.
- **`pytest addopts` has `-x`** — use `--deselect` for the known failures rather than editing config.
- **Inline `import orjson`** still exists inside `gateway/backends/base.py` (`sse_stream`) and `gateway/router.py` — the top-level import was only added to `main.py`. Moving the others to module top is a tidy-up candidate (they work as-is).
- **`health.py` timestamp** uses `__import__('time')` — works, but ugly; candidate for a normal import.

## 8. Candidate roadmap (unordered)

1. Fix the 2 known test failures (§4) — highest value, lowest risk.
2. Port `ci.sh` / set up GitHub Actions (test + ruff + mypy gates per §6 of OWNERSHIP).
3. Add `pre-commit` hooks matching the ruff/mypy config.
4. Tidy: unused imports in `config.py`, inline orjson imports, `health.py` timestamp import.
5. Add a backend test for the Cerebras token bucket (currently exercised only via config/discovery tests).
6. Reconcile upstream drift: re-diff against `skill-001-fastapi-production/gateway/` before each release.

## 9. Where the docs live

- `README.md` — user-facing, state-of-the-art.
- `OWNERSHIP.md` — who owns what, decision rights, invariants, quality gate, known-issue ledger.
- `HANDOFF.md` — this file; refresh it whenever a session ends with state to preserve.

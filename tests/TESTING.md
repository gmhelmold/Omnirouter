# SKILL-001 Testing Architecture

> This document describes every test layer, what it proves, and when to run it.

## Test Pyramid

```
                    ┌─────────────────────┐
                    │  Behavior Scenarios  │  12 domain archetypes
                    │  (real HTTP flows)   │  signup→login→CRUD→verify
                    ├─────────────────────┤
                    │  E2E Hardcore        │  12 scenarios (SQLite)
                    │  E2E PostgreSQL      │   8 scenarios (Docker)
                    ├─────────────────────┤
                    │  Cross-Composition   │  200+ tool combos boot
                    │  Soak Test           │  16K req / 0 errors / 5 min
                    ├─────────────────────┤
                    │  Behavior Tests      │  45 files, ~10 tests each
                    │  (ASGI transport)    │  boot + endpoint + quality
                    ├─────────────────────┤
                    │  Property Tests      │  8 properties × 123 tools
                    │  (universal laws)    │  idempotency, dry_run, etc.
                    ├─────────────────────┤
                    │  Boot Tests          │  100 tools boot individually
                    │  Boot Chains         │  100 tools forward + reverse
                    ├─────────────────────┤
                    │  Structural Tests    │  100 files, ~22 tests each
                    │  (per-tool unit)     │  success, no_op, files, parse
                    └─────────────────────┘
```

## Layer 1: Structural Tests (per-tool)

**Location:** `adapt/extend/{category}/test_add_{tool}.py`
**Count:** 49 files, ~1,100 tests total
**What they prove:** The tool GENERATES correct files.

Each tool has a dedicated test file with ≥20 tests:

| Test | What it verifies |
|------|-----------------|
| `test_success_status` | Tool returns `status="success"` on fresh project |
| `test_idempotent` | Second run returns `status="no_op"`, zero file changes |
| `test_dry_run` | `dry_run=True` writes zero bytes (before/after snapshot match) |
| `test_files_created_count` | Expected number of files created, all exist on disk |
| `test_files_modified_count` | Expected number of files modified, all exist on disk |
| `test_all_py_parse` | Every generated `.py` passes `ast.parse` |
| `test_no_function_over_50_loc` | No generated function exceeds 50 LOC (AST walk) |
| `test_config_fields_patched` | Settings fields exist with 4-space indent |
| `test_models_init_patched` | Model registered in `app/models/__init__.py` |
| `test_routes_registered` | Router registered in `app/routes/__init__.py` |
| `test_requirements_patched` | Package added to `requirements.txt` |
| `test_execution_time_recorded` | `execution_time_ms > 0` |
| `test_next_steps_present` | `next_steps` non-empty with relevant guidance |
| `test_idempotent_project_still_parses` | Double-run leaves all `.py` parseable |
| `test_{domain_specific}_*` | Tool-specific checks (varies per tool) |

**Run:** `PYTHONPATH=. pytest adapt/extend/{category}/test_add_{tool}.py -v`

## Layer 2: Behavior Tests (per-tool, runtime)

**Location:** `adapt/extend/{category}/test_add_{tool}_behavior.py`
**Count:** 15 files, ~150 tests total
**What they prove:** The GENERATED CODE boots and responds correctly.

Each behavior test:
1. Generates a fixture project via `create_fixture_project`
2. Applies the tool
3. Patches `app/core/db.py` to SQLite+aiosqlite (no Docker needed)
4. Patches `app/middleware/idempotency.py` to pass-through stub (no Redis)
5. Loads the app via `importlib.import_module("app.main").app`
6. Makes real HTTP requests via `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))`

| Test | What it verifies |
|------|-----------------|
| `test_app_boots` | `GET /healthz` returns 200 |
| `test_{endpoint}_exists` | Domain endpoint responds (not 404) |
| `test_lazy_imports` | Optional SDK not at module top-level (AST walk) |
| `test_max_function_loc` | All generated functions ≤50 LOC |
| `test_config_indent` | Settings fields have 4-space indent |
| `test_ruff_clean` | `ruff check --select F401` passes |
| `test_module_imports` | Domain modules import without crash |

**Run:** `PYTHONPATH=. pytest adapt/extend/{category}/test_add_{tool}_behavior.py -v`

## Layer 3: Boot Tests

**Location:** `tests/test_boot.py`, `tests/test_boot_chains.py`
**What they prove:** Every tool produces a project that BOOTS.

### Boot Individual (100 tools)
For each tool: generate project → apply tool → `from app.main import app` in subprocess → verify "BOOT_OK" in stdout.

### Boot Chains (5 chains)
- CRUD chain (7 tools, forward)
- Auth chain (6 tools, forward)
- RT+Infra chain (forward)
- Full 100 tools (forward order)
- Full 100 tools (reverse order)

**Run:** `PYTHONPATH=. python tests/test_boot.py`

## Layer 4: Property Tests

**Location:** `tests/property_tests.py`
**Count:** 8 properties × 123 tools = 984 checks
**What they prove:** Universal invariants hold for EVERY tool.

| Property | What it checks |
|----------|---------------|
| `IDEMPOTENCY` | Second run = `no_op` with zero file changes |
| `DRY_RUN_PURITY` | `dry_run=True` produces byte-identical filesystem |
| `NO_CRASH_ON_ANY_PATH` | Tool handles arbitrary `/tmp/` path without crash |
| `PARSE_CORRECTNESS` | All generated `.py` files pass `ast.parse` |
| `TOOLRESULT_CONTRACT` | Status in `{success, no_op, error}`, fields are lists, timing ≥ 0 |
| `RUFF_CRITICAL_CLEAN` | Generated `app/` passes `ruff check --select F` |
| `LAZY_SDK_IMPORTS` | Optional SDKs never at module top-level in main-path files |
| `STANDALONE_MODE` | Tool handles empty project dir gracefully (error or auto-scaffold) |

**Run:** `PYTHONPATH=. python tests/property_tests.py`

## Layer 5: E2E Tests (real HTTP flows)

### E2E Hardcore (SQLite)
**Location:** `tests/test_e2e_hardcore.py`
**Count:** 12 scenarios
**What it proves:** Generated code handles real business flows.

Generates a 4-model e-commerce project, applies 23 tools, boots with SQLite, fires real HTTP requests:
- Auth full cycle (signup → login → protected route → invalid token → wrong password)
- CRUD lifecycle (Create → Read → Update → List → Delete → verify 404 after delete)
- Health endpoints
- Validation errors (missing fields, wrong types)
- Middleware headers (security headers present)
- OpenAPI schema (valid JSON, correct paths)
- Bulk create (5 items in single request)
- Search (create items → search → verify results)
- User isolation (User A cannot see User B's data)
- Pagination (cursor-based, verify order)
- Data integrity (FK constraints, unique violations)
- Concurrent requests (10 parallel, no race conditions)

### E2E PostgreSQL
**Location:** `tests/test_e2e_postgres.py`
**Count:** 8 scenarios (requires Docker)
**What it proves:** PostgreSQL-specific features work.

- Tenant-aware auth
- PostgreSQL full-text search (@@  operator)
- RBAC schema creation
- MFA enrollment (TOTP)
- OAuth2 authorize endpoint
- Tenant isolation (cross-tenant denied)
- Bulk create with real persistence
- Audit log entries persisted

**Run:** `PYTHONPATH=. python tests/test_e2e_postgres.py`

## Layer 6: Behavior Scenarios (domain archetypes)

**Location:** `tests/test_behavior_scenarios.py`
**Count:** 12 domain archetypes
**What it proves:** Tools compose correctly in realistic business domains.

| # | Domain | Tools used | Key assertions |
|---|--------|-----------|----------------|
| 1 | E-commerce | tenancy + search + audit | Owner isolation, search returns results |
| 2 | SaaS B2B | tenancy + rbac + api_keys | Role assignment, API key CRUD |
| 3 | Healthcare (HIPAA) | audit + tenancy + rbac | Every read logged, tenant isolation |
| 4 | Fintech | audit + rbac + circuit_breaker | Audit trail, permission enforcement |
| 5 | Blog/CMS | search + pagination + export | Pagination order, export format |
| 6 | Analytics | bulk + batch + outbox | Bulk ingest, event dispatch |
| 7 | Trust & Safety | feature_flags + audit + rbac | Flag gating, moderation audit |
| 8 | Real-time Chat | websocket_chat + tenancy | WebSocket endpoint exists |
| 9 | Background Jobs | arq_worker | Worker module imports, config present |
| 10 | Stripe Payments | stripe_checkout | Checkout endpoint, webhook route |
| 11 | Email | email_templates | Template files, render module |
| 12 | Admin Panel | sqladmin | Admin routes, auth backend |

**Run:** `PYTHONPATH=. python tests/test_behavior_scenarios.py` (requires PostgreSQL)

## Layer 7: Cross-Composition

**Location:** `tests/test_cross_composition.py`
**What it proves:** ANY combination of tools boots cleanly.

- Full stack (100 tools, canonical order) → boots
- Config-patcher pairs (all pairs of tools that patch config.py) → all boot
- Main.py-patcher pairs (all pairs that patch main.py) → all boot
- Random 5-tool subsets (20 draws, seed 42) → all boot
- Full stack reversed → boots

**Run:** `PYTHONPATH=. python tests/test_cross_composition.py`

## Layer 8: Soak Test

**Location:** `tests/test_soak.py`
**What it proves:** Generated project handles sustained load without leaks.

- 300s duration, 10 concurrent coroutines
- ASGI in-process transport (no Docker needed)
- Hits `/healthz` through the full middleware stack
- SLOs: p50 < 200ms, p95 < 500ms, p99 < 1000ms, errors < 1%
- Memory growth monitoring

**Run:** `PYTHONPATH=. python tests/test_soak.py --duration 300`

## Layer 9: Mutation Testing

**Location:** `tests/mutation_runner.py`
**What it proves:** Tests actually catch behavioral changes.

Custom AST-based mutation tester (no external deps). Mutates operators/literals
one at a time, runs tests, reports kill rate.

Operators: `== ↔ !=`, `< ↔ <=`, `> ↔ >=`, `+ ↔ -`, `and ↔ or`, `True ↔ False`, `not X ↔ X`

**Run:** `PYTHONPATH=. python tests/mutation_runner.py --target adapt/extend/.../add_X.py --tests adapt/extend/.../test_add_X.py`

## Layer 10: CI Runner

**Location:** `ci.sh`
**What it proves:** Everything passes together.

10 suites in sequence:
1. Unit tests (adapt/) — all structural + behavior tests
2. Boot individual (100 tools)
3. Property tests (123 × 8)
4. SQLite E2E (12 scenarios)
5. Red team (25 attacks)
6. Determinism (4 tests)
7. FinHealth benchmark (100/100)
8. MCP discovery (175 tools)
9. PostgreSQL E2E (8 scenarios) — requires Docker
10. Behavior scenarios (12 domains) — requires Docker

**Run:** `./ci.sh` or `./ci.sh --no-pg`

## How to add tests for a new tool

1. **Structural test** (`test_add_{tool}.py`): ≥20 tests, cover all CCs from briefing template
2. **Behavior test** (`test_add_{tool}_behavior.py`): boot via ASGI, test endpoints, verify generated code quality
3. **Wire into infrastructure**: add to `test_boot.py`, `test_boot_chains.py`, `test_stress.py`, `property_tests.py`
4. **Update CI labels**: counts in `ci.sh`
5. **Add behavior scenario** (if applicable): domain-specific flow in `test_behavior_scenarios.py`

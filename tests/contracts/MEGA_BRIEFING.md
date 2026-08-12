# MEGA BRIEFING — INVIOLABLE CONTRACT FOR TOOL BUILDERS

> This is THE LAW. Every agent building tools for SKILL-001 MUST satisfy
> every item in this document. No exceptions. No shortcuts. No lies.

---

## PHASE 1: READ BEFORE ANYTHING

You MUST read these files IN ORDER before writing a single line of code:

1. This briefing (you're reading it)
2. The reference tool for your category
3. The reference tool's test file
4. `adapt/contracts/__init__.py` — ToolInput, ToolResult
5. `adapt/contracts/prerequisites.py` — ensure_prerequisites, Prereq enum
6. `adapt/contracts/migration_helper.py` — find_migration_head (if you create migrations)

---

## PHASE 2: KNOWN BUGS — DO NOT REPEAT

These bugs have been found in EVERY previous batch of agent-built tools.
If you repeat ANY of them, your delivery is REJECTED.

| # | Bug | What went wrong | How to avoid |
|---|-----|----------------|--------------|
| 1 | `_elapsed_ms` missing | `validate_project_dir` error return lacked timing | Put `execution_time_ms=_elapsed_ms(start)` on EVERY `return ToolResult(...)` — count ALL of them |
| 2 | `ast.parse` absent | Tool generated 10 .py files with zero syntax validation | Add `import ast` + validation loop BEFORE success return |
| 3 | Dead imports | Generated code had `from X import Y` never used | Mental ruff F401 on every template. Every import must be used. |
| 4 | Import without register | `_patch_main` added `from X import router` but forgot `app.include_router(router)` | Always add BOTH import AND registration |
| 5 | Top-level SDK import | Generated code had `import stripe` at module level — crashes boot | ALL optional SDKs inside function bodies. Test with import check. |
| 6 | Invented stdlib | `from enum import str as str_enum` — doesn't exist in Python 3.14 | Only use imports you KNOW exist |
| 7 | Deprecated API | `app.add_event_handler("startup", ...)` removed in modern FastAPI | Use `@asynccontextmanager` lifespan or inject into existing lifespan |
| 8 | Fingerprint mismatch | Idempotency checked `"MyClass"` but template didn't contain it | Verify the EXACT string you check EXISTS in your generated code |
| 9 | Relative paths | `scaffold_prerequisites` returns absolute paths, tool assumed relative | Use `Path(path_str)` and check `.is_file()` before `.read_text()` |
| 10 | Self-audit lies | Agent reported "PASS: _elapsed_ms on all 6 returns" but only 1 had it | Actually COUNT your returns. Don't guess. |
| 11 | PII in Public schema | Agent claimed "PII omitted" but field was in the class | Verify via AST walk on class body, not string search (docstrings match too) |

---

## PHASE 3: MANDATORY PATTERNS (tool source code)

Every tool file MUST contain ALL of these. Missing ANY = REJECTED.

```python
# 1. Future annotations (FIRST import)
from __future__ import annotations

# 2. ast import (for validation)
import ast

# 3. MCP_TOOL dict (module level, BEFORE the function)
MCP_TOOL = {
    "name": "fastapi_add_X",
    "description": "...",
    "tags": ["extend", "category"],
    "entry": "add_X",  # MUST match function name EXACTLY
}

# 4. Entry function signature
def add_X(inp: ToolInput) -> ToolResult:
    start = time.monotonic()

    # 5. validate_project_dir WITH timing on error
    err = validate_project_dir(inp.project_dir)
    if err:
        return ToolResult(status="error", error=err,
                          execution_time_ms=_elapsed_ms(start))

    # 6. ensure_prerequisites
    from adapt.contracts.prerequisites import ensure_prerequisites, Prereq
    prereq_errors, scaffolded = ensure_prerequisites(
        inp.project_dir, Prereq.CONFIG_SETTINGS, ...,
        auto_scaffold=not inp.dry_run,
    )
    if prereq_errors:
        return ToolResult(status="error", ...,
                          execution_time_ms=_elapsed_ms(start))

    # 7. Idempotency guard
    if fingerprint_file.exists() and "FINGERPRINT" in fingerprint_file.read_text():
        return ToolResult(status="no_op", ...,
                          execution_time_ms=_elapsed_ms(start))

    # 8. dry_run guard (BEFORE any writes)
    if inp.dry_run:
        return ToolResult(status="success", ...,
                          execution_time_ms=_elapsed_ms(start))

    # 9. File generation (textwrap.dedent for ALL templates)
    # ... write files ...

    # 10. ast.parse validation loop (BEFORE success return)
    for path_str in files_created:
        p = Path(path_str)
        if p.suffix == ".py" and p.is_file():
            try:
                ast.parse(p.read_text())
            except SyntaxError as exc:
                return ToolResult(status="error",
                    error=f"Generated file has syntax error: {p}: {exc}",
                    execution_time_ms=_elapsed_ms(start))

    # 11. Success return WITH timing
    return ToolResult(status="success", ...,
                      execution_time_ms=_elapsed_ms(start))

# 12. _elapsed_ms utility
def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
```

---

## PHASE 4: COMPLETENESS CRITERIA (CC)

Your structural test file MUST cover ALL of these:

| CC | Test name pattern | Assertion |
|----|-------------------|-----------|
| CC-01 | `test_success_status` | `result.status == "success"` |
| CC-02 | `test_idempotent` | `r2.status == "no_op"` and `not r2.files_created` and `not r2.files_modified` |
| CC-03 | `test_dry_run` | Before/after file snapshots match exactly |
| CC-04 | `test_files_created_count` | `len(result.files_created) >= N` and all paths exist |
| CC-05 | `test_files_modified_count` | `len(result.files_modified) >= N` and all paths exist |
| CC-06 | `test_all_py_parse` | `ast.parse` on every `.py` in project |
| CC-07 | `test_no_function_over_50_loc` | AST walk: `end_lineno - lineno + 1 <= 50` |
| CC-08 | `test_config_fields_patched` | Fields in config.py with 4-space indent |
| CC-09 | `test_models_init_patched` | Model name in `models/__init__.py` (if model created) |
| CC-10 | `test_routes_registered` | Router in `routes/__init__.py` (if routes created) |
| CC-11+ | `test_{domain}_*` | Tool-specific content verification (≥5 domain tests) |
| CC-N-1 | `test_execution_time_recorded` | `result.execution_time_ms > 0` |
| CC-N | `test_next_steps_present` | `len(result.next_steps) > 0` and keyword |
| CC-LAST | `test_idempotent_project_still_parses` | Run twice, then ast.parse all |

Minimum: **22 structural tests**.

---

## PHASE 5: QUALITY STANDARDS (QS)

| QS | Standard | How to verify |
|----|----------|---------------|
| QS-01 | Every public function in generated code has docstring | AST: `ast.get_docstring(node)` |
| QS-02 | No hardcoded secrets/passwords/API keys | Grep templates for `password=`, `secret=`, `key=` with literal values |
| QS-03 | No `# TODO`, `# FIXME`, `# HACK` | Grep templates |
| QS-04 | Optional SDKs ALWAYS inside function bodies | AST walk on tree.body for imports |
| QS-05 | `textwrap.dedent` on ALL templates | Visual inspection |
| QS-06 | `_elapsed_ms(start)` on EVERY return path | Count ALL `return ToolResult(` and verify EACH block has `_elapsed_ms` |
| QS-07 | `ast.parse` validation before success return | Code inspection |
| QS-08 | No dead imports in generated code | ruff F401 |
| QS-09 | Secrets NEVER in logs/exceptions/responses | Template inspection |
| QS-10 | Generated code uses `logging.getLogger(__name__)` | Template inspection |
| QS-11 | Pydantic models use `ConfigDict(from_attributes=True)` for ORM | Template inspection |
| QS-12 | HTTP errors use `{"detail": "message"}` format | Template inspection |

---

## PHASE 6: DEFINITION OF DONE (DoD)

### Tool file (.py)
- [ ] Exists at correct path under `adapt/extend/{category}/`
- [ ] `from __future__ import annotations` as first import
- [ ] `import ast` present
- [ ] `MCP_TOOL` dict with 4 keys, `entry` matches function name
- [ ] Entry function: `def add_X(inp: ToolInput) -> ToolResult`
- [ ] `validate_project_dir` with `_elapsed_ms` on error return
- [ ] `ensure_prerequisites()` with correct `Prereq.*` values
- [ ] Idempotency guard → `no_op` (fingerprint EXISTS in generated code)
- [ ] `dry_run` returns BEFORE any `write_text()`
- [ ] `textwrap.dedent` on ALL templates
- [ ] `ast.parse` validation loop before success return
- [ ] `_elapsed_ms(start)` on EVERY `return ToolResult(...)` path
- [ ] Every generated function ≤ 50 LOC
- [ ] Optional SDK imports lazy in generated code
- [ ] No dead imports in generated code

### Structural test file
- [ ] ≥ 22 test functions
- [ ] Covers CC-01 through CC-LAST
- [ ] Has `__main__` standalone runner
- [ ] ALL tests pass on `pytest -q`

### Behavior test file
- [ ] ≥ 8 test functions
- [ ] Patches `db.py` to SQLite+aiosqlite
- [ ] Patches `idempotency.py` to pass-through stub
- [ ] Removes `REDIS_URL` from env
- [ ] Loads app via `importlib` + `httpx.ASGITransport`
- [ ] Tests `GET /healthz` → 200
- [ ] Tests ≥ 1 domain endpoint
- [ ] Verifies lazy SDK imports via AST walk
- [ ] Verifies all functions ≤ 50 LOC
- [ ] Verifies config fields 4-space indent
- [ ] Verifies ruff F401 clean
- [ ] ALL tests pass on `pytest -q`

---

## PHASE 7: INVARIANTS (INV)

| INV | Rule | Enforcement |
|-----|------|-------------|
| INV-01 | Tool is ALWAYS idempotent | Fingerprint check → `no_op` |
| INV-02 | `dry_run=True` NEVER writes to disk | Returns before any `write_text()` |
| INV-03 | Every generated `.py` passes `ast.parse` | Validation loop in tool |
| INV-04 | `app.main` boots without optional SDKs | Lazy imports verified by behavior test |
| INV-05 | No generated function exceeds 50 LOC | AST walk in tests |
| INV-06 | `execution_time_ms` positive on EVERY return | `_elapsed_ms(start)` on all paths |
| INV-07 | Existing files NEVER overwritten without guard | Idempotency check first |
| INV-08 | Config fields inside `class Settings` body | 4-space indent verified |
| INV-09 | Alembic migrations chain to current head | `find_migration_head()` called |
| INV-10 | `MCP_TOOL["entry"]` matches function name | Verified programmatically |

---

## PHASE 8: SELF-AUDIT (before reporting)

You MUST run this EXACT checklist and report PASS/FAIL for EACH item.
If ANY item fails, FIX IT before reporting. Do NOT report PASS on failed items.

```
Self-audit:
  import ast present: PASS/FAIL
  MCP_TOOL entry matches function: PASS/FAIL
  ensure_prerequisites called: PASS/FAIL
  validate_project_dir error has _elapsed_ms: PASS/FAIL
  Idempotency fingerprint exists in generated code: PASS/FAIL
  dry_run returns before writes: PASS/FAIL
  _elapsed_ms on ALL N return paths: PASS/FAIL (counted: N/N)
  ast.parse validation loop present: PASS/FAIL
  textwrap.dedent on all templates: PASS/FAIL
  Max generated function LOC: N (PASS if ≤50)
  Optional SDK lazy imports verified: PASS/FAIL
  ruff F401 on generated code: PASS/FAIL
  Config fields 4-space indent: PASS/FAIL
  Structural tests: N/N passed
  Behavior tests: N/N passed
  ALL tests passed: PASS/FAIL
```

---

## PHASE 9: VERIFICATION COMMAND

After completing everything, run this to validate your delivery:

```bash
PYTHONPATH=. python3 -c "
from tests.contracts.delivery_contract_v3 import verify_tool
result = verify_tool(
    tool_module='adapt.extend.{category}.add_X',
    tool_function='add_X',
    test_file='adapt/extend/{category}/test_add_X.py',
    behavior_file='adapt/extend/{category}/test_add_X_behavior.py',
    sdk_names=['sdk1', 'sdk2'],
    config_fields=['FIELD_1', 'FIELD_2'],
)
print('VERIFIED:', result.tool_name)
print('Patterns:', len(result.patterns), 'all PASS')
print('Quality: max_loc=', result.quality.max_function_loc)
print('Returns:', result.return_paths_with_elapsed_ms, '/', result.total_return_paths)
"
```

If this command raises a `ValueError`, your delivery has a bug. FIX IT.

---

## PHASE 10: REPORT FORMAT

```
TOOL-NNN add_X READY
Files: tool.py (N LOC), test.py (N tests), behavior.py (N tests)
Self-audit: ALL PASS (N/N items)
Verification: verify_tool() PASSED
```

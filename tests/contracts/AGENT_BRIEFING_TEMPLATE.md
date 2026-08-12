# Agent Briefing Template — INVIOLABLE

Every agent building a tool for SKILL-001 MUST follow this checklist.
The Tech Lead (Opus) will reject deliveries that violate ANY item.

## Known bugs from previous agent deliveries — DO NOT REPEAT

1. **`_elapsed_ms` missing on `validate_project_dir` error return.** EVERY
   tool has `err = validate_project_dir(...)` as its first check. The return
   MUST include `execution_time_ms=_elapsed_ms(start)`. Previous agents forgot
   this on ALL 6 tools. Fixed post-delivery.

2. **`ast.parse` validation missing.** 2 of 6 tools generated 9-10 Python files
   without ANY syntax validation. EVERY tool MUST have this loop before the
   success return:
   ```python
   for path_str in files_created:
       p = Path(path_str)
       if p.suffix == ".py" and p.is_file():
           try:
               ast.parse(p.read_text())
           except SyntaxError as exc:
               return ToolResult(
                   status="error",
                   error=f"Generated file has syntax error: {p}: {exc}",
                   execution_time_ms=_elapsed_ms(start),
               )
   ```

3. **Dead imports in generated code.** One tool had `from app.crud.X import Y`
   that was never used. Run a mental ruff F401 check on every template.

4. **`_patch_main` importing but not registering.** One tool wrote
   `from app.X import router` but forgot `app.include_router(router)`.

5. **Top-level SDK imports in generated code.** Optional SDKs (boto3, celery,
   stripe, firebase_admin, psutil, apscheduler, temporal) MUST be imported
   inside function bodies, NOT at module top level. `app.main` must boot
   without these installed.

6. **`from enum import str as str_enum`** — does not exist in Python 3.14.
   Don't invent stdlib imports.

7. **Modern FastAPI has no `app.add_event_handler`.** Use `@asynccontextmanager`
   lifespan or `app.on_event("startup")` if lifespan isn't available.

8. **`scaffold_prerequisites` returns absolute paths.** Don't assume relative.
   The `files_created` list may contain both absolute (from scaffold) and
   absolute (from your writes). Always use `Path(path_str)` and check
   `.is_file()` before `.read_text()`.

9. **Idempotency fingerprint must exist in generated code.** If you check
   `"MyClass" in file.read_text()` but your template doesn't contain
   `"MyClass"`, the guard never triggers.

## Self-audit checklist — run BEFORE reporting

Before you report "READY", verify EACH of these yourself:

- [ ] `import ast` is in the imports
- [ ] `MCP_TOOL` dict has `name`, `description`, `tags`, `entry`
- [ ] `MCP_TOOL["entry"]` matches the actual function name
- [ ] `ensure_prerequisites()` is called with appropriate `Prereq.*` values
- [ ] `validate_project_dir` error return has `execution_time_ms=_elapsed_ms(start)`
- [ ] Idempotency guard checks a string that EXISTS in your generated code
- [ ] `dry_run` branch returns BEFORE any `write_text()` call
- [ ] `_elapsed_ms(start)` is on EVERY `return ToolResult(...)` (count them ALL)
- [ ] ast.parse validation loop exists before the success return
- [ ] Every generated function body is ≤ 50 LOC (count the lines in your templates)
- [ ] Optional SDK imports are lazy (inside function bodies in the generated code)
- [ ] No dead imports in generated code (every `import` is used)
- [ ] `textwrap.dedent` on all template strings
- [ ] Test file has ≥ 20 tests
- [ ] Behavior test file boots the app via httpx ASGITransport and tests real endpoints
- [ ] ALL tests pass when you run `pytest` yourself
- [ ] Delivery contract JSON validates against `tests/contracts/delivery_contract.py`

## MANDATORY: Generated code quality verification

After writing the tool, you MUST generate a real project, apply your tool,
and then programmatically verify the QUALITY of the generated code. This is
NOT optional. Write these checks into your behavior test file.

```python
# IN YOUR BEHAVIOR TEST — verify generated code quality:

# 1. PII-safe schemas: any *Public schema must NOT contain sensitive fields
#    as ACTUAL FIELDS (docstring mentions are OK)
for cls in ast.walk(tree):
    if isinstance(cls, ast.ClassDef) and cls.name.endswith("Public"):
        # Check class BODY for PII field assignments (not docstring)
        for node in cls.body:
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assert node.target.id not in SENSITIVE_FIELDS

# 2. Lazy SDK imports: verify optional SDK is NOT in module-level imports
#    of ANY generated file under app/ (except workers/ and admin/)
for py in (project / "app").rglob("*.py"):
    tree = ast.parse(py.read_text())
    for node in tree.body:  # ONLY top-level
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert SDK_NAME not in alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert SDK_NAME not in node.module

# 3. Max function LOC: verify ALL functions in generated app/ are ≤ 50 LOC
for py in (project / "app").rglob("*.py"):
    tree = ast.parse(py.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.end_lineno:
                assert node.end_lineno - node.lineno + 1 <= 50

# 4. No dead imports: run ruff --select F401 on generated app/
result = subprocess.run(["python", "-m", "ruff", "check", "--select", "F401",
                         str(project / "app")], capture_output=True)
assert result.returncode == 0 or "F401" not in result.stdout

# 5. Webhook sig before DB (if applicable): verify construct_event appears
#    BEFORE any session/commit reference in webhook handler

# 6. Config fields inside Settings class: verify 4-space indent
for line in config_content.splitlines():
    if FIELD_NAME in line and ":" in line and "=" in line:
        assert line.startswith("    "), f"field not inside class body"
```

If ANY of these checks fail, FIX your tool before reporting. Do NOT report
PASS with known quality failures.

## Completeness Criteria (CC) — EVERY delivery must satisfy ALL

| CC | Criterion | How to verify |
|----|-----------|---------------|
| CC-01 | Tool returns `status="success"` on fresh project | Test: `assert result.status == "success"` |
| CC-02 | Second run returns `status="no_op"` with empty files | Test: `assert r2.status == "no_op"` and `not r2.files_created` |
| CC-03 | `dry_run=True` writes zero bytes | Test: snapshot before == snapshot after |
| CC-04 | `files_created` count ≥ expected minimum | Test: `assert len(result.files_created) >= N` |
| CC-05 | `files_modified` count ≥ expected minimum | Test: `assert len(result.files_modified) >= N` |
| CC-06 | Every generated `.py` AST-parses clean | Test: `ast.parse(f.read_text())` for all `.py` |
| CC-07 | No generated function exceeds 50 LOC | Test: AST walk `end_lineno - lineno + 1 <= 50` |
| CC-08 | Config fields patched inside Settings class body (4-space indent) | Test: field in content AND line starts with `"    "` |
| CC-09 | Models registered in `app/models/__init__.py` (if model created) | Test: `"ModelName" in content` |
| CC-10 | Routes registered in `app/routes/__init__.py` (if routes created) | Test: router name in content |
| CC-11 | Domain-specific files created with expected content | Test: file exists + key identifiers present |
| CC-12 | `requirements.txt` patched with correct package | Test: package name in content |
| CC-13 | `execution_time_ms` is positive integer | Test: `result.execution_time_ms > 0` |
| CC-14 | `next_steps` is non-empty and mentions key action | Test: len > 0 and keyword present |
| CC-15 | Double-run leaves project parseable | Test: run twice, then ast.parse all |
| CC-16 | App boots via httpx ASGITransport after tool applied | Behavior test: `GET /healthz` → 200 |
| CC-17 | Optional SDK not imported at module top level in generated code | Behavior test: grep for top-level imports |
| CC-18 | Alembic migration chains to current head (if migration created) | Test: `find_migration_head` called |

## Quality Standards (QS) — non-negotiable

| QS | Standard |
|----|----------|
| QS-01 | Every public function in generated code has a docstring |
| QS-02 | No hardcoded secrets, passwords, or API keys in generated code |
| QS-03 | No `# TODO`, `# FIXME`, `# HACK` in generated code |
| QS-04 | Optional SDK imports ALWAYS inside function bodies (never module-level) |
| QS-05 | `textwrap.dedent` on ALL template strings in the tool source |
| QS-06 | `_elapsed_ms(start)` on EVERY `return ToolResult(...)` path — count ALL returns |
| QS-07 | `ast.parse` validation loop before success return |
| QS-08 | No dead imports in generated code (every import used) |
| QS-09 | Secrets NEVER in logs, exception messages, or response bodies |
| QS-10 | Generated code uses `logging.getLogger(__name__)` — never `print()` |
| QS-11 | All Pydantic models use `model_config = ConfigDict(from_attributes=True)` where ORM is involved |
| QS-12 | HTTP error responses use consistent JSON format: `{"detail": "message"}` |

## Definition of Done (DoD) — granular, mechanically verifiable

A tool is DONE when ALL of these are TRUE:

### Code (tool .py)
- [ ] File exists at correct path under `adapt/extend/`
- [ ] `MCP_TOOL` dict at module level with 4 required keys
- [ ] `from __future__ import annotations` as first import
- [ ] `import ast` present (for validation)
- [ ] Entry function accepts `ToolInput`, returns `ToolResult`
- [ ] `validate_project_dir()` is the first check with `_elapsed_ms` on error
- [ ] `ensure_prerequisites()` called with correct `Prereq.*` values
- [ ] Idempotency guard returns `no_op` when fingerprint detected
- [ ] `dry_run` guard returns before any file write
- [ ] Every file write uses `textwrap.dedent` templates
- [ ] ast.parse validation loop before success return
- [ ] `_elapsed_ms(start)` on ALL return paths (success, no_op, dry_run, error, prereq_error)
- [ ] Every generated function ≤ 50 LOC
- [ ] Optional SDK imports lazy in generated code

### Tests (test .py — structural)
- [ ] File exists at correct path
- [ ] ≥ 20 test functions
- [ ] Covers: success, idempotent, dry_run, files_created, files_modified
- [ ] Covers: all_py_parse, no_function_over_50_loc
- [ ] Covers: config_fields_patched, requirements_patched
- [ ] Covers: domain-specific file existence and content checks
- [ ] Covers: execution_time_recorded, next_steps_present
- [ ] Covers: idempotent_project_still_parses
- [ ] Has standalone `__main__` runner
- [ ] ALL tests pass on `pytest -q`

### Tests (test _behavior.py — runtime)
- [ ] File exists at correct path
- [ ] Patches `app/core/db.py` to SQLite+aiosqlite
- [ ] Patches `app/middleware/idempotency.py` to pass-through stub
- [ ] Removes `REDIS_URL` from env
- [ ] Loads app via `importlib.import_module("app.main").app`
- [ ] Uses `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))`
- [ ] Tests `GET /healthz` → 200
- [ ] Tests at least 1 domain-specific endpoint
- [ ] Verifies generated modules import without crash
- [ ] Verifies config fields present
- [ ] ALL behavior tests pass on `pytest -q`

## Invariants (INV) — NEVER violate

| INV | Rule |
|-----|------|
| INV-01 | Tool is ALWAYS idempotent — second run = `no_op` with zero file changes |
| INV-02 | `dry_run=True` NEVER writes to disk |
| INV-03 | Every generated `.py` file MUST pass `ast.parse` before tool returns success |
| INV-04 | `app.main` MUST import cleanly without optional SDKs installed |
| INV-05 | No generated function body exceeds 50 LOC |
| INV-06 | `ToolResult.execution_time_ms` MUST be positive on every return path |
| INV-07 | Existing files are NEVER overwritten without idempotency guard |
| INV-08 | Config fields MUST be inside `class Settings` body (4-space indent) |
| INV-09 | Alembic migrations MUST chain to current head via `find_migration_head` |
| INV-10 | `MCP_TOOL["entry"]` MUST match the actual public function name exactly |

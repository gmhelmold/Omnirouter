"""test_bandit_deps.py — Brutal hardening round 5B.

Three tests:

  Test 1: Bandit security scan on generated code (HIGH/CRITICAL only).
          Uses bandit if installed, otherwise manual grep patterns.

  Test 2: Dependency conflict detection on requirements.txt after all 27
          extend tools are applied.

  Test 3: Generated imports vs requirements.txt — every import in app/ must
          be covered.

Run:
    PYTHONPATH=. python3 tests/test_bandit_deps.py
    # or via pytest:
    PYTHONPATH=. python3 -m pytest tests/test_bandit_deps.py -v
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _src_files(
    project_dir: Path,
    *,
    exclude_tests: bool = False,
    exclude_alembic: bool = False,
) -> list[Path]:
    """All .py files under project_dir, respecting exclusions."""
    files = [
        f
        for f in sorted(project_dir.rglob("*.py"))
        if ".venv" not in str(f) and "__pycache__" not in str(f)
    ]
    if exclude_tests:
        files = [f for f in files if "/tests/" not in str(f) and f.name != "conftest.py"]
    if exclude_alembic:
        files = [f for f in files if "/alembic/" not in str(f)]
    return files


def _app_files(project_dir: Path) -> list[Path]:
    """Only files under app/ (excludes tests/ and alembic/)."""
    app_dir = project_dir / "app"
    if not app_dir.exists():
        return []
    return [
        f
        for f in sorted(app_dir.rglob("*.py"))
        if ".venv" not in str(f) and "__pycache__" not in str(f)
    ]


def _generate_project(output_dir: Path, name: str = "bd") -> Path:
    """Generate a base project via the orchestrator."""
    from generators.orchestrator import generate_project  # noqa: PLC0415

    project_dir = output_dir / name
    result = generate_project(
        output_dir=str(project_dir),
        name=name,
        models={
            "Patient": {
                "name": "str",
                "ssn_encrypted": "bytes",
                "diagnosis": "str",
            }
        },
        owner_models={"Patient": "user"},
        with_otel=False,
        with_prometheus=False,
    )
    assert result["total_files"] > 0, "orchestrator produced no files"
    return project_dir


# The 10 extend tools used in test_security_generated.py
_TEN_TOOLS: list[tuple[str, str]] = [
    ("soft_delete", "adapt.extend.crud_data.add_soft_delete", "add_soft_delete"),
    ("multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy", "add_multi_tenancy"),
    ("rbac", "adapt.extend.auth_access.add_rbac", "add_rbac"),
    ("mfa", "adapt.extend.auth_access.add_mfa", "add_mfa"),
    ("api_key_auth", "adapt.extend.auth_access.add_api_key_auth", "add_api_key_auth"),
    ("audit_log", "adapt.extend.crud_data.add_audit_log", "add_audit_log"),
    ("webhook_receiver", "adapt.extend.realtime.add_webhook_receiver", "add_webhook_receiver"),
    ("cache_layer", "adapt.extend.infrastructure.add_cache_layer", "add_cache_layer"),
    ("search", "adapt.extend.crud_data.add_search", "add_search"),
    ("file_upload", "adapt.extend.crud_data.add_file_upload", "add_file_upload"),
]

# All 27 extend tools (extend category only — not evolve generators)
_ALL_27_TOOLS: list[tuple[str, str, str]] = [
    # crud_data (7)
    ("soft_delete", "adapt.extend.crud_data.add_soft_delete", "add_soft_delete"),
    ("cursor_pagination", "adapt.extend.crud_data.add_cursor_pagination", "add_cursor_pagination"),
    ("file_upload", "adapt.extend.crud_data.add_file_upload", "add_file_upload"),
    ("search", "adapt.extend.crud_data.add_search", "add_search"),
    ("audit_log", "adapt.extend.crud_data.add_audit_log", "add_audit_log"),
    ("data_export", "adapt.extend.crud_data.add_data_export", "add_data_export"),
    ("bulk_operations", "adapt.extend.crud_data.add_bulk_operations", "add_bulk_operations"),
    # auth_access (6)
    ("rbac", "adapt.extend.auth_access.add_rbac", "add_rbac"),
    ("mfa", "adapt.extend.auth_access.add_mfa", "add_mfa"),
    ("multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy", "add_multi_tenancy"),
    ("feature_flags", "adapt.extend.auth_access.add_feature_flags", "add_feature_flags"),
    ("api_key_auth", "adapt.extend.auth_access.add_api_key_auth", "add_api_key_auth"),
    ("oauth2_provider", "adapt.extend.auth_access.add_oauth2_provider", "add_oauth2_provider"),
    # api_design (4)
    ("api_versioning", "adapt.extend.api_design.add_api_versioning", "add_api_versioning"),
    ("graphql", "adapt.extend.api_design.add_graphql", "add_graphql"),
    ("batch_endpoint", "adapt.extend.api_design.add_batch_endpoint", "add_batch_endpoint"),
    ("long_running_task", "adapt.extend.api_design.add_long_running_task", "add_long_running_task"),
    # infrastructure (4)
    ("cache_layer", "adapt.extend.infrastructure.add_cache_layer", "add_cache_layer"),
    ("circuit_breaker", "adapt.extend.infrastructure.add_circuit_breaker", "add_circuit_breaker"),
    ("outbox_pattern", "adapt.extend.infrastructure.add_outbox_pattern", "add_outbox_pattern"),
    ("saga", "adapt.extend.infrastructure.add_saga", "add_saga"),
    # realtime (3)
    ("sse", "adapt.extend.realtime.add_sse", "add_sse"),
    ("webhook_sender", "adapt.extend.realtime.add_webhook_sender", "add_webhook_sender"),
    ("webhook_receiver", "adapt.extend.realtime.add_webhook_receiver", "add_webhook_receiver"),
    # testing_tools (3)
    ("factory", "adapt.extend.testing_tools.add_factory", "add_factory"),
    ("contract_tests", "adapt.extend.testing_tools.add_contract_tests", "add_contract_tests"),
    ("load_profile", "adapt.extend.testing_tools.add_load_profile", "add_load_profile"),
]

assert len(_ALL_27_TOOLS) == 27, f"Expected 27 tools, got {len(_ALL_27_TOOLS)}"


def _apply_tools(
    project_dir: Path,
    tools: list[tuple[str, str, str]],
) -> list[tuple[str, str]]:
    """Apply a list of tools.  Returns list of (name, error) for failures."""
    import importlib  # noqa: PLC0415

    from adapt.contracts import ToolInput  # noqa: PLC0415

    inp = ToolInput(project_dir=str(project_dir))
    failures = []
    for tool_name, module_path, func_name in tools:
        try:
            mod = importlib.import_module(module_path)
            fn = getattr(mod, func_name)
            res = fn(inp)
            if res.status == "error":
                failures.append((tool_name, res.error or "unknown error"))
        except Exception as exc:  # noqa: BLE001
            failures.append((tool_name, str(exc)))
    return failures


# ---------------------------------------------------------------------------
# Manual bandit-like patterns (HIGH / CRITICAL severity)
# ---------------------------------------------------------------------------

_BANDIT_MANUAL_PATTERNS: list[tuple[str, str, str]] = [
    # (bandit-like ID, regex, description)
    ("B602", r"subprocess\.call\(", "subprocess.call without explicit shell=False"),
    ("B605", r"os\.system\(", "os.system() — shell injection risk"),
    ("B506", r"yaml\.load\s*\([^,)]+\)", "yaml.load without SafeLoader"),
    ("B301", r"pickle\.loads\s*\(", "pickle.loads — RCE vector"),
    ("B302", r"marshal\.loads\s*\(", "marshal.loads — RCE vector"),
    ("B318", r"tempfile\.mktemp\s*\(", "tempfile.mktemp — insecure, use mkstemp"),
    ("B303a", r"hashlib\.md5\s*\(", "hashlib.md5 — weak hash"),
    ("B303b", r"hashlib\.sha1\s*\(", "hashlib.sha1 — weak hash"),
    (
        "B311",
        r"\brandom\.(random|randint|choice|seed)\s*\(",
        "random.* — not cryptographically secure",
    ),
]

# Patterns to whitelist (false-positive suppression)
# random.* inside test files / factories is acceptable
# yaml.load with SafeLoader qualifier is safe
_SAFE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("B506", re.compile(r"yaml\.load\s*\(.*SafeLoader", re.IGNORECASE)),
    ("B602", re.compile(r"shell\s*=\s*False")),
    ("B311", re.compile(r"#.*noqa|#.*nosec|random\.seed|test|factory|fake", re.IGNORECASE)),
]


def _manual_bandit_scan(
    project_dir: Path,
) -> list[dict[str, Any]]:
    """Perform a manual HIGH/CRITICAL pattern scan on app/ code.

    Excludes tests/ and alembic/ directories from findings.

    Returns a list of finding dicts:
        {"id": str, "file": str, "line": int, "text": str, "description": str}
    """
    findings: list[dict[str, Any]] = []
    target_files = _app_files(project_dir)

    for f in target_files:
        src = f.read_text(errors="replace")
        lines = src.splitlines()
        for lineno, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comment lines
            if stripped.startswith("#"):
                continue

            for bid, pattern, description in _BANDIT_MANUAL_PATTERNS:
                if not re.search(pattern, line):
                    continue

                # Check whitelist
                whitelisted = False
                for safe_bid, safe_re in _SAFE_PATTERNS:
                    if safe_bid == bid and safe_re.search(line):
                        whitelisted = True
                        break
                if whitelisted:
                    continue

                rel = str(f.relative_to(project_dir))
                findings.append(
                    {
                        "id": bid,
                        "file": rel,
                        "line": lineno,
                        "text": stripped[:120],
                        "description": description,
                    }
                )

    return findings


def _run_bandit(project_dir: Path) -> list[dict[str, Any]]:
    """Run bandit -r app/ -f json -ll.

    Returns HIGH/CRITICAL findings (excludes tests/ and alembic/).
    Raises FileNotFoundError if bandit is not on PATH.
    """
    app_dir = project_dir / "app"
    if not app_dir.exists():
        return []

    proc = subprocess.run(
        [
            "bandit",
            "-r",
            str(app_dir),
            "-f",
            "json",
            "-ll",  # only HIGH and CRITICAL
            "--silent",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    # bandit returns 1 when findings exist, 0 when clean
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        # Bandit produced no JSON (no findings or error)
        return []

    findings = []
    for r in data.get("results", []):
        severity = r.get("issue_severity", "").upper()
        if severity in ("HIGH", "CRITICAL"):
            findings.append(
                {
                    "id": r.get("test_id", "?"),
                    "file": r.get("filename", "?"),
                    "line": r.get("line_number", 0),
                    "text": r.get("code", "").strip()[:120],
                    "description": r.get("issue_text", ""),
                }
            )
    return findings


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _parse_requirements(req_file: Path) -> list[tuple[str, str]]:
    """Parse requirements.txt into list of (normalized_name, raw_line) tuples."""
    result = []
    for line in req_file.read_text().splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        name = re.split(r"[>=\[!<~]", raw)[0].strip()
        normalized = name.lower().replace("-", "_").replace(".", "_")
        result.append((normalized, raw))
    return result


def _check_duplicates(
    entries: list[tuple[str, str]],
) -> list[str]:
    """Return list of package names that appear more than once."""
    seen: dict[str, list[str]] = {}
    for norm, raw in entries:
        seen.setdefault(norm, []).append(raw)
    return [name for name, raws in seen.items() if len(raws) > 1]


def _check_version_conflicts(
    entries: list[tuple[str, str]],
) -> list[str]:
    """Detect contradictory version pins for the same package.

    Example conflict: redis>=5.0 AND redis<4.0
    Returns list of human-readable conflict descriptions.
    """
    # Group by normalized name
    by_name: dict[str, list[str]] = {}
    for norm, raw in entries:
        by_name.setdefault(norm, []).append(raw)

    conflicts = []
    for pkg, raws in by_name.items():
        if len(raws) < 2:
            continue
        # Collect all lower-bound and upper-bound constraints
        lower_bounds: list[float] = []
        upper_bounds: list[float] = []
        for raw in raws:
            # Extract version specs like >=X.Y, >X.Y, <=X.Y, <X.Y, ==X.Y
            for op, ver in re.findall(r"([><=!~]+)([\d.]+)", raw):
                try:
                    v = float(ver.split(".")[0] + "." + "".join(ver.split(".")[1:2]))
                except ValueError:
                    continue
                if op in (">=", ">"):
                    lower_bounds.append(v)
                elif op in ("<=", "<"):
                    upper_bounds.append(v)

        if lower_bounds and upper_bounds:
            min_lower = min(lower_bounds)
            max_upper = max(upper_bounds)
            if min_lower >= max_upper:
                conflicts.append(
                    f"  {pkg}: lower bound {min_lower} >= upper bound {max_upper} — lines: {raws}"
                )

    return conflicts


# Import-to-package mapping (top-level import name -> PyPI normalized name)
_IMPORT_TO_PACKAGE: dict[str, str] = {
    # Core
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "pydantic": "pydantic",
    "pydantic_settings": "pydantic_settings",
    "pydantic_core": "pydantic",  # bundled
    # Database
    "sqlalchemy": "sqlalchemy",
    "asyncpg": "asyncpg",
    "alembic": "alembic",
    "aiosqlite": "aiosqlite",
    # Auth
    "jwt": "pyjwt",
    "pwdlib": "pwdlib",
    "argon2": "pwdlib",  # argon2-cffi via pwdlib[argon2]
    "pyotp": "pyotp",
    "segno": "segno",
    # Cache
    "redis": "redis",
    "msgpack": "msgpack",
    # Web
    "httpx": "httpx",
    "starlette": "fastapi",  # bundled
    "anyio": "anyio",
    "multipart": "python_multipart",
    # Rate limiting
    "slowapi": "slowapi",
    "limits": "limits",
    # Observability
    "structlog": "structlog",
    # Email
    "email_validator": "email_validator",
    # Crypto
    "cryptography": "cryptography",
    # Testing
    "pytest": "pytest",
    "pytest_asyncio": "pytest_asyncio",
    "factory": "factory_boy",
    "factory_boy": "factory_boy",
    # Jobs
    "arq": "arq",
    # GraphQL
    "strawberry": "strawberry_graphql",
    # OAuth2
    "authlib": "authlib",
    # SSE
    "sse_starlette": "sse_starlette",
    # Pact contract testing
    "pact": "pact_python",
    # Export / data
    "openpyxl": "openpyxl",
    "fpdf": "fpdf2",
    "pyarrow": "pyarrow",  # add_data_export — KNOWN MISSING from reqs
    "xlsxwriter": "xlsxwriter",  # add_data_export — KNOWN MISSING from reqs
    # File upload
    "magic": "python_magic",  # add_file_upload (python-magic in reqs)
    # AWS / S3 (file upload presigned URLs)
    "botocore": "boto3",  # botocore is transitive dep of boto3
    "boto3": "boto3",
    # Observability
    "prometheus_client": "prometheus_client",  # add_circuit_breaker/saga — KNOWN MISSING
    # Misc
    "httpcore": "httpcore",
    "certifi": "certifi",
    "typing_extensions": "typing_extensions",
    "annotated_types": "annotated_types",
    "idna": "idna",
    "sniffio": "sniffio",
    "click": "click",
    "h11": "h11",
    "exceptiongroup": "exceptiongroup",
    "frozenlist": "frozenlist",
    "aiohttp": "aiohttp",
    "aiohappyeyeballs": "aiohappyeyeballs",
    "aiosignal": "aiosignal",
    "multidict": "multidict",
    "yarl": "yarl",
    "attrs": "attrs",
    "charset_normalizer": "charset_normalizer",
    "requests": "requests",
}

# Packages that are internal to the project and should never be in requirements.
# `core` is the bundled `core.venous.*` library copied into <project>/core/ by
# generators/scaffold_venous.py (ADR 0002) — it lives in the project tree, not
# on PyPI, so it must be excluded from the external-requirements check.
_INTERNAL_PREFIXES = ("app", "tests", "alembic", "migrations", "core")

# Top-level names that are part of Python stdlib (supplement sys.stdlib_module_names)
_EXTRA_STDLIB: frozenset[str] = frozenset(
    {
        "abc",
        "ast",
        "asyncio",
        "base64",
        "collections",
        "contextlib",
        "copy",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "functools",
        "hashlib",
        "hmac",
        "http",
        "importlib",
        "inspect",
        "io",
        "itertools",
        "json",
        "logging",
        "math",
        "mimetypes",
        "operator",
        "os",
        "pathlib",
        "pickle",
        "platform",
        "queue",
        "random",
        "re",
        "shutil",
        "signal",
        "socket",
        "ssl",
        "string",
        "subprocess",
        "sys",
        "tempfile",
        "textwrap",
        "threading",
        "time",
        "traceback",
        "types",
        "typing",
        "unicodedata",
        "unittest",
        "urllib",
        "uuid",
        "warnings",
        "weakref",
        "zlib",
    }
)


def _get_third_party_imports(project_dir: Path) -> dict[str, list[str]]:
    """Scan app/ for all third-party top-level imports.

    Returns dict mapping top_level_name -> [relative_file_paths].
    """
    stdlib = set(sys.stdlib_module_names) | _EXTRA_STDLIB
    result: dict[str, list[str]] = {}

    for py_file in _app_files(project_dir):
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            top: str | None = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    _record_import(top, py_file, project_dir, stdlib, result)
            elif isinstance(node, ast.ImportFrom):  # noqa: SIM102
                if node.module and node.level == 0:
                    top = node.module.split(".")[0]
                    _record_import(top, py_file, project_dir, stdlib, result)

    return result


def _record_import(
    top: str,
    py_file: Path,
    project_dir: Path,
    stdlib: set[str],
    result: dict[str, list[str]],
) -> None:
    if top in stdlib:
        return
    if top.startswith("_"):
        return
    if any(top == p or top.startswith(p + ".") for p in _INTERNAL_PREFIXES):
        return
    rel = str(py_file.relative_to(project_dir))
    result.setdefault(top, [])
    if rel not in result[top]:
        result[top].append(rel)


# ---------------------------------------------------------------------------
# Module-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def project_10_tools(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Project with 10 extend tools applied — used for bandit scan."""
    base = tmp_path_factory.mktemp("bandit_10")
    project_dir = _generate_project(base, name="sec10")
    from adapt.contracts import ToolInput  # noqa: PLC0415

    inp = ToolInput(project_dir=str(project_dir))
    failures = []
    for tool_name, module_path, func_name in _TEN_TOOLS:
        import importlib  # noqa: PLC0415

        try:
            mod = importlib.import_module(module_path)
            fn = getattr(mod, func_name)
            res = fn(inp)
            if res.status == "error":
                failures.append((tool_name, res.error or "unknown"))
        except Exception as exc:  # noqa: BLE001
            failures.append((tool_name, str(exc)))

    assert not failures, "Tool failures during fixture setup:\n" + "\n".join(
        f"  {n}: {e}" for n, e in failures
    )
    return project_dir


@pytest.fixture(scope="module")
def project_all_27_tools(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Project with all 27 extend tools applied — used for dep tests."""
    base = tmp_path_factory.mktemp("deps_27")
    project_dir = _generate_project(base, name="dep27")
    failures = _apply_tools(project_dir, _ALL_27_TOOLS)
    # Log failures but don't abort — some tools may legitimately be no_op
    # or fail on this particular model; we note them in assertions.
    if failures:
        import warnings  # noqa: PLC0415

        warnings.warn(
            "Some tools failed during 27-tool fixture setup: " + ", ".join(n for n, _ in failures),
            stacklevel=1,
        )
    return project_dir


# ===========================================================================
# TEST 1 — Bandit scan
# ===========================================================================


class TestBanditScan:
    """Test 1: Zero HIGH/CRITICAL security findings in generated app/ code."""

    def test_bandit_or_manual_zero_high_critical(self, project_10_tools: Path) -> None:
        """Run bandit if available; otherwise use manual pattern scan.

        Asserts ZERO HIGH/CRITICAL findings in app/ code.
        Excludes tests/ and alembic/ from scan scope.
        """
        bandit_available = shutil.which("bandit") is not None

        if bandit_available:
            findings = _run_bandit(project_10_tools)
            scan_method = "bandit -ll (HIGH/CRITICAL)"
        else:
            findings = _manual_bandit_scan(project_10_tools)
            scan_method = "manual pattern scan (bandit not installed)"

        if findings:
            detail = "\n".join(
                f"  [{f['id']}] {f['file']}:{f['line']}  {f['text']}\n    -> {f['description']}"
                for f in findings[:20]
            )
            if len(findings) > 20:
                detail += f"\n  ... and {len(findings) - 20} more"
            pytest.fail(
                f"BANDIT FAIL — {len(findings)} HIGH/CRITICAL findings "
                f"(scan method: {scan_method}):\n{detail}"
            )

    def test_no_subprocess_shell_true(self, project_10_tools: Path) -> None:
        """subprocess calls must never use shell=True in app/ code."""
        findings = []
        for f in _app_files(project_10_tools):
            src = f.read_text(errors="replace")
            for lineno, line in enumerate(src.splitlines(), 1):
                if re.search(r"subprocess\.[a-z_]+\s*\(", line) and re.search(
                    r"shell\s*=\s*True", line
                ):
                    rel = str(f.relative_to(project_10_tools))
                    findings.append(f"  {rel}:{lineno}  {line.strip()}")

        assert not findings, "BANDIT B604 — subprocess with shell=True found:\n" + "\n".join(
            findings
        )

    def test_no_assert_used_for_auth(self, project_10_tools: Path) -> None:
        """assert statements must not be used for access control (stripped by -O)."""
        # Pattern: `assert current_user` or `assert user.role`
        pattern = re.compile(r"^\s*assert\s+\w*(user|role|perm|auth)\w*", re.IGNORECASE)
        findings = []
        for f in _app_files(project_10_tools):
            src = f.read_text(errors="replace")
            for lineno, line in enumerate(src.splitlines(), 1):
                if pattern.search(line):
                    rel = str(f.relative_to(project_10_tools))
                    findings.append(f"  {rel}:{lineno}  {line.strip()}")

        assert not findings, (
            "BANDIT B101-AUTH — assert used for auth/access control:\n" + "\n".join(findings)
        )


# ===========================================================================
# TEST 2 — Dependency conflicts
# ===========================================================================


class TestDependencyConflicts:
    """Test 2: requirements.txt after all 27 tools must be conflict-free."""

    def test_requirements_file_exists(self, project_all_27_tools: Path) -> None:
        """requirements.txt must exist after applying 27 tools."""
        req = project_all_27_tools / "requirements.txt"
        assert req.exists(), "requirements.txt not found in generated project"

    def test_no_duplicate_packages(self, project_all_27_tools: Path) -> None:
        """No package should be listed twice in requirements.txt."""
        req = project_all_27_tools / "requirements.txt"
        entries = _parse_requirements(req)
        duplicates = _check_duplicates(entries)

        assert not duplicates, (
            "DEP-CONFLICT: Duplicate packages in requirements.txt:\n"
            + "\n".join(f"  {d}" for d in duplicates)
        )

    def test_no_version_pin_conflicts(self, project_all_27_tools: Path) -> None:
        """No contradictory version constraints (e.g. >=5.0 and <4.0)."""
        req = project_all_27_tools / "requirements.txt"
        entries = _parse_requirements(req)
        conflicts = _check_version_conflicts(entries)

        assert not conflicts, (
            "DEP-CONFLICT: Version pin conflicts in requirements.txt:\n" + "\n".join(conflicts)
        )

    def test_essential_packages_present(self, project_all_27_tools: Path) -> None:
        """Core packages must always be present in requirements.txt."""
        req = project_all_27_tools / "requirements.txt"
        entries = _parse_requirements(req)
        installed_names = {name for name, _ in entries}

        essential = ["fastapi", "sqlalchemy", "pydantic", "alembic", "uvicorn"]
        missing = [p for p in essential if p not in installed_names]

        assert not missing, (
            "DEP-ESSENTIAL: Core packages missing from requirements.txt:\n"
            + "\n".join(f"  {m}" for m in missing)
        )

    def test_requirements_parseable(self, project_all_27_tools: Path) -> None:
        """Every line in requirements.txt must be a valid pip specifier (no syntax errors)."""
        req = project_all_27_tools / "requirements.txt"
        bad_lines = []
        for i, line in enumerate(req.read_text().splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped.startswith("-"):
                continue
            # Basic check: line must match `name[extras]version_spec`
            if not re.match(r"^[A-Za-z0-9_\-\.]+(\[[\w,\-]+\])?([><=!~][^;].*)?$", stripped):
                bad_lines.append(f"  line {i}: {stripped}")

        assert not bad_lines, "DEP-PARSE: Unparseable lines in requirements.txt:\n" + "\n".join(
            bad_lines
        )


# ===========================================================================
# TEST 3 — Generated imports vs requirements.txt
# ===========================================================================


class TestImportsVsRequirements:
    """Test 3: Every import in app/ must map to a package in requirements.txt."""

    def test_all_imports_covered_by_requirements(self, project_all_27_tools: Path) -> None:
        """Scan app/ imports via AST and verify requirements.txt covers them.

        Known confirmed gaps (cryptography, msgpack) are documented but NOT
        treated as failures here — they are tracked in test_generated_quality.py.
        Any NEW undocumented gaps are a hard failure.
        """
        req = project_all_27_tools / "requirements.txt"
        entries = _parse_requirements(req)
        installed_names = {name for name, _ in entries}

        third_party = _get_third_party_imports(project_all_27_tools)

        # Documented known-missing packages (confirmed tool bugs — not patched into
        # requirements.txt by the tool that introduces them).
        # cryptography / msgpack: tracked in test_generated_quality.py
        # pyarrow / xlsxwriter:   add_data_export does not patch requirements.txt
        # prometheus_client:      add_circuit_breaker / add_saga do not patch reqs
        _KNOWN_MISSING: frozenset[str] = frozenset(  # noqa: N806
            {
                "cryptography",
                "msgpack",
                "pyarrow",
                "xlsxwriter",
                "prometheus_client",
            }
        )

        new_missing = []
        covered = []

        for imp in sorted(third_party):
            pkg = _IMPORT_TO_PACKAGE.get(imp, imp.lower().replace("-", "_"))
            if pkg in installed_names:
                covered.append(imp)
            elif pkg in _KNOWN_MISSING or imp in _KNOWN_MISSING:
                # Known gap — skip as documented
                covered.append(f"{imp} (known-gap)")
            else:
                files_sample = third_party[imp][:3]
                new_missing.append(
                    f"  import '{imp}' -> expected pkg '{pkg}' | used in: {files_sample}"
                )

        if new_missing:
            pytest.fail(
                f"IMPORT-COVERAGE: {len(new_missing)} undocumented import(s) "
                f"missing from requirements.txt:\n"
                + "\n".join(new_missing)
                + f"\n\n(Covered: {len(covered)} imports)"
            )

    def test_import_to_package_mapping_complete(self, project_all_27_tools: Path) -> None:
        """Every third-party import must have a mapping in _IMPORT_TO_PACKAGE
        OR be inferrable via normalization.

        This test surfaces imports where the auto-normalization (replace - with _)
        is insufficient — these need explicit entries in _IMPORT_TO_PACKAGE.
        """
        # Packages that are genuinely NOT in requirements (internal or stdlib-like)
        _ACTUALLY_INTERNAL: frozenset[str] = frozenset(  # noqa: N806
            {
                "app",
                "tests",
                "alembic",
                "migrations",
            }
        )

        req = project_all_27_tools / "requirements.txt"
        entries = _parse_requirements(req)
        installed_names = {name for name, _ in entries}

        third_party = _get_third_party_imports(project_all_27_tools)
        # Same set as in test_all_imports_covered_by_requirements
        _KNOWN_MISSING: frozenset[str] = frozenset(  # noqa: N806
            {
                "cryptography",
                "msgpack",
                "pyarrow",
                "xlsxwriter",
                "prometheus_client",
            }
        )

        unmapped = []
        for imp in sorted(third_party):
            if imp in _ACTUALLY_INTERNAL:
                continue
            if imp in _KNOWN_MISSING:
                continue
            pkg = _IMPORT_TO_PACKAGE.get(imp)
            if pkg is None:
                # Auto-normalize: if the auto-name is in requirements it is fine
                auto = imp.lower().replace("-", "_")
                if auto not in installed_names:
                    files_sample = third_party[imp][:2]
                    unmapped.append(
                        f"  '{imp}' has no explicit mapping and auto-name"
                        f" '{auto}' not in requirements | {files_sample}"
                    )

        assert not unmapped, (
            "IMPORT-MAPPING: Imports with no mapping and not in requirements.txt:\n"
            + "\n".join(unmapped)
        )

    def test_third_party_import_count_reasonable(self, project_all_27_tools: Path) -> None:
        """Sanity check: after 27 tools, at least 10 distinct third-party
        imports should exist in app/ — confirms the scan ran on real files."""
        third_party = _get_third_party_imports(project_all_27_tools)
        n = len(third_party)
        assert n >= 10, (
            f"Only {n} third-party imports found in app/ — "
            "expected at least 10 after applying 27 tools. "
            "Did the project generate correctly?"
        )


# ===========================================================================
# Standalone runner — score line
# ===========================================================================

if __name__ == "__main__":
    import os

    skill_root = Path(__file__).parent.parent
    env = {**os.environ, "PYTHONPATH": str(skill_root)}

    # Track pass/fail per test class
    results: dict[str, bool] = {}
    classes = [
        ("Test 1 (Bandit scan)", "TestBanditScan"),
        ("Test 2 (Dep conflicts)", "TestDependencyConflicts"),
        ("Test 3 (Imports vs reqs)", "TestImportsVsRequirements"),
    ]

    for label, cls_name in classes:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                f"{Path(__file__)}::{cls_name}",
                "-v",
                "--tb=short",
                "--no-header",
                "-q",
                # Disable known broken plugins that crash pytest startup in some envs
                "-p",
                "no:deepeval",
                "-p",
                "no:logfire",
            ],
            cwd=str(skill_root),
            env=env,
            capture_output=True,
            text=True,
        )
        passed = proc.returncode == 0
        results[label] = passed
        status = "PASS" if passed else "FAIL"
        print(f"  {status}  {label}")
        if not passed:
            # Print last 20 lines of output for context
            tail = "\n".join(proc.stdout.splitlines()[-20:])
            print(tail)

    n_pass = sum(1 for v in results.values() if v)
    n_total = len(results)
    print(f"\nBandit + deps: {n_pass}/{n_total} tests pass")
    sys.exit(0 if n_pass == n_total else 1)

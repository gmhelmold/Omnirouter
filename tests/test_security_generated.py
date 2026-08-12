"""test_security_generated.py — Brutal hardening round 4B.

Security audit on GENERATED code.  Generates a full project, applies all
10 adapt tools, and verifies 15 security invariants via static grep.

If any check fails it is a REAL security bug in the generated code.
Bugs are documented but NOT fixed here.

Run:
    PYTHONPATH=. python3 tests/test_security_generated.py
    # or via pytest:
    PYTHONPATH=. python3 -m pytest tests/test_security_generated.py -v
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

# ---------------------------------------------------------------------------
# Fixture: one generated project shared by all security tests
# ---------------------------------------------------------------------------


def _generate_project_with_all_tools(output_dir: Path) -> Path:
    """Generate project + apply all 10 adapt tools.  Returns the project dir."""
    from generators.orchestrator import generate_project  # noqa: PLC0415
    from adapt.contracts import ToolInput  # noqa: PLC0415
    from adapt.extend.crud_data.add_soft_delete import add_soft_delete  # noqa: PLC0415
    from adapt.extend.auth_access.add_multi_tenancy import add_multi_tenancy  # noqa: PLC0415
    from adapt.extend.auth_access.add_rbac import add_rbac  # noqa: PLC0415
    from adapt.extend.auth_access.add_mfa import add_mfa  # noqa: PLC0415
    from adapt.extend.auth_access.add_api_key_auth import add_api_key_auth  # noqa: PLC0415
    from adapt.extend.crud_data.add_audit_log import add_audit_log  # noqa: PLC0415
    from adapt.extend.realtime.add_webhook_receiver import add_webhook_receiver  # noqa: PLC0415
    from adapt.extend.infrastructure.add_cache_layer import add_cache_layer  # noqa: PLC0415
    from adapt.extend.crud_data.add_search import add_search  # noqa: PLC0415
    from adapt.extend.crud_data.add_file_upload import add_file_upload  # noqa: PLC0415

    project_dir = output_dir / "sec"
    result = generate_project(
        output_dir=str(project_dir),
        name="sec",
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

    inp = ToolInput(project_dir=str(project_dir))
    tools = [
        ("soft_delete", add_soft_delete),
        ("multi_tenancy", add_multi_tenancy),
        ("rbac", add_rbac),
        ("mfa", add_mfa),
        ("api_key_auth", add_api_key_auth),
        ("audit_log", add_audit_log),
        ("webhook_receiver", add_webhook_receiver),
        ("cache_layer", add_cache_layer),
        ("search", add_search),
        ("file_upload", add_file_upload),
    ]
    for tool_name, fn in tools:
        res = fn(inp)
        assert res.status in ("success", "no_op"), (
            f"Tool '{tool_name}' failed: {res.error}"
        )

    return project_dir


@pytest.fixture(scope="module")
def sec_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Module-scoped fixture — generates the project once for all 15 checks."""
    base = tmp_path_factory.mktemp("sec_audit")
    return _generate_project_with_all_tools(base)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COMMENT_OR_DOCSTRING_RE = re.compile(
    r'(?:#[^\n]*|"""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')',
    re.MULTILINE,
)


def _src_files(project_dir: Path, *, exclude_tests: bool = False) -> list[Path]:
    """All .py files under *project_dir*, optionally excluding tests/."""
    files = [
        f for f in sorted(project_dir.rglob("*.py"))
        if ".venv" not in str(f)
    ]
    if exclude_tests:
        files = [f for f in files if "/tests/" not in str(f) and f.name != "conftest.py"]
    return files


def _strip_comments(src: str) -> str:
    """Remove Python comments and docstrings from source text."""
    return _COMMENT_OR_DOCSTRING_RE.sub("", src)


def _collect_matches(
    project_dir: Path,
    pattern: str,
    *,
    exclude_tests: bool = False,
    strip_comments: bool = True,
    flags: int = 0,
) -> list[tuple[Path, int, str]]:
    """Return (file, lineno, line) tuples for every match of *pattern*."""
    regex = re.compile(pattern, flags)
    hits: list[tuple[Path, int, str]] = []
    for f in _src_files(project_dir, exclude_tests=exclude_tests):
        src = f.read_text(errors="replace")
        if strip_comments:
            src_clean = _strip_comments(src)
        else:
            src_clean = src
        # Scan line-by-line on the cleaned source but report original line numbers
        for lineno, line in enumerate(src.splitlines(), 1):
            # Check the cleaned version of this line
            cleaned_line = _strip_comments(line)
            if regex.search(cleaned_line):
                hits.append((f, lineno, line.strip()))
    return hits


def _format_hits(hits: list[tuple[Path, int, str]], project_dir: Path) -> str:
    lines = []
    for f, lineno, line in hits[:20]:
        rel = f.relative_to(project_dir)
        lines.append(f"  {rel}:{lineno}  {line}")
    if len(hits) > 20:
        lines.append(f"  ... and {len(hits) - 20} more")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# AST-based helpers (precise — no substring/docstring false positives)
# ---------------------------------------------------------------------------

# Names that indicate a secret/credential value when compared in pure Python.
_SECRET_NAME_RE = re.compile(
    r"(hash|token|secret|signature|hmac|password|passwd|pwd)",
    re.IGNORECASE,
)
# Suffixes that look secret-ish but denote metadata, not the secret value.
_SECRET_NAME_SKIP_SUFFIXES = (
    "_type",
    "_url",
    "_name",
    "_id",
    "_field",
    "_column",
)


def _node_secret_name(node: ast.AST) -> str | None:
    """Return the identifier of *node* if it is a bare Name/attribute that
    names a secret value (e.g. ``token``, ``code_hash``), else None.

    Crucially this returns None for SQLAlchemy column expressions of the form
    ``Model.column`` — those are ``ast.Attribute`` nodes whose ``.value`` is a
    Name (the model class), and comparing them builds a SQL WHERE clause, not a
    Python byte compare.  We only treat a *plain* local variable (``ast.Name``)
    or a ``self.<attr>`` access as a candidate Python secret.
    """
    if isinstance(node, ast.Name):
        ident = node.id
    elif (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    ):
        # self.token, self.code_hash — a real Python attribute access
        ident = node.attr
    else:
        return None
    lowered = ident.lower()
    if not _SECRET_NAME_RE.search(lowered):
        return None
    if any(lowered.endswith(suf) for suf in _SECRET_NAME_SKIP_SUFFIXES):
        return None
    return ident


def _is_orm_column(node: ast.AST) -> bool:
    """True if *node* is ``SomeModel.column`` — a class-qualified attribute,
    i.e. a SQLAlchemy mapped-column expression rather than a local variable.
    """
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id != "self"
    )


def _find_unsafe_secret_compares(src: str) -> list[tuple[int, str]]:
    """Return (lineno, secret_name) for every pure-Python ``==``/``!=``
    comparison where an operand names a secret value (password/token/hash/...).

    FALSE POSITIVES excluded by construction (AST, not substring):
      - SQLAlchemy column comparisons (``Model.column == value``)
      - comparisons against ``None``
      - occurrences inside docstrings / string literals / comments
    These should use ``hmac.compare_digest`` / ``secrets.compare_digest``.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        # Only == / != are timing-unsafe equality checks.
        if not all(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops):
            continue
        operands = [node.left, *node.comparators]
        # Ignore ``x == None`` style identity checks.
        if any(isinstance(o, ast.Constant) and o.value is None for o in operands):
            continue
        # If any operand is an ORM column expression, this is a SQL WHERE
        # clause, not a Python byte compare — skip the whole comparison.
        if any(_is_orm_column(o) for o in operands):
            continue
        secret_names = [n for o in operands if (n := _node_secret_name(o))]
        if secret_names:
            hits.append((node.lineno, secret_names[0]))
    return hits


def _find_real_print_calls(src: str) -> list[int]:
    """Return line numbers of genuine ``print(...)`` Call nodes in *src*.

    AST-based, so ``print(`` appearing inside a docstring example, a string
    literal, or a comment is NOT flagged — only real executable calls.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            lines.append(node.lineno)
    return lines


# ---------------------------------------------------------------------------
# SEC-01 — No hardcoded secrets
# ---------------------------------------------------------------------------

class TestNoHardcodedSecrets:
    """SEC-01: No hardcoded credentials/secrets in generated code."""

    PATTERNS = [
        r'(?<![#\w])"changeme"',
        r"""password\s*=\s*["'][^"']{3,}["']""",
        r"""token\s*=\s*["'][^"']{8,}["']""",
        r"""secret\s*=\s*["'][^"']{8,}["']""",
        r"""api_key\s*=\s*["'][^"']{8,}["']""",
    ]

    def test_no_changeme_literal(self, sec_project: Path) -> None:
        """'changeme' must never appear as a bare string literal."""
        hits = _collect_matches(sec_project, self.PATTERNS[0], exclude_tests=True)
        assert not hits, (
            "SEC-01 FAIL — hardcoded 'changeme' found:\n"
            + _format_hits(hits, sec_project)
        )

    def test_no_hardcoded_password(self, sec_project: Path) -> None:
        """password = '...' must not appear in production code."""
        hits = _collect_matches(sec_project, self.PATTERNS[1], exclude_tests=True)
        assert not hits, (
            "SEC-01 FAIL — hardcoded password found:\n"
            + _format_hits(hits, sec_project)
        )

    def test_no_hardcoded_secret(self, sec_project: Path) -> None:
        """secret = '...' must not appear in production code."""
        hits = _collect_matches(sec_project, self.PATTERNS[3], exclude_tests=True)
        assert not hits, (
            "SEC-01 FAIL — hardcoded secret found:\n"
            + _format_hits(hits, sec_project)
        )


# ---------------------------------------------------------------------------
# SEC-02 — No eval/exec/unsafe deserialisation
# ---------------------------------------------------------------------------

class TestNoEvalExec:
    """SEC-02: No eval, exec, pickle.loads, or unsafe yaml.load."""

    def test_no_eval(self, sec_project: Path) -> None:
        """eval() must never appear in generated production code."""
        # Allow redis.eval() — it is a Redis Lua script call, not Python eval
        hits = []
        for f, lineno, line in _collect_matches(sec_project, r"\beval\(", exclude_tests=True):
            # Filter out redis.eval(...) calls
            if re.search(r"redis.*\.eval\(|\.eval\(", line):
                continue
            hits.append((f, lineno, line))
        assert not hits, (
            "SEC-02 FAIL — eval() found:\n" + _format_hits(hits, sec_project)
        )

    def test_no_exec(self, sec_project: Path) -> None:
        """exec() must never appear in generated production code."""
        hits = _collect_matches(sec_project, r"\bexec\(", exclude_tests=True)
        assert not hits, (
            "SEC-02 FAIL — exec() found:\n" + _format_hits(hits, sec_project)
        )

    def test_no_pickle_loads(self, sec_project: Path) -> None:
        """pickle.loads() must never appear (RCE vector)."""
        hits = _collect_matches(sec_project, r"pickle\.loads\(", exclude_tests=True)
        assert not hits, (
            "SEC-02 FAIL — pickle.loads() found:\n" + _format_hits(hits, sec_project)
        )

    def test_no_unsafe_yaml_load(self, sec_project: Path) -> None:
        """yaml.load() without SafeLoader is a code-execution vector."""
        # yaml.safe_load() and yaml.load(..., Loader=yaml.SafeLoader) are OK
        hits = []
        for f, lineno, line in _collect_matches(sec_project, r"yaml\.load\(", exclude_tests=True):
            # Safe pattern: yaml.load(..., Loader=yaml.SafeLoader)
            if "SafeLoader" in line or "safe_load" in line:
                continue
            hits.append((f, lineno, line))
        assert not hits, (
            "SEC-02 FAIL — unsafe yaml.load() found:\n" + _format_hits(hits, sec_project)
        )


# ---------------------------------------------------------------------------
# SEC-03 — No f-string SQL injection
# ---------------------------------------------------------------------------

class TestNoFstringSql:
    """SEC-03: SQL must never be built with f-strings."""

    SQL_KEYWORDS = ["SELECT", "INSERT", "UPDATE", "DELETE", "FROM", "WHERE"]

    def test_no_fstring_sql(self, sec_project: Path) -> None:
        """f-string SQL (f\"SELECT ...\") is a SQLi vector — must be zero."""
        pattern = r"""f["'](SELECT|INSERT|UPDATE|DELETE|FROM|WHERE)"""
        hits = _collect_matches(sec_project, pattern, flags=re.IGNORECASE)
        assert not hits, (
            "SEC-03 FAIL — f-string SQL found:\n" + _format_hits(hits, sec_project)
        )

    def test_no_string_format_sql(self, sec_project: Path) -> None:
        """'SELECT %s' % var or .format() SQL is also a SQLi vector."""
        pattern = r"""["'](SELECT|INSERT|UPDATE|DELETE)\b.*["']\s*(%|\.format\()"""
        hits = _collect_matches(sec_project, pattern, flags=re.IGNORECASE)
        assert not hits, (
            "SEC-03 FAIL — string-format SQL found:\n"
            + _format_hits(hits, sec_project)
        )


# ---------------------------------------------------------------------------
# SEC-04 — Timing-safe comparisons
# ---------------------------------------------------------------------------

class TestTimingSafeAuth:
    """SEC-04: Secrets/tokens must be compared with hmac.compare_digest.

    The check is AST-based (see ``_find_unsafe_secret_compares``).  A pure
    Python ``==``/``!=`` on a secret value is timing-unsafe and flagged; a
    SQLAlchemy column comparison (``Model.column == value``) is a SQL WHERE
    clause and is NOT flagged — it never touches the secret in Python.
    """

    def test_no_direct_equality_on_secrets(self, sec_project: Path) -> None:
        """Local variables / ``self`` attributes named *hash*, *token*,
        *secret*, *signature*, *password* must never be compared with plain
        ``==``/``!=`` (use ``hmac.compare_digest``).

        Excluded (AST, not substring):
        - SQLAlchemy column comparisons (``Model.code_hash == code_hash``)
        - ``token_type``/``token_url``/... metadata identifiers
        - ``x == None`` identity checks
        - occurrences inside docstrings / string literals / comments
        """
        hits = []
        for f in _src_files(sec_project, exclude_tests=True):
            src = f.read_text(errors="replace")
            src_lines = src.splitlines()
            for lineno, _secret_name in _find_unsafe_secret_compares(src):
                # lineno comes from the AST of *this* src, so it is always valid.
                hits.append((f, lineno, src_lines[lineno - 1].strip()))

        assert not hits, (
            "SEC-04 FAIL — timing-unsafe == comparison on Python secret value:\n"
            + _format_hits(hits, sec_project)
        )


# ---------------------------------------------------------------------------
# SEC-05 — Sensitive fields excluded from Public schemas
# ---------------------------------------------------------------------------

class TestSensitiveFieldsInPublicSchemas:
    """SEC-05: Sensitive fields must NEVER appear in *Public schemas."""

    SENSITIVE_FIELDS = [
        "ssn_encrypted",
        "hashed_password",
        "secret_hash",
        "token_hash",
    ]

    def _find_public_class_bodies(self, src: str) -> list[str]:
        """Return source blocks for every class whose name ends with 'Public'."""
        blocks = []
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return blocks
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and "Public" in node.name:
                # Collect all attribute names defined in the class
                attrs = []
                for item in ast.walk(node):
                    if isinstance(item, ast.AnnAssign):
                        if isinstance(item.target, ast.Name):
                            attrs.append(item.target.id)
                    if isinstance(item, (ast.Assign,)):
                        for t in item.targets:
                            if isinstance(t, ast.Name):
                                attrs.append(t.id)
                blocks.append((node.name, attrs))
        return blocks

    def test_sensitive_fields_not_in_public_schemas(self, sec_project: Path) -> None:
        """ssn_encrypted, hashed_password, etc. must not be fields of *Public classes."""
        violations = []
        for f in _src_files(sec_project):
            src = f.read_text(errors="replace")
            if "Public" not in src:
                continue
            for class_name, attrs in self._find_public_class_bodies(src):
                for field in self.SENSITIVE_FIELDS:
                    if field in attrs:
                        rel = f.relative_to(sec_project)
                        violations.append(f"  {rel}: class {class_name} exposes '{field}'")

        assert not violations, (
            "SEC-05 FAIL — sensitive fields in Public schemas:\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# SEC-06 — Auth on all mutation endpoints
# ---------------------------------------------------------------------------

class TestAuthOnMutationEndpoints:
    """SEC-06: POST/PATCH/DELETE routes must declare a dependency."""

    # Endpoints that are legitimately public
    _PUBLIC_ROUTE_PATTERNS = re.compile(
        r"(login|signup|register|webhook|health|token|refresh|verify)",
        re.IGNORECASE,
    )

    def test_mutation_routes_have_dependency(self, sec_project: Path) -> None:
        """Every @router.post/patch/delete must have Depends( in params or decorator."""
        violations = []
        route_pattern = re.compile(
            r'@\w*router\.(post|patch|delete)\s*\(',
            re.IGNORECASE,
        )
        for f in _src_files(sec_project, exclude_tests=True):
            if "/api/routes/" not in str(f) and "/routes/" not in str(f):
                continue
            src = f.read_text(errors="replace")
            lines = src.splitlines()
            for i, line in enumerate(lines):
                m = route_pattern.search(line)
                if not m:
                    continue

                # Collect the decorator + function signature (up to 20 lines ahead)
                chunk = "\n".join(lines[i : i + 20])

                # Skip known public endpoints
                if self._PUBLIC_ROUTE_PATTERNS.search(chunk):
                    continue

                # Check if Depends( appears in the chunk (up to async def body)
                if "Depends(" not in chunk and "current_user" not in chunk:
                    rel = f.relative_to(sec_project)
                    violations.append(
                        f"  {rel}:{i + 1}  {line.strip()}"
                    )

        assert not violations, (
            "SEC-06 FAIL — mutation endpoint without auth dependency:\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# SEC-07 — CORS not wildcard
# ---------------------------------------------------------------------------

class TestCorsNotWildcard:
    """SEC-07: allow_origins must never contain '*'."""

    def test_cors_allow_origins_not_wildcard(self, sec_project: Path) -> None:
        """allow_origins=['*'] is a security misconfiguration."""
        pattern = r"""allow_origins\s*=\s*\[["']\*["']\]"""
        hits = _collect_matches(sec_project, pattern)
        assert not hits, (
            "SEC-07 FAIL — wildcard CORS allow_origins found:\n"
            + _format_hits(hits, sec_project)
        )

    def test_cors_origins_from_config_not_literal(self, sec_project: Path) -> None:
        """CORS origins should come from config, not hardcoded URL literals."""
        # Hardcoded http:// or https:// inside allow_origins list
        pattern = r"""allow_origins\s*=\s*\[["']https?://"""
        hits = _collect_matches(sec_project, pattern, exclude_tests=True)
        assert not hits, (
            "SEC-07 FAIL — hardcoded URL in CORS allow_origins (use config):\n"
            + _format_hits(hits, sec_project)
        )


# ---------------------------------------------------------------------------
# SEC-08 — No debug / print in production code
# ---------------------------------------------------------------------------

class TestNoDebugInProduction:
    """SEC-08: print() and docs_url=None must be respected in production config."""

    def test_no_print_in_production_code(self, sec_project: Path) -> None:
        """print() is a data-leak risk in production FastAPI code.

        AST-based (see ``_find_real_print_calls``): only genuine ``print(...)``
        call expressions are flagged.  A ``print(`` inside a docstring example
        (e.g. a ``python -c "..."`` snippet), a string literal, or a comment is
        NOT executable and is therefore NOT a false positive.
        """
        hits = []
        for f in _src_files(sec_project, exclude_tests=True):
            src = f.read_text(errors="replace")
            src_lines = src.splitlines()
            for lineno in _find_real_print_calls(src):
                # lineno comes from the AST of *this* src, so it is always valid.
                hits.append((f, lineno, src_lines[lineno - 1].strip()))

        assert not hits, (
            "SEC-08 FAIL — print() in production code:\n"
            + _format_hits(hits, sec_project)
        )

    def test_docs_url_disabled_in_prod_config(self, sec_project: Path) -> None:
        """docs_url=None must be set for production (disable Swagger in prod)."""
        # Look in config.py or settings.py for docs_url
        config_files = list(sec_project.rglob("config.py")) + list(sec_project.rglob("settings.py"))
        found_docs_url_none = False
        for cf in config_files:
            content = cf.read_text(errors="replace")
            if "docs_url" in content and "None" in content:
                found_docs_url_none = True
                break
        # Also check main.py for docs_url=None
        main_files = list(sec_project.rglob("main.py"))
        for mf in main_files:
            content = mf.read_text(errors="replace")
            if re.search(r'docs_url\s*=\s*None', content):
                found_docs_url_none = True
                break

        assert found_docs_url_none, (
            "SEC-08 FAIL — docs_url=None not found in config/settings/main.py. "
            "Swagger UI should be disabled in production."
        )


# ---------------------------------------------------------------------------
# SEC-09 — Rate limiting on auth endpoints
# ---------------------------------------------------------------------------

class TestRateLimitingOnAuth:
    """SEC-09: Auth endpoints must have rate limiting."""

    def test_rate_limit_imported_in_auth_routes(self, sec_project: Path) -> None:
        """Rate limiting (slowapi / RateLimiter / Limiter) must be imported in auth routes."""
        auth_route_files = (
            list(sec_project.rglob("routes/auth*"))
            + list(sec_project.rglob("routes/login*"))
            + list(sec_project.rglob("auth/routes*"))
        )
        if not auth_route_files:
            # Search broadly
            auth_route_files = [
                f for f in _src_files(sec_project, exclude_tests=True)
                if "auth" in f.name.lower() and "route" in f.name.lower()
            ]
        if not auth_route_files:
            pytest.skip("No auth route files found — cannot verify rate limiting")

        rate_limit_patterns = ["slowapi", "RateLimiter", "Limiter", "rate_limit", "limiter"]
        missing = []
        for f in auth_route_files:
            src = f.read_text(errors="replace")
            if not any(p in src for p in rate_limit_patterns):
                missing.append(str(f.relative_to(sec_project)))

        assert not missing, (
            "SEC-09 FAIL — auth route files missing rate limiting:\n"
            + "\n".join(f"  {m}" for m in missing)
        )

    def test_rate_limit_config_exists(self, sec_project: Path) -> None:
        """A rate_limit.py or rate_limiter module must exist in generated code."""
        rate_files = (
            list(sec_project.rglob("rate_limit*.py"))
            + list(sec_project.rglob("rate_limiter*.py"))
            + list(sec_project.rglob("limiter.py"))
        )
        # Filter out tests and __pycache__
        rate_files = [
            f for f in rate_files
            if ".venv" not in str(f) and "__pycache__" not in str(f)
        ]
        assert rate_files, (
            "SEC-09 FAIL — no rate_limit*.py module found in generated project"
        )


# ---------------------------------------------------------------------------
# SEC-10 — Encryption at rest for sensitive data
# ---------------------------------------------------------------------------

class TestEncryptionAtRest:
    """SEC-10: ssn_encrypted / sensitive bytes fields must use Fernet or equivalent."""

    def test_fernet_or_crypto_imported(self, sec_project: Path) -> None:
        """The generated project must import Fernet or AES from cryptography."""
        crypto_patterns = ["from cryptography", "import cryptography", "Fernet", "AES"]
        found = False
        for f in _src_files(sec_project, exclude_tests=True):
            src = f.read_text(errors="replace")
            if any(p in src for p in crypto_patterns):
                found = True
                break
        assert found, (
            "SEC-10 FAIL — no cryptography/Fernet import found in generated code. "
            "ssn_encrypted fields require encryption at rest."
        )

    def test_encrypt_decrypt_functions_exist(self, sec_project: Path) -> None:
        """An encrypt() or decrypt() helper (or Fernet.encrypt) must exist."""
        patterns = [r"\bFernet\b", r"\bencrypt\b", r"\bdecrypt\b"]
        found = False
        for f in _src_files(sec_project, exclude_tests=True):
            src = f.read_text(errors="replace")
            if any(re.search(p, src) for p in patterns):
                found = True
                break
        assert found, (
            "SEC-10 FAIL — no encrypt/decrypt/Fernet usage found. "
            "Sensitive bytes fields must be encrypted."
        )


# ---------------------------------------------------------------------------
# SEC-11 — CSRF protection
# ---------------------------------------------------------------------------

class TestCsrfProtection:
    """SEC-11: If an admin panel is generated, CSRF middleware must exist."""

    def test_csrf_middleware_or_no_admin_panel(self, sec_project: Path) -> None:
        """CSRF protection is required if admin routes are generated."""
        admin_files = [
            f for f in _src_files(sec_project, exclude_tests=True)
            if "admin" in f.name.lower() or "admin" in str(f.parent).lower()
        ]
        if not admin_files:
            pytest.skip("No admin panel generated — CSRF check not applicable")

        csrf_patterns = ["CSRFMiddleware", "csrf_protect", "starlette_csrf", "fastapi_csrf"]
        csrf_found = False
        for f in _src_files(sec_project, exclude_tests=True):
            src = f.read_text(errors="replace")
            if any(p in src for p in csrf_patterns):
                csrf_found = True
                break

        assert csrf_found, (
            "SEC-11 FAIL — admin panel exists but no CSRF middleware found.\n"
            f"Admin files: {[str(f.relative_to(sec_project)) for f in admin_files]}"
        )


# ---------------------------------------------------------------------------
# SEC-12 — Secure cookie flags
# ---------------------------------------------------------------------------

class TestSecureCookieFlags:
    """SEC-12: Session cookies must have httponly, secure, and samesite."""

    def test_set_cookie_has_httponly(self, sec_project: Path) -> None:
        """set_cookie() calls must include httponly=True."""
        cookie_files = [
            f for f in _src_files(sec_project, exclude_tests=True)
            if "cookie" in f.read_text(errors="replace").lower()
            and "set_cookie" in f.read_text(errors="replace")
        ]
        if not cookie_files:
            pytest.skip("No set_cookie() calls in generated code")

        violations = []
        for f in cookie_files:
            src = f.read_text(errors="replace")
            lines = src.splitlines()
            for i, line in enumerate(lines):
                if "set_cookie" not in line:
                    continue
                # Collect the full call (may span several lines)
                chunk = "\n".join(lines[i : i + 5])
                if "httponly" not in chunk.lower():
                    rel = f.relative_to(sec_project)
                    violations.append(f"  {rel}:{i + 1}  {line.strip()}")

        assert not violations, (
            "SEC-12 FAIL — set_cookie() without httponly=True:\n"
            + "\n".join(violations)
        )

    def test_set_cookie_has_secure_flag(self, sec_project: Path) -> None:
        """set_cookie() calls must include secure=True."""
        cookie_files = [
            f for f in _src_files(sec_project, exclude_tests=True)
            if "set_cookie" in f.read_text(errors="replace")
        ]
        if not cookie_files:
            pytest.skip("No set_cookie() calls in generated code")

        violations = []
        for f in cookie_files:
            src = f.read_text(errors="replace")
            lines = src.splitlines()
            for i, line in enumerate(lines):
                if "set_cookie" not in line:
                    continue
                chunk = "\n".join(lines[i : i + 5])
                # Allow tests and conftest
                if not re.search(r'\bsecure\s*=\s*True\b', chunk):
                    rel = f.relative_to(sec_project)
                    violations.append(f"  {rel}:{i + 1}  {line.strip()}")

        assert not violations, (
            "SEC-12 FAIL — set_cookie() without secure=True:\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# SEC-13 — No raw SQL with f-strings via SQLAlchemy text()
# ---------------------------------------------------------------------------

class TestNoRawSqlFstring:
    """SEC-13: text() with f-string is a SQLi vector."""

    def test_no_text_fstring(self, sec_project: Path) -> None:
        """text(f\"...\") is unsafe — must be zero occurrences."""
        pattern = r"""text\s*\(\s*f["']"""
        hits = _collect_matches(sec_project, pattern)
        assert not hits, (
            "SEC-13 FAIL — text(f'...') found (SQL injection risk):\n"
            + _format_hits(hits, sec_project)
        )

    def test_no_execute_with_format_string(self, sec_project: Path) -> None:
        """execute() with % format string is unsafe."""
        pattern = r"""\.execute\s*\(\s*["'][^"']*(SELECT|INSERT|UPDATE|DELETE)"""
        hits = _collect_matches(sec_project, pattern, flags=re.IGNORECASE)
        # Filter out test files for raw string execute that's actually parameterised
        hits = [(f, l, line) for f, l, line in hits if "/tests/" not in str(f)]
        assert not hits, (
            "SEC-13 FAIL — raw .execute() with SQL string found:\n"
            + _format_hits(hits, sec_project)
        )


# ---------------------------------------------------------------------------
# SEC-14 — No directory listing via StaticFiles
# ---------------------------------------------------------------------------

class TestNoDirectoryListing:
    """SEC-14: StaticFiles must not have html=True (enables directory listing)."""

    def test_static_files_no_html_true(self, sec_project: Path) -> None:
        """StaticFiles(html=True) enables directory listing — must be zero."""
        pattern = r"""StaticFiles\s*\([^)]*html\s*=\s*True"""
        hits = _collect_matches(sec_project, pattern)
        assert not hits, (
            "SEC-14 FAIL — StaticFiles(html=True) found (enables dir listing):\n"
            + _format_hits(hits, sec_project)
        )


# ---------------------------------------------------------------------------
# SEC-15 — No user input reflected in error responses (XSS)
# ---------------------------------------------------------------------------

class TestNoXssInErrors:
    """SEC-15: Error handlers must not reflect user input verbatim."""

    _REFLECT_PATTERNS = [
        # Directly inserting request data into response body
        r"""["\{].*\{request\.(query_params|path_params|body|form|headers)\b""",
        r"""detail\s*=\s*f["'].*\{.*request\.""",
        r"""content\s*=\s*\{[^}]*"detail"\s*:\s*f["'].*\{""",
    ]

    def test_error_handlers_dont_reflect_request_data(self, sec_project: Path) -> None:
        """Error handlers must not embed raw request data in responses."""
        error_handler_files = [
            f for f in _src_files(sec_project, exclude_tests=True)
            if "error" in f.name.lower() or "exception" in f.name.lower()
        ]
        if not error_handler_files:
            # Check main.py for exception_handler decorators
            error_handler_files = list(sec_project.rglob("main.py"))

        violations = []
        for f in error_handler_files:
            src = f.read_text(errors="replace")
            for pattern in self._REFLECT_PATTERNS:
                for m in re.finditer(pattern, src, re.IGNORECASE):
                    lineno = src[: m.start()].count("\n") + 1
                    violations.append(
                        f"  {f.relative_to(sec_project)}:{lineno}  {m.group()[:80]}"
                    )

        assert not violations, (
            "SEC-15 FAIL — user input reflected in error responses (XSS):\n"
            + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# SEC-16 — BOLA: ownerless model docstrings must not claim 403 owner check
# Regression test for: generators/endpoints/crud_routes.py
# Bug: GET/{id} and DELETE/{id} for non-owner-scoped models emitted
#      "HTTPException 403: Not authorized (owner check)" in docstrings even
#      though no such check existed, creating a lying contract.
# ---------------------------------------------------------------------------

class TestOwnerlessModelDocstringHonesty:
    """SEC-16: Ownerless model routes must NOT claim a 403 owner check."""

    def _generate_ownerless_routes(self, tmp_path: Path) -> str:
        """Generate CRUD routes for an ownerless model and return the source."""
        sys.path.insert(0, str(_SKILL_ROOT))
        from generators.endpoints.crud_routes import generate_crud_routes  # noqa: PLC0415

        result = generate_crud_routes(
            output_dir=str(tmp_path),
            model_name="MedicalRecord",
            fields={"diagnosis": "str"},
            auth="required",
            owner_field=None,  # NOT owner-scoped
        )
        return Path(result["files_created"][0]).read_text()

    def _generate_owner_scoped_routes(self, tmp_path: Path) -> str:
        """Generate CRUD routes for an owner-scoped model and return the source."""
        sys.path.insert(0, str(_SKILL_ROOT))
        from generators.endpoints.crud_routes import generate_crud_routes  # noqa: PLC0415

        result = generate_crud_routes(
            output_dir=str(tmp_path),
            model_name="Pet",
            fields={"name": "str"},
            auth="required",
            owner_field="user",  # owner-scoped
        )
        return Path(result["files_created"][0]).read_text()

    def test_ownerless_read_route_no_false_403_claim(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """GET/{id} for ownerless model must NOT claim 'HTTPException 403: Not authorized'.

        Before the fix the docstring contained a literal '403: Not authorized (owner check)'
        claim even though no ownership check was present in the generated code — a lying contract.
        """
        if isinstance(tmp_path, pytest.TempPathFactory):
            base = tmp_path.mktemp("ownerless_read")
        else:
            base = tmp_path
        src = self._generate_ownerless_routes(base)
        # The route must not claim ownership-based 403
        assert "Not authorized (owner check)" not in src, (
            "SEC-16 FAIL — ownerless read route docstring falsely claims '403: Not authorized "
            "(owner check)'. Fix: generate honest docstring that matches actual code policy."
        )

    def test_ownerless_delete_route_no_false_403_claim(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """DELETE/{id} for ownerless model must NOT claim 'HTTPException 403: Not authorized'.

        Mirrors test_ownerless_read_route_no_false_403_claim for the DELETE endpoint.
        """
        if isinstance(tmp_path, pytest.TempPathFactory):
            base = tmp_path.mktemp("ownerless_delete")
        else:
            base = tmp_path
        src = self._generate_ownerless_routes(base)
        assert "Not authorized (owner check)" not in src, (
            "SEC-16 FAIL — ownerless delete route docstring falsely claims '403: Not authorized "
            "(owner check)'. Fix: generate honest docstring that matches actual code policy."
        )

    def test_ownerless_route_docstring_states_actual_policy(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Ownerless routes must declare the actual access policy in their docstring."""
        if isinstance(tmp_path, pytest.TempPathFactory):
            base = tmp_path.mktemp("ownerless_policy")
        else:
            base = tmp_path
        src = self._generate_ownerless_routes(base)
        # The generated docstring must explicitly state the real policy
        assert "no per-object ownership check is enforced" in src, (
            "SEC-16 FAIL — ownerless route docstring does not state that no per-object "
            "ownership check is enforced. Consumers need honest documentation."
        )

    def test_owner_scoped_route_still_has_403_claim(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """Owner-scoped models MUST still document the 403 in their docstrings.

        This is a non-regression check: the fix for ownerless models must not
        accidentally remove the correct 403 documentation from owner-scoped models.
        """
        if isinstance(tmp_path, pytest.TempPathFactory):
            base = tmp_path.mktemp("owner_scoped")
        else:
            base = tmp_path
        src = self._generate_owner_scoped_routes(base)
        assert "HTTPException 403" in src, (
            "SEC-16 NON-REGRESSION FAIL — owner-scoped model routes lost the 403 documentation. "
            "Owner-scoped routes must still document the ownership 403 check."
        )
        assert "does not own this record" in src, (
            "SEC-16 NON-REGRESSION FAIL — owner-scoped model routes lost ownership language "
            "in the 403 documentation."
        )


# ---------------------------------------------------------------------------
# SEC-17 — Token-type confusion: get_current_user must reject non-access tokens
# Regression test for: generators/auth/deps.py
# Bug: generated get_current_user accepted refresh and password_reset tokens
#      as bearer credentials because it never checked payload["type"].
# ---------------------------------------------------------------------------

class TestTokenTypeEnforcement:
    """SEC-17: Generated get_current_user must reject non-access JWT tokens."""

    def _generate_deps(self, tmp_path: Path) -> str:
        """Generate api/deps.py and return its source."""
        sys.path.insert(0, str(_SKILL_ROOT))
        from generators.auth.deps import generate_auth_deps  # noqa: PLC0415

        result = generate_auth_deps(output_dir=str(tmp_path))
        return Path(result["files_created"][0]).read_text()

    def test_get_current_user_checks_token_type(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """get_current_user must validate payload['type'] == 'access'.

        Without this check a long-lived refresh token (type='refresh') is
        silently accepted as a bearer credential on every protected endpoint,
        bypassing the short-lived nature of access tokens.
        """
        if isinstance(tmp_path, pytest.TempPathFactory):
            base = tmp_path.mktemp("deps_type_check")
        else:
            base = tmp_path
        src = self._generate_deps(base)
        # Check that type validation is present
        assert 'payload.get("type")' in src or "payload.get('type')" in src, (
            "SEC-17 FAIL — generated get_current_user does not check payload['type']. "
            "Refresh and password-reset tokens will be accepted as bearer credentials."
        )
        assert '"access"' in src or "'access'" in src, (
            "SEC-17 FAIL — generated get_current_user does not compare token type to 'access'. "
            "Token-type confusion attack vector is open."
        )

    def test_get_current_user_rejects_refresh_token_at_source_level(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """The generated code must raise _CREDENTIALS_ERROR for type != 'access'.

        This checks that the type comparison is tied to a 401 rejection path,
        not just an unreachable assertion.
        """
        if isinstance(tmp_path, pytest.TempPathFactory):
            base = tmp_path.mktemp("deps_reject_refresh")
        else:
            base = tmp_path
        src = self._generate_deps(base)
        # The rejection must follow the type check (both must appear in deps.py)
        assert "_CREDENTIALS_ERROR" in src, (
            "SEC-17 FAIL — _CREDENTIALS_ERROR constant missing from generated deps.py"
        )
        # Verify both the type check AND the credentials error appear in the same function
        get_user_fn_start = src.find("async def get_current_user(")
        get_user_fn_end = src.find("\nasync def ", get_user_fn_start + 1)
        if get_user_fn_end == -1:
            get_user_fn_end = src.find("\nCurrentUser", get_user_fn_start)
        get_user_body = src[get_user_fn_start:get_user_fn_end] if get_user_fn_end > 0 else src[get_user_fn_start:]
        assert ("type" in get_user_body and "access" in get_user_body), (
            "SEC-17 FAIL — type/access check not found inside get_current_user body."
        )
        assert "_CREDENTIALS_ERROR" in get_user_body, (
            "SEC-17 FAIL — _CREDENTIALS_ERROR not raised inside get_current_user body "
            "on token type mismatch."
        )

    def test_generated_deps_parses_as_valid_python(
        self, tmp_path: pytest.TempPathFactory
    ) -> None:
        """The generated deps.py must be syntactically valid Python after the fix."""
        import ast as _ast  # noqa: PLC0415
        if isinstance(tmp_path, pytest.TempPathFactory):
            base = tmp_path.mktemp("deps_syntax")
        else:
            base = tmp_path
        src = self._generate_deps(base)
        try:
            _ast.parse(src)
        except SyntaxError as exc:
            pytest.fail(f"SEC-17 FAIL — generated deps.py has syntax error: {exc}")


# ---------------------------------------------------------------------------
# Gate-precision unit tests — prove SEC-04 / SEC-08 stay STRICT on real cases
# and DO NOT fire on the known false positives.
# ---------------------------------------------------------------------------


class TestSecretComparePrecision:
    """SEC-04 rule (``_find_unsafe_secret_compares``) precision."""

    def test_flags_real_python_secret_equality(self) -> None:
        """A pure-Python ``password == x`` MUST be flagged."""
        src = "def f(password, supplied):\n    return password == supplied\n"
        hits = _find_unsafe_secret_compares(src)
        assert hits and hits[0][1] == "password", hits

    def test_flags_real_token_inequality(self) -> None:
        """``token != expected`` is also timing-unsafe and MUST be flagged."""
        src = "def f(token, expected):\n    return token != expected\n"
        assert _find_unsafe_secret_compares(src), "expected != on token to flag"

    def test_flags_self_secret_attribute(self) -> None:
        """``self.secret == other`` (real Python attr) MUST be flagged."""
        src = "class C:\n    def chk(self, other):\n        return self.secret == other\n"
        assert _find_unsafe_secret_compares(src), "expected self.secret to flag"

    def test_ignores_orm_column_comparison(self) -> None:
        """``Model.code_hash == code_hash`` builds a SQL WHERE — NOT flagged."""
        src = (
            "def q(code_hash):\n"
            "    return update(MFARecoveryCode).where(\n"
            "        MFARecoveryCode.code_hash == code_hash,\n"
            "    )\n"
        )
        assert _find_unsafe_secret_compares(src) == [], _find_unsafe_secret_compares(src)

    def test_ignores_none_check(self) -> None:
        """``token == None`` is an identity check — NOT flagged."""
        src = "def f(token):\n    return token == None\n"
        assert _find_unsafe_secret_compares(src) == []

    def test_ignores_metadata_suffix(self) -> None:
        """``token_type == 'bearer'`` is metadata, not a secret — NOT flagged."""
        src = "def f(token_type):\n    return token_type == 'bearer'\n"
        assert _find_unsafe_secret_compares(src) == []

    def test_ignores_secret_in_docstring(self) -> None:
        """A ``password == x`` written inside a docstring is NOT real code."""
        src = '"""Example: password == supplied is unsafe."""\nX = 1\n'
        assert _find_unsafe_secret_compares(src) == []


class TestPrintCallPrecision:
    """SEC-08 rule (``_find_real_print_calls``) precision."""

    def test_flags_real_top_level_print(self) -> None:
        """A genuine top-level ``print(...)`` call MUST be flagged."""
        src = "x = 1\nprint(x)\n"
        assert _find_real_print_calls(src) == [2]

    def test_flags_print_inside_function(self) -> None:
        """A ``print(...)`` in a function body MUST be flagged."""
        src = "def f():\n    print('debug')\n"
        assert _find_real_print_calls(src) == [2]

    def test_ignores_print_in_module_docstring(self) -> None:
        """``print(`` inside a docstring example (python -c snippet) — NOT flagged."""
        src = '"""Generate a key::\n\n    python -c "from x import Y; print(Y.gen())"\n"""\nZ = 1\n'
        assert _find_real_print_calls(src) == []

    def test_ignores_print_in_string_literal(self) -> None:
        """``print(`` inside an ordinary string literal — NOT flagged."""
        src = 'msg = "use print(x) to debug"\n'
        assert _find_real_print_calls(src) == []

    def test_ignores_print_in_comment(self) -> None:
        """``print(`` inside a comment — NOT flagged."""
        src = "x = 1  # could print(x) here\n"
        assert _find_real_print_calls(src) == []


# ---------------------------------------------------------------------------
# Standalone runner — produces the final score line
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import subprocess
    import os

    skill_root = Path(__file__).parent.parent
    env = {**os.environ, "PYTHONPATH": str(skill_root)}

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(Path(__file__)),
            "-v",
            "--tb=short",
            "--no-header",
            "-q",
        ],
        cwd=str(skill_root),
        env=env,
        capture_output=False,
        text=True,
    )

    # Parse summary
    sys.exit(proc.returncode)

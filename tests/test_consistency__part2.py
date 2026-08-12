"""test_consistency__part2.py — Pydantic schema + route consistency.

Split out of tests/test_consistency.py (BRUTAL hardening round 4C) to keep
every sibling file under the 500-LOC cap. Carries Test 3 (Pydantic schema
hygiene) and Test 4 (route registration sanity).
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

from tests.test_consistency__shared import (  # noqa: E402
    _call_name,
    _collect_py_files,
    _extract_type_names,
    _get_base_names,
    _parse_file,
)

# ---------------------------------------------------------------------------
# Test 3 — Pydantic schema consistency
# ---------------------------------------------------------------------------


def _collect_pydantic_schemas(app_dir: Path) -> dict[str, dict]:
    """Collect all FastAPI-compatible response model classes via AST.

    Searches all of app/ (not just app/schemas/) because tools like
    add_audit_log generate @dataclass response types in app/core/.

    Covers:
    - Direct/indirect BaseModel subclasses (Pydantic v2)
    - @dataclass classes (FastAPI supports them natively via dataclasses module)
    - TypedDict / NamedTuple subclasses

    Returns:
        Dict {ClassName: {"file": Path, "fields": [str], "base_names": [str],
                          "is_dataclass": bool}}
    """
    raw_classes: dict[str, dict] = {}
    py_files = _collect_py_files(app_dir, exclude_dirs={"__pycache__"})

    for py_file in py_files:
        try:
            tree = _parse_file(py_file)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = _get_base_names(node)
            fields: list[str] = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.append(item.target.id)
            # Detect @dataclass decorator in one pass
            is_dc = any(
                (isinstance(d, ast.Name) and d.id == "dataclass")
                or (isinstance(d, ast.Attribute) and d.attr == "dataclass")
                for d in node.decorator_list
            )
            raw_classes[node.name] = {
                "file": py_file,
                "fields": fields,
                "base_names": base_names,
                "is_dataclass": is_dc,
            }

    def _is_response_model_compatible(class_name: str, visited: set[str] | None = None) -> bool:
        """Return True if class_name can be used as a FastAPI response_model."""
        if visited is None:
            visited = set()
        if class_name in visited:
            return False
        visited.add(class_name)
        if class_name in {"BaseModel", "BaseSettings", "RootModel", "TypedDict", "NamedTuple"}:
            return True
        info = raw_classes.get(class_name)
        if info is None:
            return False
        if info.get("is_dataclass"):
            return True
        return any(_is_response_model_compatible(bn, visited) for bn in info["base_names"])

    return {name: info for name, info in raw_classes.items() if _is_response_model_compatible(name)}


# Sensitive field patterns — "Public" schemas MUST NOT expose these
_SENSITIVE_FIELD_PATTERNS = re.compile(
    r"^(password|hashed_password|password_hash|hash|secret|encrypted|"
    r"secret_key|token|private_key|api_secret)$",
    re.IGNORECASE,
)


def _collect_response_model_names(app_dir: Path) -> list[tuple[str, str, Path]]:
    """Find all response_model=<Name> usages in route files.

    Handles: response_model=Name, response_model=list[Name],
    response_model=Optional[Name], response_model=dict (builtins).

    Returns:
        List of (schema_name, route_function_name, file_path).
    """
    results: list[tuple[str, str, Path]] = []
    route_dirs = [app_dir / "api" / "routes", app_dir / "routes"]

    for route_dir in route_dirs:
        if not route_dir.exists():
            continue
        for py_file in _collect_py_files(route_dir, exclude_dirs={"__pycache__"}):
            try:
                tree = _parse_file(py_file)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    for kw in decorator.keywords:
                        if kw.arg != "response_model":
                            continue
                        for name in _extract_type_names(kw.value):
                            results.append((name, node.name, py_file))
    return results


def check_3_schema_consistency(project_dir: Path) -> tuple[bool, str]:
    """Test 3: Pydantic schema hygiene.

    Returns:
        (passed, message)
    """
    app_dir = project_dir / "app"
    schemas = _collect_pydantic_schemas(app_dir)
    failures: list[str] = []

    schemas_dir = app_dir / "schemas"

    # (a) No duplicate schema names within app/schemas/ files
    seen: dict[str, Path] = {}
    for name, info in schemas.items():
        # Only check within app/schemas/ for duplicates
        try:
            info["file"].relative_to(schemas_dir)
        except ValueError:
            continue
        if name in seen:
            failures.append(
                f"Duplicate schema '{name}' in {info['file'].name} and {seen[name].name}"
            )
        seen[name] = info["file"]

    # (b) 'Public' schemas (in app/schemas/) must not expose sensitive fields
    for name, info in schemas.items():
        if not name.endswith("Public"):
            continue
        # Only check classes defined in app/schemas/ — other 'Public' classes
        # (e.g. dataclasses in app/core/) are not API response schemas.
        try:
            info["file"].relative_to(schemas_dir)
        except ValueError:
            continue
        for field in info["fields"]:
            if _SENSITIVE_FIELD_PATTERNS.match(field):
                rel = info["file"].relative_to(project_dir)
                failures.append(
                    f"{name} ({rel}): 'Public' schema exposes sensitive field '{field}'"
                )

    # (c) Every schema used as response_model= must exist in the known schema set
    response_model_usages = _collect_response_model_names(app_dir)
    all_known_schemas = set(schemas.keys())
    # Builtins and well-known non-schema names that can appear in response_model
    _ALLOWED_NON_SCHEMA = {  # noqa: N806 -- SCREAMING_CASE constant set, scoped to this test
        "dict",
        "list",
        "None",
        "Any",
        "bool",
        "str",
        "int",
        "float",
        "bytes",
        "set",
        "tuple",
    }
    for schema_name, fn_name, route_file in response_model_usages:
        if schema_name in _ALLOWED_NON_SCHEMA:
            continue
        if schema_name not in all_known_schemas:
            rel = route_file.relative_to(project_dir)
            failures.append(
                f"{fn_name} ({rel}): response_model={schema_name!r} not found in app/schemas/"
            )

    if failures:
        detail = "\n  ".join(failures)
        return False, f"Schema consistency failures ({len(failures)}):\n  {detail}"

    return True, (
        f"All {len(schemas)} schemas consistent; "
        f"{len(response_model_usages)} response_model references verified; "
        "no sensitive fields in Public schemas"
    )


# ---------------------------------------------------------------------------
# Test 4 — Route consistency
# ---------------------------------------------------------------------------

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Type-alias names for injected dependencies — these count as Depends() for
# our purposes (they are Annotated[T, Depends(...)] at module level).
_DEPENDS_ALIAS_PATTERNS = re.compile(
    r"^(CurrentUser|CurrentSuperuser|CurrentAuditor|SessionDep|"
    r"AsyncSessionDep|get_async_session|DBSession|"
    r"Current[A-Z][A-Za-z]+|[A-Z][A-Za-z]+Dep)$"
)


def _collect_routes(app_dir: Path) -> list[dict]:
    """Collect all route handlers via AST, including router prefix.

    Returns:
        List of dicts: method, path, full_path, fn_name, is_async,
        has_depends, file.
    """
    routes: list[dict] = []
    route_dirs = [app_dir / "api" / "routes", app_dir / "routes"]

    for route_dir in route_dirs:
        if not route_dir.exists():
            continue
        for py_file in _collect_py_files(route_dir, exclude_dirs={"__pycache__"}):
            # Skip underscore-prefixed reference/example modules (e.g.
            # ``_mfa_example.py``). By Python convention these are private and
            # the generated ``app/routes/__init__.py`` never imports/mounts
            # them — they are copy-paste references explicitly marked "remove
            # before prod", so their handlers are dead code, not live routes.
            # Scanning them produces false "missing Depends()" failures.
            if py_file.name.startswith("_") and py_file.name != "__init__.py":
                continue
            try:
                tree = _parse_file(py_file)
            except SyntaxError:
                continue

            # Extract router prefix from APIRouter(prefix="...")
            router_prefix = _extract_router_prefix(tree)

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    func = decorator.func
                    if not isinstance(func, ast.Attribute):
                        continue
                    method = func.attr.lower()
                    if method not in _HTTP_METHODS:
                        continue
                    # Extract path (first positional arg)
                    path: str | None = None
                    if decorator.args and isinstance(decorator.args[0], ast.Constant):
                        path = decorator.args[0].value
                    if path is None:
                        continue

                    # Build fully-qualified path for duplicate detection
                    full_path = (router_prefix.rstrip("/") + "/" + path.lstrip("/")).rstrip(
                        "/"
                    ) or "/"

                    # Check for Depends in function params (direct or via alias)
                    has_depends = _fn_has_depends_param(node)

                    # Check decorator-level dependencies=[Depends(...)]
                    has_dec_depends = _decorator_has_depends(decorator)

                    routes.append(
                        {
                            "method": method,
                            "path": path,
                            "full_path": full_path,
                            "fn_name": node.name,
                            "is_async": isinstance(node, ast.AsyncFunctionDef),
                            "has_depends": has_depends or has_dec_depends,
                            "file": py_file,
                        }
                    )

    return routes


def _extract_router_prefix(tree: ast.Module) -> str:
    """Extract the prefix= string from APIRouter(prefix=...) calls."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = _call_name(node.func)
        if func_name != "APIRouter":
            continue
        for kw in node.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
    return ""


def _fn_has_depends_param(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function has any Depends-related parameter.

    Detects:
    - param: SomeType = Depends(...)
    - param: Annotated[T, Depends(...)]  (raw form)
    - param: CurrentUser / SessionDep / etc. (well-known aliases)
    """
    all_args = node.args.args + node.args.kwonlyargs
    all_defaults = node.args.defaults + node.args.kw_defaults

    for arg in all_args:
        ann = arg.annotation
        if ann is None:
            continue
        # Alias patterns: CurrentUser, SessionDep, etc.
        if isinstance(ann, ast.Name) and _DEPENDS_ALIAS_PATTERNS.match(ann.id):
            return True
        # Annotated[T, Depends(...)]: ast.Subscript where .slice is a Tuple
        if isinstance(ann, ast.Subscript) and isinstance(ann.slice, ast.Tuple):
            for elt in ann.slice.elts:
                if isinstance(elt, ast.Call) and _call_name(elt.func) == "Depends":
                    return True

    for default in all_defaults:
        if default is None:
            continue
        if isinstance(default, ast.Call) and _call_name(default.func) == "Depends":
            return True

    return False


def _decorator_has_depends(decorator: ast.Call) -> bool:
    """Check if a route decorator has dependencies=[Depends(...)]."""
    for kw in decorator.keywords:
        if kw.arg == "dependencies" and isinstance(kw.value, ast.List):
            for elt in kw.value.elts:
                if isinstance(elt, ast.Call) and _call_name(elt.func) == "Depends":
                    return True
    return False


def check_4_route_consistency(project_dir: Path) -> tuple[bool, str]:
    """Test 4: Route registration sanity checks.

    Returns:
        (passed, message)
    """
    app_dir = project_dir / "app"
    routes = _collect_routes(app_dir)
    failures: list[str] = []

    # (a) No duplicate method + full_path combinations (within the same file)
    # We check per-file to avoid false positives from routers that get
    # mounted at different prefixes in main.py.
    file_route_map: dict[Path, dict[tuple[str, str], str]] = defaultdict(dict)
    for route in routes:
        key = (route["method"].upper(), route["full_path"])
        existing = file_route_map[route["file"]].get(key)
        if existing is not None and existing != route["fn_name"]:
            rel = route["file"].relative_to(project_dir)
            failures.append(
                f"Duplicate route {key[0]} {key[1]!r} in {rel}: "
                f"'{route['fn_name']}' conflicts with '{existing}'"
            )
        else:
            file_route_map[route["file"]][key] = route["fn_name"]

    # (b) All handlers must be async def
    for route in routes:
        if not route["is_async"]:
            rel = route["file"].relative_to(project_dir)
            failures.append(
                f"{route['fn_name']} ({rel}): {route['method'].upper()} {route['path']!r} "
                "is sync def — should be async def"
            )

    # (c) Mutating methods (POST/PATCH/DELETE) must have Depends-based injection
    # Exception: public endpoints like /signup, /password-recovery, /login, webhooks
    _PUBLIC_PATH_PATTERNS = re.compile(  # noqa: N806 -- SCREAMING_CASE constant pattern, scoped to this test
        r"(/signup|/login|/password-recovery|/reset-password|/access-token|"
        r"/incoming/|/refresh)"
    )
    for route in routes:
        if route["method"] not in {"post", "patch", "delete"}:
            continue
        if route["has_depends"]:
            continue
        if _PUBLIC_PATH_PATTERNS.search(route["full_path"]):
            continue  # Public endpoints legitimately have no auth
        rel = route["file"].relative_to(project_dir)
        failures.append(
            f"{route['fn_name']} ({rel}): "
            f"{route['method'].upper()} {route['path']!r} has no Depends() injection "
            "— mutating handlers should use auth/session via Depends"
        )

    if failures:
        detail = "\n  ".join(failures)
        return False, f"Route consistency failures ({len(failures)}):\n  {detail}"

    return True, (
        f"All {len(routes)} routes consistent "
        f"(no duplicates, all async, mutating handlers have Depends)"
    )

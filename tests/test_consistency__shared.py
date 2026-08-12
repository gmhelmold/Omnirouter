"""test_consistency__shared.py — shared setup for consistency test split.

Houses the module-level path setup, the 10-tool fixture builder, and the
generic AST helpers reused across the split test parts. Split out of the
original tests/test_consistency.py (BRUTAL hardening round 4C) to keep every
sibling file under the 500-LOC cap.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root & path setup
# ---------------------------------------------------------------------------

_SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

from adapt.contracts import ToolInput  # noqa: E402
from tests.common.fixture_factory import create_fixture_project  # noqa: E402

# ---------------------------------------------------------------------------
# 10-tool fixture
# ---------------------------------------------------------------------------

TOOLS_10: list[tuple[str, str, str]] = [
    ("add_soft_delete", "adapt.extend.crud_data.add_soft_delete", "add_soft_delete"),
    ("add_multi_tenancy", "adapt.extend.auth_access.add_multi_tenancy", "add_multi_tenancy"),
    ("add_rbac", "adapt.extend.auth_access.add_rbac", "add_rbac"),
    ("add_mfa", "adapt.extend.auth_access.add_mfa", "add_mfa"),
    ("add_api_key_auth", "adapt.extend.auth_access.add_api_key_auth", "add_api_key_auth"),
    ("add_audit_log", "adapt.extend.crud_data.add_audit_log", "add_audit_log"),
    ("add_cache_layer", "adapt.extend.infrastructure.add_cache_layer", "add_cache_layer"),
    ("add_sse", "adapt.extend.realtime.add_sse", "add_sse"),
    ("add_webhook_receiver", "adapt.extend.realtime.add_webhook_receiver", "add_webhook_receiver"),
    ("add_search", "adapt.extend.crud_data.add_search", "add_search"),
]


def _build_10_tool_project(tmp_dir: Path) -> Path:
    """Generate base project and apply all 10 tools.

    Returns:
        Path to the generated project root.
    """
    project_dir = create_fixture_project(name="consistency_test", tmp_dir=tmp_dir)
    for _name, mod_path, fn_name in TOOLS_10:
        mod = importlib.import_module(mod_path)
        fn = getattr(mod, fn_name)
        result = fn(ToolInput(project_dir=str(project_dir)))
        if result.status == "error":
            raise RuntimeError(f"Tool {_name} failed: {result.error}")
    return project_dir


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _parse_file(path: Path) -> ast.Module:
    """Parse a Python file to AST, raising SyntaxError with path context."""
    source = path.read_text(encoding="utf-8")
    try:
        return ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise SyntaxError(f"SyntaxError in {path}: {exc}") from exc


def _collect_py_files(directory: Path, *, exclude_dirs: set[str] | None = None) -> list[Path]:
    """Return all .py files under directory, excluding given subdirectory names."""
    excludes = exclude_dirs or set()
    return [
        p for p in sorted(directory.rglob("*.py")) if not any(part in excludes for part in p.parts)
    ]


def _module_key(py_file: Path, root: Path) -> str:
    """Convert a file path to a dotted module key relative to root."""
    rel = py_file.relative_to(root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1][:-3]  # strip .py
    return ".".join(parts)


def _get_base_names(node: ast.ClassDef) -> list[str]:
    """Extract simple base class names from a ClassDef node."""
    names: list[str] = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            names.append(base.id)
        elif isinstance(base, ast.Attribute):
            names.append(base.attr)
    return names


def _call_name(func_node: ast.expr) -> str:
    """Extract the function name from a Call's func node."""
    if isinstance(func_node, ast.Name):
        return func_node.id
    if isinstance(func_node, ast.Attribute):
        return func_node.attr
    return ""


def _extract_type_names(node: ast.expr) -> list[str]:
    """Recursively extract simple Name nodes from a type expression."""
    names: list[str] = []
    if isinstance(node, ast.Name):
        names.append(node.id)
    elif isinstance(node, ast.Attribute):
        names.append(node.attr)
    elif isinstance(node, ast.Subscript):
        # list[X], Optional[X], dict[str, X], etc.
        names.extend(_extract_type_names(node.slice))
        # Also check the container itself (e.g., "list" is a builtin — skip)
    elif isinstance(node, ast.Tuple):
        for elt in node.elts:
            names.extend(_extract_type_names(elt))
    return names

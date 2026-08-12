"""test_consistency__part1.py — import-cycle + SQLAlchemy model consistency.

Split out of tests/test_consistency.py (BRUTAL hardening round 4C) to keep
every sibling file under the 500-LOC cap. Carries Test 1 (import cycle
detection via Tarjan SCC) and Test 2 (SQLAlchemy model sanity).
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests.test_consistency__shared import (  # noqa: E402
    _call_name,
    _collect_py_files,
    _get_base_names,
    _module_key,
    _parse_file,
)

# ---------------------------------------------------------------------------
# Test 1 — Import cycle detection (Tarjan SCC)
# ---------------------------------------------------------------------------


def _build_import_graph(app_dir: Path) -> dict[str, set[str]]:
    """Build an intra-app import graph from AST.

    Returns:
        Adjacency dict {module_key: set of imported module_keys}.
        Only edges within app/ are recorded (no stdlib/third-party).
    """
    py_files = _collect_py_files(app_dir, exclude_dirs={"__pycache__"})
    all_modules = {_module_key(f, app_dir.parent): f for f in py_files}
    graph: dict[str, set[str]] = {m: set() for m in all_modules}

    for mod_key, py_file in all_modules.items():
        try:
            tree = _parse_file(py_file)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name
                    if target in graph:
                        graph[mod_key].add(target)
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                # Handle relative imports
                if node.level and node.level > 0:
                    parts = mod_key.split(".")
                    base_parts = parts[: max(0, len(parts) - node.level)]
                    if node.module:
                        abs_module = ".".join(base_parts + node.module.split("."))
                    else:
                        abs_module = ".".join(base_parts)
                else:
                    abs_module = node.module

                if abs_module in graph:
                    graph[mod_key].add(abs_module)
                else:
                    # from app.models import Base → abs_module = "app.models"
                    for known in graph:
                        if known == abs_module or known.startswith(abs_module + "."):
                            graph[mod_key].add(known)

    return graph


def _tarjan_scc(graph: dict[str, set[str]]) -> list[list[str]]:
    """Tarjan's algorithm for strongly connected components.

    Returns:
        List of SCCs with more than one node (= cycles).
    """
    index_counter = [0]
    stack: list[str] = []
    lowlink: dict[str, int] = {}
    index: dict[str, int] = {}
    on_stack: dict[str, bool] = {}
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        index[v] = lowlink[v] = index_counter[0]
        index_counter[0] += 1
        stack.append(v)
        on_stack[v] = True

        for w in graph.get(v, set()):
            if w not in index:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif on_stack.get(w, False):
                lowlink[v] = min(lowlink[v], index[w])

        if lowlink[v] == index[v]:
            scc: list[str] = []
            while True:
                w = stack.pop()
                on_stack[w] = False
                scc.append(w)
                if w == v:
                    break
            sccs.append(scc)

    for v in list(graph.keys()):
        if v not in index:
            strongconnect(v)

    return [scc for scc in sccs if len(scc) > 1]


def check_1_no_import_cycles(project_dir: Path) -> tuple[bool, str]:
    """Test 1: Zero circular imports inside app/.

    Returns:
        (passed, message)
    """
    app_dir = project_dir / "app"
    graph = _build_import_graph(app_dir)
    cycles = _tarjan_scc(graph)

    if cycles:
        cycle_details = "\n".join(
            f"  Cycle {i + 1}: {' <-> '.join(sorted(c))}" for i, c in enumerate(cycles)
        )
        return False, f"Found {len(cycles)} import cycle(s):\n{cycle_details}"

    node_count = len(graph)
    edge_count = sum(len(v) for v in graph.values())
    return True, f"No import cycles detected ({node_count} modules, {edge_count} edges)"


# ---------------------------------------------------------------------------
# Test 2 — SQLAlchemy model consistency
# ---------------------------------------------------------------------------


def _collect_sa_models(app_dir: Path) -> dict[str, dict]:
    """Collect all SQLAlchemy models (classes inheriting Base) via AST.

    Also collects mixin classes so we can detect what base classes are
    SQLAlchemy-related (even through mixin intermediate classes).

    Returns:
        Dict {ClassName: {"file": Path, "tablename": str|None,
                          "columns": [str], "fk_refs": [(col, ref_table)],
                          "has_metadata_field": bool}}
    """
    # First pass: collect ALL class definitions to build inheritance map
    all_classes: dict[str, dict] = {}
    py_files = _collect_py_files(app_dir / "models", exclude_dirs={"__pycache__"})

    for py_file in py_files:
        try:
            tree = _parse_file(py_file)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            base_names = _get_base_names(node)
            tablename: str | None = None
            columns: list[str] = []
            fk_refs: list[tuple[str, str]] = []
            has_metadata_field = False

            for item in node.body:
                # __tablename__ = "..."
                if isinstance(item, ast.Assign):
                    for tgt in item.targets:
                        if (
                            isinstance(tgt, ast.Name)
                            and tgt.id == "__tablename__"
                            and isinstance(item.value, ast.Constant)
                        ):
                            tablename = item.value.value
                        if isinstance(tgt, ast.Name) and tgt.id == "metadata":
                            has_metadata_field = True

                # __tablename__: str = "..." (annotated)
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    if (
                        item.target.id == "__tablename__"
                        and item.value
                        and isinstance(item.value, ast.Constant)
                    ):
                        tablename = item.value.value
                    col_name = item.target.id
                    if col_name not in ("__tablename__",):
                        columns.append(col_name)
                    if col_name == "metadata":
                        has_metadata_field = True
                    # Check for ForeignKey inside mapped_column(...)
                    if item.value:
                        for subnode in ast.walk(item.value):
                            if isinstance(subnode, ast.Call):
                                fname = _call_name(subnode.func)
                                if fname == "ForeignKey":
                                    for arg in subnode.args:
                                        if isinstance(arg, ast.Constant) and isinstance(
                                            arg.value, str
                                        ):
                                            ref_table = arg.value.split(".")[0]
                                            fk_refs.append((col_name, ref_table))

            all_classes[node.name] = {
                "file": py_file,
                "tablename": tablename,
                "columns": columns,
                "fk_refs": fk_refs,
                "has_metadata_field": has_metadata_field,
                "base_names": base_names,
            }

    # Second pass: identify which classes are ORM models (inherit from Base,
    # directly or transitively through mixins)
    def _inherits_base(class_name: str, visited: set[str] | None = None) -> bool:
        if visited is None:
            visited = set()
        if class_name in visited:
            return False
        visited.add(class_name)
        info = all_classes.get(class_name)
        if info is None:
            return class_name == "Base"
        for bn in info["base_names"]:
            if bn == "Base":
                return True
            if _inherits_base(bn, visited):
                return True
        return False

    models: dict[str, dict] = {}
    for name, info in all_classes.items():
        if "Mixin" in name:
            continue
        if _inherits_base(name):
            models[name] = info

    return models


def check_2_model_consistency(project_dir: Path) -> tuple[bool, str]:
    """Test 2: SQLAlchemy model sanity checks.

    Returns:
        (passed, message with all failures)
    """
    app_dir = project_dir / "app"
    models = _collect_sa_models(app_dir)
    failures: list[str] = []

    # Build tablename → model map for FK validation
    tablename_to_model: dict[str, str] = {}
    for name, info in models.items():
        if info["tablename"]:
            tablename_to_model[info["tablename"]] = name

    seen_names: dict[str, Path] = {}
    for name, info in models.items():
        rel = info["file"].relative_to(project_dir)

        # (a) No duplicate model names
        if name in seen_names:
            failures.append(f"Duplicate model name '{name}' in {rel} and {seen_names[name]}")
        seen_names[name] = info["file"]

        # (b) Every model must have __tablename__
        if info["tablename"] is None:
            failures.append(f"{name} ({rel}): missing __tablename__")

        # (c) Every FK must reference a table that exists
        for col_name, ref_table in info["fk_refs"]:
            if ref_table not in tablename_to_model:
                failures.append(
                    f"{name}.{col_name} ({rel}): FK references table "
                    f"'{ref_table}' which has no corresponding model"
                )

        # (d) No field named 'metadata' (reserved by SQLAlchemy DeclarativeBase)
        if info["has_metadata_field"]:
            failures.append(
                f"{name} ({rel}): field named 'metadata' conflicts with "
                "SQLAlchemy's reserved DeclarativeBase attribute"
            )

    if failures:
        detail = "\n  ".join(failures)
        return False, f"Model consistency failures ({len(failures)}):\n  {detail}"

    return True, (
        f"All {len(models)} models consistent (tablenames OK, FKs OK, no 'metadata' field)"
    )

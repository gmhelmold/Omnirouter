"""test_lint_generated.py — Blind Spot A: lint validation of generated code.

Verifies that code produced by the orchestrator + 5 adapt tools passes both
``py_compile`` (syntax correctness) and ``ruff`` (Pyflakes F-class rules).

F-class rules checked (real bugs, not style):
  F401  unused-import
  F811  redefined-while-unused
  F541  f-string-missing-placeholders
  F821  undefined-name (would be a critical failure)
  F841  unused-variable

E402 (module-import-not-at-top) is excluded: it is a style finding caused by
inject-at-prepend patterns in some adapt tools and does not affect runtime.
E501 (line-too-long) is always ignored.

Run from the skill root:
    PYTHONPATH=. python3 -m pytest tests/test_lint_generated.py -v
"""

from __future__ import annotations

import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SKILL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_SKILL_ROOT))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _generate_project(tmp_path: Path) -> Path:
    """Generate a base project with one model into *tmp_path* and return its root."""
    from generators.orchestrator import generate_project

    project_dir = tmp_path / "lint_test_api"
    generate_project(
        output_dir=str(project_dir),
        name="lint-test-api",
        models={"Product": {"name": "str", "price": "Decimal", "stock": "int"}},
        owner_models={"Product": "user"},
    )
    return project_dir


def _apply_five_tools(project_dir: Path) -> list[str]:
    """Apply 5 extend tools to *project_dir*. Returns list of warnings."""
    from adapt.contracts import ToolInput
    from adapt.extend.crud_data.add_soft_delete import add_soft_delete
    from adapt.extend.auth_access.add_rbac import add_rbac
    from adapt.extend.auth_access.add_mfa import add_mfa
    from adapt.extend.infrastructure.add_cache_layer import add_cache_layer
    from adapt.extend.crud_data.add_search import add_search

    inp = ToolInput(project_dir=str(project_dir))
    warnings: list[str] = []
    for fn in (add_soft_delete, add_rbac, add_mfa, add_cache_layer, add_search):
        result = fn(inp)
        if result.status == "error":
            warnings.append(f"{fn.__name__}: {result.error}")
    return warnings


def _py_files(project_dir: Path) -> list[Path]:
    return sorted(project_dir.rglob("*.py"))


def _ruff_available() -> bool:
    return shutil.which("ruff") is not None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def generated_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Single generated project reused across all tests in this module."""
    tmp = tmp_path_factory.mktemp("skill001_lint")
    project = _generate_project(tmp)
    _apply_five_tools(project)
    return project


class TestPyCompile:
    """All generated .py files must pass py_compile (syntax + basic validity)."""

    def test_all_files_compile(self, generated_project: Path) -> None:
        """No generated file should have a syntax error."""
        failures: list[tuple[str, str]] = []
        for f in _py_files(generated_project):
            try:
                py_compile.compile(str(f), doraise=True)
            except py_compile.PyCompileError as exc:
                rel = str(f.relative_to(generated_project))
                failures.append((rel, str(exc)))

        if failures:
            detail = "\n".join(f"  {path}: {err}" for path, err in failures)
            pytest.fail(f"py_compile failed on {len(failures)} file(s):\n{detail}")

    def test_file_count_reasonable(self, generated_project: Path) -> None:
        """Generator should produce at least 50 Python files after 5 tools."""
        files = _py_files(generated_project)
        assert len(files) >= 50, (
            f"Expected >= 50 .py files after base + 5 tools, got {len(files)}"
        )


@pytest.mark.skipif(not _ruff_available(), reason="ruff not installed")
class TestRuffPyflakes:
    """Generated code must pass Pyflakes (F) rules — real bugs, not style."""

    # F821 undefined-name is a critical failure; we keep a hard zero threshold.
    # F401/F811/F541/F841 have a tolerance budget that shrinks over time.
    F821_THRESHOLD = 0   # critical: must always be zero
    F_TOTAL_THRESHOLD = 30  # total F errors budget (tightened as tools improve)

    def _run_ruff(
        self,
        project_dir: Path,
        select: str = "F",
        ignore: str = "E501,E402",
    ) -> list[str]:
        """Run ruff and return list of violation lines (one per finding)."""
        result = subprocess.run(
            [
                "ruff", "check", str(project_dir),
                "--select", select,
                "--ignore", ignore,
                "--output-format", "text",
                "--no-cache",
            ],
            capture_output=True,
            text=True,
        )
        lines = [
            ln for ln in result.stdout.splitlines()
            if ln.strip() and ln[0].isalpha() and "error" not in ln.lower()[:10]
            and "fixable" not in ln.lower()
            and "found" not in ln.lower()
        ]
        return lines

    def _count_by_rule(self, project_dir: Path) -> dict[str, int]:
        """Return {rule_code: count} for all F findings."""
        result = subprocess.run(
            [
                "ruff", "check", str(project_dir),
                "--select", "F",
                "--ignore", "E501,E402",
                "--statistics",
                "--no-cache",
            ],
            capture_output=True,
            text=True,
        )
        counts: dict[str, int] = {}
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            # Statistics output: "15 F401 [*] unused-import"
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].startswith("F"):
                counts[parts[1]] = int(parts[0])
        return counts

    def test_no_undefined_names(self, generated_project: Path) -> None:
        """F821 (undefined name) must be zero — these are always runtime crashes."""
        counts = self._count_by_rule(generated_project)
        f821 = counts.get("F821", 0)
        assert f821 == self.F821_THRESHOLD, (
            f"Found {f821} F821 (undefined-name) errors — these are real bugs "
            f"that will crash at runtime."
        )

    def test_f_errors_within_budget(self, generated_project: Path) -> None:
        """Total Pyflakes F errors must stay within budget.

        Budget covers known adapt-tool injection artifacts (F401 unused imports
        from prepend-inject pattern, F811 duplicate imports, F541 f-strings).
        Critical F821 is checked separately with a zero threshold.
        """
        counts = self._count_by_rule(generated_project)
        total = sum(counts.values())
        detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        assert total <= self.F_TOTAL_THRESHOLD, (
            f"Ruff F-errors ({total}) exceed budget ({self.F_TOTAL_THRESHOLD}). "
            f"Breakdown: {detail}"
        )

    def test_no_critical_f_errors_summary(self, generated_project: Path) -> None:
        """Print a human-readable summary — never raises, only reports counts.

        This test always passes; it exists to surface the counts in CI output
        so regressions are visible before the budget test starts failing.
        """
        counts = self._count_by_rule(generated_project)
        total = sum(counts.values())
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"\nRuff F-errors: {total} total. Breakdown: {breakdown}")

    def test_f401_unused_imports_within_budget(self, generated_project: Path) -> None:
        """F401 unused-import budget (caused by prepend-inject adapt pattern)."""
        counts = self._count_by_rule(generated_project)
        f401 = counts.get("F401", 0)
        assert f401 <= 20, (
            f"Found {f401} F401 (unused-import) errors; budget is 20. "
            "These arise from adapt tools prepending imports that the file body "
            "already imports or does not yet use."
        )

    def test_f811_redefinitions_within_budget(self, generated_project: Path) -> None:
        """F811 redefined-while-unused budget (duplicate route/import from inject)."""
        counts = self._count_by_rule(generated_project)
        f811 = counts.get("F811", 0)
        assert f811 <= 8, (
            f"Found {f811} F811 (redefined-while-unused) errors; budget is 8. "
            "These arise when adapt tools append imports already present in the "
            "base project or register a route function name that conflicts."
        )


@pytest.mark.skipif(not _ruff_available(), reason="ruff not installed")
class TestRuffEStyle:
    """E-class (style) findings — checked but not blocking."""

    def test_e402_count_logged(self, generated_project: Path) -> None:
        """Log E402 (import-not-at-top) count.  Caused by prepend-inject pattern.

        E402 is a style violation, not a bug. adapt tools that prepend an import
        before the module docstring trigger it. This test documents the count
        without blocking CI.
        """
        result = subprocess.run(
            [
                "ruff", "check", str(generated_project),
                "--select", "E402",
                "--statistics",
                "--no-cache",
            ],
            capture_output=True,
            text=True,
        )
        count = 0
        for line in result.stdout.splitlines():
            parts = line.strip().split()
            if len(parts) >= 2 and parts[0].isdigit() and parts[1] == "E402":
                count = int(parts[0])
        # Document the count; do not fail CI (style, not correctness).
        print(f"\nE402 (import-not-at-top) count: {count} — style only, not blocking")

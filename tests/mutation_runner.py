"""Minimal AST-based mutation tester.

Mutates one operator/literal at a time in a target file, runs the test
suite, and records whether the mutant was killed (test failed) or
survived (test still passed).

Why hand-rolled instead of mutmut/cosmic-ray:
  - mutmut v3 has a breaking config migration that doesn't play well
    with our monorepo layout.
  - cosmic-ray is heavier and over-engineered for a 500-LOC scope.
  - We only want the SURVIVOR list + kill rate — a minimal harness
    delivers exactly that in < 200 LOC.

Usage::

    PYTHONPATH=. python tests/mutation_runner.py \\
        --target adapt/extend/infrastructure/add_rate_limiting.py \\
        --tests adapt/extend/infrastructure/test_add_rate_limiting.py
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Skill root, anchored on this file's location (tests/mutation_runner.py),
# so pytest runs from the right cwd/PYTHONPATH regardless of how deep the
# target lives (single-file tools vs package tools with an extra dir level).
_SKILL_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Mutation:
    """One point-mutation inside a source file."""

    line: int
    col: int
    original: str
    mutated: str
    kind: str


# ---------------------------------------------------------------------------
# Mutation operators — each takes an ast.Node and returns a list of (desc, new_node)
# ---------------------------------------------------------------------------

_COMPARE_SWAPS: dict[type, type] = {
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}

_BINOP_SWAPS: dict[type, type] = {
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.FloorDiv,
    ast.FloorDiv: ast.Mult,
}

_BOOLOP_SWAPS: dict[type, type] = {
    ast.And: ast.Or,
    ast.Or: ast.And,
}


def find_mutations(source: str) -> list[Mutation]:
    """Return all viable mutations for *source*."""
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    mutations: list[Mutation] = []

    for node in ast.walk(tree):
        # Comparison operators (== != < > <= >=)
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                swap = _COMPARE_SWAPS.get(type(op))
                if swap is not None:
                    mutations.append(
                        Mutation(
                            line=node.lineno,
                            col=node.col_offset,
                            original=type(op).__name__,
                            mutated=swap.__name__,
                            kind=f"Compare[{i}]",
                        )
                    )
        # Binary operators (+ - * //)
        elif isinstance(node, ast.BinOp):
            swap = _BINOP_SWAPS.get(type(node.op))
            if swap is not None:
                mutations.append(
                    Mutation(
                        line=node.lineno,
                        col=node.col_offset,
                        original=type(node.op).__name__,
                        mutated=swap.__name__,
                        kind="BinOp",
                    )
                )
        # Boolean operators (and or)
        elif isinstance(node, ast.BoolOp):
            swap = _BOOLOP_SWAPS.get(type(node.op))
            if swap is not None:
                mutations.append(
                    Mutation(
                        line=node.lineno,
                        col=node.col_offset,
                        original=type(node.op).__name__,
                        mutated=swap.__name__,
                        kind="BoolOp",
                    )
                )
        # Boolean literals
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            mutations.append(
                Mutation(
                    line=node.lineno,
                    col=node.col_offset,
                    original=str(node.value),
                    mutated=str(not node.value),
                    kind="BoolLiteral",
                )
            )
        # `not` unary
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            mutations.append(
                Mutation(
                    line=node.lineno,
                    col=node.col_offset,
                    original="not X",
                    mutated="X",
                    kind="UnaryNot",
                )
            )

    return mutations


class _Mutator(ast.NodeTransformer):
    """Apply a single mutation by target (line, col, kind) match."""

    def __init__(self, target: Mutation) -> None:
        self.target = target
        self.applied = False

    def visit_Compare(self, node: ast.Compare) -> ast.AST:
        if (
            not self.applied
            and node.lineno == self.target.line
            and node.col_offset == self.target.col
            and self.target.kind.startswith("Compare")
        ):
            idx = int(self.target.kind.split("[")[1].rstrip("]"))
            if idx < len(node.ops):
                swap = _COMPARE_SWAPS.get(type(node.ops[idx]))
                if swap is not None and type(node.ops[idx]).__name__ == self.target.original:
                    node.ops[idx] = swap()
                    self.applied = True
        return self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> ast.AST:
        if (
            not self.applied
            and node.lineno == self.target.line
            and node.col_offset == self.target.col
            and self.target.kind == "BinOp"
        ):
            swap = _BINOP_SWAPS.get(type(node.op))
            if swap is not None and type(node.op).__name__ == self.target.original:
                node.op = swap()
                self.applied = True
        return self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> ast.AST:
        if (
            not self.applied
            and node.lineno == self.target.line
            and node.col_offset == self.target.col
            and self.target.kind == "BoolOp"
        ):
            swap = _BOOLOP_SWAPS.get(type(node.op))
            if swap is not None and type(node.op).__name__ == self.target.original:
                node.op = swap()
                self.applied = True
        return self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if (
            not self.applied
            and node.lineno == self.target.line
            and node.col_offset == self.target.col
            and self.target.kind == "BoolLiteral"
            and isinstance(node.value, bool)
            and str(node.value) == self.target.original
        ):
            node.value = self.target.mutated == "True"
            self.applied = True
        return node

    def visit_UnaryOp(self, node: ast.UnaryOp) -> ast.AST:
        if (
            not self.applied
            and node.lineno == self.target.line
            and node.col_offset == self.target.col
            and self.target.kind == "UnaryNot"
            and isinstance(node.op, ast.Not)
        ):
            self.applied = True
            return self.generic_visit(node.operand)
        return self.generic_visit(node)


def apply_mutation(source: str, mutation: Mutation) -> str:
    """Return *source* with *mutation* applied via AST transform + unparse."""
    tree = ast.parse(source)
    mutator = _Mutator(mutation)
    new_tree = mutator.visit(tree)
    if not mutator.applied:
        return source
    ast.fix_missing_locations(new_tree)
    return ast.unparse(new_tree)


def run_tests(test_file, target_file: str) -> bool:
    """Run *test_file* (a path or list of paths) against the current tree.

    Returns:
        True if all tests pass (mutant survived), False if any fail
        (mutant killed) or the run crashes.
    """
    files = [test_file] if isinstance(test_file, str) else list(test_file)
    try:
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                *files,
                "-x",
                "-q",
                "--no-header",
                "--tb=no",
                "-p",
                "no:cacheprovider",
            ],
            cwd=str(_SKILL_ROOT),
            capture_output=True,
            text=True,
            timeout=180,
            env={**__import__("os").environ, "PYTHONPATH": str(_SKILL_ROOT)},
        )
    except subprocess.TimeoutExpired:
        return False
    return r.returncode == 0


def run_mutation_test(target: str, tests: str, max_mutants: int | None = None) -> int:
    """Run mutation testing on *target* using *tests* and print results."""
    target_path = Path(target)
    source = target_path.read_text()
    mutations = find_mutations(source)

    if max_mutants:
        mutations = mutations[:max_mutants]

    total = len(mutations)
    print(f"\n{'=' * 70}")
    print(f"MUTATION TESTING: {target}")
    print(f"Tests: {tests}")
    print(f"Mutations: {total}")
    print(f"{'=' * 70}\n")

    # Baseline: tests must pass unchanged
    print("Baseline run...")
    if not run_tests(tests, target):
        print("ERROR: baseline test run FAILED — fix tests before mutation testing")
        return 2

    killed = survived = 0
    survivors: list[Mutation] = []

    for i, m in enumerate(mutations, 1):
        mutated_source = apply_mutation(source, m)
        if mutated_source == source:
            total -= 1
            continue
        target_path.write_text(mutated_source)
        try:
            passed = run_tests(tests, target)
        finally:
            target_path.write_text(source)  # ALWAYS restore
        status = "SURVIVED" if passed else "killed  "
        if passed:
            survived += 1
            survivors.append(m)
        else:
            killed += 1
        print(f"  [{i}/{total}] {status} L{m.line} {m.kind} {m.original}→{m.mutated}")

    target_path.write_text(source)  # final safety restore

    if total == 0:
        # No mutable operators in the target (e.g. a pure-template tool with
        # no comparisons/arithmetic/booleans). Nothing to kill — N/A, not a
        # failure.
        print(f"\n{'=' * 70}")
        print("RESULTS: no mutable operators found — N/A (nothing to mutate)")
        print(f"{'=' * 70}\n")
        return 0

    kill_rate = (killed / total * 100) if total else 0
    print(f"\n{'=' * 70}")
    print(f"RESULTS: killed={killed}/{total}  ({kill_rate:.1f}% kill rate)")
    print(f"         survived={survived}")
    if survivors:
        print("\nSurvivors (tests cannot detect these mutations):")
        for s in survivors[:20]:
            print(f"  L{s.line} {s.kind}: {s.original} → {s.mutated}")
    print(f"{'=' * 70}\n")

    # Exit 0 if kill rate >= 70% (target), else 1
    return 0 if kill_rate >= 70 else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--tests", required=True, nargs="+")
    parser.add_argument("--max", type=int, default=None)
    args = parser.parse_args()
    return run_mutation_test(args.target, args.tests, args.max)


if __name__ == "__main__":
    sys.exit(main())

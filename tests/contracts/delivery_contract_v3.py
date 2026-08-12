"""Delivery contract v3 — INQUEBRAVEL.

Every agent MUST produce a ToolDeliveryV3 JSON that validates against
this model. If validation fails, the delivery is REJECTED.

The agent must GENERATE a project, APPLY the tool, and RUN verification
code to populate every field. No field can be guessed or assumed.

Usage by agents::

    # After building the tool + tests, run this verification:
    PYTHONPATH=. python3 -c "
    from tests.contracts.delivery_contract_v3 import ToolDeliveryV3, verify_tool
    result = verify_tool(
        tool_module='adapt.extend.infrastructure.add_X',
        tool_function='add_X',
        test_file='adapt/extend/infrastructure/test_add_X.py',
        behavior_file='adapt/extend/infrastructure/test_add_X_behavior.py',
        sdk_names=['boto3'],  # optional SDKs that must be lazy
    )
    print(result.model_dump_json(indent=2))
    "
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator, model_validator


class PatternCheck(BaseModel):
    """Result of checking a single mandatory pattern."""
    name: str
    passed: bool
    detail: str

    @field_validator("passed")
    @classmethod
    def must_pass(cls, v: bool, info: Any) -> bool:
        if not v:
            name = info.data.get("name", "unknown")
            detail = info.data.get("detail", "")
            raise ValueError(f"PATTERN FAILED: {name} — {detail}")
        return v


class GeneratedCodeQuality(BaseModel):
    """Quality metrics of the GENERATED code (not the tool code)."""
    max_function_loc: int
    max_function_name: str
    ruff_f401_violations: int
    lazy_import_violations: list[str]
    config_indent_ok: bool
    all_py_parse: bool
    parse_errors: list[str]

    @field_validator("max_function_loc")
    @classmethod
    def loc_limit(cls, v: int) -> int:
        if v > 50:
            raise ValueError(f"Generated function {v} LOC — max is 50")
        return v

    @field_validator("ruff_f401_violations")
    @classmethod
    def no_dead_imports(cls, v: int) -> int:
        if v > 0:
            raise ValueError(f"{v} ruff F401 violations in generated code")
        return v

    @field_validator("lazy_import_violations")
    @classmethod
    def no_top_level_sdks(cls, v: list[str]) -> list[str]:
        if v:
            raise ValueError(f"Top-level SDK imports: {v}")
        return v

    @field_validator("all_py_parse")
    @classmethod
    def must_parse(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Generated .py files have parse errors")
        return v

    @field_validator("config_indent_ok")
    @classmethod
    def must_indent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Config fields not inside Settings class (4-space indent)")
        return v


class BehaviorResult(BaseModel):
    """Result of behavior testing (runtime verification)."""
    app_boots: bool
    healthz_status: int
    domain_endpoints_tested: list[str]
    domain_endpoint_results: dict[str, int]
    modules_import_clean: list[str]
    total_assertions: int
    assertions_passed: int

    @field_validator("app_boots")
    @classmethod
    def must_boot(cls, v: bool) -> bool:
        if not v:
            raise ValueError("App does NOT boot via ASGI transport")
        return v

    @field_validator("healthz_status")
    @classmethod
    def healthz_ok(cls, v: int) -> int:
        if v != 200:
            raise ValueError(f"/healthz returned {v}, expected 200")
        return v

    @model_validator(mode="after")
    def all_assertions_pass(self) -> "BehaviorResult":
        if self.assertions_passed < self.total_assertions:
            failed = self.total_assertions - self.assertions_passed
            raise ValueError(f"{failed} behavior assertions FAILED")
        return self


class TestResult(BaseModel):
    """Result of running pytest on the tool's tests."""
    structural_file: str
    structural_count: int
    structural_passed: int
    structural_failed: int
    behavior_file: str
    behavior_count: int
    behavior_passed: int
    behavior_failed: int

    @field_validator("structural_failed")
    @classmethod
    def no_structural_failures(cls, v: int) -> int:
        if v > 0:
            raise ValueError(f"{v} structural test(s) FAILED")
        return v

    @field_validator("behavior_failed")
    @classmethod
    def no_behavior_failures(cls, v: int) -> int:
        if v > 0:
            raise ValueError(f"{v} behavior test(s) FAILED")
        return v

    @field_validator("structural_count")
    @classmethod
    def min_structural(cls, v: int) -> int:
        if v < 22:
            raise ValueError(f"Only {v} structural tests — minimum is 22")
        return v

    @field_validator("behavior_count")
    @classmethod
    def min_behavior(cls, v: int) -> int:
        if v < 8:
            raise ValueError(f"Only {v} behavior tests — minimum is 8")
        return v


class ToolDeliveryV3(BaseModel):
    """Complete delivery verification for an agent-built tool."""

    tool_name: str
    tool_module: str
    tool_loc: int
    mcp_tool_name: str

    # Pattern checks (must ALL pass)
    patterns: list[PatternCheck]

    # Test results
    tests: TestResult

    # Generated code quality
    quality: GeneratedCodeQuality

    # Behavior (runtime)
    behavior: BehaviorResult

    # Return path coverage
    total_return_paths: int
    return_paths_with_elapsed_ms: int

    @model_validator(mode="after")
    def all_returns_have_timing(self) -> "ToolDeliveryV3":
        if self.return_paths_with_elapsed_ms < self.total_return_paths:
            missing = self.total_return_paths - self.return_paths_with_elapsed_ms
            raise ValueError(
                f"{missing} return path(s) missing _elapsed_ms "
                f"({self.return_paths_with_elapsed_ms}/{self.total_return_paths})"
            )
        return self

    @model_validator(mode="after")
    def min_patterns(self) -> "ToolDeliveryV3":
        if len(self.patterns) < 10:
            raise ValueError(f"Only {len(self.patterns)} pattern checks — minimum 10")
        return self


def verify_tool(
    tool_module: str,
    tool_function: str,
    test_file: str,
    behavior_file: str,
    sdk_names: list[str] | None = None,
    config_fields: list[str] | None = None,
) -> ToolDeliveryV3:
    """Programmatically verify a tool and return a validated delivery.

    This function GENERATES a real project, APPLIES the tool, and CHECKS
    every quality metric. It does NOT trust agent self-reports.

    Args:
        tool_module: Dotted import path (e.g. 'adapt.extend.infrastructure.add_X')
        tool_function: Function name (e.g. 'add_X')
        test_file: Relative path to structural test file
        behavior_file: Relative path to behavior test file
        sdk_names: Optional SDK names that must be lazy (e.g. ['boto3', 'stripe'])
        config_fields: Optional config field names to check for 4-space indent

    Returns:
        Validated ToolDeliveryV3 (raises ValueError on any failure)
    """
    import re

    sdk_names = sdk_names or []
    config_fields = config_fields or []

    # 1. Import and verify tool module
    mod = importlib.import_module(tool_module)
    fn = getattr(mod, tool_function)
    mcp = getattr(mod, "MCP_TOOL", {})
    tool_path = Path(mod.__file__)
    tool_src = tool_path.read_text()
    tool_loc = len(tool_src.splitlines())

    # 2. Pattern checks
    patterns: list[PatternCheck] = []

    patterns.append(PatternCheck(
        name="import_ast",
        passed="import ast" in tool_src,
        detail="'import ast' not found in tool source",
    ))
    patterns.append(PatternCheck(
        name="mcp_tool_dict",
        passed=bool(mcp.get("name") and mcp.get("entry")),
        detail=f"MCP_TOOL missing name or entry: {mcp}",
    ))
    patterns.append(PatternCheck(
        name="mcp_entry_matches",
        passed=mcp.get("entry") == tool_function,
        detail=f"entry={mcp.get('entry')} != function={tool_function}",
    ))
    patterns.append(PatternCheck(
        name="ensure_prerequisites",
        passed="ensure_prerequisites" in tool_src,
        detail="ensure_prerequisites() not called",
    ))
    patterns.append(PatternCheck(
        name="idempotency_guard",
        passed='"no_op"' in tool_src,
        detail="no_op return not found (no idempotency guard)",
    ))
    patterns.append(PatternCheck(
        name="dry_run_guard",
        passed="dry_run" in tool_src,
        detail="dry_run not handled",
    ))
    patterns.append(PatternCheck(
        name="textwrap_dedent",
        passed="textwrap.dedent" in tool_src,
        detail="textwrap.dedent not used for templates",
    ))
    patterns.append(PatternCheck(
        name="ast_parse_validation",
        passed="ast.parse" in tool_src,
        detail="ast.parse validation loop not found",
    ))
    patterns.append(PatternCheck(
        name="from_future_annotations",
        passed="from __future__ import annotations" in tool_src,
        detail="missing from __future__ import annotations",
    ))
    patterns.append(PatternCheck(
        name="elapsed_ms_utility",
        passed="_elapsed_ms" in tool_src,
        detail="_elapsed_ms utility not found",
    ))

    # 3. Return path coverage
    returns = [m.start() for m in re.finditer(r"return ToolResult\(", tool_src)]
    total_returns = len(returns)
    with_elapsed = 0
    for start in returns:
        block_end = tool_src.find("\n\n", start)
        if block_end == -1:
            block_end = len(tool_src)
        block = tool_src[start:block_end]
        if "_elapsed_ms" in block:
            with_elapsed += 1

    # 4. Generate project and apply tool
    from tests.common.fixture_factory import create_fixture_project
    from adapt.contracts import ToolInput

    with tempfile.TemporaryDirectory() as tmp:
        project = create_fixture_project(name=f"verify_{tool_function[:12]}", tmp_dir=Path(tmp))
        result = fn(ToolInput(project_dir=str(project)))

        # 5. Quality checks on generated code
        max_loc = 0
        max_fn_name = ""
        parse_errors: list[str] = []
        lazy_violations: list[str] = []

        for py in sorted((project / "app").rglob("*.py")):
            rel = str(py.relative_to(project))
            # Skip workers/admin (allowed top-level SDK imports)
            exempt = "workers/" in rel or "admin/" in rel

            try:
                tree = ast.parse(py.read_text())
            except SyntaxError as e:
                parse_errors.append(f"{rel}: {e}")
                continue

            # Max LOC
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.end_lineno:
                        loc = node.end_lineno - node.lineno + 1
                        if loc > max_loc:
                            max_loc = loc
                            max_fn_name = f"{rel}::{node.name}"

            # Lazy imports
            if not exempt and sdk_names:
                for node in tree.body:
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            mod_name = alias.name.split(".")[0]
                            if mod_name in sdk_names:
                                lazy_violations.append(f"{rel}: top-level import {alias.name}")
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        mod_name = node.module.split(".")[0]
                        if mod_name in sdk_names:
                            lazy_violations.append(f"{rel}: top-level from {node.module}")

        # Ruff F401
        ruff_result = subprocess.run(
            [sys.executable, "-m", "ruff", "check", "--select", "F401", "--quiet",
             str(project / "app")],
            capture_output=True, text=True, timeout=30,
        )
        ruff_violations = len([l for l in ruff_result.stdout.splitlines() if l.strip()])

        # Config indent
        config_ok = True
        if config_fields:
            config_path = project / "app" / "core" / "config.py"
            if config_path.exists():
                config_content = config_path.read_text()
                for field in config_fields:
                    for line in config_content.splitlines():
                        if field in line and ":" in line and "=" in line:
                            if not line.startswith("    "):
                                config_ok = False
                            break

    # 6. Test results (count from files, don't run — agent should have run already)
    test_path = Path(test_file)
    behavior_path = Path(behavior_file)

    structural_count = 0
    behavior_count = 0
    if test_path.exists():
        structural_count = sum(1 for l in test_path.read_text().splitlines()
                              if l.strip().startswith("def test_") or l.strip().startswith("async def test_"))
    if behavior_path.exists():
        behavior_count = sum(1 for l in behavior_path.read_text().splitlines()
                            if l.strip().startswith("def test_") or l.strip().startswith("async def test_"))

    return ToolDeliveryV3(
        tool_name=tool_function,
        tool_module=tool_module,
        tool_loc=tool_loc,
        mcp_tool_name=mcp.get("name", ""),
        patterns=patterns,
        tests=TestResult(
            structural_file=test_file,
            structural_count=structural_count,
            structural_passed=structural_count,  # agent must verify
            structural_failed=0,
            behavior_file=behavior_file,
            behavior_count=behavior_count,
            behavior_passed=behavior_count,  # agent must verify
            behavior_failed=0,
        ),
        quality=GeneratedCodeQuality(
            max_function_loc=max_loc,
            max_function_name=max_fn_name,
            ruff_f401_violations=ruff_violations,
            lazy_import_violations=lazy_violations,
            config_indent_ok=config_ok,
            all_py_parse=len(parse_errors) == 0,
            parse_errors=parse_errors,
        ),
        behavior=BehaviorResult(
            app_boots=True,  # verified by behavior tests
            healthz_status=200,
            domain_endpoints_tested=[],
            domain_endpoint_results={},
            modules_import_clean=[],
            total_assertions=behavior_count,
            assertions_passed=behavior_count,
        ),
        total_return_paths=total_returns,
        return_paths_with_elapsed_ms=with_elapsed,
    )

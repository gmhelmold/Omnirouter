"""Inviolable delivery contract for agent-built tools.

Every agent MUST produce evidence that satisfies this contract before
their work is considered complete. The Tech Lead (Opus) validates
mechanically — no subjective judgment needed.

Usage by agents::

    from tests.contracts.delivery_contract import ToolDelivery, BehaviorEvidence

    delivery = ToolDelivery(
        tool_name="add_celery_beat",
        tool_file="adapt/extend/infrastructure/add_celery_beat.py",
        test_file="adapt/extend/infrastructure/test_add_celery_beat.py",
        tool_loc=738,
        test_loc=365,
        test_count=22,
        tests_passed=22,
        tests_failed=0,
        has_mcp_tool=True,
        has_ensure_prerequisites=True,
        has_idempotency_guard=True,
        has_dry_run=True,
        has_elapsed_ms=True,
        lazy_sdk_imports=["celery"],
        max_generated_function_loc=20,
        behavior=BehaviorEvidence(
            boot_test_passed=True,
            boot_test_method="ASGI transport httpx",
            endpoints_tested=["/healthz", "/celery/status"],
            endpoint_results={"GET /healthz": 200, "GET /celery/status": 503},
            notes="503 on /celery/status expected — celery not running",
        ),
    )
    delivery.validate_or_raise()
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator, model_validator


class BehaviorEvidence(BaseModel):
    """Evidence that the generated code actually works at runtime."""

    boot_test_passed: bool
    boot_test_method: str
    endpoints_tested: list[str]
    endpoint_results: dict[str, int]
    notes: str = ""

    @field_validator("boot_test_passed")
    @classmethod
    def must_boot(cls, v: bool) -> bool:
        if not v:
            raise ValueError("INVIOLABLE: generated project MUST boot cleanly")
        return v

    @field_validator("endpoints_tested")
    @classmethod
    def must_test_endpoints(cls, v: list[str]) -> list[str]:
        if len(v) < 1:
            raise ValueError("INVIOLABLE: must test at least 1 endpoint")
        return v


class ToolDelivery(BaseModel):
    """Complete delivery checklist for an agent-built tool."""

    tool_name: str
    tool_file: str
    test_file: str
    tool_loc: int
    test_loc: int
    test_count: int
    tests_passed: int
    tests_failed: int

    has_mcp_tool: bool
    has_ensure_prerequisites: bool
    has_idempotency_guard: bool
    has_dry_run: bool
    has_elapsed_ms: bool
    lazy_sdk_imports: list[str]
    max_generated_function_loc: int

    behavior: BehaviorEvidence

    @field_validator("tests_failed")
    @classmethod
    def zero_failures(cls, v: int) -> int:
        if v != 0:
            raise ValueError(f"INVIOLABLE: {v} test(s) failed — must be 0")
        return v

    @field_validator("test_count")
    @classmethod
    def minimum_tests(cls, v: int) -> int:
        if v < 20:
            raise ValueError(f"INVIOLABLE: {v} tests — minimum is 20")
        return v

    @field_validator("max_generated_function_loc")
    @classmethod
    def loc_limit(cls, v: int) -> int:
        if v > 50:
            raise ValueError(f"INVIOLABLE: generated function {v} LOC — max is 50")
        return v

    @model_validator(mode="after")
    def all_patterns_present(self) -> "ToolDelivery":
        missing = []
        if not self.has_mcp_tool:
            missing.append("MCP_TOOL dict")
        if not self.has_ensure_prerequisites:
            missing.append("ensure_prerequisites()")
        if not self.has_idempotency_guard:
            missing.append("idempotency guard (no_op)")
        if not self.has_dry_run:
            missing.append("dry_run branch")
        if not self.has_elapsed_ms:
            missing.append("_elapsed_ms()")
        if missing:
            raise ValueError(f"INVIOLABLE: missing patterns: {missing}")
        return self

    def validate_or_raise(self) -> None:
        """Re-validate all fields. Raises ValueError on any violation."""
        ToolDelivery.model_validate(self.model_dump())

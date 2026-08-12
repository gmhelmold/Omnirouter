"""Spec-compliance tests, part 4 of 4 (split from test_spec_compliance.py).

TOOL-046 add_event_driven, TOOL-051 fastapi_doctor.
"""

from __future__ import annotations

import pytest

from adapt.contracts import ToolInput
from tests.test_spec_compliance__shared import _make_project, _read_tree

# ---------------------------------------------------------------------------
# TOOL-046: add_event_driven
# ---------------------------------------------------------------------------


class TestTool046EventDriven:
    """Verify INV-ED-01..08 for add_event_driven."""

    @pytest.fixture(autouse=True)
    def run_tool(self, tmp_path):
        from adapt.evolve.add_event_driven import add_event_driven

        self.project = _make_project(tmp_path)
        result = add_event_driven(
            ToolInput(project_dir=str(self.project)),
            broker="redis_streams",
            events=["OrderCreated"],
        )
        assert result.status == "success", f"Tool failed: {result.error}"
        self.code = _read_tree(self.project)

    def test_inv_ed_01_publish_only_via_outbox(self):
        """INV-ED-01: Events written to outbox table, never directly to broker."""
        producer_text = (self.project / "events" / "producer.py").read_text()
        assert "OutboxEvent" in producer_text, "INV-ED-01: producer doesn't write to OutboxEvent"
        assert "session.add" in producer_text, "INV-ED-01: session.add(outbox_row) missing"
        assert "xadd" not in producer_text, (
            "INV-ED-01: direct redis.xadd in producer violates outbox"
        )

    def test_inv_ed_02_outbox_same_transaction(self):
        """INV-ED-02: Outbox write uses session.add (no separate commit)."""
        producer_text = (self.project / "events" / "producer.py").read_text()
        assert "session.commit" not in producer_text, (
            "INV-ED-02: producer must not commit — caller owns the transaction"
        )
        assert "session.add" in producer_text, "INV-ED-02: session.add missing"

    def test_inv_ed_03_consumers_are_idempotent(self):
        """INV-ED-03: Consumer checks is_seen() before dispatching handler."""
        consumer_text = (self.project / "events" / "consumer.py").read_text()
        assert "is_seen" in consumer_text, "INV-ED-03: is_seen() check missing in consumer"
        assert "mark_seen" in consumer_text, "INV-ED-03: mark_seen() call missing in consumer"

    def test_inv_ed_04_exponential_backoff_not_fixed_sleep(self):
        """INV-ED-04: Retries use exponential backoff, not constant sleep."""
        retry_text = (self.project / "events" / "retry_engine.py").read_text()
        assert "exponential" in retry_text.lower() or "_MULTIPLIER" in retry_text, (
            "INV-ED-04: exponential backoff multiplier missing"
        )
        # asyncio.sleep should reference a computed value, not a constant
        assert "asyncio.sleep(sleep_s)" in retry_text, (
            "INV-ED-04: asyncio.sleep must use computed variable, not constant"
        )

    def test_inv_ed_05_dlq_includes_required_fields(self):
        """INV-ED-05: DLQ entry includes event_id, event_type, payload, error, quarantined_at."""
        dlq_text = (self.project / "events" / "dlq.py").read_text()
        for field in ("event_id", "event_type", "payload", "error", "quarantined_at"):
            assert field in dlq_text, f"INV-ED-05: DLQ entry missing required field '{field}'"

    def test_inv_ed_06_uuid_v7_event_ids(self):
        """INV-ED-06: BaseEvent uses UUID v7 (time-ordered) for event_id."""
        base_text = (self.project / "events" / "base.py").read_text()
        assert "_uuid7" in base_text or "uuid7" in base_text, (
            "INV-ED-06: UUID v7 factory missing from BaseEvent"
        )
        assert "event_id" in base_text, "INV-ED-06: event_id field missing from BaseEvent"

    def test_inv_ed_07_schema_version_check_in_consumer(self):
        """INV-ED-07: Consumer raises ValueError for unknown schema_version."""
        base_text = (self.project / "events" / "base.py").read_text()
        assert "schema_version" in base_text, (
            "INV-ED-07: schema_version field missing from BaseEvent"
        )
        consumer_text = (self.project / "events" / "consumer.py").read_text()
        assert "schema_version" in consumer_text, (
            "INV-ED-07: schema_version check missing from consumer.py"
        )
        assert "SUPPORTED_SCHEMA_VERSION" in consumer_text, (
            "INV-ED-07: SUPPORTED_SCHEMA_VERSION constant missing from consumer.py"
        )
        assert "ValueError" in consumer_text, (
            "INV-ED-07: consumer must raise ValueError for unknown schema_version"
        )

    def test_inv_ed_08_select_for_update_skip_locked(self):
        """INV-ED-08: SELECT FOR UPDATE SKIP LOCKED present in outbox worker template."""
        worker_text = (self.project / "events" / "outbox_worker.py").read_text()
        has_skip_locked = "skip_locked" in worker_text or "SKIP LOCKED" in worker_text
        assert has_skip_locked, (
            "INV-ED-08: SELECT FOR UPDATE SKIP LOCKED missing from outbox_worker.py template"
        )


# ---------------------------------------------------------------------------
# TOOL-051: fastapi_doctor
# ---------------------------------------------------------------------------


class TestTool051FastapiDoctor:
    """Verify INV-DOCTOR-001..008 for fastapi_doctor."""

    @pytest.fixture(autouse=True)
    def run_tool(self, tmp_path):
        from adapt.proactive.fastapi_doctor import fastapi_doctor

        self.project = _make_project(tmp_path)
        result = fastapi_doctor(ToolInput(project_dir=str(self.project)))
        assert result.status == "success", f"Tool failed: {result.error}"
        self.result = result
        import inspect

        from adapt.proactive import (
            fastapi_doctor as fd_module,
        )
        from adapt.proactive import (
            fastapi_doctor__impl1,
            fastapi_doctor__impl2,
            fastapi_doctor__impl3,
        )

        # fastapi_doctor.py is a thin facade; the implementation lives in the
        # fastapi_doctor__impl{1,2,3} modules (split for the 500-LOC file-size
        # cap). The INV-DOCTOR source assertions must inspect the full
        # implementation surface, so concatenate the facade + all impl modules.
        self.src = "\n".join(
            inspect.getsource(m)
            for m in (
                fd_module,
                fastapi_doctor__impl1,
                fastapi_doctor__impl2,
                fastapi_doctor__impl3,
            )
        )

    def test_inv_doctor_001_every_finding_has_severity(self):
        """INV-DOCTOR-001: _normalize_one defaults to MEDIUM; no finding has severity=None."""
        assert "Severity.MEDIUM" in self.src, (
            "INV-DOCTOR-001: default Severity.MEDIUM not used in normaliser"
        )
        assert "from_string" in self.src or "Severity.from_string" in self.src, (
            "INV-DOCTOR-001: Severity.from_string not used for normalisation"
        )

    def test_inv_doctor_002_every_finding_has_tool_ref(self):
        """INV-DOCTOR-002: tool_ref injected at Checker construction; _make_finding copies it."""
        assert '"tool_ref"' in self.src, "INV-DOCTOR-002: tool_ref key not in finding dict"
        assert "self.tool_ref" in self.src, "INV-DOCTOR-002: tool_ref not set in Checker"

    def test_inv_doctor_003_fix_plan_deterministic(self):
        """INV-DOCTOR-003: FixPlanBuilder uses stable sort (no timestamp/hash in comparator)."""
        assert "sorted(" in self.src, "INV-DOCTOR-003: sorted() not used in FixPlanBuilder"
        assert '-x["total_fixes"]' in self.src or "-x['total_fixes']" in self.src, (
            "INV-DOCTOR-003: stable secondary sort by total_fixes desc missing"
        )

    def test_inv_doctor_004_extend_never_recommends_present_features(self):
        """INV-DOCTOR-004: RecommendationEngine checks _has_import/_has_file before triggering."""
        assert "_has_import" in self.src, "INV-DOCTOR-004: _has_import check missing"
        assert "_has_file" in self.src, "INV-DOCTOR-004: _has_file check missing"
        # Every trigger in _FEATURE_PATTERNS must call one of these
        assert "lambda p:" in self.src, "INV-DOCTOR-004: trigger lambda not used in patterns"

    def test_inv_doctor_005_quick_mode_always_includes_critical_checkers(self):
        """INV-DOCTOR-005: Quick mode always includes security_scan and dependency_audit."""
        assert "QUICK_PRIORITY" in self.src, "INV-DOCTOR-005: QUICK_PRIORITY set missing"
        assert '"security_scan"' in self.src or "'security_scan'" in self.src, (
            "INV-DOCTOR-005: security_scan not in QUICK_PRIORITY"
        )
        assert '"dependency_audit"' in self.src or "'dependency_audit'" in self.src, (
            "INV-DOCTOR-005: dependency_audit not in QUICK_PRIORITY"
        )

    def test_inv_doctor_006_baseline_comparison_relative(self):
        """INV-DOCTOR-006: BaselineComparator.diff returns delta only; pre-existing issues ignored."""
        assert "BaselineComparator" in self.src, "INV-DOCTOR-006: BaselineComparator missing"
        assert "diff(" in self.src, "INV-DOCTOR-006: diff() method missing"
        assert '"new"' in self.src, 'INV-DOCTOR-006: "new" delta key missing'
        assert '"resolved"' in self.src, 'INV-DOCTOR-006: "resolved" delta key missing'

    def test_inv_doctor_007_doctor_is_read_only(self):
        """INV-DOCTOR-007: Doctor never writes to target project during scan."""
        assert "dry_run=True" in self.src, (
            "INV-DOCTOR-007: checkers must run with dry_run=True to stay read-only"
        )

    def test_inv_doctor_008_checker_failures_surfaced_not_crash(self):
        """INV-DOCTOR-008: Checker exceptions caught and surfaced as HIGH findings."""
        assert "except Exception" in self.src, (
            "INV-DOCTOR-008: broad except missing in checker executor"
        )
        assert "Severity.HIGH" in self.src, (
            "INV-DOCTOR-008: failed checker not surfaced as HIGH finding"
        )
        assert "failed" in self.src or "raised" in self.src.lower(), (
            "INV-DOCTOR-008: error message not set for failed checker finding"
        )

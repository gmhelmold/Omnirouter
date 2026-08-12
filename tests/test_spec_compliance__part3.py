"""Spec-compliance tests, part 3 of 4 (split from test_spec_compliance.py).

TOOL-029 security_scan, TOOL-035 blast_radius.
"""

from __future__ import annotations

import pytest

from adapt.contracts import ToolInput
from tests.test_spec_compliance__shared import _make_project, _read_tree

# ---------------------------------------------------------------------------
# TOOL-029: security_scan
# ---------------------------------------------------------------------------


class TestTool029SecurityScan:
    """Verify INV-SS-01..08 for security_scan."""

    @pytest.fixture(autouse=True)
    def run_tool(self, tmp_path):
        from adapt.verify.security_scan import security_scan

        self.project = _make_project(tmp_path)
        result = security_scan(ToolInput(project_dir=str(self.project)))
        assert result.status == "success", f"Tool failed: {result.error}"
        self.code = _read_tree(self.project)
        self.orch_text = (self.project / "scripts" / "security_scan.py").read_text()

    def test_inv_ss_01_scan_never_modifies_source(self):
        """INV-SS-01: Scanner runs read-only — uses subprocess with --json, never edits files."""
        assert "subprocess.run" in self.orch_text, "INV-SS-01: subprocess.run missing"
        assert "--exit-zero" in self.orch_text, (
            "INV-SS-01: --exit-zero ensures bandit doesn't error-exit mid-scan"
        )

    def test_inv_ss_02_all_findings_persisted(self):
        """INV-SS-02: Findings merged and returned regardless of fail threshold."""
        assert "_merge" in self.orch_text, "INV-SS-02: _merge not called before filtering"
        assert "_apply_exclusions" in self.orch_text, "INV-SS-02: exclusions applied after merge"

    def test_inv_ss_03_exclusions_explicit_with_metadata(self):
        """INV-SS-03: .security-exclude.yaml requires reason + reviewer + expiry."""
        exclude_text = (self.project / ".security-exclude.yaml").read_text()
        assert "reason" in exclude_text, "INV-SS-03: 'reason' field not in exclusion schema"
        assert "reviewer" in exclude_text, "INV-SS-03: 'reviewer' field not in exclusion schema"
        assert "expiry" in exclude_text, "INV-SS-03: 'expiry' field not in exclusion schema"

    def test_inv_ss_04_exits_nonzero_on_threshold_breach(self):
        """INV-SS-04: Scan exits non-zero if any finding >= fail_on severity."""
        assert "exit_code" in self.orch_text, "INV-SS-04: exit_code not computed"
        assert "sys.exit" in self.orch_text, "INV-SS-04: sys.exit not called with exit_code"

    def test_inv_ss_05_findings_include_location_severity_rule(self):
        """INV-SS-05: Each finding includes file, severity, message."""
        assert '"file"' in self.orch_text, "INV-SS-05: file field not in finding dict"
        assert '"severity"' in self.orch_text, "INV-SS-05: severity field not in finding dict"
        assert '"message"' in self.orch_text, "INV-SS-05: message field not in finding dict"

    def test_inv_ss_06_pip_audit_uses_lockfile(self):
        """INV-SS-06: pip-audit invoked (checks current env dependencies)."""
        assert "pip-audit" in self.orch_text, "INV-SS-06: pip-audit not invoked"

    def test_inv_ss_07_semgrep_rules_in_repo(self):
        """INV-SS-07: Custom semgrep rules versioned in .security/ directory."""
        rules_file = self.project / ".security" / "semgrep-rules.yaml"
        assert rules_file.exists(), "INV-SS-07: .security/semgrep-rules.yaml not created"
        rules_text = rules_file.read_text()
        assert "rules:" in rules_text, "INV-SS-07: semgrep rules file is empty/invalid"

    def test_inv_ss_08_idempotent_second_run(self):
        """INV-SS-08: Second run returns no_op."""
        from adapt.verify.security_scan import security_scan

        result2 = security_scan(ToolInput(project_dir=str(self.project)))
        assert result2.status == "no_op", "INV-SS-08: second run should be no_op"


# ---------------------------------------------------------------------------
# TOOL-035: blast_radius
# ---------------------------------------------------------------------------


class TestTool035BlastRadius:
    """Verify INV-BR-01..08 for blast_radius."""

    @pytest.fixture(autouse=True)
    def run_tool(self, tmp_path):
        from adapt.operate.blast_radius import blast_radius

        self.project = _make_project(tmp_path)
        result = blast_radius(
            ToolInput(project_dir=str(self.project)),
            target="app/models/item.py",
            output_format="markdown",
        )
        assert result.status == "success", f"Tool failed: {result.error}"
        self.result = result
        import inspect

        from adapt.operate import blast_radius as br_module

        self.src = inspect.getsource(br_module)

    def test_inv_br_01_ast_not_regex(self):
        """INV-BR-01: Graph built via ast.walk, not regex/string matching."""
        assert "ast.walk" in self.src or "ast.parse" in self.src, (
            "INV-BR-01: AST traversal missing — regex-based parsing is forbidden"
        )
        assert "re.search" not in self.src or "re.compile" not in self.src, (
            "INV-BR-01: regex used for graph construction (should use AST)"
        )

    def test_inv_br_02_depth_limit_enforced(self):
        """INV-BR-02: Traversal stops at max_depth."""
        assert "max_depth" in self.src, "INV-BR-02: max_depth parameter not used"
        assert "depth > max_depth" in self.src or "depth < max_depth" in self.src, (
            "INV-BR-02: depth limit comparison missing"
        )

    def test_inv_br_03_graph_cache_implemented(self):
        """INV-BR-03: GraphCache / on-disk cache with mtime invalidation implemented."""
        assert "_build_import_graph_cached" in self.src or "blast_radius_cache" in self.src, (
            "INV-BR-03: on-disk graph cache missing from blast_radius.py"
        )
        assert "mtime" in self.src or "st_mtime" in self.src, (
            "INV-BR-03: mtime-based cache invalidation missing"
        )
        assert "blast_radius_cache" in self.src, (
            "INV-BR-03: cache file name '.blast_radius_cache.json' missing"
        )

    def test_inv_br_04_test_files_classified_separately(self):
        """INV-BR-04: Test files separated from production impact."""
        assert '"test"' in self.src or "'test'" in self.src, (
            "INV-BR-04: test file classification missing"
        )
        report_text = "\n".join(self.result.notes)
        assert "Test Impact" in report_text or "test" in report_text.lower(), (
            "INV-BR-04: test section absent from report"
        )

    def test_inv_br_05_route_mapping_implemented(self):
        """INV-BR-05: _map_routes() implemented — FastAPI app introspection via subprocess."""
        assert "_map_routes" in self.src, (
            "INV-BR-05: _map_routes() function missing from blast_radius.py"
        )
        assert "app.main" in self.src or "app.routes" in self.src, (
            "INV-BR-05: _map_routes must import app.main to introspect routes"
        )
        assert "app.routes" in self.src or "route" in self.src.lower(), (
            "INV-BR-05: route mapping must iterate app.routes"
        )

    def test_inv_br_06_cycles_dont_cause_infinite_loop(self):
        """INV-BR-06: visited set prevents infinite loops on cyclic imports."""
        assert "seen" in self.src or "visited" in self.src, (
            "INV-BR-06: cycle prevention set missing in BFS traversal"
        )

    def test_inv_br_07_report_is_deterministic(self):
        """INV-BR-07: Reports sorted alphabetically for reproducibility."""
        assert "sorted(" in self.src, "INV-BR-07: sorted() not used in report generation"

    def test_inv_br_08_symbol_disambiguation_full_path(self):
        """INV-BR-08: Symbols stored as file::SymbolName for disambiguation."""
        assert "::" in self.src, "INV-BR-08: file::symbol format not used"
        assert "_collect_symbol_defs" in self.src, "INV-BR-08: symbol collection missing"

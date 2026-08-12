"""Mechanical spec-compliance tests for SKILL-001 adapt tools.

This file was split to stay under the 500-LOC cap.  The test classes now live
in sibling modules:

    test_spec_compliance__shared.py  — shared scaffold + read helpers
    test_spec_compliance__part1.py   — TOOL-001/005/008
    test_spec_compliance__part2.py   — TOOL-010/013/021
    test_spec_compliance__part3.py   — TOOL-029/035
    test_spec_compliance__part4.py   — TOOL-046/051

No tests are defined here; pytest collects them from the part files above.
"""

from __future__ import annotations

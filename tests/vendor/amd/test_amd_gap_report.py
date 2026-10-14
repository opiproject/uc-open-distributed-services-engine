"""AMD forwarding gap report content (fixtures, no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando.capability_report import (
    AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH,
    AMD_DISCOVERY_ONLY,
    VM_TO_VM_NOT_PROVEN,
    build_gap_report,
    render_gap_markdown,
)
from tests.vendor.harness import amd_samples


class AmdGapReportTests(unittest.TestCase):
    def test_markdown_includes_required_rows_and_warning(self):
        report = build_gap_report({}, samples=amd_samples())
        md = render_gap_markdown(report)
        self.assertIn("ionic detected", md)
        self.assertIn("representors detected or not detected", md)
        self.assertIn("VM-to-VM forwarding readiness", md)
        self.assertIn(VM_TO_VM_NOT_PROVEN, md)
        self.assertEqual(report["final_status"], AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH)

    def test_no_samples_is_discovery_only(self):
        report = build_gap_report({})
        self.assertEqual(report["final_status"], AMD_DISCOVERY_ONLY)
        self.assertIn(VM_TO_VM_NOT_PROVEN, report["vm_to_vm_note"])


if __name__ == "__main__":
    unittest.main()

"""AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH when ionic exists but no control path."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando.capability_report import (
    AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH,
    AMD_READY_FOR_VM_TO_VM_DRY_RUN,
    build_gap_report,
)
from tests.vendor.harness import amd_samples


class AmdBlockedNoForwardingPathTests(unittest.TestCase):
    def test_ionic_without_representors_is_blocked(self):
        report = build_gap_report({}, samples=amd_samples())
        self.assertEqual(report["final_status"], AMD_BLOCKED_NO_FORWARDING_CONTROL_PATH)
        self.assertEqual(report["matrix"]["vm_to_vm_forwarding_readiness"], "discovery-required")

    def test_proven_control_path_ready(self):
        samples = amd_samples()
        samples["representors"] = ["ionicvf0"]
        samples["switchdev"] = True
        report = build_gap_report({}, samples=samples)
        self.assertEqual(report["final_status"], AMD_READY_FOR_VM_TO_VM_DRY_RUN)


if __name__ == "__main__":
    unittest.main()

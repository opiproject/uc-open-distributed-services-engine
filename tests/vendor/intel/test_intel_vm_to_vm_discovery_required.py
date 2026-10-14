"""Intel VM-to-VM stays discovery-required until capability is proven."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.intel_ipu.readiness import INTEL_VM_TO_VM_DISCOVERY_REQUIRED, evaluate_intel_readiness


class IntelVmToVmDiscoveryRequiredTests(unittest.TestCase):
    def test_unproven_vm_to_vm(self):
        result = evaluate_intel_readiness(
            {"opi": {"endpoint": "https://opi.example.invalid"}},
            samples={"vm_to_vm_forwarding_supported": False},
        )
        self.assertEqual(result.details["readiness_status"], INTEL_VM_TO_VM_DISCOVERY_REQUIRED)


if __name__ == "__main__":
    unittest.main()

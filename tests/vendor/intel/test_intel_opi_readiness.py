"""Intel OPI / DPU Operator readiness (fixtures, no kubectl)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config
from plugins.intel_ipu.readiness import (
    INTEL_NOT_READY,
    INTEL_READY_FOR_OPI_DRY_RUN,
    INTEL_VM_TO_VM_DISCOVERY_REQUIRED,
    evaluate_intel_readiness,
)

FIXTURES = ROOT / "tests" / "fixtures" / "intel_ipu"


class IntelOpiReadinessTests(unittest.TestCase):
    def test_example_profile(self):
        cfg = load_vm2vm_config(ROOT / "config" / "lab-intel-vm2vm.example.yaml")
        self.assertEqual(cfg["vendor"]["profile"], "intel-ipu-opi-dpu-operator")
        self.assertEqual(cfg["intel"]["vm_to_vm_forwarding_default"], "discovery-required")
        self.assertFalse(cfg["intel"]["allow_ovs_assumption"])

    def test_vm_to_vm_discovery_required_by_default(self):
        result = evaluate_intel_readiness(
            {
                "opi": {"endpoint": "https://opi.example.invalid:443"},
                "intel": {"require_opi": True},
            },
            samples={"lspci": (FIXTURES / "lspci.txt").read_text(encoding="utf-8")},
        )
        self.assertEqual(result.details["readiness_status"], INTEL_VM_TO_VM_DISCOVERY_REQUIRED)

    def test_ready_when_vm_to_vm_proven_in_samples(self):
        result = evaluate_intel_readiness(
            {"opi": {"endpoint": "https://opi.example.invalid:443"}},
            samples={
                "lspci": (FIXTURES / "lspci.txt").read_text(encoding="utf-8"),
                "vm_to_vm_forwarding_supported": True,
            },
        )
        self.assertEqual(result.details["readiness_status"], INTEL_READY_FOR_OPI_DRY_RUN)

    def test_ovs_assumption_is_not_ready(self):
        result = evaluate_intel_readiness({"intel": {"allow_ovs_assumption": True}})
        self.assertEqual(result.details["readiness_status"], INTEL_NOT_READY)


if __name__ == "__main__":
    unittest.main()

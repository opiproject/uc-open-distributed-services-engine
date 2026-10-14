"""AMD Pensando discovery-first readiness (fixtures, no DSC)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config
from plugins.amd_pensando.readiness import (
    AMD_IONIC_MISSING,
    AMD_NOT_READY,
    AMD_PENSANDO_DISCOVERY_READY,
    evaluate_amd_readiness,
)
from tests.vendor.harness import amd_samples


class AmdPensandoReadinessTests(unittest.TestCase):
    def test_example_profile(self):
        cfg = load_vm2vm_config(ROOT / "config" / "lab-amd-vm2vm.example.yaml")
        self.assertEqual(cfg["vendor"]["profile"], "amd-pensando-discovery-first")
        self.assertEqual(cfg["amd_pensando"]["mode"], "discovery-first")
        self.assertFalse(cfg["amd_pensando"]["allow_assumed_forwarding"])

    def test_discovery_ready_with_ionic_samples(self):
        result = evaluate_amd_readiness({"amd_pensando": {"require_ionic": True}}, samples=amd_samples())
        self.assertEqual(result.details["readiness_status"], AMD_PENSANDO_DISCOVERY_READY)

    def test_assumed_forwarding_not_ready(self):
        result = evaluate_amd_readiness({"amd_pensando": {"allow_assumed_forwarding": True}})
        self.assertEqual(result.details["readiness_status"], AMD_NOT_READY)

    def test_ionic_missing_when_probed(self):
        result = evaluate_amd_readiness(
            {"amd_pensando": {"require_ionic": True}},
            samples={"lsmod": "e1000 1 0\n", "ethtool_i": {"eth0": "driver: ice\n"}},
        )
        self.assertEqual(result.details["readiness_status"], AMD_IONIC_MISSING)


if __name__ == "__main__":
    unittest.main()

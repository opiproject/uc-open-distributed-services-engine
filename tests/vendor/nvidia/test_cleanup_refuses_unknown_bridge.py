"""Cleanup must not delete a DPU/host bridge it did not create."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config
from plugins.nvidia_bluefield.dpu_ovs import UNKNOWN_BRIDGE_REFUSED, plan_dpu_ovs_cleanup

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"


class CleanupRefusesUnknownBridgeTests(unittest.TestCase):
    def test_refuses_unknown_bridge(self):
        cfg = load_vm2vm_config(NVIDIA_EXAMPLE)
        result = plan_dpu_ovs_cleanup(
            cfg,
            owned_bridge="br-underlay",
            owned_ports=["pf0vf0"],
            lab_owned_bridge=False,
            dry_run=True,
        )
        self.assertEqual(result.details.get("dpu_ovs_status"), UNKNOWN_BRIDGE_REFUSED)
        self.assertTrue(result.details.get("refused_del_br"))
        joined = " ".join(" ".join(c.argv or []) for c in result.commands)
        self.assertNotIn("del-br", joined)

    def test_deletes_only_when_lab_owned(self):
        cfg = load_vm2vm_config(NVIDIA_EXAMPLE)
        result = plan_dpu_ovs_cleanup(
            cfg,
            owned_bridge="br-int",
            owned_ports=["pf0vf0"],
            lab_owned_bridge=True,
            dry_run=True,
        )
        self.assertNotEqual(result.message, UNKNOWN_BRIDGE_REFUSED)
        joined = " ".join(" ".join(c.argv or []) for c in result.commands)
        self.assertIn("del-br", joined)
        self.assertIn("del-port", joined)
        self.assertIn("br-int", joined)


if __name__ == "__main__":
    unittest.main()

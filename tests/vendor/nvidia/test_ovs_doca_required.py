"""OVS-DOCA is required when nvidia.datapath is ovs-doca."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config
from plugins.nvidia_bluefield.dpu_ovs import OVS_DOCA_AVAILABLE, OVS_DOCA_MISSING, validate_dpu_ovs_environment


class OvsDocaRequiredTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_vm2vm_config(ROOT / "config" / "lab-nvidia-vm2vm.example.yaml")

    def test_doca_missing_errors(self):
        result = validate_dpu_ovs_environment(
            self.cfg,
            samples={
                "ovs-vsctl": True,
                "ovs-appctl": True,
                "systemctl_is_active": "active",
                "ovs-vsctl_show": "Bridge br-int\n",
                "datapath_types": "system netdev",
            },
        )
        self.assertEqual(result.details["dpu_ovs_status"], OVS_DOCA_MISSING)
        self.assertEqual(result.status, "error")

    def test_doca_listed(self):
        result = validate_dpu_ovs_environment(
            self.cfg,
            samples={
                "ovs-vsctl": True,
                "ovs-appctl": True,
                "systemctl_is_active": "active",
                "ovs-vsctl_show": "Bridge br-int\n",
                "datapath_types": "system doca",
            },
        )
        self.assertEqual(result.details["dpu_ovs_status"], OVS_DOCA_AVAILABLE)

    def test_fallback_ok(self):
        result = validate_dpu_ovs_environment(
            self.cfg,
            samples={
                "ovs-vsctl": True,
                "datapath_types": "system",
                "ovs-vsctl_show": "x",
            },
            allow_doca_fallback=True,
        )
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.details["dpu_ovs_status"], OVS_DOCA_MISSING)

"""DPU-side OVS validation from samples (no SSH)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config
from plugins.nvidia_bluefield.dpu_ovs import (
    OVS_AVAILABLE,
    OVS_DOCA_AVAILABLE,
    OVS_DOCA_MISSING,
    OVS_MISSING,
    OVS_SERVICE_NOT_RUNNING,
    validate_dpu_ovs_environment,
)


class DpuOvsValidationTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_vm2vm_config(ROOT / "config" / "lab-nvidia-vm2vm.example.yaml")

    def test_ovs_available(self):
        cfg = dict(self.cfg)
        cfg["nvidia"] = dict(self.cfg["nvidia"])
        cfg["nvidia"]["datapath"] = "ovs-tc"
        result = validate_dpu_ovs_environment(
            cfg,
            samples={
                "ovs-vsctl": True,
                "ovs-appctl": True,
                "systemctl_is_active": "active",
                "ovs-vsctl_show": "Bridge br-int\n",
                "datapath_types": "system netdev",
            },
        )
        self.assertEqual(result.details["dpu_ovs_status"], OVS_AVAILABLE)

    def test_ovs_missing(self):
        result = validate_dpu_ovs_environment(self.cfg, samples={"ovs-vsctl": False})
        self.assertEqual(result.details["dpu_ovs_status"], OVS_MISSING)
        self.assertEqual(result.status, "error")

    def test_inactive_systemd_unit_does_not_override_a_real_show(self):
        """Real host2/BlueField-2 evidence: `systemctl is-active openvswitch`
        reports "inactive" (this BFB image does not manage ovs-vswitchd
        under that unit name) while `ovs-vsctl show` succeeds with real
        bridges/ports. A working `show` is stronger evidence than a
        systemd unit-name guess and must not be overridden by it."""
        cfg = dict(self.cfg)
        cfg["nvidia"] = dict(self.cfg["nvidia"])
        cfg["nvidia"]["datapath"] = "system"
        result = validate_dpu_ovs_environment(
            cfg,
            samples={
                "ovs-vsctl": True,
                "ovs-appctl": True,
                "systemctl_is_active": "inactive",
                "ovs-vsctl_show": "Bridge ovsbr1\n    Port pf0hpf\n",
                "datapath_types": "netdev system",
            },
        )
        self.assertEqual(result.details["dpu_ovs_status"], OVS_AVAILABLE)
        self.assertEqual(result.status, "ok")

    def test_inactive_systemd_unit_with_no_show_still_errors(self):
        """Without a real `show`, an inactive service is still treated as
        OVS not running -- the fix only defers to a *successful* show."""
        result = validate_dpu_ovs_environment(
            self.cfg,
            samples={
                "ovs-vsctl": True,
                "ovs-appctl": True,
                "systemctl_is_active": "inactive",
                "ovs-vsctl_show": "",
                "datapath_types": "",
            },
        )
        self.assertEqual(result.details["dpu_ovs_status"], OVS_SERVICE_NOT_RUNNING)
        self.assertEqual(result.status, "error")

    def test_validate_config_does_not_plan_host_add_br(self):
        result = validate_dpu_ovs_environment(self.cfg, dry_run=True)
        self.assertTrue(result.details.get("host_ovs_untouched"))
        self.assertTrue(result.commands)
        for cmd in result.commands:
            argv = list(cmd.argv or [])
            self.assertIn("ssh", argv)
            self.assertNotEqual(argv[:1], ["ovs-vsctl"])
            self.assertFalse(cmd.executed)
            self.assertNotIn("add-br", argv)

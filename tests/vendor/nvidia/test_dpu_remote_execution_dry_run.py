"""NVIDIA DPU-mode remote execution stays dry-run."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config
from plugins.nvidia_bluefield.backend import NvidiaBlueFieldBackend


class DpuRemoteDryRunTests(unittest.TestCase):
    def test_representors_and_ovs_plan_ssh_not_execute(self):
        cfg = load_vm2vm_config(ROOT / "config" / "lab-nvidia-vm2vm.example.yaml")
        backend = NvidiaBlueFieldBackend(lab_config=cfg)
        reps = backend.discover_representors()
        self.assertEqual(reps.details.get("location"), "dpu")
        self.assertFalse(reps.executed)
        self.assertTrue(any("ssh" in (c.argv or []) for c in reps.commands))
        for cmd in reps.commands:
            self.assertFalse(cmd.executed)
        ovs = backend.configure_ovs_bridge("br-int")
        self.assertFalse(ovs.executed)
        self.assertTrue(ovs.commands)
        for cmd in ovs.commands:
            self.assertFalse(cmd.executed)
            self.assertIn("ssh", cmd.argv or [])
            self.assertNotEqual((cmd.argv or [])[:1], ["ovs-vsctl"])
        tel = backend.collect_telemetry()
        self.assertEqual(tel.details.get("location"), "dpu")
        self.assertEqual(tel.telemetry, [])

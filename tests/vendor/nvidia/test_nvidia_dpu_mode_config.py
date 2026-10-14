"""NVIDIA DPU-mode example config (fixtures only)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config, validate_vm2vm_config


class NvidiaDpuModeConfigTests(unittest.TestCase):
    def test_default_example_is_dpu_mode(self):
        cfg = load_vm2vm_config(ROOT / "config" / "lab-nvidia-vm2vm.example.yaml")
        self.assertEqual(cfg["vendor"]["profile"], "nvidia-bluefield-dpu-mode")
        self.assertEqual(cfg["nvidia"]["mode"], "dpu")
        self.assertEqual(cfg["nvidia"]["ovs_location"], "dpu")
        self.assertEqual(cfg["nvidia"]["representor_location"], "dpu")
        self.assertEqual(cfg["nvidia"]["datapath"], "ovs-doca")
        self.assertFalse(cfg["host_forwarding_control"]["enabled"])
        self.assertTrue(cfg["dpu_forwarding_control"]["enabled"])
        self.assertIn("kvm", cfg["host"]["role"])
        self.assertIn("forwarding-owner", cfg["dpu"]["role"])
        self.assertEqual(validate_vm2vm_config(cfg), [])

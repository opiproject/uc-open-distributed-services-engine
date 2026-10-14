"""Reject host OVS forwarding in NVIDIA DPU mode."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import NVIDIA_DPU_MODE_ERROR, load_vm2vm_config, validate_vm2vm_config


class NvidiaRejectHostOvsTests(unittest.TestCase):
    def test_host_ovs_flag(self):
        cfg = copy.deepcopy(
            load_vm2vm_config(ROOT / "config" / "lab-nvidia-vm2vm.example.yaml")
        )
        cfg["nvidia"]["allow_host_ovs_for_forwarding"] = True
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(any(NVIDIA_DPU_MODE_ERROR in i for i in issues))

    def test_host_forwarding_control(self):
        cfg = copy.deepcopy(
            load_vm2vm_config(ROOT / "config" / "lab-nvidia-vm2vm.example.yaml")
        )
        cfg["host_forwarding_control"]["enabled"] = True
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(any(NVIDIA_DPU_MODE_ERROR in i for i in issues))

    def test_ovs_location_host(self):
        cfg = copy.deepcopy(
            load_vm2vm_config(ROOT / "config" / "lab-nvidia-vm2vm.example.yaml")
        )
        cfg["nvidia"]["ovs_location"] = "host"
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(any(NVIDIA_DPU_MODE_ERROR in i for i in issues))

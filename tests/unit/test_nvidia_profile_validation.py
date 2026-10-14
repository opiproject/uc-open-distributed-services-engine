"""NVIDIA DPU-mode profile validation (no hardware)."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import (  # noqa: E402
    NVIDIA_DPU_MODE_ERROR,
    NVIDIA_DPU_PROFILE,
    load_vm2vm_config,
    validate_nvidia_dpu_mode,
    validate_vm2vm_config,
)

NVIDIA_EXAMPLE = ROOT / "config" / "lab-nvidia-vm2vm.example.yaml"
DPU_PROFILE = ROOT / "config" / "profiles" / "nvidia-bluefield-dpu-mode.yaml"


class NvidiaProfileValidationTests(unittest.TestCase):
    def test_example_passes(self):
        config = load_vm2vm_config(NVIDIA_EXAMPLE)
        self.assertEqual(validate_nvidia_dpu_mode(config), [])
        self.assertEqual(validate_vm2vm_config(config), [])
        self.assertEqual(config["vendor"]["profile"], NVIDIA_DPU_PROFILE)

    def test_profile_file_exists(self):
        self.assertTrue(DPU_PROFILE.is_file())
        profile = load_vm2vm_config(DPU_PROFILE)
        self.assertEqual(profile["nvidia"]["ovs_location"], "dpu")

    def test_host_ovs_rejected(self):
        cfg = copy.deepcopy(load_vm2vm_config(NVIDIA_EXAMPLE))
        cfg["nvidia"]["allow_host_ovs_for_forwarding"] = True
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(issues)
        self.assertTrue(any(NVIDIA_DPU_MODE_ERROR in i for i in issues))

    def test_missing_dpu_forwarding_rejected(self):
        cfg = copy.deepcopy(load_vm2vm_config(NVIDIA_EXAMPLE))
        cfg["dpu_forwarding_control"]["enabled"] = False
        issues = validate_nvidia_dpu_mode(cfg)
        self.assertTrue(any(NVIDIA_DPU_MODE_ERROR in i for i in issues))

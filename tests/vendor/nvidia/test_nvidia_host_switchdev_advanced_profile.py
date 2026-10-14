"""Advanced NVIDIA host-switchdev profile is explicit, not default."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import (
    NVIDIA_HOST_SWITCHDEV_PROFILE,
    load_vm2vm_config,
    validate_nvidia_dpu_mode,
    validate_vm2vm_config,
)


class NvidiaHostSwitchdevProfileTests(unittest.TestCase):
    def test_profile_file(self):
        path = ROOT / "config" / "profiles" / "nvidia-connectx-host-switchdev.yaml"
        profile = load_vm2vm_config(path)
        self.assertEqual(profile["profile"], NVIDIA_HOST_SWITCHDEV_PROFILE)
        self.assertEqual(profile["nvidia"]["mode"], "host-switchdev")
        self.assertTrue(profile["nvidia"]["allow_host_ovs_for_forwarding"])
        self.assertTrue(profile["host_forwarding_control"]["enabled"])
        self.assertFalse(profile["dpu_forwarding_control"]["enabled"])
        self.assertEqual(profile["representor_mapping"]["location"], "host")
        self.assertEqual(validate_nvidia_dpu_mode(profile), [])

    def test_not_the_vm2vm_default(self):
        example = load_vm2vm_config(ROOT / "config" / "lab-nvidia-vm2vm.example.yaml")
        self.assertNotEqual(example["vendor"]["profile"], NVIDIA_HOST_SWITCHDEV_PROFILE)
        self.assertEqual(validate_vm2vm_config(example), [])

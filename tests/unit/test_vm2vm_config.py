"""VM2VM lab config loader/validator (no hardware)."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import (  # noqa: E402
    SCHEMA_ID,
    config_dry_run,
    get_vm,
    get_vm_by_vf_index,
    load_vm2vm_config,
    preserved_interfaces,
    public_vm2vm_view,
    validate_vm2vm_config,
)

CONFIG_DIR = ROOT / "config"
EXAMPLES = {
    "nvidia": CONFIG_DIR / "lab-nvidia-vm2vm.example.yaml",
    "marvell": CONFIG_DIR / "lab-marvell-vm2vm.example.yaml",
    "intel": CONFIG_DIR / "lab-intel-vm2vm.example.yaml",
    "amd": CONFIG_DIR / "lab-amd-vm2vm.example.yaml",
}


class ExampleFilesTests(unittest.TestCase):
    def test_all_examples_parse_and_are_valid(self):
        for vendor, path in EXAMPLES.items():
            with self.subTest(vendor=vendor):
                config = load_vm2vm_config(path)
                self.assertEqual(config["schema"], SCHEMA_ID)
                issues = validate_vm2vm_config(config)
                self.assertEqual(issues, [], "unexpected issues for %s: %s" % (vendor, issues))

    def test_nvidia_example_fields(self):
        config = load_vm2vm_config(EXAMPLES["nvidia"])
        self.assertEqual(config["vendor"]["type"], "nvidia-bluefield")
        self.assertEqual(config["vendor"]["profile"], "nvidia-bluefield-dpu-mode")
        self.assertEqual(config["nvidia"]["mode"], "dpu")
        self.assertEqual(config["nvidia"]["ovs_location"], "dpu")
        self.assertFalse(config["nvidia"]["allow_host_ovs_for_forwarding"])
        self.assertFalse(config["host_forwarding_control"]["enabled"])
        self.assertTrue(config["dpu_forwarding_control"]["enabled"])
        self.assertEqual(config["dpu"]["num_vfs"], 2)
        self.assertTrue(config_dry_run(config))
        vm1 = get_vm(config, "odse-vm2vm-vm1")
        self.assertIsNotNone(vm1)
        self.assertEqual(vm1["vf_index"], 0)
        vm2 = get_vm_by_vf_index(config, 1)
        self.assertEqual(vm2["name"], "odse-vm2vm-vm2")
        self.assertEqual(preserved_interfaces(config), [config["host"]["management_interface"]])

    def test_public_view_has_no_secrets_and_expected_fields(self):
        config = load_vm2vm_config(EXAMPLES["nvidia"])
        view = public_vm2vm_view(config)
        self.assertEqual(view["vendor_type"], "nvidia-bluefield")
        self.assertEqual(view["num_vfs"], 2)
        self.assertIn("odse-vm2vm-vm1", view["vm_names"])
        self.assertIn("odse-vm2vm-vm2", view["vm_names"])


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.base = load_vm2vm_config(EXAMPLES["nvidia"])

    def test_wrong_schema_flagged(self):
        cfg = copy.deepcopy(self.base)
        cfg["schema"] = "something-else/v1"
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(any("schema" in issue for issue in issues))

    def test_num_vfs_must_be_two(self):
        cfg = copy.deepcopy(self.base)
        cfg["dpu"]["num_vfs"] = 4
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(any("num_vfs" in issue for issue in issues))

    def test_management_interface_equals_pf_is_rejected(self):
        cfg = copy.deepcopy(self.base)
        cfg["host"]["management_interface"] = cfg["dpu"]["pf_name"]
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(any("management_interface" in issue for issue in issues))

    def test_management_interface_as_ip_address_is_rejected(self):
        cfg = copy.deepcopy(self.base)
        cfg["host"]["management_interface"] = "172.22.1.2"
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(
            any("management_interface" in issue and "IP address" in issue for issue in issues)
        )

    def test_management_interface_placeholder_is_not_flagged_as_ip(self):
        cfg = copy.deepcopy(self.base)
        cfg["host"]["management_interface"] = "<MGMT_IFACE>"
        issues = validate_vm2vm_config(cfg)
        self.assertFalse(any("IP address" in issue for issue in issues))

    def test_management_interface_netdev_name_is_not_flagged_as_ip(self):
        cfg = copy.deepcopy(self.base)
        cfg["host"]["management_interface"] = "ens10f0np0"
        issues = validate_vm2vm_config(cfg)
        self.assertFalse(any("IP address" in issue for issue in issues))

    def test_duplicate_vf_index_is_rejected(self):
        cfg = copy.deepcopy(self.base)
        cfg["vms"][1]["vf_index"] = cfg["vms"][0]["vf_index"]
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(any("duplicates" in issue for issue in issues))

    def test_wrong_vm_count_is_rejected(self):
        cfg = copy.deepcopy(self.base)
        cfg["vms"] = cfg["vms"][:1]
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(any("exactly 2 entries" in issue for issue in issues))

    def test_relaxed_safety_fields_are_rejected(self):
        for field_name in ("require_apply", "forbid_management_interface_changes"):
            with self.subTest(field=field_name):
                cfg = copy.deepcopy(self.base)
                cfg["safety"][field_name] = False
                issues = validate_vm2vm_config(cfg)
                self.assertTrue(any(field_name in issue for issue in issues))

    def test_missing_required_host_field_is_rejected(self):
        cfg = copy.deepcopy(self.base)
        del cfg["host"]["ssh_user"]
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(any("host.ssh_user" in issue for issue in issues))

    def test_dotted_pf_pci_is_rejected(self):
        cfg = copy.deepcopy(self.base)
        cfg["dpu"]["pf_pci"] = "0000.0d.00.0"
        issues = validate_vm2vm_config(cfg)
        self.assertTrue(any("dpu.pf_pci" in issue and "colons" in issue for issue in issues))

    def test_colon_pf_pci_is_accepted(self):
        cfg = copy.deepcopy(self.base)
        cfg["dpu"]["pf_pci"] = "0000:0d:00.0"
        issues = validate_vm2vm_config(cfg)
        self.assertFalse(any("dpu.pf_pci" in issue for issue in issues))
        issues = validate_vm2vm_config(None)
        self.assertEqual(issues, ["config must be a YAML mapping"])


if __name__ == "__main__":
    unittest.main()

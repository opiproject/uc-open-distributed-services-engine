"""VF assignment uniqueness and IOMMU/VFIO planning (no hardware)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm_vfio import (
    assignment_conflicts,
    hostdev_xml_snippet,
    iommu_vfio_readiness,
    resolve_vf_pci,
    wait_for_boot_requested,
)


class VfAssignmentPlanTests(unittest.TestCase):
    def test_duplicate_pci_is_refused(self):
        errors = assignment_conflicts(
            [
                {"name": "vm1", "vf_pci": "0000:03:00.2"},
                {"name": "vm2", "vf_pci": "0000:03:00.2"},
            ]
        )
        self.assertTrue(any("already assigned" in e for e in errors))

    def test_duplicate_names_refused(self):
        errors = assignment_conflicts(
            [
                {"name": "same", "vf_pci": "0000:03:00.2"},
                {"name": "same", "vf_pci": "0000:03:00.3"},
            ]
        )
        self.assertTrue(any("duplicate VM name" in e for e in errors))

    def test_resolve_vf_pci_from_mapping_not_guess(self):
        vm = {"name": "vm1", "vf_index": 0, "vf_pci": None}
        mapping = {
            "mappings": [
                {"vf_index": 0, "vf_pci": "0000:03:00.2"},
                {"vf_index": 1, "vf_pci_address": "0000:03:00.3"},
            ]
        }
        self.assertEqual(resolve_vf_pci(vm, mapping), "0000:03:00.2")
        self.assertIsNone(resolve_vf_pci({"name": "vm1", "vf_index": 9, "vf_pci": None}, mapping))
        self.assertIsNone(resolve_vf_pci({"name": "vm1", "vf_index": 0, "vf_pci": "<PF_PCI>"}, None))

    def test_iommu_from_samples_does_not_invent_ready(self):
        unknown = iommu_vfio_readiness(sys_root="/does/not/exist")
        self.assertIsNone(unknown["ready"])
        self.assertEqual(unknown["status"], "UNKNOWN")
        with tempfile.TemporaryDirectory() as tmp:
            groups_dir = Path(tmp) / "iommu_groups"
            (groups_dir / "0").mkdir(parents=True)
            confirmed = iommu_vfio_readiness(
                samples={
                    "cmdline": "BOOT_IMAGE=/vmlinuz intel_iommu=on iommu=pt",
                    "iommu_groups_dir": str(groups_dir),
                    "vfio_pci_module": "present",
                }
            )
        self.assertTrue(confirmed["ready"])
        negative = iommu_vfio_readiness(
            samples={"cmdline": "BOOT_IMAGE=/vmlinuz", "vfio_pci_module": "absent"}
        )
        self.assertFalse(negative["iommu_on_cmdline"])

    def test_iommu_groups_dir_present_but_empty_is_not_ready(self):
        """Regression: an existing-but-empty iommu_groups dir (VT-d/AMD-Vi
        not actually enabled in firmware despite intel_iommu=on/amd_iommu=on
        on the kernel command line) must not be reported as READY just
        because the directory itself exists."""
        with tempfile.TemporaryDirectory() as tmp:
            groups_dir = Path(tmp) / "iommu_groups"
            groups_dir.mkdir()
            result = iommu_vfio_readiness(
                samples={
                    "cmdline": "BOOT_IMAGE=/vmlinuz intel_iommu=on iommu=pt",
                    "iommu_groups_dir": str(groups_dir),
                    "vfio_pci_module": "present",
                }
            )
        self.assertFalse(result["iommu_groups_present"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["status"], "NOT_READY")

    def test_iommu_groups_dir_missing_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_dir = str(Path(tmp) / "does-not-exist")
            result = iommu_vfio_readiness(
                samples={
                    "cmdline": "BOOT_IMAGE=/vmlinuz intel_iommu=on iommu=pt",
                    "iommu_groups_dir": missing_dir,
                    "vfio_pci_module": "present",
                }
            )
        self.assertFalse(result["iommu_groups_present"])
        self.assertEqual(result["status"], "NOT_READY")

    def test_live_sys_root_empty_iommu_groups_dir_is_not_ready(self):
        """Same regression, exercised via the live sys_root code path
        (os.listdir) rather than the samples override."""
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "kernel" / "iommu_groups").mkdir(parents=True)
            (Path(tmp) / "module" / "vfio_pci").mkdir(parents=True)
            result = iommu_vfio_readiness(
                samples={"cmdline": "BOOT_IMAGE=/vmlinuz intel_iommu=on iommu=pt"},
                sys_root=tmp,
            )
        self.assertFalse(result["iommu_groups_present"])
        self.assertEqual(result["status"], "NOT_READY")

    def test_live_sys_root_populated_iommu_groups_dir_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            groups_dir = Path(tmp) / "kernel" / "iommu_groups"
            (groups_dir / "0").mkdir(parents=True)
            (Path(tmp) / "module" / "vfio_pci").mkdir(parents=True)
            result = iommu_vfio_readiness(
                samples={"cmdline": "BOOT_IMAGE=/vmlinuz intel_iommu=on iommu=pt"},
                sys_root=tmp,
            )
        self.assertTrue(result["iommu_groups_present"])
        self.assertEqual(result["status"], "READY")

    def test_hostdev_snippet(self):
        xml = hostdev_xml_snippet("0000:03:00.2", "52:54:00:00:00:11")
        self.assertIn("hostdev", xml)
        self.assertIn("0x03", xml)
        self.assertIn("function='0x2'", xml)

    def test_wait_for_boot_default_false(self):
        self.assertFalse(wait_for_boot_requested({}))
        self.assertTrue(wait_for_boot_requested({"create_vms": {"wait_for_boot": True}}))


if __name__ == "__main__":
    unittest.main()

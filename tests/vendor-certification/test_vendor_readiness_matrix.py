"""Cross-vendor readiness matrix over example configs (no hardware)."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "odse_vendor_readiness_matrix",
    ROOT / "tools" / "vendor_readiness_matrix.py",
)
matrix = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
sys.modules[_SPEC.name] = matrix
_SPEC.loader.exec_module(matrix)

CONFIG = ROOT / "config"


class VendorReadinessMatrixTests(unittest.TestCase):
    def test_example_configs_do_not_overclaim(self):
        paths = [
            CONFIG / "lab-nvidia-vm2vm.example.yaml",
            CONFIG / "lab-marvell-vm2vm.example.yaml",
            CONFIG / "lab-intel-vm2vm.example.yaml",
            CONFIG / "lab-amd-vm2vm.example.yaml",
        ]
        report = matrix.build_matrix(paths)
        by_vendor = {row["vendor"]: row for row in report["rows"]}
        self.assertEqual(by_vendor["nvidia-bluefield"]["status"], matrix.GO_FOR_HARDWARE_DRY_RUN)
        self.assertNotEqual(by_vendor["marvell-octeon"]["status"], matrix.GO_FOR_HARDWARE_DRY_RUN)
        self.assertEqual(by_vendor["intel-ipu"]["status"], matrix.GO_FOR_DISCOVERY_ONLY)
        self.assertIn(
            by_vendor["amd-pensando"]["status"],
            (matrix.GO_FOR_DISCOVERY_ONLY, matrix.BLOCKED_MISSING_CONTROL_PATH),
        )
        self.assertIn("discovery-required", by_vendor["intel-ipu"]["vm_to_vm_readiness"])

    def test_markdown_has_status_column(self):
        report = matrix.build_matrix([CONFIG / "lab-nvidia-vm2vm.example.yaml"])
        md = matrix.render_markdown(report)
        self.assertIn("GO_FOR_HARDWARE_DRY_RUN", md)
        self.assertIn("nvidia-bluefield", md)


if __name__ == "__main__":
    unittest.main()

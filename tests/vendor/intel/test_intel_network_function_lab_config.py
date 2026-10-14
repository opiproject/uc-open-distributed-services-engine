"""Intel network-function lab config (intel-nf, not VM-to-VM)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config
from plugins.intel_ipu.network_function import validate_network_function_config


class IntelNetworkFunctionLabConfigTests(unittest.TestCase):
    def test_example_has_network_function_block(self):
        cfg = load_vm2vm_config(ROOT / "config" / "lab-intel-network-function-offload.example.yaml")
        self.assertEqual(cfg["vendor"]["type"], "intel-ipu")
        self.assertEqual(cfg["network_function"]["placement"], "ipu")
        self.assertEqual(cfg["network_function"]["type"], "proxy")
        self.assertEqual(validate_network_function_config(cfg), [])

    def test_missing_network_function_is_invalid(self):
        issues = validate_network_function_config({"vendor": {"type": "intel-ipu"}})
        self.assertTrue(issues)


if __name__ == "__main__":
    unittest.main()

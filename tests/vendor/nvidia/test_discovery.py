"""NVIDIA discovery from tests/fixtures/nvidia_bluefield (no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.nvidia_bluefield.discovery import discover, parse_lspci_nn
from tests.vendor.harness import nvidia_samples, read_fixture, row_from_discovery


class NvidiaDiscoveryFixtureTests(unittest.TestCase):
    def test_parse_lspci_fixture(self):
        rows = parse_lspci_nn(read_fixture("nvidia_bluefield", "lspci.txt"))
        self.assertTrue(any(r["pci_address"] == "0000:03:00.0" for r in rows))

    def test_inventory_and_capability_report(self):
        result = discover(samples=nvidia_samples())
        self.assertTrue(result.devices)
        self.assertTrue(result.representors)
        self.assertNotIn("eth0v1", [r.netdev for r in result.representors])
        self.assertIsNone(result.capabilities.switchdev)
        self.assertIsNone(result.capabilities.ovs_hw_offload)

        row = row_from_discovery("nvidia-bluefield", result)
        self.assertTrue(row.detected)
        self.assertTrue(row.representors_available)
        self.assertTrue(row.ovs_available)
        self.assertIsNone(row.ovs_hw_offload_available)
        self.assertIsNone(row.switchdev_available)
        self.assertIsNone(row.vdpa_available)


if __name__ == "__main__":
    unittest.main()

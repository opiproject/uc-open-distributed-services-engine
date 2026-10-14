"""Marvell OCTEON discovery from tests/fixtures/marvell_octeon (no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.marvell_octeon import discovery, dpdk
from tests.vendor.harness import marvell_samples, read_fixture, row_from_discovery


class MarvellDiscoveryFixtureTests(unittest.TestCase):
    def test_parse_lspci_and_devbind(self):
        rows = discovery.parse_lspci_nn(read_fixture("marvell_octeon", "lspci.txt"))
        self.assertEqual(rows[0]["pci_address"], "0000:05:00.0")
        ports = dpdk.parse_devbind(read_fixture("marvell_octeon", "dpdk_devbind.txt"))
        self.assertTrue(ports)

    def test_inventory_and_capability_report(self):
        result = discovery.discover(
            samples=marvell_samples(),
            command_exists_fn=lambda _n: False,
        )
        self.assertEqual(result.devices[0].vendor, "marvell")
        self.assertIsNone(result.capabilities.switchdev)
        self.assertEqual(result.representors, [])

        row = row_from_discovery("marvell-octeon", result)
        self.assertTrue(row.detected)
        self.assertTrue(row.ovs_available)
        self.assertIsNone(row.switchdev_available)
        self.assertIsNone(row.representors_available)
        self.assertIsNone(row.ovs_hw_offload_available)
        self.assertIsNone(row.vdpa_available)


if __name__ == "__main__":
    unittest.main()

"""AMD Pensando discovery from tests/fixtures/amd_pensando (no DSC)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando import discovery
from tests.vendor.harness import amd_samples, read_fixture, row_from_discovery


class AmdDiscoveryFixtureTests(unittest.TestCase):
    def test_host_bridge_not_dsc(self):
        result = discovery.build_inventory(
            lspci="0000:00:01.0 Host bridge: Advanced Micro Devices, Inc. [AMD]\n"
        )
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.devices, [])

    def test_inventory_and_capability_report(self):
        result = discovery.discover(samples=amd_samples())
        self.assertEqual(result.devices[0].vendor, "amd")
        self.assertEqual(result.representors, [])
        self.assertIsNone(result.capabilities.switchdev)
        self.assertIsNone(result.capabilities.vdpa)

        row = row_from_discovery("amd-pensando", result)
        self.assertTrue(row.detected)
        self.assertTrue(row.rdma_available)
        self.assertTrue(row.telemetry_available)
        self.assertIsNone(row.switchdev_available)
        self.assertIsNone(row.representors_available)
        self.assertIsNone(row.ovs_hw_offload_available)
        self.assertIsNone(row.vdpa_available)
        self.assertIn("eth0", result.details["ionic_interfaces"])
        self.assertTrue(read_fixture("amd_pensando", "lspci.txt"))


if __name__ == "__main__":
    unittest.main()

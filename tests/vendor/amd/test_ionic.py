"""AMD ionic / RDMA parsers from tests/fixtures/amd_pensando (no DSC)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando import ionic, rdma
from plugins.base.models import OperationResult
from tests.vendor.harness import read_fixture, row_from_discovery


class AmdIonicFixtureTests(unittest.TestCase):
    def test_list_driver_stats(self):
        ei = {"eth0": read_fixture("amd_pensando", "ethtool_i.txt")}
        self.assertEqual(ionic.list_ionic_interfaces(ei), ["eth0"])
        info = ionic.get_ionic_driver_info("eth0", ei["eth0"])
        self.assertTrue(info.details["ionic"])
        stats = ionic.get_ionic_stats(
            "eth0", read_fixture("amd_pensando", "ethtool_s.txt")
        )
        names = {s.metric_name: s.value for s in stats.telemetry}
        self.assertEqual(names["tx_packets"], 42)
        skipped = ionic.get_ionic_driver_info("ens1", "driver: ice\n")
        self.assertEqual(skipped.status, "skipped")

    def test_rdma_optional(self):
        det = rdma.detect_ionic_rdma(
            rdma_link=read_fixture("amd_pensando", "rdma_link.txt"),
            lsmod=read_fixture("amd_pensando", "lsmod.txt"),
        )
        self.assertTrue(det.details["detected"])
        none = rdma.detect_ionic_rdma(rdma_link="", lsmod="e1000 1 0\n")
        self.assertFalse(none.details["detected"])

    def test_stats_are_not_offload_in_capability_report(self):
        stats = ionic.get_ionic_stats(
            "eth0", read_fixture("amd_pensando", "ethtool_s.txt")
        )
        result = OperationResult(
            operation="discover",
            vendor="amd",
            status="ok",
            telemetry=list(stats.telemetry),
            details={"ovs_hw_offload": False, "rdma": {"detected": True}},
        )
        row = row_from_discovery("amd-pensando", result)
        self.assertTrue(row.telemetry_available)
        self.assertTrue(row.rdma_available)
        self.assertIsNone(row.ovs_hw_offload_available)


if __name__ == "__main__":
    unittest.main()

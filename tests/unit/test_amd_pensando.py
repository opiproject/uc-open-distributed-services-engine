"""AMD Pensando discovery / ionic / rdma tests (fixtures, no DSC required)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.amd_pensando import discovery, ionic, rdma
from plugins.amd_pensando.backend import AmdPensandoBackend

FIXTURES = ROOT / "tests" / "fixtures" / "amd_pensando"


class HintTests(unittest.TestCase):
    def test_hints(self):
        self.assertTrue(discovery.looks_like_pensando("Pensando Systems DSC"))
        self.assertTrue(discovery.looks_like_pensando("driver: ionic"))
        self.assertFalse(discovery.looks_like_pensando("Advanced Micro Devices Host bridge"))
        self.assertFalse(discovery.looks_like_pensando("Mellanox ConnectX"))


class IonicTests(unittest.TestCase):
    def test_list_and_stats(self):
        ei = {"eth0": (FIXTURES / "ethtool_i.txt").read_text(encoding="utf-8")}
        self.assertEqual(ionic.list_ionic_interfaces(ei), ["eth0"])
        info = ionic.get_ionic_driver_info("eth0", ei["eth0"])
        self.assertTrue(info.details["ionic"])
        stats = ionic.get_ionic_stats(
            "eth0", (FIXTURES / "ethtool_s.txt").read_text(encoding="utf-8")
        )
        names = {s.metric_name: s.value for s in stats.telemetry}
        self.assertEqual(names["tx_packets"], 42)
        ice = ionic.get_ionic_driver_info("ens1", "driver: ice\nbus-info: 0000:01:00.0\n")
        self.assertEqual(ice.status, "skipped")


class RdmaTests(unittest.TestCase):
    def test_detect(self):
        det = rdma.detect_ionic_rdma(
            rdma_link=(FIXTURES / "rdma_link.txt").read_text(encoding="utf-8"),
            lsmod=(FIXTURES / "lsmod.txt").read_text(encoding="utf-8"),
        )
        self.assertTrue(det.details["detected"])
        listed = rdma.list_rdma_devices(
            (FIXTURES / "rdma_link.txt").read_text(encoding="utf-8")
        )
        self.assertEqual(listed.details["devices"][0]["netdev"], "eth0")
        none = rdma.detect_ionic_rdma(rdma_link="", lsmod="e1000 1 0\n")
        self.assertFalse(none.details["detected"])


class DiscoverTests(unittest.TestCase):
    def test_samples(self):
        result = discovery.discover(
            samples={
                "lspci": (FIXTURES / "lspci.txt").read_text(encoding="utf-8"),
                "ethtool_i": {
                    "eth0": (FIXTURES / "ethtool_i.txt").read_text(encoding="utf-8")
                },
                "ethtool_s": {
                    "eth0": (FIXTURES / "ethtool_s.txt").read_text(encoding="utf-8")
                },
                "rdma_link": (FIXTURES / "rdma_link.txt").read_text(encoding="utf-8"),
                "lsmod": (FIXTURES / "lsmod.txt").read_text(encoding="utf-8"),
            }
        )
        self.assertTrue(result.devices)
        self.assertEqual(result.devices[0].vendor, "amd")
        self.assertIsNone(result.devices[0].capabilities.switchdev)
        self.assertIsNone(result.devices[0].capabilities.vdpa)
        self.assertFalse(result.devices[0].capabilities.ovs_hw_offload)
        self.assertEqual(result.representors, [])
        self.assertIn("eth0", result.details["ionic_interfaces"])

    def test_amd_hostbridge_ignored(self):
        result = discovery.build_inventory(
            lspci="0000:00:01.0 Host bridge: Advanced Micro Devices, Inc. [AMD]\n"
        )
        self.assertEqual(result.devices, [])
        self.assertEqual(result.status, "skipped")

    def test_backend_skips_datapath(self):
        b = AmdPensandoBackend()
        self.assertEqual(b.configure_switchdev("eth0").status, "skipped")
        self.assertEqual(b.configure_ovs_bridge("br-int").status, "skipped")
        self.assertEqual(b.configure_vdpa("vf0").status, "skipped")
        self.assertIsNone(b.validate_offload().details["offload_state"])


if __name__ == "__main__":
    unittest.main()

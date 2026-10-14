"""Marvell OCTEON discovery / DAO / DPDK tests (fixtures, no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.marvell_octeon import dao_ovs, discovery, dpdk
from plugins.marvell_octeon.backend import MarvellOcteonBackend

FIXTURES = ROOT / "tests" / "fixtures" / "marvell_octeon"


class ParseTests(unittest.TestCase):
    def test_hints(self):
        self.assertTrue(discovery.looks_like_marvell("Marvell OCTEON TX2 CN10K"))
        self.assertTrue(discovery.looks_like_marvell("cnxk"))
        self.assertFalse(discovery.looks_like_marvell("Mellanox ConnectX"))

    def test_lspci_and_devbind(self):
        rows = discovery.parse_lspci_nn((FIXTURES / "lspci.txt").read_text(encoding="utf-8"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pci_address"], "0000:05:00.0")
        ports = dpdk.parse_devbind((FIXTURES / "dpdk_devbind.txt").read_text(encoding="utf-8"))
        self.assertEqual(len(ports), 2)
        dpdk_bound = [p for p in ports if p["binding"] == "dpdk"]
        self.assertEqual(dpdk_bound[0]["driver"], "vfio-pci")
        kern = [p for p in ports if p["binding"] == "kernel"][0]
        self.assertEqual(kern["iface"], "eth0")


class InventoryTests(unittest.TestCase):
    def test_discover_samples(self):
        result = discovery.discover(
            samples={
                "lspci": (FIXTURES / "lspci.txt").read_text(encoding="utf-8"),
                "dpdk_devbind": (FIXTURES / "dpdk_devbind.txt").read_text(encoding="utf-8"),
                "ovs": "Bridge br-int\n",
                "environ": {"DAO_PATH": "/opt/dao"},
            },
            command_exists_fn=lambda n: False,
        )
        self.assertEqual(len(result.devices), 1)
        self.assertEqual(result.devices[0].vendor, "marvell")
        self.assertIsNone(result.devices[0].capabilities.switchdev)
        self.assertTrue(result.details["dao"]["dao_env_present"])
        self.assertEqual(result.representors, [])

    def test_empty_skip(self):
        result = discovery.build_inventory(lspci="00:00.0 Host bridge: Intel\n")
        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.devices, [])


class DaoTests(unittest.TestCase):
    def test_check_dao_from_env(self):
        ok = dao_ovs.check_dao_available(
            environ={"DAO_HOME": "/opt/dao"}, command_exists_fn=lambda n: False
        )
        self.assertTrue(ok.details["available"])
        missing = dao_ovs.check_dao_available(
            environ={}, command_exists_fn=lambda n: False
        )
        self.assertFalse(missing.details["available"])

    def test_placeholders_need_apply_and_discovery(self):
        skipped = dao_ovs.configure_representor_placeholder("port0", apply=False)
        self.assertEqual(skipped.status, "skipped")
        self.assertFalse(skipped.executed)
        held = dao_ovs.start_dao_ovs_offload_placeholder(
            apply=False, dry_run=True, dao_available=True
        )
        self.assertEqual(held.status, "ok")
        self.assertFalse(held.executed)
        refused = dao_ovs.start_dao_ovs_offload_placeholder(
            apply=True, dry_run=False, dao_available=True
        )
        self.assertEqual(refused.status, "error")
        self.assertFalse(refused.executed)

    def test_telemetry_empty(self):
        tel = dao_ovs.collect_dao_telemetry_placeholder(dao_available=True)
        self.assertEqual(tel.telemetry, [])

    def test_backend_skips_nvidia_path(self):
        b = MarvellOcteonBackend()
        self.assertEqual(b.configure_switchdev("0000:05:00.0").status, "skipped")
        self.assertEqual(b.configure_ovs_bridge("br-int").status, "skipped")


if __name__ == "__main__":
    unittest.main()

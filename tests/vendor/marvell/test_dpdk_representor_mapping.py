"""Marvell DPDK representor mapping: high only with explicit vf_index."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.base.vm2vm_config import load_vm2vm_config, representor_mapping_settings
from plugins.marvell_octeon.dpdk_representor_map import map_dpdk_representors, parse_dao_port_list

FIXTURES = ROOT / "tests" / "fixtures" / "marvell_octeon"


class DpdkRepresentorMappingTests(unittest.TestCase):
    def test_example_uses_dpdk_model(self):
        cfg = load_vm2vm_config(ROOT / "config" / "lab-marvell-vm2vm.example.yaml")
        settings = representor_mapping_settings(cfg)
        self.assertEqual(settings["location"], "dpu")
        self.assertEqual(settings["model"], "dpdk")
        self.assertEqual(settings["strategy"], "marvell-dpdk-representor")
        self.assertTrue(settings["require_high_confidence"])

    def test_parse_dao_port_list(self):
        rows = parse_dao_port_list((FIXTURES / "dao_port_list.txt").read_text(encoding="utf-8"))
        self.assertEqual(rows[0]["vf_index"], 0)
        self.assertEqual(rows[0]["representor_name"], "net_octeon0")

    def test_explicit_vf_index_is_high_confidence(self):
        result = map_dpdk_representors(
            samples={
                "lspci": (FIXTURES / "lspci.txt").read_text(encoding="utf-8"),
                "dpdk_devbind": (FIXTURES / "dpdk_devbind_status.txt").read_text(encoding="utf-8"),
                "dao_port_list": (FIXTURES / "dao_port_list.txt").read_text(encoding="utf-8"),
            },
            vf_indexes=[0, 1],
        )
        mappings = result.details["mappings"]
        self.assertTrue(all(m["confidence"] == "high" for m in mappings))
        self.assertEqual(mappings[0]["representor_type"], "dpdk")
        self.assertEqual(mappings[0]["dpdk_port_id"], "0")

    def test_uncertain_map_is_low_confidence(self):
        result = map_dpdk_representors(
            samples={"dao_port_list": "pci=0000:05:00.2 name=unknown"},
            vf_indexes=[0],
        )
        self.assertEqual(result.details["mappings"][0]["confidence"], "low")
        self.assertIsNone(result.details["mappings"][0]["representor_name"])


if __name__ == "__main__":
    unittest.main()

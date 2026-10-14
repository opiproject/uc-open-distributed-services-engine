"""Intel IPU discovery from tests/fixtures/intel_ipu (no hardware)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.intel_ipu.discovery import (
    build_inventory,
    load_config,
    normalize_pci_address,
    parse_ip_d_link,
    parse_lspci_nn,
)
from tests.vendor.harness import FIXTURES, read_fixture, row_from_discovery


class IntelDiscoveryFixtureTests(unittest.TestCase):
    def test_lspci_ipu_not_e810(self):
        rows = parse_lspci_nn(read_fixture("intel_ipu", "lspci.txt"))
        self.assertEqual(len(rows), 1)
        ice = parse_lspci_nn("18:00.0 Ethernet controller: Intel E810\n")
        self.assertEqual(ice, [])

    def test_lspci_idpf_text_variant_is_ipu(self):
        # Regression for the host-4 discovery failure: the same PCI
        # device ID (8086:1452) can render as "Infrastructure Data Path
        # Function" instead of "Infrastructure Processing Unit E2100"
        # depending on the host's pci.ids database. Fixture copied from
        # example-output/host-4/discover.txt's real `lspci -nn` output.
        rows = parse_lspci_nn(read_fixture("intel_ipu", "lspci_idpf_host.txt"))
        self.assertEqual(len(rows), 1)
        self.assertIn("8086:1452", rows[0]["description"])
        self.assertIn("infrastructure data path function", rows[0]["description"].lower())

        # Broadcom NIC at a similar-looking address must not match.
        broadcom_only = parse_lspci_nn(
            "1f:00.0 Ethernet controller [0200]: Broadcom Inc. and "
            "subsidiaries BCM57416 NetXtreme-E Dual-Media 10G RDMA "
            "Ethernet Controller [14e4:16d8] (rev 01)\n"
        )
        self.assertEqual(broadcom_only, [])

    def test_pci_address_and_model_from_host_short_lspci_form(self):
        # Regression: real lspci -nn on host-4 prints the domain-less
        # short form ("0a:00.0", not "0000:0a:00.0"). The old `_PCI`
        # regex required a domain and silently left pci_address=None;
        # the old `_model_from_lspci` also mis-split on the colon
        # inside the address itself, corrupting `model`. Both must be
        # correct from this fixture alone (no ip -d link needed).
        rows = parse_lspci_nn(read_fixture("intel_ipu", "lspci_idpf_host.txt"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["pci_address"], "0000:0a:00.0")
        self.assertEqual(
            rows[0]["model"], "Intel Corporation Infrastructure Data Path Function"
        )

        # Domain-prefixed form must still work and normalize to itself.
        rows2 = parse_lspci_nn(read_fixture("intel_ipu", "lspci.txt"))
        self.assertEqual(rows2[0]["pci_address"], "0000:17:00.0")
        self.assertEqual(
            rows2[0]["model"], "Intel Corporation Infrastructure Processing Unit E2100"
        )

    def test_normalize_pci_address(self):
        self.assertEqual(normalize_pci_address("0a:00.0"), "0000:0a:00.0")
        self.assertEqual(normalize_pci_address("0000:0a:00.0"), "0000:0a:00.0")
        self.assertIsNone(normalize_pci_address(None))
        self.assertIsNone(normalize_pci_address(""))

    def test_parse_ip_d_link_reads_packed_single_line_attrs(self):
        # Regression: real `ip -d link show` packs parentdev/portname/
        # switchid onto the same line as `link/ether ...`, not one
        # attribute per line. Fixture copied verbatim from host-4.
        ifaces = {
            i["netdev"]: i
            for i in parse_ip_d_link(read_fixture("intel_ipu", "ip_d_link_host4.txt"))
        }
        self.assertEqual(ifaces["eno12399np0"]["parentdev"], "0000:1f:00.0")
        self.assertEqual(ifaces["eno12399np0"]["portname"], "p0")
        self.assertEqual(ifaces["eno12399np0"]["switchid"], "20b02afeffe604d4")
        self.assertEqual(ifaces["enp10s0f0"]["parentdev"], "0000:0a:00.0")
        self.assertEqual(ifaces["enp10s0f0d1"]["parentdev"], "0000:0a:00.0")
        self.assertNotIn("lo", ifaces)

    def test_host4_discovery_yields_pf_pci_and_pf_name(self):
        # End-to-end regression for the exact bug the user hit: before
        # this fix, real host-4 output produced pci_address=None and
        # netdev=None, so there was nothing to copy into
        # config/lab-intel-vm2vm.yaml's dpu.pf_pci / dpu.pf_name.
        result = build_inventory(
            lspci=read_fixture("intel_ipu", "lspci_idpf_host.txt"),
            ip_link=read_fixture("intel_ipu", "ip_d_link_host4.txt"),
        )
        self.assertEqual(len(result.devices), 1)
        self.assertEqual(result.devices[0].pci_address, "0000:0a:00.0")
        self.assertEqual(
            result.devices[0].vendor_extensions.get("subfunction_netdevs"),
            ["enp10s0f0d1", "enp10s0f0d2", "enp10s0f0d3"],
        )
        self.assertEqual(len(result.physical_functions), 1)
        pf = result.physical_functions[0]
        self.assertEqual(pf.pci_address, "0000:0a:00.0")
        self.assertEqual(pf.netdev, "enp10s0f0")

    def test_inventory_and_capability_report(self):
        cfg = load_config(FIXTURES / "intel_ipu" / "config.json")
        result = build_inventory(
            lspci=read_fixture("intel_ipu", "lspci.txt"),
            config=cfg,
        )
        self.assertEqual(result.devices[0].vendor, "intel")
        self.assertEqual(result.representors, [])
        self.assertIsNone(result.capabilities.switchdev)
        self.assertFalse(result.details["management_cli_executed"])

        row = row_from_discovery("intel-ipu", result)
        self.assertTrue(row.detected)
        self.assertTrue(row.management_available)
        self.assertTrue(row.opi_available)
        self.assertIsNone(row.switchdev_available)
        self.assertIsNone(row.representors_available)
        self.assertIsNone(row.ovs_hw_offload_available)
        self.assertIsNone(row.vdpa_available)


if __name__ == "__main__":
    unittest.main()

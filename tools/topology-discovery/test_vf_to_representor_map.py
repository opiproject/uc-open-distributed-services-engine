#!/usr/bin/env python3
"""Unit tests for vf_to_representor_map parsers (no hardware required)."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Dict

from vf_to_representor_map import (
    apply_plugins,
    index_lspci_by_pci,
    lspci_slot_to_pci_address,
    match_representor,
    normalize_pci_address,
    parse_args,
    parse_devlink_port_json,
    parse_devlink_port_text,
    parse_ethtool_i,
    parse_ip_link_phys,
    parse_lspci,
    parse_phys_port_name,
    vendor_hint_from_ids,
)

FIXTURES = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures"
)


class NormalizePciTests(unittest.TestCase):
    def test_basename_and_path(self) -> None:
        self.assertEqual(normalize_pci_address("0000:03:00.1"), "0000:03:00.1")
        self.assertEqual(
            normalize_pci_address("/sys/bus/pci/devices/0000:03:00.1"),
            "0000:03:00.1",
        )
        self.assertIsNone(normalize_pci_address("eth0"))
        self.assertIsNone(normalize_pci_address(None))


class PhysPortNameTests(unittest.TestCase):
    def test_vf_and_pf(self) -> None:
        vf = parse_phys_port_name("pf0vf3")
        self.assertEqual(vf["kind"], "vf")
        self.assertEqual(vf["pf_index"], 0)
        self.assertEqual(vf["vf_index"], 3)
        p = parse_phys_port_name("p0vf1")
        self.assertEqual(p["vf_index"], 1)
        c = parse_phys_port_name("c1pf0vf2")
        self.assertEqual(c["controller"], 1)
        self.assertEqual(c["vf_index"], 2)
        pf = parse_phys_port_name("pf0")
        self.assertEqual(pf["kind"], "pf")
        self.assertIsNone(pf["vf_index"])
        unknown = parse_phys_port_name("eth0v1")
        self.assertIsNone(unknown["kind"])
        self.assertIsNone(unknown["vf_index"])
        empty = parse_phys_port_name(None)
        self.assertIsNone(empty["raw"])

    def test_subfunction(self) -> None:
        sf = parse_phys_port_name("pf0sf0")
        self.assertEqual(sf["kind"], "sf")
        self.assertEqual(sf["pf_index"], 0)
        self.assertEqual(sf["sf_index"], 0)
        self.assertIsNone(sf["vf_index"])
        controller_sf = parse_phys_port_name("c1pf0sf3")
        self.assertEqual(controller_sf["kind"], "sf")
        self.assertEqual(controller_sf["controller"], 1)
        self.assertEqual(controller_sf["sf_index"], 3)
        # An SF name must never be interpreted as a VF.
        not_a_vf = parse_phys_port_name("pf0sf0")
        self.assertNotEqual(not_a_vf["kind"], "vf")


class EthtoolAndDevlinkTests(unittest.TestCase):
    def test_ethtool_i(self) -> None:
        parsed = parse_ethtool_i("driver: mlx5_core\nversion: 1.0\n")
        self.assertEqual(parsed["driver"], "mlx5_core")

    def test_devlink_text_pcivf(self) -> None:
        raw = (
            "pci/0000:03:00.0/2: type eth netdev eth1v0 flavour pcivf "
            "pfnum 0 vfnum 1 splittable false"
        )
        ports = parse_devlink_port_text(raw)
        self.assertEqual(len(ports), 1)
        self.assertEqual(ports[0]["flavour"], "pcivf")
        self.assertEqual(ports[0]["vfnum"], 1)
        self.assertEqual(ports[0]["netdev"], "eth1v0")
        self.assertEqual(ports[0]["pci_address"], "0000:03:00.0")

    def test_devlink_json(self) -> None:
        raw = """
        {"port": {
          "pci/0000:03:00.0/2": {
            "type": "eth", "netdev": "eth1v0", "flavour": "pcivf",
            "pfnum": 0, "vfnum": 1
          }
        }}
        """
        ports = parse_devlink_port_json(raw)
        self.assertEqual(ports[0]["vfnum"], 1)
        self.assertEqual(parse_devlink_port_json("not-json"), [])


class LspciTests(unittest.TestCase):
    def test_parse_lspci_line(self) -> None:
        raw = (
            "03:00.0 Ethernet controller [0200]: Mellanox Technologies "
            "MT42822 BlueField-2 [15b3:a2d2] (rev 01)\n"
            "03:00.1 Ethernet controller [0200]: Mellanox Technologies "
            "MT42822 BlueField-2 [15b3:a2d2]"
        )
        devices = parse_lspci(raw)
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0]["vendor_id"], "15b3")
        self.assertEqual(devices[0]["device_id"], "a2d2")
        self.assertEqual(devices[0]["revision"], "01")
        self.assertIsNone(devices[1]["revision"])

    def test_parse_lspci_unparseable_line_preserved(self) -> None:
        devices = parse_lspci("not a valid lspci line at all")
        self.assertEqual(len(devices), 1)
        self.assertTrue(devices[0]["parse_error"])

    def test_lspci_slot_to_pci_address_domain_omitted(self) -> None:
        self.assertEqual(lspci_slot_to_pci_address("03:00.1"), "0000:03:00.1")
        self.assertEqual(
            lspci_slot_to_pci_address("0000:03:00.1"), "0000:03:00.1"
        )
        self.assertIsNone(lspci_slot_to_pci_address(None))
        self.assertIsNone(lspci_slot_to_pci_address("not-a-slot"))

    def test_index_lspci_by_pci(self) -> None:
        devices = parse_lspci(
            "03:00.0 Ethernet controller [0200]: Mellanox Technologies "
            "MT42822 BlueField-2 [15b3:a2d2]"
        )
        indexed = index_lspci_by_pci(devices)
        self.assertIn("0000:03:00.0", indexed)
        self.assertEqual(indexed["0000:03:00.0"]["vendor_id"], "15b3")


class MatchAndVendorTests(unittest.TestCase):
    def test_match_prefers_devlink(self) -> None:
        ports = parse_devlink_port_text(
            "pci/0000:03:00.0/2: type eth netdev repr0 flavour pcivf pfnum 0 vfnum 0"
        )
        match = match_representor(0, "0000:03:00.0", "aabb", ports, {})
        self.assertEqual(match["representor_netdev"], "repr0")
        self.assertEqual(match["mapping_method"], "devlink_pcivf")

    def test_match_phys_port_name_fallback(self) -> None:
        ip_phys = {
            "eth0": {"phys_port_name": "pf0", "phys_switch_id": "aabb"},
            "eth0v1": {"phys_port_name": "pf0vf1", "phys_switch_id": "aabb"},
        }
        match = match_representor(1, "0000:03:00.0", "aabb", [], ip_phys)
        self.assertEqual(match["representor_netdev"], "eth0v1")
        self.assertEqual(match["mapping_method"], "phys_port_name")

    def test_no_guess_on_netdev_name(self) -> None:
        ip_phys = {"eth0v1": {"phys_port_name": None, "phys_switch_id": None}}
        match = match_representor(1, "0000:03:00.0", None, [], ip_phys)
        self.assertIsNone(match["representor_netdev"])

    def test_vendor_hint(self) -> None:
        high = vendor_hint_from_ids("15b3", None)
        self.assertEqual(high["vendor"], "nvidia_mellanox_bluefield")
        self.assertEqual(high["confidence"], "high")
        intel = vendor_hint_from_ids(None, "ice")
        self.assertEqual(intel["confidence"], "low")
        self.assertIsNone(vendor_hint_from_ids(None, "virtio_net"))

    def test_vendor_hint_lspci_fallback_only_when_others_absent(self) -> None:
        # sysfs vendor unreadable, no driver — lspci vendor id is used,
        # but only at medium confidence, never promoted to high.
        fallback = vendor_hint_from_ids(None, None, lspci_vendor_id="15b3")
        self.assertEqual(fallback["vendor"], "nvidia_mellanox_bluefield")
        self.assertEqual(fallback["confidence"], "medium")
        # A real sysfs vendor id still wins over the lspci fallback.
        preferred = vendor_hint_from_ids("177d", None, lspci_vendor_id="15b3")
        self.assertEqual(preferred["vendor"], "marvell_octeon")
        self.assertEqual(preferred["confidence"], "high")

    def test_no_representor_reason_no_data_sources(self) -> None:
        match = match_representor(0, "0000:03:00.0", None, [], {})
        self.assertIsNone(match["representor_netdev"])
        self.assertIn("no data source available", match["reason"])
        self.assertTrue(match["troubleshooting_hints"])

    def test_no_representor_reason_vfnum_not_found(self) -> None:
        ports = parse_devlink_port_text(
            "pci/0000:03:00.0/2: type eth netdev repr0 flavour pcivf pfnum 0 vfnum 5"
        )
        match = match_representor(0, "0000:03:00.0", None, ports, {})
        self.assertIsNone(match["representor_netdev"])
        self.assertIn("vfnum=0", match["reason"])

    def test_no_representor_reason_switch_id_mismatch(self) -> None:
        ip_phys = {"eth0v1": {"phys_port_name": "pf0vf1", "phys_switch_id": "ccdd"}}
        match = match_representor(1, "0000:03:00.0", "aabb", [], ip_phys)
        self.assertIsNone(match["representor_netdev"])
        self.assertIn("phys_switch_id did not match", match["reason"])


@unittest.skipUnless(FIXTURES.is_dir(), "shared vendor fixtures not found")
class FixtureIntegrationTests(unittest.TestCase):
    """Exercise the parsing/matching chain against the same recorded
    vendor command output used by tools/inventory/dpu_inventory.py and
    tools/telemetry-collector, instead of only hand-written strings.
    """

    def _read(self, *parts: str) -> str:
        return (FIXTURES.joinpath(*parts)).read_text(encoding="utf-8")

    def test_nvidia_bluefield_vf_and_sf_from_fixtures(self) -> None:
        ip_phys = parse_ip_link_phys(self._read("nvidia_bluefield", "ip_d_link.txt"))
        devlink_ports = parse_devlink_port_text(
            self._read("nvidia_bluefield", "devlink_port.txt")
        )
        lspci_devices = parse_lspci(self._read("nvidia_bluefield", "lspci.txt"))
        lspci_by_pci = index_lspci_by_pci(lspci_devices)

        self.assertEqual(lspci_by_pci["0000:03:00.0"]["vendor_id"], "15b3")

        # VF0 resolves via devlink (preferred path).
        match = match_representor(0, "0000:03:00.0", "7cfe90abcdef", devlink_ports, ip_phys)
        self.assertEqual(match["representor_netdev"], "pf0vf0")
        self.assertEqual(match["mapping_method"], "devlink_pcivf")

        # The SF entry in the same fixture must never be treated as a VF
        # match, even though it shares the pf0-prefixed naming scheme.
        sf_parsed = parse_phys_port_name(ip_phys["pf0sf0"]["phys_port_name"])
        self.assertEqual(sf_parsed["kind"], "sf")
        self.assertEqual(sf_parsed["sf_index"], 0)
        no_vf1 = match_representor(1, "0000:03:00.0", "7cfe90abcdef", devlink_ports, ip_phys)
        self.assertIsNone(no_vf1["representor_netdev"])
        self.assertIn("vfnum=1", no_vf1["reason"])

    def test_marvell_octeon_vendor_hint_from_lspci(self) -> None:
        lspci_devices = parse_lspci(self._read("marvell_octeon", "lspci.txt"))
        lspci_by_pci = index_lspci_by_pci(lspci_devices)
        marvell_device = lspci_by_pci["0000:05:00.0"]
        hint = vendor_hint_from_ids(
            None, None, lspci_vendor_id=marvell_device["vendor_id"]
        )
        self.assertEqual(hint["vendor"], "marvell_octeon")
        self.assertEqual(hint["confidence"], "medium")


class ApplyPluginsRawContextTests(unittest.TestCase):
    def test_raw_context_passed_but_not_serialized(self) -> None:
        seen: Dict[str, Any] = {}

        class _Recorder:
            name = "recorder"

            def annotate_mapping(self, mapping, context):
                seen["raw"] = context.get("raw")
                return mapping

            def extra_sections(self, context):
                return {}

        document = {"mappings": [{"vf_index": 0}]}
        raw = {"ip_phys": {"pf0sf0": {"phys_port_name": "pf0sf0"}}, "devlink_ports": []}
        result = apply_plugins([_Recorder()], document, raw=raw)
        self.assertEqual(seen["raw"], raw)
        self.assertNotIn("ip_phys", result)
        self.assertNotIn("devlink_ports", result)


class CliFlagTests(unittest.TestCase):
    def test_new_flags_parse(self) -> None:
        args = parse_args(
            [
                "--pf-pci",
                "0000:03:00.0",
                "--vendor",
                "nvidia_mellanox_bluefield",
                "--json",
                "--strict",
            ]
        )
        self.assertEqual(args.pf_pci, "0000:03:00.0")
        self.assertEqual(args.vendor, "nvidia_mellanox_bluefield")
        self.assertTrue(args.json)
        self.assertTrue(args.strict)

    def test_defaults_when_omitted(self) -> None:
        args = parse_args([])
        self.assertIsNone(args.pf_pci)
        self.assertIsNone(args.vendor)
        self.assertFalse(args.json)
        self.assertFalse(args.strict)


if __name__ == "__main__":
    unittest.main()
